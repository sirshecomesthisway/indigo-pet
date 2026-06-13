"""
WanderController — SERVICE MODE.

After the unify-idle-rhythm refactor (2026-06-13), the wanderer no longer
owns its own scheduling loop. It exposes two fire-and-forget methods that
the RoutineController calls:

    wanderer.request_walk("short" | "medium" | "edge")
    wanderer.request_look_around()

Plus the still-internal `sprint_perimeter()` easter egg, which runs its
own thread and is invoked from the right-click menu.

Edge tracking is still done here (every walk updates the current edge
via _update_edge) because the frontend sprite rotation depends on it.

What got removed in service-mode:
  - `_loop`, `_tick`, `start()` thread spawn
  - `_idle_since`, `_next_wander_at` scheduling state
  - `STROLL_MODE_*`, `_stroll_mode`, `set_stroll_mode`, `get_stroll_mode`
  - `walk_to_nearest_corner` + `_pending_destination` queue
  - `PAUSE_WHEN_CP_IDLE_SEC` + `_get_agent_idle` (routine owns mood gating)
  - `is_pinned`, `is_busy` constructor params (routine owns gating)

Why Python-side NSWindow moves (not CSS): pywebview's window is a real
NSWindow. CSS translations only move the sprite WITHIN the 200×220 viewport.
To actually roam the desktop we have to move the NSWindow itself.
"""
from __future__ import annotations

import math
import random
import threading
import time
from typing import Callable, Optional


# Motion params (unchanged from pre-refactor)
WANDER_SPEED_PX_PER_SEC = 110          # walking speed
WANDER_MAX_DURATION_SEC = 3.0          # hard cap — never walk longer than this
WANDER_TICK_HZ = 30                    # smoothness
EDGE_MARGIN_PX = 12                    # keep this far from visibleFrame left/right/top
BOTTOM_MARGIN_PX = -40                 # feet AT visible-frame bottom (auto-hide Dock)
LOOK_AROUND_DURATION_SEC = 1.4         # how long mid-walk look-around lasts
LOOK_AROUND_PROBABILITY = 0.45         # chance of pause-to-look mid-walk
WIN_W = 200                            # window width (must match window.py)
WIN_H = 220                            # window height
EDGE_BAND_PX = 60                      # within this distance of an edge counts as "on" it

# Distance bands for request_walk(band)
BAND_DISTANCES = {
    "short":  (60, 180),
    "medium": (120, 320),
    # "edge" uses edge-picker, not polar
}

# Sprint params (unchanged)
SPRINT_SPEED_MULT = 5.0
SPRINT_ROTATION_TRANSITION_SEC = 0.20
SPRINT_WAKE_WAIT_SEC = 1.6
ROTATION_PREAMBLE_SEC = 0.7


def _ease_in_out(t: float) -> float:
    """Smooth ease — slow start, fast middle, slow end."""
    return 3 * t * t - 2 * t * t * t


class WanderController:
    """Owns the walk/look/sprint primitives. Stateless w.r.t. scheduling —
    the RoutineController drives all idle-time invocations."""

    def __init__(
        self,
        get_state: Callable[[], str],          # returns current state name
        is_drag_active: Callable[[], bool],    # returns True if ugging
        get_window_origin: Callable[[], tuple[float, float] | None],
        set_window_origin: Callable[[float, float], None],
        get_visible_frame: Callable[[], tuple[float, float, float, float]],
        set_sub_state: Callable[[str], None],
        set_edge: Callable[[str], None] = None,
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

        # Sprint callbacks (injected via setters from window.py)
        self._set_wrapper_deg_cb = lambda _d: None
        self._clear_wrapper_deg_cb = lambda: None
        self._trigger_wake_cb = lambda: None
        self._set_sprint_fast_transition_cb = lambda _b: None

        # Sprint state
        self._sprint_mode: bool = False

        # Shared stop event (mostly for sprint thread)
        self._stop = threading.Event()

    # ── lifecycle ──────────────────────────────────────────────────────
    def stop(self) -> None:
        """Signal in-flight walks / sprints to abort."""
        self._stop.set()

    # ── public SERVICE methods (called by RoutineController) ───────────
    def request_walk(self, band: str) -> None:
        """Fire-and-forget walk in the given distance band.

        band: "short" | "medium" | "edge"
            "short"  ≈ 60-180px hop nearby
            "medium" ≈ 120-320px traverse
            "edge"   walk to a screen-edge point (corner-hop logic)

        Returns immediately. Walk runs on a daemon thread. Safe to call
        while sprint is running (no-op) or while another walk is mid-flight
        (no-op via internal lock to avoid origin-fight).
        """
        if band not in ("short", "medium", "edge"):
            print(f"[indigo-pet] request_walk: unknown band '{band}'", flush=True)
            return
        if self._sprint_mode:
            return
        t = threading.Thread(target=self._do_request_walk, args=(band,),
                             daemon=True, name=f"indigo-walk-{band}")
        t.start()

    def request_look_around(self) -> None:
        """Fire a transient look-around (~1.4s). No-op during sprint or
        while a walk is animating (would stomp sub_state)."""
        if self._sprint_mode:
            return
        t = threading.Thread(target=self._do_look_around, daemon=True,
                             name="indigo-look")
        t.start()

    # ── walk implementation ────────────────────────────────────────────
    def _do_request_walk(self, band: str) -> None:
        origin = self._get_origin()
        frame = self._get_frame()
        if origin is None or frame is None:
            return
        ox, oy = origin
        vx, vy, vw, vh = frame
        min_x = vx + EDGE_MARGIN_PX
        max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
        min_y = vy + BOTTOM_MARGIN_PX
        max_y = vy + vh - WIN_H - EDGE_MARGIN_PX
        if max_x <= min_x or max_y <= min_y:
            return
        tx, ty = self._pick_target_for_band(band, ox, oy,
                                            min_x, max_x, min_y, max_y)
        self._walk_to(ox, oy, tx, ty, vx, vy, vw, vh)

    def _pick_target_for_band(self, band, ox, oy, min_x, max_x, min_y, max_y):
        if band == "edge":
            return self._pick_edge_destination(ox, oy,
                                               min_x, max_x, min_y, max_y)
        dmin, dmax = BAND_DISTANCES[band]
        # Polar pick — random angle + distance, clamp to visible frame.
        cand_x = cand_y = None
        for _ in range(12):
            angle = random.uniform(0, 2 * math.pi)
            dist = random.uniform(dmin, dmax)
            cand_x = ox + dist * math.cos(angle)
            cand_y = oy + dist * math.sin(angle)
            if min_x <= cand_x <= max_x and min_y <= cand_y <= max_y:
                return cand_x, cand_y
        # Fallback: clamp last candidate
        return (max(min_x, min(max_x, cand_x)),
                max(min_y, min(max_y, cand_y)))

    def _walk_to(self, ox, oy, tx, ty, vx, vy, vw, vh) -> None:
        """Animate window origin from (ox,oy) → (tx,ty)."""
        dist = ((tx - ox) ** 2 + (ty - oy) ** 2) ** 0.5
        speed = WANDER_SPEED_PX_PER_SEC
        duration = max(0.8, min(WANDER_MAX_DURATION_SEC, dist / speed))
        facing = "left" if tx < ox else "right"
        print(
            f"[indigo-pet] walk: ({ox:.0f},{oy:.0f}) → ({tx:.0f},{ty:.0f}) "
            f"dist={dist:.0f}px dur={duration:.2f}s facing={facing}",
            flush=True,
        )

        # Rotate-first if destination is on a different edge
        self._rotate_first_preamble(tx, ty)

        # Tell the frontend: legs go!
        self._set_sub_state(f"walking-{facing}")

        steps = max(8, int(duration * WANDER_TICK_HZ))
        start_t = time.time()
        ABORT_STREAK = 8
        non_idle_streak = 0

        # Optional mid-walk look-around (longer walks only)
        look_at_step = -1
        if dist > 100 and random.random() < LOOK_AROUND_PROBABILITY:
            look_at_step = random.randint(int(steps * 0.35), int(steps * 0.70))

        for i in range(steps + 1):
            if self._stop.is_set():
                break
            cur = self._get_state()
            if cur != "idle" and not self._sprint_mode:
                non_idle_streak += 1
                if non_idle_streak >= ABORT_STREAK:
                    print(f"[indigo-pet] walk aborted: state={cur}", flush=True)
                    break
            else:
                non_idle_streak = 0
            if self._is_drag_active():
                print("[indigo-pet] walk aborted: user dragging", flush=True)
                break

            t = i / steps
            e = _ease_in_out(t)
            cx = ox + (tx - ox) * e
            cy = oy + (ty - oy) * e
            self._set_origin(cx, cy)

            if i == look_at_step:
                self._set_sub_state(f"looking-around-{facing}")
                pause_until = time.time() + LOOK_AROUND_DURATION_SEC
                while time.time() < pause_until and not self._stop.is_set():
                    if self._get_state() != "idle" or self._is_drag_active():
                        break
                    time.sleep(0.05)
                start_t = time.time() - (i + 1) / WANDER_TICK_HZ
                self._set_sub_state(f"walking-{facing}")

            target_t = start_t + (i + 1) / WANDER_TICK_HZ
            sleep_for = target_t - time.time()
            if sleep_for > 0:
                time.sleep(sleep_for)

        self._set_sub_state("")

    def _do_look_around(self) -> None:
        """Set looking-around sub_state for ~1.4s, then clear."""
        facing = random.choice(["left", "right"])
        print(f"[indigo-pet] look-around-{facing}", flush=True)
        self._set_sub_state(f"looking-around-{facing}")
        end_at = time.time() + LOOK_AROUND_DURATION_SEC
        while time.time() < end_at and not self._stop.is_set():
            if self._get_state() != "idle" or self._is_drag_active():
                break
            time.sleep(0.05)
        self._set_sub_state("")

    # ── edge picking (used for band="edge") ────────────────────────────
    def _pick_edge_destination(self, ox, oy, min_x, max_x, min_y, max_y):
        """Pick a destination that hugs the screen edge.
        Strategy:
          1) If not near any edge, head straight to the nearest one.
          2) Otherwise walk along the current edge to a corner.
        """
        d_left = max(0, ox - min_x)
        d_right = max(0, max_x - ox)
        d_bottom = max(0, oy - min_y)
        d_top = max(0, max_y - oy)
        edges = [("bottom", d_bottom, 0), ("left", d_left, 1),
                 ("right", d_right, 2),   ("top", d_top, 3)]
        edges.sort(key=lambda x: (x[1], x[2]))
        nearest_edge, nearest_dist, _ = edges[0]

        # Off-edge → walk straight to nearest edge.
        if nearest_dist > EDGE_BAND_PX:
            if nearest_edge == "left":
                return min_x, oy
            if nearest_edge == "right":
                return max_x, oy
            if nearest_edge == "bottom":
                return ox, min_y
            return ox, max_y  # top

        # On an edge: walk toward a corner of that edge.
        direction_to_corner = random.choice([-1, 1])
        if nearest_edge == "left":
            return min_x, (min_y if direction_to_corner < 0 else max_y)
        if nearest_edge == "right":
            return max_x, (min_y if direction_to_corner < 0 else max_y)
        if nearest_edge == "bottom":
            return (min_x if direction_to_corner < 0 else max_x), min_y
        return (min_x if direction_to_corner < 0 else max_x), max_y  # top

    # ── edge tracking (for frontend sprite rotation) ───────────────────
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

    def _update_edge(self, ox: float, oy: float) -> None:
        """Edge tracker — notify frontend on transitions."""
        try:
            edge = self._compute_edge_at(ox, oy)
            if edge != self._last_edge:
                self._last_edge = edge
                self._set_edge(edge)
                print(f"[indigo-pet] edge -> {edge or '(none)'}", flush=True)
        except Exception as e:
            print(f"[indigo-pet] _update_edge error: {e}", flush=True)

    def _rotate_first_preamble(self, tx: float, ty: float) -> None:
        """Pre-rotate wrapper if destination is on a different edge, then sleep
        for the rotation transition. Prevents the 'rotating mid-walk' look."""
        try:
            origin = self._get_origin()
            if origin is None:
                return
            target_edge = self._compute_edge_at(tx, ty)
            current_edge = self._compute_edge_at(origin[0], origin[1])
            if target_edge and target_edge != current_edge:
                self._last_edge = target_edge
                self._set_edge(target_edge)
                print(f"[indigo-pet]   rotate-first: "
                      f"{current_edge or '(none)'} -> {target_edge}",
                      flush=True)
                time.sleep(ROTATION_PREAMBLE_SEC)
        except Exception as e:
            print(f"[indigo-pet] rotate-first error: {e}", flush=True)

    # ── sprint (unchanged easter egg) ──────────────────────────────────
    def set_sprint_callbacks(self, wake_cb, fast_transition_cb) -> None:
        self._trigger_wake_cb = wake_cb or (lambda: None)
        self._set_sprint_fast_transition_cb = fast_transition_cb or (lambda _b: None)

    def set_wrapper_deg_callbacks(self, set_cb, clear_cb) -> None:
        self._set_wrapper_deg_cb = set_cb or (lambda _d: None)
        self._clear_wrapper_deg_cb = clear_cb or (lambda: None)

    def _trigger_wake(self) -> None:
        try: self._trigger_wake_cb()
        except Exception as e:
            print(f"[indigo-pet] trigger_wake err: {e}", flush=True)

    def _set_sprint_fast_transition(self, on: bool) -> None:
        try: self._set_sprint_fast_transition_cb(bool(on))
        except Exception as e:
            print(f"[indigo-pet] sprint_fast_transition err: {e}", flush=True)

    def _set_wrapper_deg(self, deg: float) -> None:
        try: self._set_wrapper_deg_cb(float(deg))
        except Exception as e:
            print(f"[indigo-pet] set_wrapper_deg err: {e}", flush=True)

    def _clear_wrapper_deg(self) -> None:
        try: self._clear_wrapper_deg_cb()
        except Exception as e:
            print(f"[indigo-pet] clear_wrapper_deg err: {e}", flush=True)

    def sprint_perimeter(self) -> None:
        """Funny one-shot: sprint through all 4 corners CW from nearest."""
        threading.Thread(target=self._do_sprint_perimeter,
                         daemon=True, name="indigo-sprint").start()

    def _do_sprint_perimeter(self) -> None:
        try:
            # Wake from drowsy/sleeping first
            self._trigger_wake()
            print(f"[indigo-pet] SPRINT: wake-up wait ({SPRINT_WAKE_WAIT_SEC}s)",
                  flush=True)
            time.sleep(SPRINT_WAKE_WAIT_SEC)

            origin = self._get_origin()
            frame = self._get_frame()
            if origin is None or frame is None:
                return
            vx, vy, vw, vh = frame
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
            print(f"[indigo-pet] SPRINT v3: corner #{start_idx} "
                  f"from ({ox:.0f},{oy:.0f})", flush=True)

            self._sprint_mode = True
            try:
                self._set_sprint_fast_transition(True)
                sx, sy = corners[start_idx]
                self._set_origin(sx, sy)
                base_deg_per_corner = {0: 0, 1: 90, 2: 180, 3: 270}
                start_deg = base_deg_per_corner[start_idx]
                self._set_wrapper_deg(start_deg)
                time.sleep(SPRINT_ROTATION_TRANSITION_SEC + 0.05)

                prev = (sx, sy)
                cur_deg = start_deg
                for leg_i in range(4):
                    if self._stop.is_set():
                        break
                    target_idx = (start_idx + leg_i + 1) % 4
                    tx, ty = corners[target_idx]
                    target_deg = cur_deg + 90
                    facing = "right" if tx > prev[0] else ("left" if tx < prev[0] else "right")
                    self._set_sub_state(f"walking-{facing}")
                    self._set_wrapper_deg(target_deg)
                    print(f"[indigo-pet]   leg {leg_i+1}/4 turn -> deg {target_deg}",
                          flush=True)
                    time.sleep(SPRINT_ROTATION_TRANSITION_SEC + 0.10)
                    print(f"[indigo-pet]   leg {leg_i+1}/4 walk -> "
                          f"({tx:.0f},{ty:.0f}) facing={facing}", flush=True)
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

    def _sprint_walk_leg(self, ox, oy, tx, ty, facing) -> None:
        """Dumb straight-line walk for sprint mode."""
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
