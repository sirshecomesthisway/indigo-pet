"""
Indigo wanders! When she's been idle for a while, every 10-25 seconds she
picks a random nearby destination and walks there over 2-3 seconds.

Architecture:
  - Daemon thread polls the current state via a callback.
  - When state==idle and not dragging and no other state ≥10 seconds,
    schedule a wander.
  - Wander = ease the NSWindow position from current → target over T seconds,
    while setting sub_state="walking" so the frontend animates her legs.
  - If state changes mid-wander (agent activates, user drags, etc.), abort.

Why Python-side window moves (not CSS): pywebview's window is a real NSWindow.
CSS translations only move the sprite WITHIN the 200×220 viewport. To actually
roam the desktop we have to move the NSWindow itself.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, Optional


# Cadence + motion params
IDLE_BEFORE_WANDER_SEC = 15.0          # how long Indigo must be idle before first wander
WANDER_INTERVAL_MIN_SEC = 12.0         # min gap between wanders
WANDER_INTERVAL_MAX_SEC = 28.0         # max gap between wanders
WANDER_DISTANCE_MIN_PX = 60            # min distance to walk
WANDER_DISTANCE_MAX_PX = 320           # max distance to walk
WANDER_SPEED_PX_PER_SEC = 110          # walking speed
WANDER_MAX_DURATION_SEC = 3.0          # hard cap — never walk longer than this
WANDER_TICK_HZ = 30                    # smoothness
EDGE_MARGIN_PX = 12                    # keep this far from visibleFrame left/right/top edges
BOTTOM_MARGIN_PX = -40                 # feet AT visible-frame bottom (auto-hide Dock).
                                       # Pink: feet may sit on Dock edge when Dock pops up — that's fine.
                                       # Sprite has 41px transparent padding at bottom of image, this
                                       # negative margin pulls window down to compensate so visible feet
                                       # land at vy. For always-show Dock, raise this to ~50.
                                       # (visibleFrame is supposed to exclude Dock but with
                                       # magnification + window shadow a 12px margin is too tight)
LOOK_AROUND_PROBABILITY = 0.45         # chance of pausing mid-walk to look around
LOOK_AROUND_DURATION_SEC = 1.4         # how long the look-around lasts
WIN_W = 200                            # window width (must match window.py)
WIN_H = 220                            # window height

# Stroll-mode tunables
STROLL_MODE_ANYWHERE = "anywhere"      # default: free roam (polar pick)
STROLL_MODE_EDGES = "edges"            # hug screen edges
EDGE_BAND_PX = 60                      # within this distance of an edge, we count as "on" it
PAUSE_WHEN_CP_IDLE_SEC = 60.0          # mood drowsy/sleeping threshold - stop wandering past this
EDGE_HOP_PROBABILITY = 0.45            # along-edge: chance to walk to a corner of current edge
CORNER_HOP_PROBABILITY = 0.80          # at-corner: chance to leave the priority edge for a perpendicular one
ROTATION_PREAMBLE_SEC = 0.7            # wait for wrapper rotation before walking when changing edges
SPRINT_SPEED_MULT = 5.0                # sprint mode multiplier (Pink: faster)
SPRINT_ROTATION_TRANSITION_SEC = 0.20  # CSS transition during sprint (fast turn)
SPRINT_WAKE_WAIT_SEC = 1.6             # wait for stretch animation if she was drowsy/sleeping


def _ease_in_out(t: float) -> float:
    """Smooth ease — slow start, fast middle, slow end."""
    return 3 * t * t - 2 * t * t * t


class WanderController:
    """Owns the wander loop. Plug callbacks for state inspection + window moves."""

    def __init__(
        self,
        get_state: Callable[[], str],          # returns current state name
        is_drag_active: Callable[[], bool],    # returns True if user is dragging
        get_window_origin: Callable[[], tuple[float, float] | None],
        set_window_origin: Callable[[float, float], None],
        get_visible_frame: Callable[[], tuple[float, float, float, float]],
        set_sub_state: Callable[[str], None],  # set "walking" / "" on PetApi
        set_edge: Callable[[str], None] = None,   # set current edge ("bottom"/"left"/etc.)
        is_pinned: Callable[[], bool] = None,  # ⚓ if True, skip wandering
        is_busy:   Callable[[], bool] = None,  # see _is_busy below
        get_agent_idle_seconds: Callable[[], float] = None,  # for mood-based pause
    ):
        self._get_state = get_state
        self._is_drag_active = is_drag_active
        self._get_origin = get_window_origin
        self._raw_set_origin = set_window_origin
        # Wrapped origin setter: also computes edge and notifies frontend
        def _origin_with_edge(ox, oy):
            self._raw_set_origin(ox, oy)
            self._update_edge(ox, oy)
        self._set_origin = _origin_with_edge
        self._get_frame = get_visible_frame
        self._set_sub_state = set_sub_state
        self._set_edge = set_edge or (lambda _e: None)
        self._last_edge = ""
        self._is_pinned = is_pinned or (lambda: False)
        self._sprint_mode: bool = False        # True during sprint_perimeter
        self._set_wrapper_deg_cb = lambda _d: None      # set by window.py
        self._clear_wrapper_deg_cb = lambda: None       # set by window.py
        self._trigger_wake_cb = lambda: None             # fires stretch animation
        self._set_sprint_fast_transition_cb = lambda _b: None  # CSS transition speedup
        # is_busy: returns True when Pink is actively working (e.g. Code
        # Puppy session running). When True, the wander loop bails so the
        # pet stays put instead of strolling during active work.
        self._is_busy = is_busy or (lambda: False)
        # When agent_idle >= PAUSE_WHEN_CP_IDLE_SEC she's drowsy/sleeping -
        # wanderer should pause so the mood sprite stays put.
        self._get_agent_idle = get_agent_idle_seconds or (lambda: 0.0)

        # Stroll mode: "anywhere" (free roam) or "edges" (hug screen border).
        # Can be flipped live via set_stroll_mode().
        self._stroll_mode = STROLL_MODE_EDGES

        # Queue for an externally-requested destination (e.g. "go to nearest
        # corner right now"). Consumed by _tick() ahead of normal scheduling.
        self._pending_destination: Optional[tuple[float, float]] = None
        self._pending_lock = threading.Lock()

        self._stop = threading.Event()
        self._enabled = True
        self._idle_since: Optional[float] = None
        self._next_wander_at: Optional[float] = None

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True, name="indigo-wanderer")
        t.start()
        print("[indigo-pet] wanderer thread started", flush=True)

    def stop(self) -> None:
        self._stop.set()

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    def set_stroll_mode(self, mode: str) -> None:
        """Change stroll path live. Valid: 'anywhere' | 'edges'."""
        if mode not in (STROLL_MODE_ANYWHERE, STROLL_MODE_EDGES):
            return
        if mode != self._stroll_mode:
            print(f"[indigo-pet] stroll mode: {self._stroll_mode} → {mode}",
                  flush=True)
            self._stroll_mode = mode

    def get_stroll_mode(self) -> str:
        return self._stroll_mode

    def walk_to_nearest_corner(self) -> None:
        """Queue an immediate walk to the nearest of the 4 screen corners.
        Used when stroll mode flips to 'edges' so she settles in right away
        instead of waiting up to ~28s for the next scheduled wander."""
        origin = self._get_origin()
        frame = self._get_frame()
        if origin is None or frame is None:
            return
        ox, oy = origin
        vx, vy, vw, vh = frame
        min_x = vx + EDGE_MARGIN_PX
        max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
        min_y = vy + BOTTOM_MARGIN_PX           # Dock clearance, not EDGE_MARGIN_PX
        max_y = vy + vh - WIN_H - EDGE_MARGIN_PX
        # 4 corners, pick the nearest by squared distance.
        corners = [
            (min_x, min_y),  # bottom-left
            (max_x, min_y),  # bottom-right
            (min_x, max_y),  # top-left
            (max_x, max_y),  # top-right
        ]
        tx, ty = min(corners, key=lambda c: (c[0]-ox)**2 + (c[1]-oy)**2)
        with self._pending_lock:
            self._pending_destination = (tx, ty)
        print(f"[indigo-pet] queued immediate walk → corner ({tx:.0f},{ty:.0f})",
              flush=True)

    def set_sprint_callbacks(self, wake_cb, fast_transition_cb) -> None:
        """Inject wake + fast-transition callbacks for sprint."""
        self._trigger_wake_cb = wake_cb or (lambda: None)
        self._set_sprint_fast_transition_cb = fast_transition_cb or (lambda _b: None)

    def _trigger_wake(self) -> None:
        try: self._trigger_wake_cb()
        except Exception as e:
            print(f"[indigo-pet] trigger_wake err: {e}", flush=True)

    def _set_sprint_fast_transition(self, on: bool) -> None:
        try: self._set_sprint_fast_transition_cb(bool(on))
        except Exception as e:
            print(f"[indigo-pet] sprint_fast_transition err: {e}", flush=True)

    def set_wrapper_deg_callbacks(self, set_cb, clear_cb) -> None:
        """Inject callbacks for frontend wrapper-deg override (used by sprint)."""
        self._set_wrapper_deg_cb = set_cb or (lambda _d: None)
        self._clear_wrapper_deg_cb = clear_cb or (lambda: None)

    def _set_wrapper_deg(self, deg: float) -> None:
        try: self._set_wrapper_deg_cb(float(deg))
        except Exception as e:
            print(f"[indigo-pet] set_wrapper_deg err: {e}", flush=True)

    def _clear_wrapper_deg(self) -> None:
        try: self._clear_wrapper_deg_cb()
        except Exception as e:
            print(f"[indigo-pet] clear_wrapper_deg err: {e}", flush=True)

    def _compute_edge_at(self, x: float, y: float) -> str:
        """Edge that (x,y) sits on with bottom>left>right>top priority."""
        frame = self._get_frame()
        if frame is None:
            return ""
        vx, vy, vw, vh = frame
        min_x = vx + EDGE_MARGIN_PX
        max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
        min_y = vy + BOTTOM_MARGIN_PX
        max_y = vy + vh - WIN_H - EDGE_MARGIN_PX
        d_left = max(0, x - min_x)
        d_right = max(0, max_x - x)
        d_bottom = max(0, y - min_y)
        d_top = max(0, max_y - y)
        edges = [("bottom", d_bottom, 0), ("left", d_left, 1),
                 ("right", d_right, 2),   ("top", d_top, 3)]
        edges.sort(key=lambda e: (e[1], e[2]))
        nearest, nearest_d, _ = edges[0]
        return nearest if nearest_d <= EDGE_BAND_PX else ""

    def _rotate_first_preamble(self, tx: float, ty: float) -> None:
        """Pre-rotate wrapper if destination is on a different edge, then sleep
        for the rotation transition. Prevents the 'rotating mid-sprint' look."""
        try:
            origin = self._get_origin()
            if origin is None:
                return
            target_edge = self._compute_edge_at(tx, ty)
            current_edge = self._compute_edge_at(origin[0], origin[1])
            if target_edge and target_edge != current_edge:
                self._last_edge = target_edge   # block _update_edge from fighting
                self._set_edge(target_edge)
                print(f"[indigo-pet]   rotate-first: {current_edge or '(none)'} -> "
                      f"{target_edge} (sleeping {ROTATION_PREAMBLE_SEC}s)", flush=True)
                time.sleep(ROTATION_PREAMBLE_SEC)
        except Exception as e:
            print(f"[indigo-pet] rotate-first preamble error: {e}", flush=True)

    def sprint_perimeter(self) -> None:
        """Funny one-shot: sprint through all 4 corners CW from nearest."""
        import threading
        threading.Thread(target=self._do_sprint_perimeter,
                         daemon=True, name="indigo-sprint").start()

    def _do_sprint_perimeter(self) -> None:
        """One full CW lap. Wakes from drowsy/sleeping first. Sequential
        turn-then-walk per leg (rotation fully completes BEFORE walk starts)."""
        try:
            # Step 0: Wake from drowsy/sleeping first.
            # Reset agent_idle counter so frontends drowsy gate clears, AND fire
            # an explicit wake_trigger so the stretch animation plays.
            self._trigger_wake()
            # Wait for the stretch animation to complete before sprinting.
            # (frontend STRETCH_DURATION_MS = 1500ms)
            print(f"[indigo-pet] SPRINT: wake-up wait ({SPRINT_WAKE_WAIT_SEC}s)",
                  flush=True)
            time.sleep(SPRINT_WAKE_WAIT_SEC)

            origin = self._get_origin()
            frame = self._get_frame()
            if origin is None or frame is None:
                return
            vx, vy, vw, vh = frame
            # Sprint hugs the TRUE visibleFrame edges (not BOTTOM_MARGIN_PX).
            # visibleFrame already excludes Dock, so EDGE_MARGIN_PX is sufficient.
            # This adapts to ANY screen size since vx/vy/vw/vh come from NSScreen.
            min_x = vx + EDGE_MARGIN_PX
            max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
            min_y = vy + EDGE_MARGIN_PX
            max_y = vy + vh - WIN_H - EDGE_MARGIN_PX
            corners = [
                (min_x, min_y),  # 0 BL  -> 0deg
                (min_x, max_y),  # 1 TL  -> 90deg
                (max_x, max_y),  # 2 TR  -> 180deg
                (max_x, min_y),  # 3 BR  -> 270deg
            ]
            ox, oy = origin
            dists = [((c[0]-ox)**2 + (c[1]-oy)**2) ** 0.5 for c in corners]
            start_idx = dists.index(min(dists))
            print(f"[indigo-pet] SPRINT v3: corner #{start_idx} from ({ox:.0f},{oy:.0f})",
                  flush=True)

            self._sprint_mode = True
            try:
                # Tell frontend to use FAST rotation transition during sprint.
                self._set_sprint_fast_transition(True)

                # Snap to start corner.
                sx, sy = corners[start_idx]
                self._set_origin(sx, sy)
                base_deg_per_corner = {0: 0, 1: 90, 2: 180, 3: 270}
                start_deg = base_deg_per_corner[start_idx]
                self._set_wrapper_deg(start_deg)
                time.sleep(SPRINT_ROTATION_TRANSITION_SEC + 0.05)  # let initial rotation settle

                prev = (sx, sy)
                cur_deg = start_deg
                for leg_i in range(4):
                    if self._stop.is_set():
                        break
                    target_idx = (start_idx + leg_i + 1) % 4
                    tx, ty = corners[target_idx]
                    target_deg = cur_deg + 90   # always CW
                    facing = "right" if tx > prev[0] else ("left" if tx < prev[0] else "right")

                    # Sub_state stays set throughout (no drowsy/breathe gap).
                    self._set_sub_state(f"walking-{facing}")

                    # TURN FIRST: push new deg and WAIT for rotation to complete.
                    self._set_wrapper_deg(target_deg)
                    print(f"[indigo-pet]   leg {leg_i+1}/4 turn -> deg {target_deg} "
                          f"(wait {SPRINT_ROTATION_TRANSITION_SEC}s)", flush=True)
                    time.sleep(SPRINT_ROTATION_TRANSITION_SEC + 0.10)  # poll cycle (80ms) + transition + margin

                    # THEN walk.
                    print(f"[indigo-pet]   leg {leg_i+1}/4 walk -> ({tx:.0f},{ty:.0f}) "
                          f"facing={facing}", flush=True)
                    self._sprint_walk_leg(prev[0], prev[1], tx, ty, facing)
                    prev = (tx, ty)
                    cur_deg = target_deg

                self._set_sub_state("")
                self._clear_wrapper_deg()
                self._set_sprint_fast_transition(False)
            finally:
                self._sprint_mode = False
            print("[indigo-pet] SPRINT complete", flush=True)
        except Exception as e:
            print(f"[indigo-pet] sprint error: {e}", flush=True)
            self._set_sub_state("")
            try: self._clear_wrapper_deg()
            except Exception: pass
            try: self._set_sprint_fast_transition(False)
            except Exception: pass
            self._sprint_mode = False

    def _sprint_walk_leg(self, ox: float, oy: float, tx: float, ty: float,
                         facing: str) -> None:
        """Dumb straight-line walk for sprint mode. No look-around, no aborts."""
        dist = ((tx - ox) ** 2 + (ty - oy) ** 2) ** 0.5
        speed = WANDER_SPEED_PX_PER_SEC * SPRINT_SPEED_MULT
        duration = max(0.5, dist / speed)
        steps = max(8, int(duration * WANDER_TICK_HZ))
        start_t = time.time()
        for i in range(steps + 1):
            if self._stop.is_set():
                return
            t = i / steps
            e = _ease_in_out(t)
            cx = ox + (tx - ox) * e
            cy = oy + (ty - oy) * e
            self._set_origin(cx, cy)
            target_t = start_t + (i + 1) / WANDER_TICK_HZ
            sleep_for = target_t - time.time()
            if sleep_for > 0:
                time.sleep(sleep_for)


    def _pick_edge_destination(self, ox, oy, min_x, max_x, min_y, max_y):
        """Pick a destination that hugs the screen edge.
        Returns (tx, ty). Strategy:
          1) If not near any edge yet, head straight to the nearest one.
          2) Otherwise walk along the current edge by random distance,
             with a small chance to hop to an adjacent edge (corner pivot).
        """
        # Distance to each edge (clamped to >= 0)
        d_left = max(0, ox - min_x)
        d_right = max(0, max_x - ox)
        d_bottom = max(0, oy - min_y)
        d_top = max(0, max_y - oy)
        # Priority: bottom > left > right > top (Pink's corner spec).
        # bottom-corners -> head up; top-left -> head right; top-right -> head left.
        edges = [("bottom", d_bottom, 0), ("left", d_left, 1),
                 ("right", d_right, 2),   ("top", d_top, 3)]
        edges.sort(key=lambda x: (x[1], x[2]))
        nearest_edge, nearest_dist, _ = edges[0]

        # If she's wandered off-edge (e.g. user just dragged her to center),
        # walk straight to the nearest edge first. Keep the OTHER axis fixed.
        if nearest_dist > EDGE_BAND_PX:
            if nearest_edge == "left":
                return min_x, oy
            if nearest_edge == "right":
                return max_x, oy
            if nearest_edge == "bottom":
                return ox, min_y
            return ox, max_y  # top

        # Already on an edge → walk along it.
        dist = random.uniform(WANDER_DISTANCE_MIN_PX, WANDER_DISTANCE_MAX_PX)
        direction = random.choice([-1, 1])

        # ====== AT-CORNER: route to perpendicular edge ======
        # If she's within EDGE_BAND_PX of TWO or more edges simultaneously,
        # she's at a corner. The priority tie-break above always picks
        # "bottom" first, which would pin her to the bottom edge forever.
        # When at a corner, with CORNER_HOP_PROBABILITY chance, walk her
        # ALONG a perpendicular edge so she actually traverses the screen.
        near_edges = [name for name, d, _ in edges if d <= EDGE_BAND_PX]
        if len(near_edges) >= 2 and random.random() < CORNER_HOP_PROBABILITY:
            # Pick a near edge that ISN'T the priority pick
            perpendicular = [n for n in near_edges if n != nearest_edge]
            if perpendicular:
                target_edge = random.choice(perpendicular)
                hop_dist = random.uniform(WANDER_DISTANCE_MIN_PX, WANDER_DISTANCE_MAX_PX)
                hop_dir = random.choice([-1, 1])
                print(f"[indigo-pet] corner hop: {nearest_edge} -> {target_edge}", flush=True)
                if target_edge == "left":
                    return min_x, max(min_y, min(max_y, oy + hop_dist * hop_dir))
                if target_edge == "right":
                    return max_x, max(min_y, min(max_y, oy + hop_dist * hop_dir))
                if target_edge == "bottom":
                    return max(min_x, min(max_x, ox + hop_dist * hop_dir)), min_y
                if target_edge == "top":
                    return max(min_x, min(max_x, ox + hop_dist * hop_dir)), max_y
        # ====== END at-corner branch ======

        # Hop to an adjacent edge by walking to her current edge's CORNER
        # first. Pink reported diagonal cuts across the open screen which
        # broke "edges only" immersion.
        # NEW (fixed 2026-06-11): walk to a corner of her CURRENT edge.
        # When she arrives at the corner, the at-corner branch above will
        # route her perpendicular, achieving real screen traversal.
        if random.random() < EDGE_HOP_PROBABILITY:
            direction_to_corner = random.choice([-1, 1])
            if nearest_edge == "left":
                return min_x, (min_y if direction_to_corner < 0 else max_y)
            if nearest_edge == "right":
                return max_x, (min_y if direction_to_corner < 0 else max_y)
            if nearest_edge == "bottom":
                return (min_x if direction_to_corner < 0 else max_x), min_y
            return (min_x if direction_to_corner < 0 else max_x), max_y  # top

        # Walk along this edge, snap the other axis to the edge precisely.
        if nearest_edge in ("left", "right"):
            new_y = max(min_y, min(max_y, oy + dist * direction))
            new_x = min_x if nearest_edge == "left" else max_x
            return new_x, new_y
        else:
            new_x = max(min_x, min(max_x, ox + dist * direction))
            new_y = min_y if nearest_edge == "bottom" else max_y
            return new_x, new_y

    def _update_edge(self, ox: float, oy: float) -> None:
        """Determine which screen edge she's on right now (if any) and notify
        the frontend so it can rotate the sprite. EDGE_BAND_PX is the band
        within which she counts as 'on' an edge."""
        try:
            frame = self._get_frame()
            if not frame:
                return
            vx, vy, vw, vh = frame
            min_x = vx + EDGE_MARGIN_PX
            max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
            min_y = vy + BOTTOM_MARGIN_PX
            max_y = vy + vh - WIN_H - EDGE_MARGIN_PX
            d_left = max(0, ox - min_x)
            d_right = max(0, max_x - ox)
            d_bottom = max(0, oy - min_y)
            d_top = max(0, max_y - oy)
            # Priority: bottom > left > right > top (Pink's corner spec).
            # bottom-corners -> head up; top-left -> head right; top-right -> head left.
            edges = [("bottom", d_bottom, 0), ("left", d_left, 1),
                     ("right", d_right, 2),   ("top", d_top, 3)]
            edges.sort(key=lambda x: (x[1], x[2]))
            nearest, nearest_d, _ = edges[0]
            edge = nearest if nearest_d <= EDGE_BAND_PX else ""
            if edge != self._last_edge:
                self._last_edge = edge
                self._set_edge(edge)
                print(f"[indigo-pet] edge -> {edge or '(none)'}", flush=True)
        except Exception as e:
            print(f"[indigo-pet] _update_edge error: {e}", flush=True)

    # ──────────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[indigo-pet] wanderer error: {e}", flush=True)
            time.sleep(0.5)  # cheap outer loop; actual walk uses its own tick rate

    def _tick(self) -> None:
        if not self._enabled:
            return

        # Refresh edge FIRST, before any state gates. Edge tracking must work
        # regardless of whether she's idle/working/sleeping — the frontend
        # rotation depends on it always being accurate to current position.
        try:
            cur = self._get_origin()
            if cur:
                self._update_edge(cur[0], cur[1])
        except Exception as e:
            print(f"[indigo-pet] tick edge refresh err: {e}", flush=True)

        # Sprint is running on its own thread; main wander loop must stay
        # out of the way (no fighting for origin/sub_state).
        if self._sprint_mode:
            return

        state = self._get_state()
        now = time.time()

        # Mood pause: if she's been TPA-idle long enough to be drowsy/sleeping,
        # stop wandering. Sleeping pets don't pace. Edge stays updated via
        # the refresh above so rotation remains correct if she's on a wall.
        if self._get_agent_idle() >= PAUSE_WHEN_CP_IDLE_SEC:
            self._idle_since = None
            self._next_wander_at = None
            return

        # Respect ⚓ pin from the right-click menu: no wandering.
        if self._is_pinned():
            self._idle_since = None
            self._next_wander_at = None
            return
        # Skip wandering when actively working (TPA session running)
        # -- Pink's rule: pet stays put during active work.
        if self._is_busy():
            self._idle_since = None
            self._next_wander_at = None
            return
        # Only wander when she's idle and not being dragged.
        if state != "idle" or self._is_drag_active():
            self._idle_since = None
            self._next_wander_at = None
            return

        if self._idle_since is None:
            self._idle_since = now

        # ── Priority: external pending destination (e.g. nearest corner) ──
        # Bypass idle threshold and scheduled-time wait entirely.
        with self._pending_lock:
            pending = self._pending_destination
            self._pending_destination = None
        if pending is not None:
            self._do_one_wander(target_override=pending)
            # After settling, resume normal scheduling.
            self._next_wander_at = time.time() + random.uniform(
                WANDER_INTERVAL_MIN_SEC, WANDER_INTERVAL_MAX_SEC
            )
            return

        if now - self._idle_since < IDLE_BEFORE_WANDER_SEC:
            return

        if self._next_wander_at is None:
            # Schedule the first wander after the idle threshold.
            self._next_wander_at = now + random.uniform(0, 4.0)

        if now < self._next_wander_at:
            return

        # Time to wander!
        self._do_one_wander()

        # Schedule the next wander.
        self._next_wander_at = time.time() + random.uniform(
            WANDER_INTERVAL_MIN_SEC, WANDER_INTERVAL_MAX_SEC
        )

    def _do_one_wander(self, target_override=None) -> None:
        origin = self._get_origin()
        frame = self._get_frame()  # (vx, vy, vw, vh) in Cocoa coords (y from bottom)
        if origin is None or frame is None:
            return
        ox, oy = origin
        vx, vy, vw, vh = frame

        # Pick a destination within the visible frame, at least MIN distance away.
        min_x = vx + EDGE_MARGIN_PX
        max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
        min_y = vy + BOTTOM_MARGIN_PX           # Dock clearance, not EDGE_MARGIN_PX
        max_y = vy + vh - WIN_H - EDGE_MARGIN_PX
        if max_x <= min_x or max_y <= min_y:
            return

        # If caller forced a target (e.g. nearest-corner snap), use it as-is.
        if target_override is not None:
            tx, ty = target_override
            tx = max(min_x, min(max_x, tx))
            ty = max(min_y, min(max_y, ty))
        # Pick destination based on stroll mode.
        elif self._stroll_mode == STROLL_MODE_EDGES:
            tx, ty = self._pick_edge_destination(ox, oy,
                                                 min_x, max_x, min_y, max_y)
        else:
            # ANYWHERE (default): POLAR pick — random angle + distance,
            # clamp to visible frame. Guarantees distance ∈ [MIN, MAX]
            # regardless of starting position.
            import math
            tx = ty = None
            for _attempt in range(12):
                angle = random.uniform(0, 2 * math.pi)
                dist = random.uniform(WANDER_DISTANCE_MIN_PX, WANDER_DISTANCE_MAX_PX)
                cand_x = ox + dist * math.cos(angle)
                cand_y = oy + dist * math.sin(angle)
                if min_x <= cand_x <= max_x and min_y <= cand_y <= max_y:
                    tx, ty = cand_x, cand_y
                    break
            if tx is None:
                # Fallback: clamp last candidate. Distance may be < MIN.
                tx = max(min_x, min(max_x, cand_x))
                ty = max(min_y, min(max_y, cand_y))
        dist = ((tx - ox) ** 2 + (ty - oy) ** 2) ** 0.5

        speed = WANDER_SPEED_PX_PER_SEC * (SPRINT_SPEED_MULT if self._sprint_mode else 1.0)
        duration = max(0.8, min(WANDER_MAX_DURATION_SEC, dist / speed))
        facing = "left" if tx < ox else "right"
        print(
            f"[indigo-pet] wander: ({ox:.0f},{oy:.0f}) → ({tx:.0f},{ty:.0f}) "
            f"dist={dist:.0f}px duration={duration:.2f}s facing={facing}",
            flush=True,
        )

        # Rotate-first: if destination is on a different edge, pre-rotate
        # the wrapper and wait briefly before legs start moving.
        self._rotate_first_preamble(tx, ty)

        # Tell the frontend: legs go!
        self._set_sub_state(f"walking-{facing}")

        steps = max(8, int(duration * WANDER_TICK_HZ))
        start_t = time.time()
        # Brief state-changes (1-2 ticks) shouldn't kill the wander.
        # Only abort if state has been non-idle for ABORT_STREAK consecutive checks.
        ABORT_STREAK = 8   # ~130ms of non-idle to abort
        non_idle_streak = 0
        last_print_step = -1

        # Decide whether (and where) to pause for a curious look-around mid-walk.
        # Only on walks of decent length, and not 100% of the time — surprise!
        look_at_step = -1
        if dist > 100 and random.random() < LOOK_AROUND_PROBABILITY:
            look_at_step = random.randint(int(steps * 0.35), int(steps * 0.70))
            print(f"[indigo-pet]   (will pause to look around at step {look_at_step}/{steps})",
                  flush=True)

        for i in range(steps + 1):
            if self._stop.is_set():
                print("[indigo-pet] wander stopped by signal", flush=True)
                break
            cur = self._get_state()
            if cur != "idle" and not self._sprint_mode:
                non_idle_streak += 1
                if non_idle_streak >= ABORT_STREAK:
                    print(f"[indigo-pet] wander aborted: state={cur} (sustained)", flush=True)
                    break
            else:
                non_idle_streak = 0
            if self._is_drag_active():
                print("[indigo-pet] wander aborted: user is dragging", flush=True)
                break

            t = i / steps
            e = _ease_in_out(t)
            cx = ox + (tx - ox) * e
            cy = oy + (ty - oy) * e
            self._set_origin(cx, cy)

            # Step debug every 20 steps
            if i - last_print_step >= 20 or i == steps:
                print(f"[indigo-pet]   wander step {i}/{steps}: pos=({cx:.0f},{cy:.0f}) "
                      f"t={t:.2f} cur_state={cur}", flush=True)
                last_print_step = i

            # ─── Look-around pause ───
            if i == look_at_step:
                print(f"[indigo-pet]   ✨ pausing to look around (1.4s)", flush=True)
                self._set_sub_state(f"looking-around-{facing}")
                # Hold for LOOK_AROUND_DURATION, checking for aborts.
                pause_until = time.time() + LOOK_AROUND_DURATION_SEC
                while time.time() < pause_until and not self._stop.is_set():
                    if self._get_state() != "idle" or self._is_drag_active():
                        break
                    time.sleep(0.05)
                # Resume walking. Restart timing baseline so the remaining
                # path uses fresh pacing (no compressed catch-up).
                start_t = time.time() - (i + 1) / WANDER_TICK_HZ
                self._set_sub_state(f"walking-{facing}")

            # pace
            target_t = start_t + (i + 1) / WANDER_TICK_HZ
            sleep_for = target_t - time.time()
            if sleep_for > 0:
                time.sleep(sleep_for)

        # Stop walking animation
        self._set_sub_state("")
        # Confirm final position
        final = self._get_origin()
        if final:
            print(f"[indigo-pet] wander finished: final pos=({final[0]:.0f},{final[1]:.0f}) "
                  f"target=({tx:.0f},{ty:.0f})", flush=True)
