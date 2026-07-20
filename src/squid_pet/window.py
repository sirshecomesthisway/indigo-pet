"""
Squid Pet Window — uses NSWindow directly for accurate positioning + dragging.

Why NSWindow direct: pywebview's window.move() can have origin issues on
multi-display setups. NSWindow's setFrameOrigin_ uses Cocoa's native bottom-left
origin and works correctly with NSScreen.visibleFrame.

Drag is implemented via JS mousemove → Python api.move_window_by() rather than
-webkit-app-region (which seems flaky in pywebview's WKWebView build).
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import asdict
from pathlib import Path

import webview

from . import watcher
from .passthrough import PassthroughController
from . import llm_client as _llm_client_mod
from . import observer
from . import config


# ──────────────────────────────────────────────────────────────────
HERE = Path(__file__).parent
FRONTEND_HTML = HERE / "frontend" / "index.html"

WINDOW_WIDTH  = 200
WINDOW_HEIGHT = 300  # was 220; bumped to give hearts headroom above sprite
EDGE_MARGIN   = 20

POSITION_FILE = Path.home() / ".squid-pet" / "position.json"
SETTINGS_FILE = Path.home() / ".squid-pet" / "settings.json"
CORNERS = ["top-right", "bottom-right", "bottom-left", "top-left"]


# ──────────────────────────────────────────────────────────────────
# NSWindow helpers (Cocoa direct — no accessibility needed)
# ──────────────────────────────────────────────────────────────────
def _get_ns_window():
    """Return the NSWindow for our pywebview window, or None."""
    try:
        from AppKit import NSApp
        app = NSApp() if callable(NSApp) else NSApp
        if app is None:
            return None
        for w in app.windows():
            # Filter — skip helper/hidden windows
            try:
                if w.isVisible():
                    return w
            except Exception:
                continue
    except Exception as e:
        print(f"[squid-pet] NSWindow fetch failed: {e}", flush=True)
    return None


# ── On-screen bbox of the visible character within the window ──
# Window-local cocoa offsets (origin = bottom-left of window).
# Per kennel drawer #126: sprite alpha bbox is (38,35)-(138,138) inside the
# 180x180 sprite, which sits at SPRITE_LEFT=10, SPRITE_TOP=120 within the
# 200x300 window. Convert to window-local cocoa Y (bottom-up) for clamping.
# Worst-case envelope across ALL sprite states (idle/blink/thinking/celebrating/etc).
# Computed: max extent of opaque pixels across every PNG in frontend/sprites/.
# Wider states (thinking max_x=159, celebrating min_x=21) were getting clipped
# at screen edge when clamped to idle bbox only. (Pink 2026-06-11.)
CHAR_LEFT_IN_WIN   = 51     # SPRITE_LEFT(10) + min_x(21) + 20px rotation padding (CSS rotate up to ±14deg)
CHAR_RIGHT_IN_WIN  = 190    # SPRITE_LEFT(10) + max_x(160) + 20px rotation padding (CSS rotate up to ±14deg)
CHAR_BOTTOM_IN_WIN = 8      # WINDOW_H(300) - SPRITE_TOP(120) - worst max_y(172) [DROWSY — was missed in prior analysis]
CHAR_TOP_IN_WIN    = 145    # WINDOW_H(300) - SPRITE_TOP(120) - typical min_y(35, idle).
                            # Was 165 (thinking/flag-wave worst-case) but that left her
                            # head 20-40px below menu bar in idle. Trade-off: flag tips
                            # (attention_needed) clip ~34px behind menu bar. Menu bar is
                            # opaque so it is a clean cut, not a visual glitch. Pink 2026-07-07.
TOP_MARGIN_PX      = 35     # MIRRORS wanderer.TOP_MARGIN_PX — pulls top-edge origin down so the
                            # 180deg-rotated body doesn't clip the physical screen top. Pink 2026-07-12.


def clamp_origin_to_screen(ox, oy):
    """Clamp NSWindow cocoa origin so the visible character body stays fully
    inside NSScreen.visibleFrame. Returns (clamped_ox, clamped_oy)."""
    try:
        from AppKit import NSScreen
        main = NSScreen.mainScreen()
        if main is None:
            return ox, oy
        f = main.visibleFrame()
        vx, vy = f.origin.x, f.origin.y
        vw, vh = f.size.width, f.size.height
        min_ox = vx - CHAR_LEFT_IN_WIN
        max_ox = vx + vw - CHAR_RIGHT_IN_WIN
        min_oy = vy - CHAR_BOTTOM_IN_WIN
        max_oy = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
        return (max(min_ox, min(max_ox, ox)),
                max(min_oy, min(max_oy, oy)))
    except Exception:
        return ox, oy


def _visible_frame():
    """Return NSScreen.mainScreen().visibleFrame as (x, y, w, h) in Cocoa coords."""
    from AppKit import NSScreen
    main = NSScreen.mainScreen()
    if main is None:
        screens = list(NSScreen.screens())
        if not screens:
            return (0, 0, 1440, 900)
        main = screens[0]
    f = main.visibleFrame()
    return (f.origin.x, f.origin.y, f.size.width, f.size.height)


def corner_origin(corner: str) -> tuple[float, float]:
    """
    Compute NSWindow setFrameOrigin (bottom-left of window in Cocoa coords)
    for each corner of the visible frame.
    """
    vx, vy, vw, vh = _visible_frame()
    if corner == "top-right":
        return (vx + vw - WINDOW_WIDTH - EDGE_MARGIN,
                vy + vh - WINDOW_HEIGHT - EDGE_MARGIN)
    elif corner == "bottom-right":
        return (vx + vw - WINDOW_WIDTH - EDGE_MARGIN,
                vy + EDGE_MARGIN)
    elif corner == "bottom-left":
        return (vx + EDGE_MARGIN,
                vy + EDGE_MARGIN)
    elif corner == "top-left":
        return (vx + EDGE_MARGIN,
                vy + vh - WINDOW_HEIGHT - EDGE_MARGIN)
    else:
        return (vx + EDGE_MARGIN, vy + EDGE_MARGIN)


from squid_pet.threading_guards import cocoa_main_thread


@cocoa_main_thread
def move_to_corner(corner: str) -> bool:
    """Move our NSWindow to the named corner. Returns True on success."""
    nw = _get_ns_window()
    if nw is None:
        return False
    x, y = corner_origin(corner)
    try:
        from Foundation import NSPoint
        nw.setFrameOrigin_(NSPoint(x, y))
        return True
    except Exception as e:
        print(f"[squid-pet] move_to_corner failed: {e}", flush=True)
        return False


@cocoa_main_thread
def move_window_by_delta(dx: float, dy: float) -> tuple[float, float] | None:
    """
    Move our NSWindow by (dx, dy) in SCREEN pixels. dy is positive = DOWN
    in screen coords (we'll flip to Cocoa internally). Returns new origin.
    """
    nw = _get_ns_window()
    if nw is None:
        return None
    try:
        from Foundation import NSPoint
        frame = nw.frame()
        # Cocoa: y is from bottom. Screen movement: positive dy = down = subtract from y
        new_x = frame.origin.x + dx
        new_y = frame.origin.y - dy
        nw.setFrameOrigin_(NSPoint(new_x, new_y))
        return (new_x, new_y)
    except Exception as e:
        print(f"[squid-pet] move_window_by_delta failed: {e}", flush=True)
        return None


# ──────────────────────────────────────────────────────────────────
# Persistent corner
# ──────────────────────────────────────────────────────────────────
def load_corner() -> str:
    """Resolve which corner Squid should start in.

    Priority: position.json (last saved location) -> settings.json
    starting_corner (user-configured intent) -> "bottom-right"
    (Pink-blessed default 2026-06-25; feels more natural than top-right
    because the dock + macOS menu bar already crowd the top edge)."""
    try:
        if POSITION_FILE.exists():
            data = json.loads(POSITION_FILE.read_text())
            c = data.get("corner")
            if c in CORNERS:
                return c
    except Exception:
        pass
    # Fall back to user-configured starting_corner from settings.json.
    try:
        settings_file = Path.home() / ".squid-pet" / "settings.json"
        if settings_file.exists():
            s = json.loads(settings_file.read_text())
            c = s.get("starting_corner")
            if c in CORNERS:
                return c
    except Exception:
        pass
    return "bottom-right"


def save_corner(corner: str) -> None:
    POSITION_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSITION_FILE.write_text(json.dumps({"corner": corner}, indent=2))


def load_settings() -> dict:
    """Persistent settings (stroll mode, future toggles). Safe defaults."""
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


# ──────────────────────────────────────────────────────────────────
# JS↔Python bridge
# ──────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────
# Python-side drag thread
# Reads cursor via NSEvent.mouseLocation() at 60Hz and moves the window.
# Auto-stops when the OS reports the left mouse button has been released
# (so a lost JS mouseup can never strand the drag).
# ──────────────────────────────────────────────────────────────────
import threading as _threading
import time as _time

_drag_thread: _threading.Thread | None = None
_drag_stop = _threading.Event()


def _native_drag_loop(start_cursor, start_origin, passthrough, on_end, on_swing=None):
    """Poll NSEvent at 60Hz; move window to follow cursor; stop on button-up.
    If on_swing callback is provided, fires when a vigorous up-down swing
    gesture is detected (4+ y-direction reversals within ~500ms = 2+ swings).
    """
    try:
        from AppKit import NSEvent
    except Exception as e:
        print(f"[squid-pet] drag loop import failed: {e}", flush=True)
        return

    nw = _get_ns_window()
    if nw is None:
        return

    sx0, sy0 = start_cursor       # initial cursor (Cocoa coords, y-from-bottom)
    ox0, oy0 = start_origin       # initial window origin (Cocoa)

    deadline = _time.time() + 30.0   # 30s safety watchdog
    last_print = 0

    # ─── Swing detection state ───
    # Track last cursor y to detect direction changes. We count reversals
    # in a sliding time window (~500ms). 4 reversals = up-down-up-down = 2
    # full swings = clearly intentional "shake her awake" gesture.
    swing_history = []            # list of (timestamp, direction) for reversals
    swing_window = 0.6            # seconds — how long a swing motion can take
    swing_threshold = 4           # reversals required to fire
    swing_min_delta_px = 8        # ignore tiny jitters
    last_cy = None
    last_direction = 0            # +1 = moving up, -1 = down, 0 = none
    swing_fired = False           # only fire once per drag

    while not _drag_stop.is_set():
        try:
            # Bit 0 = primary (left) button. If 0, user released.
            buttons = NSEvent.pressedMouseButtons()
            if (buttons & 1) == 0:
                print("[squid-pet] drag: OS reports button released → auto-end", flush=True)
                break

            loc = NSEvent.mouseLocation()
            cx, cy = loc.x, loc.y
            dx = cx - sx0
            dy = cy - sy0  # Cocoa: positive y = up

            # ─── Swing detection: track y-direction reversals ───
            if on_swing is not None and not swing_fired:
                if last_cy is not None:
                    cy_delta = cy - last_cy
                    if abs(cy_delta) >= swing_min_delta_px:
                        new_direction = 1 if cy_delta > 0 else -1
                        if last_direction != 0 and new_direction != last_direction:
                            # Direction reversal!
                            now = _time.time()
                            swing_history.append(now)
                            # Drop old entries outside the window
                            swing_history = [t for t in swing_history if now - t < swing_window]
                            if len(swing_history) >= swing_threshold:
                                print(f"[squid-pet] SWING detected ({len(swing_history)} "
                                      f"reversals in {swing_window}s) → wake!", flush=True)
                                try: on_swing()
                                except Exception as e:
                                    print(f"[squid-pet] swing callback err: {e}", flush=True)
                                swing_fired = True
                        last_direction = new_direction
                last_cy = cy
            new_x = ox0 + dx
            new_y = oy0 + dy

            # Restrict drag: clamp window origin so visible character bbox
            # stays inside visibleFrame. (Pink 2026-06-11: she dragged Squid
            # off-screen accidentally; clamp + snap-back is the fix.)
            new_x, new_y = clamp_origin_to_screen(new_x, new_y)

            from Foundation import NSPoint
            from PyObjCTools import AppHelper
            AppHelper.callAfter(nw.setFrameOrigin_, NSPoint(new_x, new_y))

            # Throttled debug print
            now = _time.time()
            if now - last_print > 0.5:
                print(f"[squid-pet] drag tick: cursor=({cx:.0f},{cy:.0f}) "
                      f"origin=({new_x:.0f},{new_y:.0f})", flush=True)
                last_print = now

            if _time.time() > deadline:
                print("[squid-pet] drag: 30s watchdog hit → auto-end", flush=True)
                break

            _time.sleep(1.0 / 60.0)
        except Exception as e:
            print(f"[squid-pet] drag loop error: {e}", flush=True)
            break

    # Post-drag snap-back guard (in case cursor raced off-screen
    # between drag ticks or visibleFrame changed mid-drag).
    try:
        frame = nw.frame()
        ox, oy = frame.origin.x, frame.origin.y
        cx, cy = clamp_origin_to_screen(ox, oy)
        if (cx, cy) != (ox, oy):
            from Foundation import NSPoint
            from PyObjCTools import AppHelper
            AppHelper.callAfter(nw.setFrameOrigin_, NSPoint(cx, cy))
            print(f"[squid-pet] drag end: snap-back ({ox:.0f},{oy:.0f}) -> ({cx:.0f},{cy:.0f}) (was out of visibleFrame)", flush=True)
    except Exception as e:
        print(f"[squid-pet] drag end snap-back error: {e}", flush=True)

    # Cleanup
    try:
        on_end()
    except Exception as e:
        print(f"[squid-pet] drag end-callback failed: {e}", flush=True)


def start_native_drag(passthrough, on_end, on_swing=None) -> bool:
    """Begin a Python-driven drag. Returns True if started."""
    global _drag_thread
    if _drag_thread is not None and _drag_thread.is_alive():
        print("[squid-pet] drag already in progress; ignoring start", flush=True)
        return False
    try:
        from AppKit import NSEvent
    except Exception as e:
        print(f"[squid-pet] start_native_drag import failed: {e}", flush=True)
        return False
    nw = _get_ns_window()
    if nw is None:
        return False
    loc = NSEvent.mouseLocation()
    frame = nw.frame()
    _drag_stop.clear()
    if passthrough:
        passthrough.pause()
    _drag_thread = _threading.Thread(
        target=_native_drag_loop,
        args=((loc.x, loc.y), (frame.origin.x, frame.origin.y), passthrough, on_end, on_swing),
        daemon=True,
        name="squid-drag",
    )
    _drag_thread.start()
    print(f"[squid-pet] drag started: cursor=({loc.x:.0f},{loc.y:.0f}) "
          f"origin=({frame.origin.x:.0f},{frame.origin.y:.0f})", flush=True)
    return True


def stop_native_drag() -> None:
    """Signal the drag loop to stop and clean up."""
    global _drag_thread
    _drag_stop.set()
    if _drag_thread is not None:
        _drag_thread.join(timeout=0.5)
    _drag_thread = None


def is_drag_active() -> bool:
    """True iff a Python drag thread is currently running."""
    return _drag_thread is not None and _drag_thread.is_alive()


def get_window_origin() -> tuple[float, float] | None:
    nw = _get_ns_window()
    if nw is None:
        return None
    f = nw.frame()
    return (f.origin.x, f.origin.y)


def set_window_origin(x: float, y: float) -> None:
    """Move the window origin, CLAMPED to keep the character fully on-screen.
    SAFE to call from any thread — Cocoa mutations are dispatched to the
    AppKit main thread via PyObjCTools.AppHelper.

    The clamp is critical: the wanderer (and other callers like move_to_corner)
    used to bypass it, causing tentacles to clip off-screen at L/R edges."""
    nw = _get_ns_window()
    if nw is None:
        return
    try:
        from Foundation import NSPoint
        from PyObjCTools import AppHelper
        cx, cy = clamp_origin_to_screen(float(x), float(y))
        pt = NSPoint(cx, cy)
        AppHelper.callAfter(nw.setFrameOrigin_, pt)
    except Exception as e:
        print(f"[squid-pet] set_window_origin failed: {e}", flush=True)


def get_visible_frame() -> tuple[float, float, float, float] | None:
    vf = _visible_frame()
    if vf is None:
        return None
    return tuple(vf)



class PetApi:
    def __init__(self) -> None:
        self._latest = watcher.PetState()
        self._lock = threading.Lock()
        self._corner = load_corner()
        self._forced_state: str | None = None  # JS dbl-click override
        self._passthrough: PassthroughController | None = None  # set later
        self._wander_sub_state = ""  # "walking-left" / "walking-right" / ""
        self._wander_edge = ""       # "" | "bottom" | "left" | "right" | "top"
        self._pinned: bool = False             # ⚓ disables wandering when True
        self._wander_paused_until: float = 0.0  # epoch seconds; wandering off until this time
        # menubar-hide (2026-06-28 Pink/Indigo): when True, Squid's
        # NSWindow alpha is forced to 0 so she's invisible without
        # being quit. Toggled from menu bar (left/right click on 🦑)
        # AND from the same item in the right-click menu. State lives
        # in-memory only -- restart brings her back visible by design
        # (avoids the "where did Squid go?" footgun across reboots).
        self._hidden: bool = False
        self._wrapper_deg_override = None  # Optional[float]: bypass edge->deg mapping when set
        self._wake_trigger_seq: int = 0    # increments on wake-fire; frontend tracks last seen
        self._user_wake_until: float = 0.0  # epoch sec; user-interaction wake override (poke, sprint)
        self._sprint_fast_transition: bool = False  # frontend uses 0.2s CSS transition when True
        # Stroll mode: "edges" (hug border) or "anywhere" (free roam).
        # Removed by unify-idle-rhythm 2026-06-13 (regression), restored
        # the same day. Persisted to settings.json so it survives restart.
        # Defaults to "edges" to match pre-regression behavior.
        try:
            _s_stroll = load_settings()
        except Exception:
            _s_stroll = {}
        self._stroll_mode: str = _s_stroll.get("stroll_mode", "edges")
        if self._stroll_mode not in ("anywhere", "edges"):
            self._stroll_mode = "edges"
        self._hint_text: str = ""              # one-shot hint shown via #hint
        self._hint_seq: int = 0                # increments per hint; JS dedupes
        self._menu = None                      # SquidMenu instance (set in on_loaded)
        self._wanderer = None                  # WanderController (set in on_loaded)
        self._routine = None                   # RoutineController (set in on_loaded)
        self._frontend_mood: str = ""          # JS mood: ""/drowsy/sleeping/stretch
        # Set when on_loaded fires; the watchdog in main() uses this to
        # detect WKWebView startup hangs and self-terminate within 10s
        # so `squid start` / launchd can recover cleanly.
        self._loaded: threading.Event = threading.Event()

        # Observer / speech-bubble layer (observer-mode change 2026-06-13)
        # Bubbles are ephemeral chat-events, not persisted to state.json.
        # _last_state_for_bubble tracks the previous PetState.state so we
        # only fire observer.on_state_change on actual transitions, not
        # every watcher tick. _last_mood_for_bubble does the same for the
        # frontend mood layer (drowsy/sleeping/stretch).
        self._pending_bubble: str | None = None
        self._last_state_for_bubble: str = "idle"
        self._last_mood_for_bubble: str = ""
        # LLM-enriched bubbles (opt-in via ~/.squid-pet/config.json).
        # llm-bubbles polish 2026-06-27, item 1: construct the LLMClient
        # unconditionally (cheap -- just reads puppy.cfg once) so that
        # toggling `llm_bubbles` later via the menu takes effect live
        # without a restart. Observer gates on config.llm_bubbles_enabled
        # per-dispatch instead of caching the decision here.
        _model = config.get("llm_bubbles_model", "claude-sonnet-4-6")
        _llm = _llm_client_mod.LLMClient(model=_model)
        if not _llm.is_available():
            print(
                "[squid-pet] llm_client: no puppy_token in ~/.tpa/puppy.cfg "
                "-- bubbles will be rule-based only until token appears",
                flush=True,
            )
            _llm = None  # Observer treats None as "never dispatch LLM"
        else:
            cur_state = "ON" if config.llm_bubbles_enabled() else "OFF"
            print(f"[squid-pet] llm_client wired, model={_model}, "
                  f"current llm_bubbles={cur_state} (toggle live via menu)",
                  flush=True)
        self._observer = observer.Observer(
            get_muted=config.is_muted,
            llm_client=_llm,
            publish_cb=self._publish_llm_bubble,
            get_llm_enabled=config.llm_bubbles_enabled,
        )

    def signal_ready(self) -> dict:
        """Called by JS on its first successful get_state poll. Provides a
        backup path to disarm the startup watchdog in case pywebview's
        native `loaded` event swizzle misses (a cocoa-backend race that
        produced ~40% hang rate empirically; see squid-pet.md gotchas)."""
        if not self._loaded.is_set():
            self._loaded.set()
            print(
                "[squid-pet] watchdog disarmed via JS signal_ready() "
                "(native loaded event missed)",
                flush=True,
            )
        return {"ok": True}

    def set_passthrough(self, p: PassthroughController) -> None:
        self._passthrough = p

    def update(self, state: watcher.PetState) -> None:
        with self._lock:
            prev_state = self._last_state_for_bubble
            self._latest = state
            self._last_state_for_bubble = state.state
            shown = self._forced_state or state.state
        if self._passthrough:
            self._passthrough.set_state(shown)
        # Observer: fire on actual state transitions only
        if prev_state != state.state:
            # Enrich working bubble with shell cmdline if available
            shell_cmd = None
            if state.state == "working":
                try:
                    procs = watcher.find_tpa_processes()
                    shell_cmd = watcher.latest_shell_child_cmdline(procs)
                except Exception:
                    shell_cmd = None
            # Fix B (2026-06-28): collect richer signals for LLM context
            # so the model can be specific ("shipped", "fetching deps")
            # instead of generic ("settle in"). All best-effort -- a
            # failure to read any signal just omits it from the context.
            subagent_name = None
            llm_streaming = False
            git_active = False
            cpu_pct = 0.0
            tool_age = float("inf")
            try:
                # TPA detector signals (already scanned this tick)
                cp = getattr(self, "_tpa_detector", None) or getattr(watcher, "_tpa_detector_singleton", None)
                if cp is not None:
                    llm_streaming = bool(getattr(cp, "llm_streaming", False))
                    cpu_pct = float(getattr(cp, "cpu_percent", 0.0))
                    tool_age = float(getattr(cp, "tool_activity_age", float("inf")))
            except Exception:
                pass
            try:
                # Subagent name: newest *.pkl in subagent_sessions/
                _sub_dir = watcher.SUBAGENT_DIR
                if _sub_dir.exists():
                    _pkls = sorted(_sub_dir.glob("*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if _pkls and (state.timestamp - _pkls[0].stat().st_mtime) < 60:
                        subagent_name = _pkls[0].stem
            except Exception:
                pass
            try:
                # Git active: GitDetector reads .git/HEAD/index/refs mtimes
                gd = getattr(self, "_git_detector", None)
                if gd is not None:
                    git_active = bool(gd.is_celebrating(state.timestamp) or gd.is_busy(state.timestamp))
            except Exception:
                pass
            bubble = self._observer.on_state_change(
                prev_state, state.state,
                concern_reason=getattr(state, "concern_reason", "") or "",
                shell_cmdline=shell_cmd,
                subagent_name=subagent_name,
                llm_streaming=llm_streaming,
                git_active=git_active,
                cpu_pct=cpu_pct,
                tool_age=tool_age,
                state_reason=getattr(state, "state_reason", "") or "",
            )
            if bubble is not None:
                with self._lock:
                    self._pending_bubble = bubble

    def get_state(self) -> dict:
        with self._lock:
            d = asdict(self._latest)
            if self._forced_state:
                d["state"] = self._forced_state
            # Overlay walking sub_state if active (lets frontend animate legs)
            if self._wander_sub_state and d.get("state") == "idle":
                d["sub_state"] = self._wander_sub_state
            # Edge tells frontend which way to rotate sprite (feet hug screen edge)
            d["edge"] = self._wander_edge
            # Menu-driven fields (hint pill, pin status)
            d["hint_text"] = self._hint_text
            d["hint_seq"] = self._hint_seq
            d["pinned"] = self._pinned
        if self._wrapper_deg_override is not None:
            d["wrapper_deg"] = self._wrapper_deg_override
        d["wake_trigger_seq"] = self._wake_trigger_seq
        # User-interaction wake override (poke/sprint take prime over TPA-idle counter)
        d["user_wake_remaining"] = max(0.0, self._user_wake_until - _time.time())
        d["sprint_fast_transition"] = self._sprint_fast_transition
        # Observer-mode: surface the pending bubble for frontend.
        # Frontend acks via clear_bubble() once fade-out completes.
        with self._lock:
            d["pending_bubble"] = self._pending_bubble
        return d

    def clear_bubble(self) -> None:
        """JS-exposed: frontend calls this after a bubble has finished
        fading out. Acknowledges receipt so the next get_state() poll
        sees pending_bubble=None instead of replaying the same line."""
        with self._lock:
            self._pending_bubble = None

    def _publish_llm_bubble(self, text: str) -> None:
        """Callback the Observer's LLM worker thread uses to publish a
        background-generated bubble. Overwrites whatever rule-based
        bubble was set immediately by the same state transition.

        SAFE TO CALL FROM ANY THREAD: uses self._lock for the write.
        """
        if not text:
            return
        with self._lock:
            self._pending_bubble = text

    def set_wander_edge(self, edge: str) -> None:
        """Called by WanderController when she crosses an edge boundary.
        Values: "" (off-edge), "bottom", "left", "right", "top"."""
        with self._lock:
            self._wander_edge = edge or ""
        # Notify passthrough so hit-test accounts for top-edge CSS offset
        if self._passthrough:
            self._passthrough.set_edge(edge or "")

    def set_wander_sub_state(self, s: str) -> None:
        with self._lock:
            self._wander_sub_state = s

    def get_wander_sub_state(self) -> str:
        """Return current wander sub_state (used by PulseController to avoid
        stomping the wanderer's animation slot)."""
        with self._lock:
            return self._wander_sub_state

    # -- Frontend mood bridge (unify-idle-rhythm 2026-06-13) --------
    def notify_mood(self, mood: str) -> dict:
        """JS calls this whenever _mood changes. Values:
            "" (awake/active), "drowsy", "sleeping", "stretch".
        RoutineController polls via get_frontend_mood() and pauses ticks
        whenever mood is in MOODS_THAT_PAUSE."""
        prev = self._frontend_mood
        self._frontend_mood = (mood or "").strip()
        if self._frontend_mood != prev:
            print(f"[squid-pet] mood notify: {prev or '(awake)'} -> "
                  f"{self._frontend_mood or '(awake)'}", flush=True)
            # Observer: fire mood-change bubble (drowsy/waking; sleeping is silent)
            bubble = self._observer.on_mood_change(prev, self._frontend_mood)
            if bubble is not None:
                with self._lock:
                    self._pending_bubble = bubble
        return {"ok": True}

    def get_frontend_mood(self) -> str:
        """Read latest mood. Empty string == awake/active."""
        return self._frontend_mood

    def force_state(self, name: str) -> str:
        """Pin pet to a specific state (called from dbl-click)."""
        self._forced_state = name if name else None
        if self._passthrough and name:
            self._passthrough.set_state(name)
        return name or ""

    def clear_force(self) -> None:
        self._forced_state = None

    def next_corner(self) -> str:
        """Snap to next corner via NSWindow."""
        idx = CORNERS.index(self._corner)
        self._corner = CORNERS[(idx + 1) % len(CORNERS)]
        save_corner(self._corner)
        ok = move_to_corner(self._corner)
        # Refresh edge so sprite rotates to match new corner position.
        try:
            if self._wanderer is not None:
                self._wanderer.refresh_edge()
        except Exception as e:
            print(f"[squid-pet] corner snap edge-refresh err: {e}", flush=True)
        print(f"[squid-pet] corner snap -> {self._corner} (ok={ok})", flush=True)
        return self._corner

    def drag_start(self) -> dict:
        """JS calls this on mousedown. Spawns a Python-side drag thread that
        polls NSEvent at 60Hz and auto-ends when the OS sees button-up.
        Eliminates JS↔Python RPC backpressure and lost-mouseup stalls."""
        def _on_end():
            # Called from the drag-thread when it exits (button up, watchdog, or stop_native_drag)
            if self._passthrough:
                self._passthrough.resume()
            # Refresh edge tracker from live position so sprite rotates to
            # match new edge (drag bypasses wanderer's wrapped origin setter).
            try:
                if self._wanderer is not None:
                    new_edge = self._wanderer.refresh_edge()
                    print(f"[squid-pet] drag end: edge refreshed -> {new_edge or '(none)'}", flush=True)
            except Exception as e:
                print(f"[squid-pet] drag end edge-refresh err: {e}", flush=True)
            print("[squid-pet] drag ended cleanly", flush=True)
        # on_swing handler: shake-to-wake gesture during drag triggers same
        # 60s user_wake override as poke. Pink can either single-click OR
        # shake her up-down to wake her up.
        def _on_swing():
            self._user_wake_until = _time.time() + 60.0
            self._wake_trigger_seq += 1
            # Observer bubble (was: "wheee!" hint pill; deduped 2026-06-13)
            bubble = self._observer.on_interaction("shake")
            if bubble is not None:
                with self._lock:
                    self._pending_bubble = bubble
            print("[squid-pet] swing-to-wake -> 60s awake override + observer bubble", flush=True)
        started = start_native_drag(self._passthrough, _on_end, on_swing=_on_swing)
        return {"ok": started}

    def drag_end(self) -> dict:
        """JS mouseup fallback — Python OS-level button-up usually fires first."""
        stop_native_drag()
        if self._passthrough:
            self._passthrough.resume()
        return {"ok": True}

    def move_window_by(self, dx: float, dy: float) -> dict:
        """Legacy JS-driven move. Now a no-op because the Python drag thread
        handles movement directly. Kept so old frontend cache works."""
        return {"ok": True, "noop": True}

    # ─────────────────────────────────────────────────────────────
    # Context menu — JS calls show_context_menu() on right-click;
    # the rest are invoked by _MenuTarget when items are clicked.
    # ─────────────────────────────────────────────────────────────
    def show_context_menu(self) -> dict:
        """JS-exposed: pop up the native right-click menu at the cursor."""
        if self._menu is None:
            return {"ok": False, "error": "menu not initialized"}
        self._menu.show_at_cursor()
        return {"ok": True}

    def _emit_hint(self, text: str) -> None:
        """Push a one-shot hint to the frontend (#hint pill)."""
        with self._lock:
            self._hint_text = text
            self._hint_seq += 1

    # ─── Position ───
    def _menu_snap(self, corner: str) -> None:
        if move_to_corner(corner):
            save_corner(corner)
            self._emit_hint(f"📍 {corner}")

    def _menu_toggle_pin(self) -> None:
        self._pinned = not self._pinned
        self._emit_hint("⚓ pinned" if self._pinned else "⚓ unpinned (wandering on)")

    def _menu_pause_wander(self, minutes: int) -> None:
        """Pause wandering for N minutes. Resumes automatically when expired."""
        import time as _t
        self._wander_paused_until = _t.time() + minutes * 60
        self._emit_hint(f"⏸ wandering paused for {minutes} min")
        print(f"[squid-pet] wander paused for {minutes} min", flush=True)

    def _menu_resume_wander(self) -> None:
        """Cancel any active pause."""
        self._wander_paused_until = 0.0
        self._emit_hint("▶ wandering resumed")
        print("[squid-pet] wander resumed", flush=True)

    def debug_log(self, msg: str) -> str:
        """JS-exposed: print arbitrary debug message from frontend to /tmp/squid-pet.log."""
        print(f"[squid-pet][js] {msg}", flush=True)
        return "logged"

    def poke(self) -> str:
        """JS-exposed: single click without drag = poke Squid.
        - Wakes if drowsy/sleeping (bumps wake_trigger_seq)
        - Sets user_wake override for 60s (Pink's poke takes prime over the
          TPA-idle counter; without this, stretch transition completes and
          mood layer immediately re-enters drowsy)
        - Clears any forced state (poke = "go back to normal")
        - Fires observer bubble (was: dual "boop!" hint pill + bubble,
          deduped 2026-06-13 per Pink's screenshot showing both stacked)"""
        self._wake_trigger_seq += 1
        self._user_wake_until = _time.time() + 60.0
        cleared = self._forced_state is not None
        self._forced_state = None
        # Observer bubble owns the poke reaction now (no more "boop!" hint pill)
        bubble = self._observer.on_interaction("poke")
        if bubble is not None:
            with self._lock:
                self._pending_bubble = bubble
        msg = "poke -> 60s awake override + observer bubble"
        if cleared:
            msg += " + cleared forced state"
        print(f"[squid-pet] {msg}", flush=True)
        return "poked"


    # ----- Observer / mute toggle (observer-mode 2026-06-13) -----

    # ── Stroll path (restored 2026-06-13) ─────────────────────────────
    def _menu_set_stroll_mode(self, mode: str) -> None:
        """Flip stroll mode (anywhere ↔ edges) and persist.

        Updates self._stroll_mode, propagates to live wanderer if present,
        writes to settings.json so restart preserves the choice, and
        flashes a hint pill confirming the new mode.
        """
        if mode not in ("anywhere", "edges"):
            print(f"[squid-pet] _menu_set_stroll_mode: invalid {mode!r}",
                  flush=True)
            return
        self._stroll_mode = mode
        # Push to live wanderer (no-op if not yet constructed)
        try:
            if self._wanderer is not None:
                self._wanderer.set_stroll_mode(mode)
        except Exception as e:
            print(f"[squid-pet] wanderer.set_stroll_mode failed: {e}",
                  flush=True)
        # Persist
        try:
            settings = load_settings()
            settings["stroll_mode"] = mode
            save_settings(settings)
        except Exception as e:
            print(f"[squid-pet] save settings failed: {e}", flush=True)
        label = "edges only" if mode == "edges" else "anywhere"
        self._emit_hint(f"stroll path -> {label}")

    def _menu_toggle_hide(self) -> None:
        """menubar-hide: flip self._hidden + actually hide/show the
        NSWindow by setting alpha. Bubbles, wandering, watcher all
        keep running -- only the visual is suppressed. Cheaper than
        actually closing the window (which would force re-init)."""
        self._hidden = not self._hidden
        self._apply_hide_state()
        if self._menu is not None:
            try:
                self._menu.refresh_status_icon()
            except Exception as e:
                print(f"[squid-pet] status icon refresh failed: {e}", flush=True)
        msg = ("hidden -- click 💤 in menu bar to show"
               if self._hidden else "back!")
        self._emit_hint(msg)
        print(f"[squid-pet] hide toggled -> {self._hidden}", flush=True)

    def is_hidden(self) -> bool:
        """Menu + menu bar query the current hide state."""
        return self._hidden

    def _apply_hide_state(self) -> None:
        """Set the NSWindow alpha to 0 (hidden) or 1.0 (shown).

        When hidden we ALSO (a) freeze wandering -- the routine's
        is_pinned gate now honors self._hidden, so she stops walking
        the moment she goes invisible -- and (b) force the passthrough
        controller into always-ignore mode, so the invisible, frozen
        window can never intercept a click (the "still there but you
        can't see it" footgun Pink hit 2026-07-16). Restart/unhide
        restores normal click behavior.

        Must run on the AppKit main thread.
        """
        # Make the (now invisible) window fully click-through so it is
        # truly "not available" while hidden. Guarded -- passthrough is
        # wired in after startup.
        try:
            if self._passthrough is not None:
                self._passthrough.set_hidden(self._hidden)
        except Exception as e:
            print(f"[squid-pet] passthrough hide sync failed: {e}", flush=True)
        try:
            from PyObjCTools import AppHelper
            alpha = 0.0 if self._hidden else 1.0
            def _set_alpha():
                try:
                    w = _get_ns_window()
                    if w is not None:
                        w.setAlphaValue_(alpha)
                except Exception as e:
                    print(f"[squid-pet] setAlpha failed: {e}", flush=True)
            AppHelper.callAfter(_set_alpha)
        except Exception as e:
            print(f"[squid-pet] hide dispatch failed: {e}", flush=True)

    def _menu_toggle_mute(self) -> None:
        """Right-click menu: Mute/Unmute Squid. Persists to config.json."""
        new_val = config.toggle_muted()
        msg = "muted (no bubbles)" if new_val else "unmuted"
        self._emit_hint(msg)
        # Clear any in-flight bubble so a stale muted line doesn't leak
        if new_val:
            with self._lock:
                self._pending_bubble = None
        print(f"[squid-pet] mute toggled -> {new_val}", flush=True)
        if self._menu is not None:
            try:
                self._menu.refresh_status_icon()
            except Exception as e:
                print(f"[squid-pet] status icon refresh failed: {e}", flush=True)

    def is_llm_bubbles_enabled(self) -> bool:
        """JS+menu exposed: current state of llm_bubbles flag."""
        return config.llm_bubbles_enabled()

    def _menu_toggle_llm_bubbles(self) -> None:
        """Menu action: flip llm_bubbles flag. Takes effect on next
        Squid restart -- the LLMClient is constructed once at startup
        because we don't want to read puppy.cfg on every state change.
        Shows a brief bubble so the user knows the toggle landed."""
        new_state = config.toggle_llm_bubbles()
        msg = "llm on next restart" if new_state else "llm off next restart"
        with self._lock:
            self._pending_bubble = msg
        print(f"[squid-pet] llm_bubbles toggled -> {new_state}", flush=True)

    def is_approval_alert_enabled(self) -> bool:
        from . import config as _cfg
        return _cfg.approval_alert_enabled()

    def _menu_toggle_approval_alert(self) -> None:
        """Toggle the 'your turn' notification + sticky bubble."""
        from . import config as _cfg
        new_val = _cfg.toggle_approval_alert()
        self._emit_hint("🔔 alerts ON" if new_val else "🔔 alerts OFF")

    def is_muted(self) -> bool:
        """Exposed so menu can show checkbox state."""
        return config.is_muted()

    # Pink-2026-06-30 v3: manual de-escalate (right-click menu)

    def is_squid_waving(self) -> bool:
        """Menu helper: is there at least one TPA currently waving that
        we could calm right now? Used to enable/disable 'Calm Squid'."""
        from . import watcher as _w
        try:
            return _w.count_currently_waving_pids() > 0
        except Exception:
            return False

    def _menu_calm_squid(self) -> None:
        """Menu action: manually snooze all in-flight awaiting_input
        waves. Reuses the direct-signal snooze mechanic -- backdates
        flag first-seen times past the snooze window so the eligibility
        filter drops them on the next tick. Auto re-arm still works:
        waves come back for genuinely new work (flag disappears when
        Pink replies, reappears when TPA hits the next prompt with a
        fresh birth-time clock)."""
        from . import watcher as _w
        try:
            n = _w.snooze_all_awaiting_now()
        except Exception as e:
            print(f"[squid-pet] calm squid failed: {e}", flush=True)
            self._emit_hint("calm failed")
            return
        if n == 0:
            self._emit_hint("nothing to calm")
        else:
            self._emit_hint(
                f"shh -- calmed {n} wave" + ("s" if n != 1 else "")
            )
        print(
            f"[squid-pet] manual de-escalate: snoozed {n} awaiting PID(s)",
            flush=True,
        )

    def _menu_sprint_perimeter(self) -> None:
        """Funny: sprint through all 4 corners CW. Background thread."""
        if self._wanderer is None:
            self._emit_hint("⚠ wanderer not ready")
            return
        self._wander_paused_until = 0.0  # cancel pause so she can move
        # User-interaction wake override: keeps her awake-faced (idle.png)
        # during the entire sprint + buffer afterward. Without this, mood
        # layer re-enters drowsy mid-sprint if agent_idle is high.
        self._user_wake_until = _time.time() + 60.0
        self._wake_trigger_seq += 1       # wake-from-drowsy stretch transition
        try:
            # Observer bubble: sprint start
            bubble = self._observer.on_interaction("sprint")
            if bubble is not None:
                with self._lock:
                    self._pending_bubble = bubble
            self._wanderer.sprint_perimeter()
            self._emit_hint("🏃‍♀️ sprinting!")
        except Exception as e:
            self._emit_hint(f"⚠ sprint failed: {e}")

    def _menu_recenter(self) -> None:
        corner = load_corner()
        if move_to_corner(corner):
            self._emit_hint(f"🎯 recentered → {corner}")

    # ─── Mood ───
    def _menu_force(self, name: str) -> None:
        self._forced_state = name
        if self._passthrough:
            self._passthrough.set_state(name)
        self._emit_hint(f"🎭 forced: {name}")

    def _menu_clear_force(self) -> None:
        self._forced_state = None
        self._emit_hint("↻ live tracking")

    # ─── Diagnostics ───
    def _menu_has_recent_error(self) -> bool:
        """True if errors.log was touched in the last 10 min."""
        try:
            from squid_pet.watcher import ERRORS_LOG
            import time as _t
            if not ERRORS_LOG.exists():
                return False
            return (_t.time() - ERRORS_LOG.stat().st_mtime) < 600
        except Exception:
            return False

    def _menu_whats_wrong(self) -> None:
        """Open the last error log in Console.app."""
        try:
            from squid_pet.watcher import ERRORS_LOG
            import subprocess
            subprocess.Popen(["open", "-a", "Console", str(ERRORS_LOG)])
            self._emit_hint("🩺 opened errors.log")
        except Exception as e:
            self._emit_hint(f"🩺 failed: {e}")

    def _menu_show_stats(self) -> None:
        """Build a compact stats string and emit as hint."""
        try:
            with self._lock:
                st = self._latest
            from squid_pet.watcher import (
                find_tpa_processes, most_recent_tool_activity_age,
            )
            procs = find_tpa_processes()
            n_proc = len(procs)
            tool_age = most_recent_tool_activity_age()
            tool_str = (f"{int(tool_age)}s ago" if tool_age != float("inf")
                        else "—")
            msg = (f"📊 cpu {st.cpu_percent:.0f}% · "
                   f"idle {int(st.idle_seconds)}s · "
                   f"procs {n_proc} · tool {tool_str}")
            self._emit_hint(msg)
        except Exception as e:
            self._emit_hint(f"📊 stats err: {e}")

    def _menu_open_log(self) -> None:
        """Open /tmp/squid-pet.log in Console.app."""
        try:
            import subprocess
            subprocess.Popen(["open", "-a", "Console", "/tmp/squid-pet.log"])
            self._emit_hint("📜 opened squid-pet.log")
        except Exception as e:
            self._emit_hint(f"📜 failed: {e}")

    # ─── Lifecycle ───
    def _menu_restart(self) -> None:
        """Re-exec squid via the launcher script — clean restart."""
        try:
            import subprocess, os
            launcher = os.path.expanduser("~/.local/bin/squid")
            subprocess.Popen(
                [launcher, "restart"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._emit_hint("↻ restarting…")
        except Exception as e:
            self._emit_hint(f"↻ failed: {e}")

    def _menu_quit(self) -> None:
        self._emit_hint("👋 bye!")
        import threading as _th
        _th.Timer(0.4, self.quit).start()

    def quit(self) -> None:
        """Fully stop Squid — and make her *stay* stopped.

        When she's managed by the LaunchAgent, closing the window is NOT
        enough: pywebview tears the process down with a signal, which launchd
        counts as a *crash*. Per the plist's ``KeepAlive.Crashed = true`` that
        triggers an instant respawn — the "she comes back right after she
        quits" bug. So when the agent is loaded we ``launchctl bootout`` it
        instead: that deregisters the job from launchd entirely, so KeepAlive
        no longer applies and she stays dead until ``squid start``.

        In dev mode (no plist installed, e.g. run straight from a terminal)
        we just close the window like before.
        """
        import os as _os
        import subprocess as _subprocess

        label = "com.pink.squid-pet"
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

        if plist.exists():
            uid = _os.getuid()
            try:
                # Detached so it outlives us. bootout SIGTERMs this process
                # AND removes the job from launchd's domain, so KeepAlive
                # cannot resurrect her.
                _subprocess.Popen(
                    ["launchctl", "bootout", f"gui/{uid}/{label}"],
                    stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL,
                    start_new_session=True,
                )
                print(f"[squid-pet] quit: booting out {label} "
                      "(won't respawn until 'squid start')", flush=True)
                return
            except Exception as e:
                print(f"[squid-pet] quit: bootout failed ({e}); "
                      "falling back to window close", flush=True)

        # Dev mode (no LaunchAgent) or bootout failed: just close the window.
        nw = _get_ns_window()
        if nw:
            nw.close()


# ──────────────────────────────────────────────────────────────────
# Watcher thread
# ──────────────────────────────────────────────────────────────────
def watcher_thread(api: PetApi, stop_event: threading.Event) -> None:
    sm = watcher.StateMachine()
    print(f"[squid-pet] watcher thread started", flush=True)
    while not stop_event.is_set():
        try:
            state = sm.compute()
            api.update(state)
            watcher.write_state(state)
        except Exception as e:
            print(f"[squid-pet] watcher error: {e}", flush=True)
        stop_event.wait(watcher.POLL_INTERVAL_SEC)


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────
def main() -> None:
    if not FRONTEND_HTML.exists():
        sys.exit(f"frontend not found: {FRONTEND_HTML}")

    api = PetApi()
    api.update(watcher.StateMachine().compute())

    # Initial position (will be re-snapped after load)
    corner = load_corner()
    # use ANY initial coords — we'll snap once NSWindow is available
    window = webview.create_window(
        title="Squid",
        url=str(FRONTEND_HTML),
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        x=100, y=100,
        frameless=True,
        easy_drag=False,
        on_top=True,
        transparent=True,
        resizable=False,
        background_color="#FFFFFF",
        js_api=api,
    )

    stop_event = threading.Event()
    t = threading.Thread(
        target=watcher_thread, args=(api, stop_event),
        daemon=True, name="squid-watcher",
    )
    t.start()

    def on_loaded() -> None:
        # Hide from Dock / Cmd-Tab via NSApplicationActivationPolicyAccessory.
        # Dispatch to main run loop (calling NSApp directly here can deadlock
        # because on_loaded fires from a WebKit callback).
        def _set_accessory():
            try:
                from AppKit import NSApp
                NSApp.setActivationPolicy_(1)  # 1 = accessory
                print("[squid-pet] activation policy → accessory (no Dock icon)", flush=True)
            except Exception as e:
                print(f"[squid-pet] accessory policy failed: {e}", flush=True)
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_set_accessory)
        except Exception as e:
            print(f"[squid-pet] couldn't dispatch accessory: {e}", flush=True)

        # Multi-Space: make Squid appear on EVERY virtual desktop (Space) and
        # over fullscreen apps too. Set NSWindow collectionBehavior bits:
        #   NSWindowCollectionBehaviorCanJoinAllSpaces      = 1 << 0 (1)
        #   NSWindowCollectionBehaviorStationary            = 1 << 4 (16)
        #   NSWindowCollectionBehaviorFullScreenAuxiliary   = 1 << 8 (256)
        # Combined: 273. Must dispatch to main thread (NSWindow is main-only).
        def _set_all_spaces():
            try:
                w = _get_ns_window()
                if w is not None:
                    ALL_SPACES_BEHAVIOR = (1 << 0) | (1 << 4) | (1 << 8)  # 273
                    w.setCollectionBehavior_(ALL_SPACES_BEHAVIOR)
                    print(f"[squid-pet] collectionBehavior set to {ALL_SPACES_BEHAVIOR} "
                          "(all Spaces + stationary + fullscreen-aux)", flush=True)
                else:
                    print("[squid-pet] all-spaces: no NSWindow yet", flush=True)
            except Exception as e:
                print(f"[squid-pet] all-spaces failed: {e}", flush=True)
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_set_all_spaces)
        except Exception as e:
            print(f"[squid-pet] couldn't dispatch all-spaces: {e}", flush=True)

        # Disable macOS auto-constraint: by default, NSWindow.constrainFrameRect:toScreen:
        # prevents the window from extending beyond the visible frame. For a desktop pet
        # that needs to hug the top edge (with 155px of transparent window above the sprite
        # hiding behind the menu bar), this constraint snaps the window 155px downward
        # after every walk cycle ends. We override it to return the requested frame as-is.
        def _disable_constrain():
            try:
                import objc
                from AppKit import NSWindow
                # Method swizzle: replace constrainFrameRect:toScreen: on the class
                # with a no-op that returns the requested frame unchanged.
                orig = NSWindow.instanceMethodForSelector_(b"constrainFrameRect:toScreen:")
                def _unconstrained(self, frame, screen):
                    return frame
                # Register the replacement
                _unconstrained = objc.selector(
                    _unconstrained,
                    selector=b"constrainFrameRect:toScreen:",
                    signature=orig.signature,
                )
                objc.classAddMethod(NSWindow, b"constrainFrameRect:toScreen:", _unconstrained)
                print("[squid-pet] constrainFrameRect override installed (no auto-snap)", flush=True)
            except Exception as e:
                print(f"[squid-pet] constrainFrame override failed: {e}", flush=True)
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_disable_constrain)
        except Exception as e:
            print(f"[squid-pet] couldn't dispatch constrainFrame override: {e}", flush=True)

        # Snap to saved corner via NSWindow (accurate, no origin issues).
        # MUST run on the main thread — on macOS 14+ NSWindow operations
        # called from a WebKit callback thread silently fail (no exception
        # raised, NSPoint just doesn't take effect). Squid stays at
        # pywebview's default (100,100) and the user thinks she "didn't
        # show up". Confirmed via CGWindowListCopyWindowInfo 2026-06-16.
        # The two NSApp/NSWindow calls above (_set_accessory, _set_all_spaces)
        # already use callAfter for the same reason; this one was missing.
        def _snap_corner():
            try:
                ok = move_to_corner(corner)
                vf = _visible_frame()
                print(f"[squid-pet] visibleFrame = {vf}", flush=True)
                print(f"[squid-pet] snapped to '{corner}' (ok={ok})", flush=True)
            except Exception as e:
                print(f"[squid-pet] corner snap failed: {e}", flush=True)
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_snap_corner)
        except Exception as e:
            print(f"[squid-pet] couldn't dispatch corner snap: {e}", flush=True)
            # Best-effort fallback: try inline anyway (may silent-fail on 14+)
            ok = move_to_corner(corner)
            print(f"[squid-pet] inline snap fallback (ok={ok})", flush=True)

        # Start pixel-perfect click passthrough
        pt = PassthroughController(_get_ns_window)
        pt.set_state(api.get_state().get("state", "idle"))
        api.set_passthrough(pt)
        pt.start()

        # Start wanderer in SERVICE MODE -- exposes request_walk /
        # request_look_around primitives. No internal scheduler;
        # RoutineController drives idle-time invocations.
        from squid_pet.wanderer import WanderController
        wc = WanderController(
            get_state=lambda: api.get_state().get("state", "idle"),
            is_drag_active=is_drag_active,
            get_window_origin=get_window_origin,
            set_window_origin=set_window_origin,
            get_visible_frame=get_visible_frame,
            set_sub_state=api.set_wander_sub_state,
            set_edge=api.set_wander_edge,
        )
        api._wanderer = wc  # keep reference so it isn't GC'd
        # Apply persisted stroll mode (restored 2026-06-13)
        try:
            wc.set_stroll_mode(api._stroll_mode)
            print(f"[squid-pet] initial stroll mode -> {api._stroll_mode}",
                  flush=True)
        except Exception as e:
            print(f"[squid-pet] initial stroll mode push failed: {e}",
                  flush=True)
        # Sprint callbacks (wrapper-deg + wake + fast-transition)
        try:
            def _set_wrap_deg(d):
                api._wrapper_deg_override = float(d)
            def _clear_wrap_deg():
                api._wrapper_deg_override = None
            wc.set_wrapper_deg_callbacks(_set_wrap_deg, _clear_wrap_deg)
            def _wake():
                api._wake_trigger_seq += 1
            def _fast_trans(on):
                api._sprint_fast_transition = bool(on)
            wc.set_sprint_callbacks(_wake, _fast_trans)
        except Exception as e:
            print(f"[squid-pet] sprint wiring failed: {e}", flush=True)

        # Start unified idle rhythm -- replaces pulse.py + wanderer's RNG
        # scheduler. Fires IDLE_ROUTINE actions when state==idle, mood
        # awake, drag clear, not pinned/paused.
        try:
            from squid_pet.routine import RoutineController
            rc = RoutineController(
                wanderer=wc,
                get_state=lambda: api.get_state().get("state", "idle"),
                is_drag_active=is_drag_active,
                # is_busy gate disabled (2026-06-08 Pink decision): she
                # roams even during active TPA work. State-gate handles
                # non-idle pauses; mood-gate handles drowsy/sleeping.
                is_busy=lambda: False,
                get_mood=api.get_frontend_mood,
                is_pinned=lambda: api._pinned or api._hidden or _time.time() < api._wander_paused_until,
            )
            api._routine = rc
            rc.start()
        except Exception as e:
            print(f"[squid-pet] routine startup failed: {e}", flush=True)

        # Build the right-click context menu (needs an active NSApp).
        from squid_pet.menu import SquidMenu
        api._menu = SquidMenu(api)
        print("[squid-pet] context menu ready")

        # Signal the startup watchdog: webview loaded + all subsystems up.
        api._loaded.set()
        print("[squid-pet] startup complete -- watchdog disarmed", flush=True)

    def on_closing() -> None:
        stop_event.set()
        try:
            if api._routine is not None:
                api._routine.stop()
        except Exception:
            pass
        try:
            if api._wanderer is not None:
                api._wanderer.stop()
        except Exception:
            pass

    window.events.loaded += on_loaded
    window.events.closing += on_closing

    # ─── Startup watchdog ───
    # If on_loaded does not fire within STARTUP_TIMEOUT_SEC the WKWebView
    # process is wedged (most common: a stale WebKit content process or
    # a kill-mid-load race). The Python process otherwise stays alive
    # forever with no visible window. Self-terminate so the user / CLI
    # can recover with a fresh `squid start`.
    STARTUP_TIMEOUT_SEC = 10.0
    def _watchdog():
        if api._loaded.wait(timeout=STARTUP_TIMEOUT_SEC):
            return  # healthy startup
        import os as _os, signal as _signal
        print(
            f"[squid-pet] FATAL: webview did not finish loading within "
            f"{STARTUP_TIMEOUT_SEC:.0f}s -- self-terminating so CLI can recover",
            flush=True,
        )
        # _exit (not sys.exit) -- we're a daemon thread and the main
        # thread is blocked inside the Cocoa run loop; only os._exit
        # tears it all down without waiting for atexit handlers.
        _os._exit(2)
    threading.Thread(
        target=_watchdog, daemon=True, name="squid-startup-watchdog",
    ).start()

    webview.start(debug=False)


if __name__ == "__main__":
    main()
