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

# Nudge trigger: repeated quick re-entries into her clickable bbox within
# a short window read as "move, you're in the way" rather than a single
# hover/click/drag attempt. A single entry never reaches the threshold,
# so a plain hover/click/drag is completely unaffected -- only bouncing
# the cursor onto her a couple of times fast triggers a nudge.
NUDGE_APPROACH_THRESHOLD = 2
NUDGE_WINDOW_SEC = 0.8
NUDGE_COOLDOWN_SEC = 1.5

# Corner-flee trigger (2026-08-21): retired the old wanderer-side scheme
# where fleeing to a corner required NUDGE_TO_CORNER_THRESHOLD *nudges* in
# a row (each nudge itself already gated behind NUDGE_APPROACH_THRESHOLD
# rapid re-entries) -- too many entries needed to pile up before she'd
# actually get out of the way. Replaced with a direct, independent count
# of raw re-entries into her clickable bbox: as soon as the cursor has
# entered CORNER_FLEE_THRESHOLD times in a row, flee straight to the
# corner, no window/cooldown gating (that's still only relevant to the
# smaller hop above). "In a row" is bounded purely by a lull -- if she
# goes CORNER_FLEE_RESET_SEC without a fresh entry, the streak resets.
CORNER_FLEE_THRESHOLD = 3
CORNER_FLEE_RESET_SEC = 6.0


class CornerFleeApproachTracker:
    """Counts consecutive fresh re-entries into the clickable bbox,
    independent of NudgeApproachTracker's window/cooldown-gated hop
    trigger -- every fresh entry counts, back to back, until a
    CORNER_FLEE_RESET_SEC lull breaks the streak. Fires (and resets) once
    the streak reaches CORNER_FLEE_THRESHOLD.

    Pure logic, no AppKit/threading -- same rationale as
    NudgeApproachTracker.
    """

    def __init__(
        self,
        threshold: int = CORNER_FLEE_THRESHOLD,
        reset_sec: float = CORNER_FLEE_RESET_SEC,
    ):
        self._threshold = threshold
        self._reset_sec = reset_sec
        self._streak = 0
        self._last_entry_time = 0.0

    def on_tick(self, now_interactive: bool, was_interactive: bool, now: float) -> bool:
        """Call once per poll tick with the freshly-computed interactive
        state. Returns True exactly on the tick that crosses the streak
        threshold -- caller should flee to the corner then."""
        if not (now_interactive and not was_interactive):
            return False  # not a fresh entry (dwelling or still outside)
        if now - self._last_entry_time > self._reset_sec:
            self._streak = 0  # long lull -- fresh streak starts here
        self._last_entry_time = now
        self._streak += 1
        if self._streak < self._threshold:
            return False
        self._streak = 0
        return True


class NudgeApproachTracker:
    """Counts fresh transitions into the clickable bbox and decides when
    that counts as repeated bumping rather than a single interaction
    attempt.

    Pure logic, no AppKit/threading -- kept separate from
    PassthroughController so it's unit-testable without a real NSWindow
    or the background poll thread.
    """

    def __init__(
        self,
        threshold: int = NUDGE_APPROACH_THRESHOLD,
        window_sec: float = NUDGE_WINDOW_SEC,
        cooldown_sec: float = NUDGE_COOLDOWN_SEC,
    ):
        self._threshold = threshold
        self._window_sec = window_sec
        self._cooldown_sec = cooldown_sec
        self._entries: list[float] = []
        self._cooldown_until = 0.0

    def on_tick(self, now_interactive: bool, was_interactive: bool, now: float) -> bool:
        """Call once per poll tick with the freshly-computed interactive
        state (cursor over an opaque pixel). Returns True exactly on the
        tick that crosses the approach threshold -- caller should fire
        the nudge then."""
        if not (now_interactive and not was_interactive):
            return False  # not a fresh entry (dwelling or still outside)
        if now < self._cooldown_until:
            return False  # cooling down from the last nudge
        self._entries = [t for t in self._entries if now - t < self._window_sec]
        self._entries.append(now)
        if len(self._entries) < self._threshold:
            return False
        self._entries.clear()
        self._cooldown_until = now + self._cooldown_sec
        return True


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
        self._current_edge = ""
        self._paused = False
        # hide-mode (2026-07-16 Pink/Indigo): when True the window is
        # invisible AND frozen, so we force always-ignore mouse events
        # -- the hidden window must never intercept a click.
        self._hidden = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_ignore: bool | None = None
        # Nudge trigger (repeated-approach detector) -- see set_nudge_callback().
        self._nudge_callback = None
        self._nudge_tracker = NudgeApproachTracker()
        # Corner-flee trigger (independent streak) -- see set_corner_flee_callback().
        self._corner_flee_callback = None
        self._corner_flee_tracker = CornerFleeApproachTracker()
        self._was_interactive = False
        print(f"[squid-pet] passthrough: loaded {len(self._masks)} alpha masks", flush=True)

    # ── Public API ──
    def set_nudge_callback(self, cb) -> None:
        """Register cb(cursor_x, cursor_y), invoked when NudgeApproachTracker
        detects repeated rapid re-entries into her clickable bbox. cb should
        be fast/non-blocking (called from the poll thread)."""
        self._nudge_callback = cb

    def set_corner_flee_callback(self, cb) -> None:
        """Register cb(cursor_x, cursor_y), invoked when
        CornerFleeApproachTracker sees CORNER_FLEE_THRESHOLD consecutive
        fresh re-entries into her clickable bbox. Independent of the
        nudge-hop callback above -- both fire off the same raw entry
        stream but track separate streaks/thresholds. cb should be
        fast/non-blocking (called from the poll thread)."""
        self._corner_flee_callback = cb

    def set_state(self, state: str) -> None:
        with self._lock:
            # Backend state "approval_needed" displays sprites/
            # attention_needed*.png (see frontend/index.html's
            # spriteUrl()) -- there is no approval_needed.png, so
            # mirror that remap here too. Without it, `state in
            # self._masks` is False and _current_state silently keeps
            # whatever mask preceded the flag-wave, so hit-testing
            # runs against the wrong sprite's alpha channel while she's
            # actively waving for attention.
            mask_key = "attention_needed" if state == "approval_needed" else state
            if mask_key in self._masks:
                self._current_state = mask_key

    def set_edge(self, edge: str) -> None:
        """Track current edge for CSS-transform-aware hit testing."""
        with self._lock:
            self._current_edge = edge or ""

    def pause(self) -> None:
        """Disable passthrough toggling (called when user is dragging)."""
        with self._lock:
            self._paused = True
        # While paused, ensure window is clickable
        self._apply_ignore(False)

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def set_hidden(self, hidden: bool) -> None:
        """Hide Squid: force full click-through while invisible.
        When hidden the loop stops hit-testing and the window always
        ignores mouse events so it is truly 'not available'."""
        with self._lock:
            self._hidden = bool(hidden)
        if hidden:
            # Immediately make the window click-through; don't wait for
            # the next poll tick.
            self._apply_ignore(True)

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True, name="squid-passthrough")
        t.start()

    def stop(self) -> None:
        self._stop.set()

    # ── Internals ──
    def _track_nudge(self, now_interactive: bool, cx: float, cy: float) -> None:
        """Feed the current interactive (opaque-hit) state to both the
        hop tracker and the corner-flee tracker, firing whichever
        callback(s) cross threshold on this tick. Only called from the
        poll loop thread, so no lock needed on _was_interactive."""
        now = time.time()
        was = self._was_interactive
        fire_hop = self._nudge_tracker.on_tick(now_interactive, was, now)
        fire_corner = self._corner_flee_tracker.on_tick(now_interactive, was, now)
        self._was_interactive = now_interactive
        if fire_hop and self._nudge_callback is not None:
            try:
                self._nudge_callback(cx, cy)
            except Exception as e:
                print(f"[squid-pet] nudge callback failed: {e}", flush=True)
        if fire_corner and self._corner_flee_callback is not None:
            try:
                self._corner_flee_callback(cx, cy)
            except Exception as e:
                print(f"[squid-pet] corner-flee callback failed: {e}", flush=True)

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
        # Guard the check-and-set on _last_ignore with self._lock, same
        # as every sibling field (_paused, _hidden, _current_state,
        # _current_edge) -- this was the one unguarded state access in
        # the class (found in a 2026-08-17 review). Without it, the
        # poll loop and a caller like pause()/set_hidden() (invoked
        # from the drag thread or main thread) could both read a stale
        # _last_ignore concurrently, both pass the equality
        # short-circuit, and dispatch conflicting main-thread mutations
        # -- whichever callAfter lands last on AppKit's queue wins the
        # real window state, but _last_ignore records whichever thread
        # wrote last in program order, which need not match. A later
        # genuine state change could then be silently skipped because
        # the cached value no longer reflects reality.
        with self._lock:
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
                    hidden = self._hidden
                    state = self._current_state

                if hidden:
                    # Hidden: keep the invisible window fully
                    # click-through, skip all hit-testing.
                    self._apply_ignore(True)
                    time.sleep(POLL_INTERVAL)
                    continue

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
                    self._track_nudge(False, cx, cy)
                    tick += 1
                    if tick % 100 == 0:
                        with self._lock:
                            _ignore_for_log = self._last_ignore
                        print(f"[squid-pet] tick {tick}: cursor=({cx:.0f},{cy:.0f}) "
                              f"win=({win_x:.0f},{win_y:.0f},{win_w:.0f}x{win_h:.0f}) "
                              f"OUTSIDE state={state} ignore={_ignore_for_log}",
                              flush=True)
                    time.sleep(POLL_INTERVAL)
                    continue

                # Cursor inside window — figure out which pixel of the sprite
                # Window-local coords (top-left origin to match image)
                local_x = cx - win_x
                local_y = win_h - (cy - win_y)   # flip Y (Cocoa→image)

                # Map to sprite-local coords
                # When edge=="top", CSS applies translateY(-80px) which
                # shifts the visual sprite up 80px. Adjust hit-test to match.
                with self._lock:
                    edge = self._current_edge
                top_offset = 80 if edge == "top" else 0
                sprite_x = int(local_x - SPRITE_LEFT)
                sprite_y = int(local_y - (SPRITE_TOP - top_offset))

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
                # Snapshot _last_ignore under the lock (2026-08-17 fix --
                # this was previously read unlocked here, racing against
                # _apply_ignore's write from pause()/set_hidden() on
                # another thread). A tiny window remains between this
                # snapshot and the _apply_ignore() call below where
                # another thread could change the real value first, but
                # that's benign: _apply_ignore's own check-and-set is
                # itself locked and always leaves _last_ignore consistent
                # with whichever write actually won.
                with self._lock:
                    _last_ignore_snapshot = self._last_ignore
                if _last_ignore_snapshot is None:
                    want_ignore = alpha_val <= ALPHA_THRESHOLD
                elif _last_ignore_snapshot:  # currently passthrough
                    want_ignore = alpha_val <= ALPHA_THRESHOLD
                else:  # currently interactive
                    want_ignore = alpha_val < 5
                opaque = not want_ignore  # for diagnostics below
                self._apply_ignore(want_ignore)
                self._track_nudge(opaque, cx, cy)

                tick += 1
                if tick % 100 == 0:  # ~3 seconds
                    with self._lock:
                        _ignore_for_log = self._last_ignore
                    print(f"[squid-pet] tick {tick}: cursor=({cx:.0f},{cy:.0f}) "
                          f"win=({win_x:.0f},{win_y:.0f},{win_w:.0f}x{win_h:.0f}) "
                          f"inside={inside} sprite=({sprite_x},{sprite_y}) "
                          f"state={state} opaque={opaque} ignore={_ignore_for_log}",
                          flush=True)

            except Exception as e:
                print(f"[squid-pet] passthrough error: {e}", flush=True)

            time.sleep(POLL_INTERVAL)
