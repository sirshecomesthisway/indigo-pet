"""
Pixel-perfect click passthrough for Squid.

Runs a background thread that:
  1. Polls global cursor position via NSEvent.mouseLocation()
  2. Maps cursor → window-local → sprite-local coords
  3. Reads alpha channel of currently-displayed sprite at that pixel
  4. Toggles NSWindow.setIgnoresMouseEvents_:
     - over opaque pixel (alpha > THRESHOLD) → ignore_mouse=False (clicks land on Squid)
     - over transparent pixel               → ignore_mouse=True  (clicks pass through)

Uses NSEvent.mouseLocation() which works regardless of window's ignore state,
so we can poll continuously and always know where the cursor is.

While the user is actively dragging Squid, passthrough is paused so dragging
doesn't accidentally toggle off mid-drag.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PIL import Image


# Window + sprite geometry (must match window.py + frontend)
WINDOW_WIDTH    = 200
WINDOW_HEIGHT   = 300  # was 220; matches window.py
SPRITE_WIDTH    = 180
SPRITE_HEIGHT   = 180
SPRITE_LEFT     = (WINDOW_WIDTH  - SPRITE_WIDTH)  // 2     # 10
SPRITE_TOP      = WINDOW_HEIGHT - SPRITE_HEIGHT            # 120 (flush with window bottom, no buffer)

# Alpha cutoff (0-255). Anything below = treat as transparent.
ALPHA_THRESHOLD = 30

# How often to poll cursor (seconds). 30ms = ~33fps, smooth & cheap.
POLL_INTERVAL = 0.03

SPRITES_DIR = Path(__file__).parent / "frontend" / "sprites"


def load_alpha_masks() -> dict[str, "Image.Image"]:
    """Pre-load alpha channels for all sprites, resized to display size."""
    masks: dict[str, "Image.Image"] = {}
    for png in sorted(SPRITES_DIR.glob("*.png")):
        if png.name.startswith("_"):
            continue
        try:
            img = Image.open(png).convert("RGBA")
            alpha = img.split()[3]  # alpha channel
            alpha = alpha.resize((SPRITE_WIDTH, SPRITE_HEIGHT), Image.NEAREST)
            # Dilate the alpha mask by ~6 pixels using MaxFilter so the hit-target
            # extends a few pixels beyond the visible silhouette. Without this,
            # clicks on the very edge of an irregular sprite (e.g. tip of head,
            # outstretched arm) fall in the transparent halo between the character
            # and its bounding box, and the click passes through. 13 = 6-pixel
            # radius cross-shaped max filter, applied twice for ~9px effective halo.
            from PIL import ImageFilter
            alpha = alpha.filter(ImageFilter.MaxFilter(25))   # 12px halo (was 6px - too tight, Pink missed too often)
            masks[png.stem] = alpha
        except Exception as e:
            print(f"[squid-pet] failed loading alpha for {png.name}: {e}", flush=True)
    return masks


def _propagate_ignore(view, ignore: bool) -> None:
    """Recursively set the macOS view's ignoresMouseEvents-equivalent.

    NSView doesn't directly have setIgnoresMouseEvents_, but we can use
    setAcceptsTouchEvents_(False) + the parent NSWindow's ignore flag.
    The most reliable approach: walk subviews and try common properties.
    """
    try:
        # Try the WKWebView-specific accessor first
        if hasattr(view, "setUserInteractionEnabled_"):
            view.setUserInteractionEnabled_(not ignore)
    except Exception:
        pass
    try:
        # NSView responder chain — disable hit testing via wantsLayer + layer.hitTest
        # Simpler: walk subviews
        subviews = view.subviews()
        for sv in subviews:
            _propagate_ignore(sv, ignore)
    except Exception:
        pass


class PassthroughController:
    """
    Manages click-through state. The window.py owns one of these and:
      - sets it `pause()` during active drag
      - calls `resume()` after drag ends
      - calls `set_state(state_name)` whenever pet state changes
      - calls `start()` once to begin the polling thread
    """

    def __init__(self, get_ns_window_callable):
        self._get_ns_window = get_ns_window_callable
        self._masks = load_alpha_masks()
        self._current_state = "idle"
        self._paused = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_ignore: bool | None = None
        print(f"[squid-pet] passthrough: loaded {len(self._masks)} alpha masks", flush=True)

    # ── Public API ──
    def set_state(self, state: str) -> None:
        with self._lock:
            if state in self._masks:
                self._current_state = state

    def pause(self) -> None:
        """Disable passthrough toggling (called when user is dragging)."""
        with self._lock:
            self._paused = True
        # While paused, ensure window is clickable
        self._apply_ignore(False)

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True, name="squid-passthrough")
        t.start()

    def stop(self) -> None:
        self._stop.set()

    # ── Internals ──
    def _alpha_at(self, mask, sx: int, sy: int) -> int:
        """MAX alpha in a 5-pixel cross neighborhood (robust to CSS animation jitter)."""
        if mask is None:
            return 0
        w, h = mask.size
        best = 0
        for dx, dy in [(0, 0), (-3, 0), (3, 0), (0, -3), (0, 3)]:
            x, y = sx + dx, sy + dy
            if 0 <= x < w and 0 <= y < h:
                a = int(mask.getpixel((x, y)))
                if a > best:
                    best = a
        return best

    def _apply_ignore(self, ignore: bool) -> None:
        """
        Set ignoresMouseEvents on the NSWindow AND its contentView.

        Why both: with a transparent + frameless window hosting a WKWebView,
        the webview's NSView can intercept mouse events even when the NSWindow
        is set to ignore them. Applying to both layers is reliable.
        """
        if ignore == self._last_ignore:
            return
        nw = self._get_ns_window()
        if nw is None:
            return
        try:
            from PyObjCTools import AppHelper
            ig = bool(ignore)

            # ── Cocoa main-thread safety (safe-startup-verification, layer 1)
            # setIgnoresMouseEvents_ + setUserInteractionEnabled_ MUST run on
            # the main thread; from a worker thread on macOS 14+ they block
            # indefinitely (the 2026-06-16 wedge bug). We dispatch via
            # AppHelper.callAfter here rather than @cocoa_main_thread because
            # _apply_on_main is a closure over nw / ig / contentView — the
            # decorator pattern wants a module-level callable. callAfter is
            # functionally equivalent (decorator wraps it under the hood) and
            # this site is the only callAfter in the codebase that ISN'T
            # behind the decorator. If this gets refactored later, lift
            # _apply_on_main to a method on PassthroughManager + apply
            # @cocoa_main_thread; tests in test_cocoa_main_thread_hook.py
            # already prove the dispatch contract.
            def _apply_on_main():
                try:
                    nw.setIgnoresMouseEvents_(ig)
                    cv = nw.contentView()
                    if cv is not None:
                        _propagate_ignore(cv, ig)
                except Exception as e:
                    print(f"[squid-pet] _apply_on_main failed: {e}", flush=True)

            AppHelper.callAfter(_apply_on_main)
            self._last_ignore = ignore
            print(f"[squid-pet] passthrough → ignore={ignore}", flush=True)
        except Exception as e:
            print(f"[squid-pet] setIgnoresMouseEvents failed: {e}", flush=True)

    def _loop(self) -> None:
        try:
            from AppKit import NSEvent
        except ImportError:
            print("[squid-pet] AppKit unavailable; passthrough disabled", flush=True)
            return

        print("[squid-pet] passthrough loop started", flush=True)
        tick = 0

        while not self._stop.is_set():
            try:
                with self._lock:
                    paused = self._paused
                    state = self._current_state

                if paused:
                    time.sleep(POLL_INTERVAL)
                    continue

                nw = self._get_ns_window()
                if nw is None:
                    time.sleep(POLL_INTERVAL)
                    continue

                # Get cursor position (Cocoa coords: origin bottom-left of main screen)
                loc = NSEvent.mouseLocation()
                cx, cy = loc.x, loc.y

                frame = nw.frame()
                win_x = frame.origin.x
                win_y = frame.origin.y
                win_w = frame.size.width
                win_h = frame.size.height

                # Is cursor inside the window's bounding box?
                inside = (win_x <= cx <= win_x + win_w and
                          win_y <= cy <= win_y + win_h)

                if not inside:
                    # Cursor outside: keep window in passthrough so it never blocks anything
                    self._apply_ignore(True)
                    tick += 1
                    if tick % 100 == 0:
                        print(f"[squid-pet] tick {tick}: cursor=({cx:.0f},{cy:.0f}) "
                              f"win=({win_x:.0f},{win_y:.0f},{win_w:.0f}x{win_h:.0f}) "
                              f"OUTSIDE state={state} ignore={self._last_ignore}",
                              flush=True)
                    time.sleep(POLL_INTERVAL)
                    continue

                # Cursor inside window — figure out which pixel of the sprite
                # Window-local coords (top-left origin to match image)
                local_x = cx - win_x
                local_y = win_h - (cy - win_y)   # flip Y (Cocoa→image)

                # Map to sprite-local coords
                sprite_x = int(local_x - SPRITE_LEFT)
                sprite_y = int(local_y - SPRITE_TOP)

                # Hit test: simple BOUNDING BOX around the character (with generous
                # halo). Was: dilated alpha mask, but irregular silhouette left
                # gaps where Pink's clicks fell through (2026-06-11). Bbox is
                # predictable and matches user intent ("anywhere ON the character").
                #
                # Character art bbox in sprite coords: (38,35)-(138,138).
                # With CLICK_HALO_PX padding on all sides:
                # Worst-case sprite envelope (was idle-only, missed wider states).
                CLICK_HALO_PX = 15
                CHAR_BBOX_MIN_X = 21 - CLICK_HALO_PX     # 6   [celebrating]
                CHAR_BBOX_MAX_X = 160 + CLICK_HALO_PX    # 175 [thinking]
                CHAR_BBOX_MIN_Y = 15 - CLICK_HALO_PX     # 0   [thinking]
                CHAR_BBOX_MAX_Y = 172 + CLICK_HALO_PX    # 187 [DROWSY — was missed in prior analysis]
                in_char_bbox = (CHAR_BBOX_MIN_X <= sprite_x <= CHAR_BBOX_MAX_X and
                                CHAR_BBOX_MIN_Y <= sprite_y <= CHAR_BBOX_MAX_Y)
                # Use alpha_val to keep the rest of the logic compatible.
                alpha_val = 255 if in_char_bbox else 0

                # Hysteresis to prevent flip-flop near body edges:
                #   was passthrough? → need alpha > 30 to become interactive
                #   was interactive? → need alpha <  5 to become passthrough
                if self._last_ignore is None:
                    want_ignore = alpha_val <= ALPHA_THRESHOLD
                elif self._last_ignore:  # currently passthrough
                    want_ignore = alpha_val <= ALPHA_THRESHOLD
                else:  # currently interactive
                    want_ignore = alpha_val < 5
                opaque = not want_ignore  # for diagnostics below
                self._apply_ignore(want_ignore)

                tick += 1
                if tick % 100 == 0:  # ~3 seconds
                    print(f"[squid-pet] tick {tick}: cursor=({cx:.0f},{cy:.0f}) "
                          f"win=({win_x:.0f},{win_y:.0f},{win_w:.0f}x{win_h:.0f}) "
                          f"inside={inside} sprite=({sprite_x},{sprite_y}) "
                          f"state={state} opaque={opaque} ignore={self._last_ignore}",
                          flush=True)

            except Exception as e:
                print(f"[squid-pet] passthrough error: {e}", flush=True)

            time.sleep(POLL_INTERVAL)
