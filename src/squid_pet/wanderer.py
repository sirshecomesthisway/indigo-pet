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

from . import window


# Motion params (unchanged from pre-refactor)
WANDER_SPEED_PX_PER_SEC = 110          # walking speed
WANDER_MAX_DURATION_SEC = 3.0          # hard cap — never walk longer than this
WANDER_TICK_HZ = 30                    # smoothness
EDGE_MARGIN_PX = -51                   # walk-TARGET selection margin for the LEFT edge only. Set
                                       # NEGATIVE 2026-08-18 so the wander-target picker's own
                                       # boundary is at window.CHAR_LEFT_IN_WIN -- the ALREADY-TRUSTED
                                       # hard clamp applied on every origin-set (drag/corner-snap/
                                       # wander alike) -- instead of stopping short of it. -51 exactly
                                       # matches CHAR_LEFT_IN_WIN(51), so LEFT's target reaches its
                                       # hard clamp exactly (see min_x below).
                                       #
                                       # RIGHT no longer shares this constant (Pink-2026-08-29): it
                                       # used to reuse this SAME value via an "overshoot past
                                       # window.CHAR_RIGHT_IN_WIN's clamp, let the clamp position her"
                                       # trick -- correct arithmetic only as long as this margin's
                                       # overshoot and CHAR_RIGHT_IN_WIN's actual clamp stayed in sync,
                                       # which nobody re-checked when CHAR_RIGHT_IN_WIN was tightened
                                       # 190->158 on 2026-08-18 for a closer idle-pose fit. The clamp
                                       # still caught her at the (now different, correct) real edge --
                                       # but this module's own d_right bookkeeping still measured
                                       # against the STALE 190-based target, permanently believing she
                                       # was 9px short of the corner. Close enough to still count as
                                       # "on the edge" for rotation (EDGE_BAND_PX=60 swallows 9px), but
                                       # far enough that _pick_edge_destination kept re-picking "walk
                                       # the remaining 9px" forever, since she could never actually
                                       # reach a target past where the clamp already caught her -- a
                                       # perpetual, visible micro-correction that never settled (Pink
                                       # report: "不會停在真正的右上角", i.e. never actually stops at
                                       # the real corner). Fixed by deriving max_x directly from
                                       # window.CHAR_RIGHT_IN_WIN via _x_bounds() below instead of
                                       # maintaining a second, separately-tuned approximation of it --
                                       # see _x_bounds' docstring.
BOTTOM_MARGIN_PX = -45                 # feet AT visible-frame bottom (auto-hide Dock). MUST match
                                       # (or exceed) window.CHAR_BOTTOM_IN_WIN, same pairing as
                                       # EDGE_MARGIN_PX/CHAR_LEFT_IN_WIN above -- was -40 against the
                                       # OLD CHAR_BOTTOM_IN_WIN=8, which meant wander's own target
                                       # already reached that clamp fine. 2026-08-18:
                                       # CHAR_BOTTOM_IN_WIN was corrected 8 -> 45 (the old value was
                                       # based on a sprite-extent number that direct measurement
                                       # disproved -- see window.CHAR_BOTTOM_IN_WIN), which made the
                                       # hard clamp TIGHTER than wander's stale -40 target. Bumped to
                                       # -45 to match and actually reach the new, correct boundary.
TOP_MARGIN_PX    = 42                   # Binary-search step. CONFIRMED broken (nearly/fully
                                       # invisible, screenshot evidence): <=35. CONFIRMED safe
                                       # (fully visible, Pink 2026-08-18): 65, 100. 50 CONFIRMED
                                       # safe too (fully visible, live screenshot, Pink
                                       # 2026-08-30 -- narrows the safe boundary down from 65).
                                       # 42 is the midpoint of the now-narrowed (35,50) range --
                                       # NOT derived from transform math (that's what broke
                                       # things twice), just bisection. Pink wants her tentacles
                                       # actually TOUCHING the top edge, i.e. as low as this
                                       # range allows without clipping.
                                       # If 42 is visible: next step down is ~38 (midpoint of 35-42).
                                       # If 42 clips: next step up is ~46 (midpoint of 42-50).
                                       # Keep halving the gap either way -- don't jump. (Was 0, then
                                       # 35 [broken], then briefly 10 [broken], then 155 [safe, too
                                       # far], then 100 [safe], then 65 [safe], then 50 [safe]. Pink
                                       # 2026-07-12, 2026-08-18, 2026-08-30.)
LOOK_AROUND_DURATION_SEC = 1.4         # how long mid-walk look-around lasts
LOOK_AROUND_PROBABILITY = 0.45         # chance of pause-to-look mid-walk
WIN_W = 200                            # window width (must match window.py)
WIN_H = 300                            # window height (MUST match window.WINDOW_HEIGHT and passthrough.WINDOW_HEIGHT)
                                       # Was 220 originally; bumped to 300 for hearts headroom (window.py).
                                       # passthrough.py was synced; wanderer.py was missed -> top-edge strobe
                                       # bug 2026-06-16 (rhythm walk targeted ty=max_y+80 which clamped to
                                       # actual max_y, causing edge classifier to flap in/out of the band).
                                       # See kennel drawer (constant-duplication anti-pattern). Future:
                                       # consolidate into squid_pet.geometry module.
CHAR_TOP_IN_WIN = 145                  # opaque-pixel top of character within window (cocoa y-up from window bottom)
                                       # MIRRORS window.CHAR_TOP_IN_WIN — see window.py:65-75 for derivation.
                                       # Used to clamp max_y so character reaches menu bar (window has 135px
                                       # transparent headroom above sprite for hearts/emotes). Fixed 2026-07-07:
                                       # was using WIN_H, stranding Squid ~147px below menu bar. (Pink report.)
                                       # Future: consolidate into squid_pet.geometry module.
EDGE_BAND_PX = 60                      # within this distance of an edge counts as "on" it
CORNER_BAND_PX = 120                   # within this distance of TWO edges counts as "at a corner"
                                       # (more generous than EDGE_BAND_PX to absorb dock/menubar clamp drift)

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

# Nudge params -- a single quick hop away from the cursor, triggered by
# passthrough.NudgeApproachTracker on repeated rapid approaches. Snappier
# than a normal wander walk (short min duration, no easing preamble) so
# it reads as a flinch, not a stroll.
NUDGE_HOP_DISTANCE_PX = 70
NUDGE_SPEED_PX_PER_SEC = 400
NUDGE_MIN_DURATION_SEC = 0.45          # Pink-2026-08-30 experiment (report: "flakey...
                                       # losing some frames" while nudging). Was 0.25;
                                       # 0.35 (first attempt, 11 updates) still felt short
                                       # 3 more frames -> 0.45 exactly (each +1/WANDER_TICK_HZ
                                       # of duration = +1 step, so +3 steps = +3/30 = +0.1s).
                                       # A typical NUDGE_HOP_DISTANCE_PX(70) hop finishes
                                       # in 70/400=0.175s by speed alone -- WAY under the
                                       # floor, so this constant (not NUDGE_SPEED_PX_PER_SEC)
                                       # is what actually decides a normal hop's duration,
                                       # and duration*WANDER_TICK_HZ(30) is what decides its
                                       # step count: at the original 0.25 that's only 7 steps
                                       # (8 origin updates total) -- too few to read as
                                       # smooth motion. 0.45 gives 13 steps (14 updates),
                                       # ~75% more than the original -- still under half a
                                       # second (reads as a reflex, not a stroll). Only the
                                       # SHORT hop is affected -- a corner-flee across most
                                       # of the wander range is already speed-bound (well
                                       # over the floor) and already has plenty of steps.
                                       # Keep widening in +1/WANDER_TICK_HZ(=+0.0333s)
                                       # increments (that's what buys exactly +1 more frame)
                                       # if still choppy; tighten back down if it starts
                                       # feeling sluggish instead of reactive -- test live,
                                       # this wasn't derived from a measurement of what
                                       # "smooth enough" requires.
# Below this much actual movement, the away-from-cursor hop is considered
# "stuck" (corner case -- see _do_request_nudge's fallback) rather than a
# genuine short hop near a wall.
NUDGE_STUCK_THRESHOLD_PX = 10

# How long a corner-stuck escape direction stays "sticky" (2026-08-21,
# Pink report follow-up): repeated real-world pushes from roughly the
# same side never land at EXACTLY the same cursor y each time -- a few
# pixels of natural jitter flips dy's sign push to push. Recomputing the
# escape direction fresh every single stuck hop from that noisy sign
# made her wobble up/down the edge instead of making steady progress
# (verified via a repeated-push simulation with +/-3px jitter: without
# stickiness she bounced 605->535->465->395->465->535->605->... instead
# of ever reaching the far corner). Reusing the same escape direction for
# this many seconds after it's first picked means one noisy push can't
# reverse a bout of stuck hops already in progress -- only a real lull
# (implying a fresh situation) lets it re-decide.
STUCK_ESCAPE_STICKY_SEC = 3.0


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
        # See _rotate_first_preamble / _update_edge: True while a walk
        # that just pre-rotated to a NEW edge is in flight, so the
        # per-tick position-based tracker doesn't fight the decision.
        self._edge_locked_for_walk = False

        # Sprint callbacks (injected via setters from window.py)
        self._set_wrapper_deg_cb = lambda _d: None
        self._clear_wrapper_deg_cb = lambda: None
        self._trigger_wake_cb = lambda: None
        self._set_sprint_fast_transition_cb = lambda _b: None

        # Sprint state. _sprint_lock guards the check-and-set in
        # sprint_perimeter() so two rapid double-clicks of the menu item
        # can't both pass the "not already sprinting" check and spawn
        # concurrent _do_sprint_perimeter threads racing on window
        # origin / wrapper-rotation state (found in a 2026-08-17 review).
        self._sprint_mode: bool = False
        self._sprint_lock = threading.Lock()

        # Corner-stuck escape stickiness (see STUCK_ESCAPE_STICKY_SEC above
        # and _corner_escape_target's docstring).
        self._corner_escape_cache: tuple[float, float] | None = None
        self._corner_escape_cache_time: float = 0.0

        # Hop/flee animation generation counter (see _bump_nudge_generation).
        self._nudge_generation: int = 0
        self._nudge_generation_lock = threading.Lock()

        # True while a nudge/flee hop (_animate_hop) is actively animating.
        # Pink-2026-08-30 report: "flakey... losing some frames when she
        # runs" during a nudge. Root cause: _do_request_walk's ambient
        # idle-wander thread and _animate_hop's nudge/flee thread had NO
        # mutual exclusion at all -- each independently ticks its own
        # interpolation and calls self._set_origin() on its own schedule,
        # so if the RoutineController's idle-walk scheduler happens to
        # fire while a nudge/flee is mid-hop (routine timing is
        # independent of user cursor activity, so this coincidence is
        # not rare), the two threads' writes interleave and the window
        # visibly jumps between two unrelated trajectories tick by tick
        # -- exactly "losing frames" / stutter, not smooth motion along
        # either path. _nudge_generation already fully protects
        # nudge/flee against EACH OTHER (a newer one preempts an older
        # one in flight) but nothing previously extended that protection
        # to a concurrently-running wander walk. A nudge is a direct
        # physical reaction to the user's cursor and should always win
        # over ambient wandering, so this flag makes wander walks defer
        # to (never fight) an in-flight hop: checked before a walk even
        # starts and on every tick of one already in progress (see
        # _do_request_walk and _walk_to). Deliberately NOT the other way
        # around -- a wander walk starting must never interrupt an
        # active nudge/flee reaction.
        self._hop_active: bool = False

        # Stroll mode: "edges" (hug border) or "anywhere" (free roam).
        # Restored 2026-06-13 after unify-idle-rhythm regression.
        # Default matches pre-regression behavior. Flipped live via
        # set_stroll_mode(); PetApi persists the choice to settings.json.
        self._stroll_mode: str = "edges"

        # Shared stop event (mostly for sprint thread)
        self._stop = threading.Event()

    # ── lifecycle ──────────────────────────────────────────────────────
    def stop(self) -> None:
        """Signal in-flight walks / sprints to abort."""
        self._stop.set()

    # ── stroll-path API (restored 2026-06-13) ─────────────────────────
    VALID_STROLL_MODES = ("anywhere", "edges")

    def set_stroll_mode(self, mode: str) -> None:
        """Change stroll path live. Valid: 'anywhere' | 'edges'.

        edges    -> walks always target the visible-frame border (hug edges)
        anywhere -> walks pick polar destinations anywhere in the frame
        """
        if mode not in self.VALID_STROLL_MODES:
            print(f"[squid-pet] set_stroll_mode: invalid {mode!r}", flush=True)
            return
        if mode != self._stroll_mode:
            print(f"[squid-pet] stroll mode: {self._stroll_mode} -> {mode}",
                  flush=True)
            self._stroll_mode = mode

    def get_stroll_mode(self) -> str:
        return self._stroll_mode

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
            print(f"[squid-pet] request_walk: unknown band '{band}'", flush=True)
            return
        if self._sprint_mode:
            return
        t = threading.Thread(target=self._do_request_walk, args=(band,),
                             daemon=True, name=f"squid-walk-{band}")
        t.start()

    def request_look_around(self) -> None:
        """Fire a transient look-around (~1.4s). No-op during sprint or
        while a walk is animating (would stomp sub_state)."""
        if self._sprint_mode:
            return
        t = threading.Thread(target=self._do_look_around, daemon=True,
                             name="squid-look")
        t.start()

    def request_nudge(self, cursor_x: float, cursor_y: float) -> None:
        """Fire-and-forget: hop once, away from (cursor_x, cursor_y).

        Called when the passthrough poll loop's NudgeApproachTracker sees
        2+ rapid re-entries into her clickable bbox within a short window
        -- read as "move, you're in the way" rather than a hover/click/drag
        attempt (those resolve on the first entry and never reach that
        threshold, so they're untouched by this).

        Deliberately does NOT gate on self._get_state() == "idle" like
        request_walk does -- a nudge is a direct physical reaction to being
        bumped, not ambient wandering, so it should fire regardless of her
        current mood/state.

        This is always just the short hop now (2026-08-21) -- escalating
        all the way to a corner is a separate, independent trigger, see
        request_flee_to_corner().

        Preempts any hop/flee animation already in flight (2026-08-21,
        Pink report: rapid continuous nudging produced only tiny net
        movement) -- see _bump_nudge_generation()."""
        if self._sprint_mode:
            return
        my_gen = self._bump_nudge_generation()
        t = threading.Thread(target=self._do_request_nudge,
                             args=(cursor_x, cursor_y, my_gen),
                             daemon=True, name="squid-nudge")
        t.start()

    def request_flee_to_corner(self, cursor_x: float, cursor_y: float) -> None:
        """Fire-and-forget: flee straight to whichever corner of her
        wander range is away from (cursor_x, cursor_y) -- skip the small
        hop entirely.

        Called when the passthrough poll loop's CornerFleeApproachTracker
        sees CORNER_FLEE_THRESHOLD consecutive fresh re-entries into her
        clickable bbox (2026-08-21) -- independent of, and a separate
        trigger from, the short-hop nudge above: a short hop reads fine
        for an occasional bump, but once the cursor keeps landing on her
        over and over, hopping the same fixed short distance each time
        barely gets her out of the way and reads as ignoring the user.

        Deliberately does NOT gate on self._get_state() == "idle", same
        reasoning as request_nudge.

        Preempts any hop/flee animation already in flight, same as
        request_nudge -- see _bump_nudge_generation()."""
        if self._sprint_mode:
            return
        my_gen = self._bump_nudge_generation()
        t = threading.Thread(target=self._do_request_flee_to_corner,
                             args=(cursor_x, cursor_y, my_gen),
                             daemon=True, name="squid-corner-flee")
        t.start()

    def _bump_nudge_generation(self) -> int:
        """Claim the next hop/flee "generation" number and return it.

        request_nudge and request_flee_to_corner both fire straight from
        the passthrough poll thread with NO gating on her current state
        (deliberately -- see their docstrings), and each spawns its own
        daemon thread to animate the hop/flee over several ticks. Nothing
        previously stopped two of these from running concurrently: nudge
        quickly enough (2026-08-21, Pink report) that a new trigger fires
        before the previous hop/flee animation finished, and the two
        threads raced writing the window origin -- each computed its
        target from its OWN stale snapshot of "where she is", so they
        fought each other and the net visible movement was tiny instead
        of one clean escape.

        Every hop/flee animation loop (_animate_hop) checks this counter
        on every step and bails out the instant a NEWER request bumps
        it -- so firing a new nudge/flee always immediately preempts
        whatever hop/flee was previously in flight instead of racing it,
        matching "she should react to wherever the cursor last pushed her
        from, right now" rather than finishing a now-stale reaction."""
        with self._nudge_generation_lock:
            self._nudge_generation += 1
            return self._nudge_generation

    # ── shared x-axis bounds (fix 2026-08-29) ───────────────────────────
    def _x_bounds(self, vx: float, vw: float) -> tuple[float, float]:
        """(min_x, max_x): left/right wander-target bounds.

        Both used to be derived independently at each of 5 call sites
        from EDGE_MARGIN_PX's "overshoot the target past
        window.CHAR_RIGHT_IN_WIN's hard clamp, let the clamp position her
        there" trick -- see EDGE_MARGIN_PX's comment above for the full
        story of how that silently went stale when CHAR_RIGHT_IN_WIN was
        retuned 190->158 and nobody was left to notice the two had
        drifted apart, at 5 duplicated call sites, all at once.

        max_x now imports window.CHAR_RIGHT_IN_WIN and computes the exact
        boundary directly -- the same "reach window.py's ALREADY-TRUSTED
        hard clamp exactly" property EDGE_MARGIN_PX already gives min_x
        for the left side (CHAR_RIGHT_IN_WIN's own tightening history
        shows this value gets revisited by hand periodically; deriving
        from it directly means the NEXT retune only has one place to
        change, not two that have to be remembered together)."""
        min_x = vx + EDGE_MARGIN_PX
        max_x = vx + vw - window.CHAR_RIGHT_IN_WIN
        return min_x, max_x

    # ── walk implementation ────────────────────────────────────────────
    def _do_request_walk(self, band: str) -> None:
        if self._hop_active:
            # A nudge/flee is already animating -- don't start a wander
            # walk that would race its origin writes (see _hop_active's
            # declaration in __init__). The routine will just try again
            # on its next tick; the hop is over in well under a second.
            return
        origin = self._get_origin()
        frame = self._get_frame()
        if origin is None or frame is None:
            return
        ox, oy = origin
        vx, vy, vw, vh = frame
        min_x, max_x = self._x_bounds(vx, vw)
        min_y = vy + BOTTOM_MARGIN_PX
        max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
        if max_x <= min_x or max_y <= min_y:
            return
        tx, ty, edge_hint = self._pick_target_for_band(band, ox, oy,
                                            min_x, max_x, min_y, max_y)
        self._walk_to(ox, oy, tx, ty, vx, vy, vw, vh, edge_hint=edge_hint)

    def _pick_target_for_band(self, band, ox, oy, min_x, max_x, min_y, max_y):
        # Stroll mode override: when locked to "edges", every walk hugs
        # the visible-frame border regardless of band (preserves the
        # pre-unify-idle-rhythm behavior Pink relied on).
        if self._stroll_mode == "edges" or band == "edge":
            return self._pick_edge_destination(ox, oy,
                                               min_x, max_x, min_y, max_y)
        dmin, dmax = BAND_DISTANCES[band]
        # Polar pick — random angle + distance, clamp to visible frame.
        # No edge hint here -- a polar destination isn't chosen against any
        # particular wall, so _rotate_first_preamble falls back to its
        # distance-based classifier for these.
        cand_x = cand_y = None
        for _ in range(12):
            angle = random.uniform(0, 2 * math.pi)
            dist = random.uniform(dmin, dmax)
            cand_x = ox + dist * math.cos(angle)
            cand_y = oy + dist * math.sin(angle)
            if min_x <= cand_x <= max_x and min_y <= cand_y <= max_y:
                return cand_x, cand_y, None
        # Fallback: clamp last candidate
        return (max(min_x, min(max_x, cand_x)),
                max(min_y, min(max_y, cand_y)), None)

    def _walk_to(self, ox, oy, tx, ty, vx, vy, vw, vh, edge_hint=None) -> None:
        """Animate window origin from (ox,oy) → (tx,ty)."""
        dist = ((tx - ox) ** 2 + (ty - oy) ** 2) ** 0.5
        speed = WANDER_SPEED_PX_PER_SEC
        duration = max(0.8, min(WANDER_MAX_DURATION_SEC, dist / speed))
        facing = "left" if tx < ox else "right"
        print(
            f"[squid-pet] walk: ({ox:.0f},{oy:.0f}) → ({tx:.0f},{ty:.0f}) "
            f"dist={dist:.0f}px dur={duration:.2f}s facing={facing}",
            flush=True,
        )

        # Rotate-first if destination is on a different edge
        self._rotate_first_preamble(tx, ty, edge_hint=edge_hint)

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
            if self._hop_active:
                # A nudge/flee started (or was already running through
                # the rotate-first preamble's sleep) -- yield to it
                # immediately rather than racing its origin writes (see
                # _hop_active's declaration in __init__).
                print("[squid-pet] walk aborted: hop in progress", flush=True)
                break
            cur = self._get_state()
            if cur != "idle" and not self._sprint_mode:
                non_idle_streak += 1
                if non_idle_streak >= ABORT_STREAK:
                    print(f"[squid-pet] walk aborted: state={cur}", flush=True)
                    break
            else:
                non_idle_streak = 0
            if self._is_drag_active():
                print("[squid-pet] walk aborted: user dragging", flush=True)
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
        self._edge_locked_for_walk = False
        # Re-sync from the ACTUAL final position now that the lock is
        # off -- covers a walk that got aborted (state change, drag
        # interrupt, stop event) before reaching the pre-rotated target
        # edge's band, so tracking doesn't stay stale until some later
        # unrelated origin-set happens to trigger it. See
        # _settle_edge_at_rest's docstring for why this settles into
        # top/bottom rather than the edge she was hugging in transit.
        self._settle_edge_at_rest()

    def _settle_edge_at_rest(self) -> None:
        """Re-classify the edge from the ACTUAL current position using
        the PLAIN fixed-priority classifier (_compute_edge_at), not the
        sticky one _update_edge uses for per-tick tracking while she's
        actively moving. Called once at the end of every animation loop
        that can end AT a corner (_walk_to and _animate_hop) -- NOT on
        every tick, and not by anything that only nudges the perpendicular
        axis without settling (drag/refresh_edge/force_edge already have
        their own callers for their own reasons and are untouched).

        Pink-2026-08-30 (heart/bubble misalignment report + screenshot):
        at rest exactly on a corner (both adjacent edges tied at distance
        0 -- always true for a walk that reached _pick_edge_destination's
        corner target, and common for a nudge/flee driven into a corner
        too), sticky classification keeps whichever edge she was locked
        to WHILE MOVING (e.g. "left", so she stays hugging the wall for a
        vertical traverse or a sideways flee -- correct, that's what
        edge_hint/the per-tick tracking is FOR) -- but that's the wrong
        choice to SETTLE on: heart/speech-bubble placement (index.html)
        only has a dedicated layout for the "top" pose (bubble moved
        below the flipped face), not "left"/"right", so resting at a top
        corner still classified "left"/"right" leaves those decorations
        floating over her un-rotated top-of-window position, visibly
        misaligned with her actual (rotated) body.

        First fix (this same day) only added this call to _walk_to's
        end -- missed that _animate_hop (nudge/flee) has the exact same
        "can end at rest on a corner" shape and needed the identical
        settle, which is why a corner reached by fleeing a cursor nudge
        specifically kept showing the wrong pose after that first fix
        (Pink follow-up report). Hence the extraction into one shared
        method instead of a second copy-pasted block -- anywhere else
        an animation loop grows the ability to end at a corner, it should
        call this too, not reinvent it.

        The plain classifier's bottom>top>left>right priority resolves a
        genuine corner tie in favor of "top" (or "bottom" at the bottom
        corners, which already matches the default un-rotated decoration
        layout) -- exactly the "hug the wall in transit, settle into the
        decorated pose on arrival" split the report asked for. A
        non-corner (single, unambiguous nearest edge) or off-edge (no
        edge in band) ending is unaffected -- there's no tie for the
        priority to resolve differently than sticky would."""
        final_origin = self._get_origin()
        if final_origin is not None:
            fx, fy = final_origin
            self.force_edge(self._compute_edge_at(fx, fy))

    def _do_look_around(self) -> None:
        """Set looking-around sub_state for ~1.4s, then clear."""
        facing = random.choice(["left", "right"])
        print(f"[squid-pet] look-around-{facing}", flush=True)
        self._set_sub_state(f"looking-around-{facing}")
        end_at = time.time() + LOOK_AROUND_DURATION_SEC
        while time.time() < end_at and not self._stop.is_set():
            if self._get_state() != "idle" or self._is_drag_active():
                break
            time.sleep(0.05)
        self._set_sub_state("")

    def _project_nudge_to_stroll_mode(self, ox, oy, tx, ty, min_x, max_x, min_y, max_y):
        """In "edges" stroll mode, a nudge target must stay ON the edge she's
        currently hugging -- sliding along it, not cutting across open
        middle space (2026-08-19: a nudge that ignored stroll_mode could
        walk her clean off the edge into the screen interior). Forces the
        perpendicular coordinate back to the edge's exact boundary while
        leaving the along-edge coordinate (tx or ty) as computed.

        No-op in "anywhere" mode, and no-op if she isn't currently
        classified as being on any edge (e.g. dragged into open space --
        nothing to stay pinned to).

        Pink-2026-08-29: was self._compute_edge_at(ox, oy) -- a fresh,
        non-sticky recompute that breaks corner ties (e.g. bottom-right,
        where left/right and bottom/top distances both hit 0) with the
        fixed bottom>top>left>right priority, same as the wander-walk bug
        fixed the same day. At a corner she's occupying BOTH tied edges
        simultaneously, so the tracked self._last_edge -- which edge she
        was actually sliding along to GET here -- is the meaningful
        answer, not an arbitrary priority order. Using the sticky
        classifier (self._last_edge as the preferred tie-break, still
        falling back to plain nearest-edge when untracked/not
        applicable) makes this consistent with _update_edge's per-tick
        tracking instead of a second, independently-tie-broken
        classifier potentially disagreeing with it."""
        if self._stroll_mode != "edges":
            return tx, ty
        edge = self._compute_edge_at_sticky(ox, oy, self._last_edge)
        if edge == "bottom":
            return tx, min_y
        if edge == "top":
            return tx, max_y
        if edge == "left":
            return min_x, ty
        if edge == "right":
            return max_x, ty
        return tx, ty

    def _corner_escape_target(self, ox, oy, cursor_x, cursor_y, min_x, max_x, min_y, max_y):
        """"Edges" stroll mode only: where she should head to get away from
        the cursor while staying edge-pinned, given she's at or near a
        corner. Slide along whichever axis still has room (the other axis
        is already pinned at its away-from-cursor boundary), or -- if
        she's genuinely pinned on BOTH axes already (a true corner dead
        end, nowhere left in a straight line) -- turn 90 degrees and flee
        along whichever axis is NOT the dominant push direction, sliding
        to the far end of the edge she's already on.

        Returns an absolute (tx, ty) target. Shared by _flee_to_corner
        (jump straight there) and _do_request_nudge's corner-stuck
        fallback (lerp a short hop toward it) -- both need the exact same
        "which way is actually open" reasoning, just at different scales.

        Root-caused 2026-08-21 (Pink report): the small hop's stuck
        fallback used to ignore all this and just retarget "toward range
        center" projected via _compute_edge_at's fixed bottom>top>
        left>right priority -- at a top-right corner that always resolves
        to "top", so every stuck hop slid LEFT along the top edge
        regardless of which direction she was actually being pushed from.
        The very next push (still from the same side) then had real room
        again since she was no longer exactly cornered, hopping her back
        RIGHT onto the corner -- an endless left-right ping-pong that
        never turned the corner, instead of sliding down the right edge
        like a cornered nudge should.

        Dead-end axis pick (2026-08-21, follow-up): picking whichever
        ADJACENT corner is geometrically farther (raw Euclidean distance
        from the cursor) seemed reasonable but is dominated by the wander
        range's aspect ratio, not by which way she's actually being
        pushed -- in a wide-but-short range, "farther corner" nearly
        always resolves to the one across the WIDE dimension regardless
        of push direction (Pink report: pushed repeatedly straight left
        while cornered top-right, and the dead-end pick sent her to
        top-left, sideways, instead of down the already-open right edge).
        Comparing |dx| vs |dy| instead picks the axis by push direction
        directly: the LARGER of the two is the dominant/blocked push
        axis (that's exactly the one already pinned), so flee along the
        OTHER, smaller one.

        Sticky (2026-08-21, follow-up): real repeated pushes from
        roughly the same side never land at EXACTLY the same cursor
        position each time -- a few pixels of natural jitter flip dx/dy's
        sign push to push. Recomputing fresh on every call meant a
        single noisy push could reverse a bout of stuck hops/flees
        already in progress (verified via a repeated-push simulation:
        without stickiness she wobbled up and down an edge). Reusing the
        same target for STUCK_ESCAPE_STICKY_SEC after it's first picked
        means one noisy call can't undo it -- only a real lull (implying
        a fresh situation) lets this re-decide.

        Deliberately does NOT self-invalidate the moment she's reached
        the cached target (tried that, reverted it): once she's ARRIVED
        at, say, the bottom of a permanently-pinned right edge (cursor
        still pushing from the same side), a same-axis-ambiguous push
        (dy still ~0) re-evaluated fresh reproduces the exact same
        dead-end symmetry that put her there, and the 90-degree turn
        logic above has only ONE other option on a 2-ended edge -- the
        end she just left. Re-deciding on arrival therefore bounced her
        straight back to the original corner on the very next trigger.
        Simply outlasting the sticky window (worst case: one harmless
        no-op flee onto an already-occupied point, if a threshold fires
        again before it expires) is a much smaller cost than that.

        (2026-08-21, second follow-up: also tried gating the whole
        single-axis-pinned/dead-end branches on |dx|/|dy| exceeding a
        deadzone, treating a near-zero secondary axis as "no signal,
        stay put" instead of forcing a direction. Reverted -- real
        continuous left/right pushing produces exactly this near-zero dy
        by nature (confirmed via simulation with realistic +/-3px
        jitter, well under any deadzone that would also filter out
        genuine small pushes), so "no signal" is the COMMON case here,
        not an edge case -- gating on it made her freeze in place
        instead of turning down the edge, reproducing the original
        reported bug. The sticky cache above already handles the
        practically-important case (steady progress during an active
        push); an eventual bounce-back after minutes of uninterrupted
        pushing at a fixed spot is a smaller cost than that regression)."""
        now = time.time()
        if (self._corner_escape_cache is not None
                and now - self._corner_escape_cache_time < STUCK_ESCAPE_STICKY_SEC):
            return self._corner_escape_cache
        win_center_x = ox + WIN_W / 2
        win_center_y = oy + CHAR_TOP_IN_WIN / 2
        dx = win_center_x - cursor_x
        dy = win_center_y - cursor_y
        away_x = max_x if dx >= 0 else min_x
        away_y = max_y if dy >= 0 else min_y
        at_x_boundary = abs(ox - away_x) < NUDGE_STUCK_THRESHOLD_PX
        at_y_boundary = abs(oy - away_y) < NUDGE_STUCK_THRESHOLD_PX
        if at_x_boundary and not at_y_boundary:
            target = (ox, away_y)
        elif at_y_boundary and not at_x_boundary:
            target = (away_x, oy)
        elif at_x_boundary and at_y_boundary:
            if abs(dx) >= abs(dy):
                other_y = min_y if away_y == max_y else max_y
                target = (ox, other_y)
            else:
                other_x = min_x if away_x == max_x else max_x
                target = (other_x, oy)
        else:
            # Not currently pinned to either boundary -- keep her on
            # whichever edge she's nearest to rather than cutting a raw
            # diagonal.
            target = self._project_nudge_to_stroll_mode(
                ox, oy, away_x, away_y, min_x, max_x, min_y, max_y)
        self._corner_escape_cache = target
        self._corner_escape_cache_time = now
        return target

    def _do_request_nudge(self, cursor_x: float, cursor_y: float, my_gen: int = 0) -> None:
        if self._is_drag_active():
            return
        origin = self._get_origin()
        frame = self._get_frame()
        if origin is None or frame is None:
            return
        ox, oy = origin
        vx, vy, vw, vh = frame
        min_x, max_x = self._x_bounds(vx, vw)
        min_y = vy + BOTTOM_MARGIN_PX
        max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
        if max_x <= min_x or max_y <= min_y:
            return

        # Direction away from cursor, in Cocoa screen coords (both cursor
        # and window origin share that space, so a plain vector subtraction
        # is valid -- no coordinate flip needed).
        win_center_x = ox + WIN_W / 2
        win_center_y = oy + CHAR_TOP_IN_WIN / 2
        dx = win_center_x - cursor_x
        dy = win_center_y - cursor_y
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < 1e-3:
            # Cursor sits exactly on her center; pick a random direction.
            angle = random.uniform(0, 2 * math.pi)
            dx, dy = math.cos(angle), math.sin(angle)
            dist = 1.0
        tx = ox + (dx / dist) * NUDGE_HOP_DISTANCE_PX
        ty = oy + (dy / dist) * NUDGE_HOP_DISTANCE_PX
        tx = max(min_x, min(max_x, tx))
        ty = max(min_y, min(max_y, ty))
        tx, ty = self._project_nudge_to_stroll_mode(ox, oy, tx, ty, min_x, max_x, min_y, max_y)

        # Corner fallback: at a corner she's pinned on BOTH axes at once, so
        # if the cursor approaches from the screen interior (the common
        # case), "away from cursor" points further into the corner on both
        # axes and the clamp above cancels it out entirely -- a silent
        # no-op nudge. Detect that (near-zero actual movement, checked AFTER
        # the stroll-mode projection above -- "edges" mode can itself
        # collapse an otherwise-real move to zero) and fall back to a
        # direction that's guaranteed open.
        stuck_dist = ((tx - ox) ** 2 + (ty - oy) ** 2) ** 0.5
        if stuck_dist < NUDGE_STUCK_THRESHOLD_PX:
            if self._stroll_mode == "edges":
                # Use the SAME "which axis actually has room" reasoning as
                # _flee_to_corner (see _corner_escape_target, including
                # its own stickiness against cursor-position jitter) --
                # NOT a generic "toward range center" direction. That
                # used to get re-projected onto whichever edge
                # _compute_edge_at's fixed bottom>top>left>right priority
                # happened to pick (always "top" at a top-right corner,
                # say), sliding her the WRONG way -- left along the top
                # edge -- instead of down the right edge she was
                # actually pinned against.
                target_x, target_y = self._corner_escape_target(
                    ox, oy, cursor_x, cursor_y, min_x, max_x, min_y, max_y)
                fdx, fdy = target_x - ox, target_y - oy
            else:
                # "anywhere" mode has no edge to stay pinned to -- toward
                # the center of her wander range (not literally screen
                # center -- the range itself is already inset by the edge
                # margins/clamps, so its center is always reachable).
                range_cx = (min_x + max_x) / 2
                range_cy = (min_y + max_y) / 2
                fdx = range_cx - ox
                fdy = range_cy - oy
            fdist = (fdx * fdx + fdy * fdy) ** 0.5
            if fdist < 1e-3:
                angle = random.uniform(0, 2 * math.pi)
                fdx, fdy = math.cos(angle), math.sin(angle)
                fdist = 1.0
            tx = ox + (fdx / fdist) * NUDGE_HOP_DISTANCE_PX
            ty = oy + (fdy / fdist) * NUDGE_HOP_DISTANCE_PX
            tx = max(min_x, min(max_x, tx))
            ty = max(min_y, min(max_y, ty))
            print(f"[squid-pet] nudge: stuck in corner, falling back to "
                  f"away-from-corner direction", flush=True)

        print(f"[squid-pet] nudge: ({ox:.0f},{oy:.0f}) -> ({tx:.0f},{ty:.0f}) "
              f"away from cursor ({cursor_x:.0f},{cursor_y:.0f})", flush=True)
        self._animate_hop(ox, oy, tx, ty, my_gen)

    def _do_request_flee_to_corner(self, cursor_x: float, cursor_y: float, my_gen: int = 0) -> None:
        if self._is_drag_active():
            return
        origin = self._get_origin()
        frame = self._get_frame()
        if origin is None or frame is None:
            return
        ox, oy = origin
        vx, vy, vw, vh = frame
        min_x, max_x = self._x_bounds(vx, vw)
        min_y = vy + BOTTOM_MARGIN_PX
        max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
        if max_x <= min_x or max_y <= min_y:
            return
        self._flee_to_corner(ox, oy, cursor_x, cursor_y, min_x, max_x, min_y, max_y, my_gen)

    def _flee_to_corner(self, ox, oy, cursor_x, cursor_y, min_x, max_x, min_y, max_y, my_gen=0) -> None:
        """Skip the small away-from-cursor hop and go straight to
        whichever corner of her wander range is furthest from the cursor.

        Deliberately picks the corner AWAY FROM THE CURSOR, not the corner
        nearest her current position (2026-08-19 bug: with the old
        nearest-to-self pick, a few short 70px hops away from the cursor
        often weren't enough to cross over to the far half of the wander
        range, so "nearest corner" was still on the cursor's side -- she'd
        flee right back toward whoever was nudging her).

        The raw (away_x, away_y) pick moves on BOTH axes at once -- fine in
        "anywhere" stroll mode, but in "edges" mode (the default) she's
        normally already pinned to one edge, and jumping to a corner on a
        DIFFERENT edge reads as an off-edge diagonal cut across open space
        (2026-08-21, Pink report: nudging her twice sent her diagonally
        instead of sliding along the edge she was already on). In "edges"
        mode, constrain movement to whichever axis actually has room:
        an axis where she's already sitting at its away-target boundary
        contributes no movement, and the OTHER axis carries the whole
        move -- a straight slide to the corner at the end of whichever
        edge she can still travel along.

        This is deliberately NOT just "project onto _compute_edge_at's
        single classified edge": AT a corner she's simultaneously pinned
        on both axes, and that classifier only returns ONE edge (fixed
        bottom>top>left>right priority), so it can pick the very edge
        with no room left in the away direction and collapse the whole
        move to a no-op (2026-08-21 report #2: nudged from the right
        while already in the bottom-left corner -- away_x correctly had
        no room, but projecting onto the classifier's "bottom" edge also
        reset the already-correct away_y back to her current y, instead
        of letting her slide up the left edge to the top-left corner).

        Dead-end turn (2026-08-21): if she's already sitting exactly at
        the away corner on BOTH axes, there's no straight-line room left
        at all -- turn 90 degrees instead of freezing in place. Pick
        whichever of the two corners ADJACENT to this one (sharing a
        single edge with it, not the diagonal-opposite) ends up farther
        from the cursor, and slide there. Applies regardless of
        stroll_mode -- "anywhere" mode's away_x/away_y is just as capable
        of landing her back on a corner she's already occupying.

        All of the above (axis-constrained slide, dead-end 90-degree
        turn) lives in _corner_escape_target, shared with the small nudge
        hop's own corner-stuck fallback -- see that method's docstring."""
        if self._stroll_mode == "edges":
            tx, ty = self._corner_escape_target(
                ox, oy, cursor_x, cursor_y, min_x, max_x, min_y, max_y)
        else:
            win_center_x = ox + WIN_W / 2
            win_center_y = oy + CHAR_TOP_IN_WIN / 2
            dx = win_center_x - cursor_x
            dy = win_center_y - cursor_y
            away_x = max_x if dx >= 0 else min_x
            away_y = max_y if dy >= 0 else min_y
            tx, ty = away_x, away_y
            stuck = (abs(tx - ox) < NUDGE_STUCK_THRESHOLD_PX
                     and abs(ty - oy) < NUDGE_STUCK_THRESHOLD_PX)
            if stuck:
                # Same axis-by-push-direction pick as _corner_escape_target
                # (see its docstring) -- flee along whichever axis is NOT
                # the dominant/already-blocked push direction.
                if abs(dx) >= abs(dy):
                    other_y = min_y if away_y == max_y else max_y
                    tx, ty = ox, other_y
                else:
                    other_x = min_x if away_x == max_x else max_x
                    tx, ty = other_x, oy
                print(f"[squid-pet] nudge: corner flee dead-end, turning 90deg "
                      f"to ({tx:.0f},{ty:.0f})", flush=True)

        print(f"[squid-pet] nudge: corner-flee threshold hit, "
              f"fleeing to corner away from cursor ({ox:.0f},{oy:.0f}) -> ({tx:.0f},{ty:.0f})",
              flush=True)
        self._animate_hop(ox, oy, tx, ty, my_gen)

    def _animate_hop(self, ox, oy, tx, ty, my_gen=0) -> None:
        """Step-animate the window origin ox,oy -> tx,ty at nudge speed.

        Tags sub_state "nudge-{facing}", NOT "walking-{facing}": a distinct
        name so the frontend can tell a nudge reaction apart from ambient
        wander and exempt it from the state=="idle"-only sub_state gate
        (window.PetApi.get_state) and the drowsy/sleeping/stretch mood
        suppression (applySubState in index.html) that ambient wander
        sub-states are deliberately subject to. See both sites for the
        2026-08-19 fix. Shared by the plain cursor-avoidance hop and the
        corner-flee escalation -- both are the same kind of flinch, just
        with a different target.

        my_gen: this call's hop/flee generation number (see
        _bump_nudge_generation). Checked on every step -- if a newer
        request has since bumped the counter, this animation is stale
        and bails immediately instead of continuing to fight a newer one
        for the window origin (2026-08-21, Pink report: rapid continuous
        nudging produced only tiny net movement because overlapping
        un-synchronized hop/flee threads were racing each other)."""
        facing = "left" if tx < ox else "right"
        self._set_sub_state(f"nudge-{facing}")
        dist_actual = ((tx - ox) ** 2 + (ty - oy) ** 2) ** 0.5
        duration = max(NUDGE_MIN_DURATION_SEC, dist_actual / NUDGE_SPEED_PX_PER_SEC)
        steps = max(4, int(duration * WANDER_TICK_HZ))
        # Pink-2026-08-30: fixed per-tick interval, NOT the wall-clock
        # "catch up to a target_t" scheme _walk_to still uses. That
        # scheme skips the sleep entirely on any tick that runs behind
        # schedule (sleep_for <= 0) -- and window.set_window_origin
        # dispatches via AppHelper.callAfter, which is FIRE-AND-FORGET
        # (queues the actual NSWindow move on the main thread's run loop
        # and returns immediately, no confirmation it landed). Combine
        # the two: if this loop ever falls even slightly behind (GIL
        # contention, a slow tick, system load), several consecutive
        # set_origin calls fire back-to-back with ZERO real gap between
        # them, each just queuing another callAfter -- so several
        # intended-to-be-smooth intermediate positions land on the main
        # thread in one burst and get applied almost simultaneously
        # instead of one-per-frame. That reads as exactly "losing
        # frames"/choppy motion, and explains why raising the STEP COUNT
        # alone (2 earlier attempts, NUDGE_MIN_DURATION_SEC 0.25->0.35->
        # 0.45) had no effect: more steps queued into the same bursty
        # delivery just means more items competing to burst, not smoother
        # motion. A plain fixed `time.sleep(tick_interval)` after every
        # tick can never go negative or skip -- guarantees real wall-
        # clock space between every queued move so the main thread's
        # callAfter queue has a chance to actually drain one at a time.
        # Total animation time can run slightly over `duration` if a
        # tick's own work is slow, which is an acceptable tradeoff (a
        # hop finishing 10-20ms later is imperceptible; visibly bursty
        # motion is not).
        tick_interval = 1.0 / WANDER_TICK_HZ
        # Claim the "a hop is animating" flag for the whole loop (see its
        # declaration in __init__) so a concurrently-running wander walk
        # notices and yields instead of racing this loop's origin writes.
        # try/finally covers every exit path, including the early
        # `return` below.
        self._hop_active = True
        try:
            for i in range(steps + 1):
                if self._stop.is_set() or self._is_drag_active():
                    break
                if self._nudge_generation != my_gen:
                    return  # superseded by a newer nudge/flee -- don't clear its sub_state either
                t = i / steps
                e = _ease_in_out(t)
                cx = ox + (tx - ox) * e
                cy = oy + (ty - oy) * e
                self._set_origin(cx, cy)
                time.sleep(tick_interval)
        finally:
            self._hop_active = False
        if self._nudge_generation == my_gen:
            self._set_sub_state("")
            # See _settle_edge_at_rest's docstring (Pink-2026-08-30
            # follow-up report) -- a hop/flee that ends AT a corner (the
            # common case for a corner-flee specifically) needs the same
            # settle-into-top/bottom treatment _walk_to already gets,
            # or she keeps showing whichever wall she was hugging while
            # fleeing instead of the decorated corner pose. Skipped when
            # superseded (my_gen mismatch) for the same reason
            # _set_sub_state("") is skipped above: a newer hop/flee is
            # already in flight and will settle for itself when IT ends.
            self._settle_edge_at_rest()

    # ── edge picking (used for band="edge") ────────────────────────────
    def _pick_edge_destination(self, ox, oy, min_x, max_x, min_y, max_y):
        """Pick a destination that hugs the screen edge.
        Strategy:
          1) If not near any edge, head straight to the nearest one.
          2) Otherwise walk along the current edge to a corner.

        Returns (tx, ty, edge) -- `edge` is the wall this specific walk
        slides along, decided HERE from the same branch that picked
        (tx, ty), and handed to _rotate_first_preamble as an authoritative
        hint instead of being re-derived later.

        Pink-2026-08-29 (recurring report, "walking up the left/right
        edge still shows feet-up"): every corner is a geometric tie
        between two edges (e.g. top-left ties left(0) and top(0)), so
        re-deriving the edge from (tx, ty) alone after the fact -- as the
        old code did via _compute_edge_at_preferring in
        _rotate_first_preamble -- can only resolve that tie by guessing
        (fixed bottom>top>left>right priority, or "prefer whatever edge
        she happened to be tracked as before this walk"). Both guesses
        fail whenever she arrives at a corner via one wall (e.g. tracked
        edge = "bottom") and then picks a walk that slides along a
        DIFFERENT wall to the opposite end of THAT wall (e.g. up the left
        edge to the top-left corner): the destination ties left/top, her
        incoming tracked edge is "bottom" (matches neither side of the
        tie), so the priority fallback picks "top" (feet-up) for the
        entire vertical traverse instead of "left" (feet hugging the
        wall) -- exactly the reported symptom, and not the same case the
        2026-08-27i same-edge-tie fix covered (that fix only helps when
        the tracked edge already equals one side of the tie).
        There is no ambiguity to resolve at the source: this function
        already knows definitively which wall (tx, ty) slides along --
        it just picked it. Passing that through removes the guess
        entirely instead of refining it further.
        """
        d_left = max(0, ox - min_x)
        d_right = max(0, max_x - ox)
        d_bottom = max(0, oy - min_y)
        d_top = max(0, max_y - oy)
        # Priority: bottom(0) > top(1) > left(2) > right(3).
        edges = [("bottom", d_bottom, 0), ("top", d_top, 1),
                 ("left", d_left, 2),     ("right", d_right, 3)]
        edges.sort(key=lambda x: (x[1], x[2]))
        nearest_edge, nearest_dist, _ = edges[0]

        # Off-edge -> walk straight to nearest edge.
        if nearest_dist > EDGE_BAND_PX:
            if nearest_edge == "left":
                return min_x, oy, "left"
            if nearest_edge == "right":
                return max_x, oy, "right"
            if nearest_edge == "bottom":
                return ox, min_y, "bottom"
            return ox, max_y, "top"

        # On an edge -- if within EDGE_BAND_PX of TWO edges (at a corner),
        # randomly pick which adjacent edge to walk along next. This breaks
        # the lock where the priority tiebreak (bottom>left>right>top) would
        # otherwise trap Squid on whichever edge owns each corner. Without
        # this, the right edge sticks (top-right <-> bottom-right ping-pong)
        # because "right" always loses to "bottom"/"top" -- but at top-right
        # there is no "bottom" in band so "right" wins and locks her in.
        nearby = [e for e in edges if e[1] <= CORNER_BAND_PX]
        chosen_edge = random.choice(nearby)[0] if nearby else nearest_edge

        # Walk toward one of the two corners of the chosen edge.
        direction_to_corner = random.choice([-1, 1])
        if chosen_edge == "left":
            return min_x, (min_y if direction_to_corner < 0 else max_y), "left"
        if chosen_edge == "right":
            return max_x, (min_y if direction_to_corner < 0 else max_y), "right"
        if chosen_edge == "bottom":
            return (min_x if direction_to_corner < 0 else max_x), min_y, "bottom"
        return (min_x if direction_to_corner < 0 else max_x), max_y, "top"

    # ── edge tracking (for frontend sprite rotation) ───────────────────
    # Priority: bottom(0) > top(1) > left(2) > right(3), used as a tiebreak
    # when two edges are equidistant. Top must beat left/right so she
    # flips upside-down at top corners (otherwise left/right always win
    # the tiebreak and she never rotates 180deg).
    _EDGE_PRIORITY = {"bottom": 0, "top": 1, "left": 2, "right": 3}

    def _edge_distances(self, x: float, y: float) -> dict[str, float] | None:
        """Raw distance from (x,y) to each of the 4 wander-range edges,
        or None if the visible frame isn't available yet. Shared by
        _compute_edge_at and its sticky variant below."""
        frame = self._get_frame()
        if frame is None:
            return None
        vx, vy, vw, vh = frame
        min_x, max_x = self._x_bounds(vx, vw)
        min_y = vy + BOTTOM_MARGIN_PX
        max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
        return {
            "left": max(0, x - min_x),
            "right": max(0, max_x - x),
            "bottom": max(0, y - min_y),
            "top": max(0, max_y - y),
        }

    def _nearest_edge(self, distances: dict[str, float]) -> str:
        name = min(distances, key=lambda n: (distances[n], self._EDGE_PRIORITY[n]))
        return name if distances[name] <= EDGE_BAND_PX else ""

    def _compute_edge_at(self, x: float, y: float) -> str:
        """Edge that (x,y) sits on with bottom>top>left>right priority."""
        d = self._edge_distances(x, y)
        return "" if d is None else self._nearest_edge(d)

    def _compute_edge_at_sticky(self, x: float, y: float, prev_edge: str) -> str:
        """Same classification as _compute_edge_at, but sticky: stays on
        prev_edge as long as (x,y) is still within EDGE_BAND_PX of it,
        even if a different edge is now nominally nearer.

        Without this, a walk or hop/flee passing near a corner -- where
        two edges' bands legitimately overlap -- flips the classification
        (and the sprite's rotation) back and forth multiple times as the
        raw nearest-edge comparison see-saws between two close distances
        on every ~33ms motion tick. Confirmed live (user report,
        2026-08-27): "flips and turns multiple times... not able to
        determine which direction it should be going". Only switches
        once she's genuinely LEFT the previous edge's band -- a one-shot,
        monotonic handoff instead of a per-tick nearest-wins comparison.
        """
        d = self._edge_distances(x, y)
        if d is None:
            return ""
        if prev_edge and d.get(prev_edge, float("inf")) <= EDGE_BAND_PX:
            return prev_edge
        return self._nearest_edge(d)

    def _compute_edge_at_preferring(self, x: float, y: float, prefer_edge: str) -> str:
        """Same classification as _compute_edge_at, but when (x,y) is a
        genuine TIE between two edges (a corner), prefers `prefer_edge`
        over the fixed bottom>top>left>right priority.

        _pick_edge_destination's "walk along the current edge to ITS OWN
        corner" targets the corner point exactly -- e.g. walking down the
        left edge targets (min_x, min_y), where left's distance (0) ties
        exactly with bottom's (0). _compute_edge_at's fixed priority then
        picks "bottom" (deg=0, feet down) or, for the top-left corner,
        "top" (deg=180, feet up) instead of "left" (deg=90, feet hugging
        the wall) -- and because _rotate_first_preamble locks that
        decision for the whole walk, the entire vertical traverse plays
        with the wrong rotation: feet-down walking down, feet-up walking
        up, instead of staying rotated into the wall the whole way
        (Pink report, 2026-08-27). Preferring the edge she's already on
        resolves the tie correctly for a same-edge corner walk, while a
        genuine edge-to-edge transition (destination clearly nearer a
        different edge, not tied) is untouched -- prefer_edge only wins
        when it's within a hair of the true minimum distance."""
        d = self._edge_distances(x, y)
        if d is None:
            return ""
        if prefer_edge and d.get(prefer_edge, float("inf")) <= EDGE_BAND_PX:
            if d[prefer_edge] <= min(d.values()) + 0.5:
                return prefer_edge
        return self._nearest_edge(d)

    def _update_edge(self, ox: float, oy: float) -> None:
        """Edge tracker — notify frontend on transitions.

        Pink-2026-08-27j: "flipping when she starts the idle walk". Root
        cause: _rotate_first_preamble decides the edge for an upcoming
        walk and pre-rotates to it BEFORE she's actually moved -- but
        _walk_to's very first step still lands essentially at the OLD
        position (t≈0), and even the STICKY position-based check above
        can legitimately fail there (the old position isn't necessarily
        within EDGE_BAND_PX of the NEW target edge, e.g. walking to a
        distant, previously off-edge destination). That reclassified her
        straight back to the old edge one tick after the preamble just
        rotated her to the new one -- then forward again once she
        actually got close enough -- a visible flip right at the start
        of the walk. While _edge_locked_for_walk is set (during an
        in-flight walk that just pre-rotated), trust that decision
        instead of re-deriving from raw position tick by tick; normal
        position-based tracking resumes once the walk ends (drag, hop,
        flee, and refresh_edge/force_edge are untouched -- none of them
        set this lock)."""
        if self._edge_locked_for_walk:
            return
        try:
            edge = self._compute_edge_at_sticky(ox, oy, self._last_edge)
            if edge != self._last_edge:
                self._last_edge = edge
                self._set_edge(edge)
                print(f"[squid-pet] edge -> {edge or '(none)'}", flush=True)
        except Exception as e:
            print(f"[squid-pet] _update_edge error: {e}", flush=True)

    def refresh_edge(self) -> str:
        """Public: re-compute current edge from live window origin and notify
        frontend. Used after drag-end -- an arbitrary landing position, so it
        genuinely needs the distance-based classifier (_compute_edge_at).

        Pink-2026-08-27d: do NOT use this for corner-snap paths (menu
        snap, next_corner, startup) -- window.move_to_corner's own
        margin was never coordinated with this classifier's tighter
        margins, so a corner-snapped window reliably lands just outside
        EDGE_BAND_PX and comes back "" (no edge), leaving the sprite
        un-rotated. Corner-snap callers already know their edge
        authoritatively (see window._edge_for_corner) and should call
        force_edge() instead."""
        try:
            origin = self._get_origin()
            if origin is None:
                return self._last_edge
            ox, oy = origin
            self._update_edge(ox, oy)
            return self._last_edge
        except Exception as e:
            print(f"[squid-pet] refresh_edge error: {e}", flush=True)
            return self._last_edge

    def force_edge(self, edge: str) -> str:
        """Public: set the edge classification directly from a caller-
        supplied, authoritative value (e.g. window._edge_for_corner(name))
        instead of inferring it from live window-origin distance. See
        refresh_edge()'s docstring for why corner-snap callers need this
        rather than the distance-based path. Mirrors _update_edge's
        change-detection (only notifies frontend + logs on an actual
        transition) so it composes safely with organic wander ticks that
        may run before or after it. Returns the edge now in effect."""
        edge = edge or ""
        try:
            if edge != self._last_edge:
                self._last_edge = edge
                self._set_edge(edge)
                print(f"[squid-pet] edge -> {edge or '(none)'} (forced)", flush=True)
        except Exception as e:
            print(f"[squid-pet] force_edge error: {e}", flush=True)
        return self._last_edge

    def _rotate_first_preamble(self, tx: float, ty: float, edge_hint: str | None = None) -> None:
        """Pre-rotate wrapper if destination is on a different edge, then sleep
        for the rotation transition. Prevents the 'rotating mid-walk' look.

        edge_hint: when the caller already knows, with certainty, which
        wall (tx, ty) sits on -- _pick_edge_destination always does, since
        it picked (tx, ty) FOR that wall -- use it directly instead of
        re-deriving the edge from (tx, ty) alone. See
        _pick_edge_destination's docstring (Pink-2026-08-29) for why the
        re-derivation is fundamentally a guess at any corner (a genuine
        tie between two edges) while the hint is not."""
        try:
            origin = self._get_origin()
            if origin is None:
                return
            # Pink-2026-08-27h: was self._compute_edge_at(origin) -- a
            # fresh, non-sticky recompute that could disagree with the
            # tracked self._last_edge (e.g. near a corner, where the raw
            # nearest-edge comparison can differ from what she's actually
            # been classified as). Using the tracked value keeps this
            # decision consistent with what _update_edge will do during
            # the walk itself, instead of two independent classifiers
            # potentially fighting over "what edge is she on right now".
            current_edge = self._last_edge
            if edge_hint:
                target_edge = edge_hint
            else:
                # No authoritative hint (polar/off-edge-band walk that
                # happens to land near a wall) -- fall back to the
                # distance-based guess. Pink-2026-08-27i: _compute_edge_at
                # (tx, ty) alone breaks corner ties with the fixed
                # bottom>top>left>right priority, which mis-picks
                # "bottom"/"top" over "left"/"right" when the destination
                # IS that edge's own corner (see
                # _compute_edge_at_preferring's docstring). Preferring the
                # edge she's currently on resolves same-edge corner walks
                # correctly without affecting genuine edge-to-edge
                # transitions.
                target_edge = self._compute_edge_at_preferring(tx, ty, current_edge)
            if target_edge and target_edge != current_edge:
                self._last_edge = target_edge
                self._edge_locked_for_walk = True
                self._set_edge(target_edge)
                print(f"[squid-pet]   rotate-first: "
                      f"{current_edge or '(none)'} -> {target_edge}",
                      flush=True)
                time.sleep(ROTATION_PREAMBLE_SEC)
        except Exception as e:
            print(f"[squid-pet] rotate-first error: {e}", flush=True)

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
            print(f"[squid-pet] trigger_wake err: {e}", flush=True)

    def _set_sprint_fast_transition(self, on: bool) -> None:
        try: self._set_sprint_fast_transition_cb(bool(on))
        except Exception as e:
            print(f"[squid-pet] sprint_fast_transition err: {e}", flush=True)

    def _set_wrapper_deg(self, deg: float) -> None:
        try: self._set_wrapper_deg_cb(float(deg))
        except Exception as e:
            print(f"[squid-pet] set_wrapper_deg err: {e}", flush=True)

    def _clear_wrapper_deg(self) -> None:
        try: self._clear_wrapper_deg_cb()
        except Exception as e:
            print(f"[squid-pet] clear_wrapper_deg err: {e}", flush=True)

    def sprint_perimeter(self) -> None:
        """Funny one-shot: sprint through all 4 corners CW from nearest.

        No-op if a sprint is already in flight -- the check-and-set is
        atomic under _sprint_lock and happens here, synchronously,
        before the thread spawns (not inside the thread after its
        wake-up sleep), so two rapid double-clicks of the menu item
        can't both pass the check and race on window origin /
        wrapper-rotation state.
        """
        with self._sprint_lock:
            if self._sprint_mode:
                print("[squid-pet] SPRINT: already in flight, ignoring", flush=True)
                return
            self._sprint_mode = True
        threading.Thread(target=self._do_sprint_perimeter,
                         daemon=True, name="squid-sprint").start()

    def _do_sprint_perimeter(self) -> None:
        # _sprint_mode was already set True by sprint_perimeter() before
        # this thread started. This single try/finally is now the ONE
        # place it gets cleared, covering every exit path (early return
        # on missing origin/frame, normal completion, and exceptions)
        # -- previously the flag was set deep inside this function
        # (after the wake-wait sleep) and the early-return path never
        # reset it, which would have left it stuck True forever once
        # sprint_perimeter() started eagerly setting it up front.
        try:
            # Wake from drowsy/sleeping first
            self._trigger_wake()
            print(f"[squid-pet] SPRINT: wake-up wait ({SPRINT_WAKE_WAIT_SEC}s)",
                  flush=True)
            time.sleep(SPRINT_WAKE_WAIT_SEC)

            origin = self._get_origin()
            frame = self._get_frame()
            if origin is None or frame is None:
                return
            vx, vy, vw, vh = frame
            min_x, max_x = self._x_bounds(vx, vw)
            min_y = vy + EDGE_MARGIN_PX
            max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
            corners = [
                (min_x, min_y),  # 0 BL  -> 0deg
                (min_x, max_y),  # 1 TL  -> 90deg
                (max_x, max_y),  # 2 TR  -> 180deg
                (max_x, min_y),  # 3 BR  -> 270deg
            ]
            ox, oy = origin
            dists = [((c[0]-ox)**2 + (c[1]-oy)**2) ** 0.5 for c in corners]
            start_idx = dists.index(min(dists))
            print(f"[squid-pet] SPRINT v3: corner #{start_idx} "
                  f"from ({ox:.0f},{oy:.0f})", flush=True)

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
                print(f"[squid-pet]   leg {leg_i+1}/4 turn -> deg {target_deg}",
                      flush=True)
                time.sleep(SPRINT_ROTATION_TRANSITION_SEC + 0.10)
                print(f"[squid-pet]   leg {leg_i+1}/4 walk -> "
                      f"({tx:.0f},{ty:.0f}) facing={facing}", flush=True)
                self._sprint_walk_leg(prev[0], prev[1], tx, ty, facing)
                prev = (tx, ty)
                cur_deg = target_deg

            self._set_sub_state("")
            self._clear_wrapper_deg()
            self._set_sprint_fast_transition(False)
            # Sprint drives rotation directly via wrapper_deg the whole
            # time and never touches _last_edge/_wander_edge. Normal
            # completion always lands back on the start corner (4 legs
            # of 90deg = 360deg), so the stale edge happens to still be
            # correct -- but an early stop (self._stop.is_set() break)
            # leaves her at an intermediate corner with a now-wrong
            # edge, and clear_wrapper_deg() makes the frontend fall
            # back to that stale value immediately. Same class of bug
            # as the missing startup refresh -- always resync from the
            # real, live window position instead of trusting old state.
            self.refresh_edge()
            print("[squid-pet] SPRINT complete", flush=True)
        except Exception as e:
            print(f"[squid-pet] sprint error: {e}", flush=True)
            self._set_sub_state("")
            try: self._clear_wrapper_deg()
            except Exception: pass
            try: self._set_sprint_fast_transition(False)
            except Exception: pass
            try: self.refresh_edge()
            except Exception: pass
        finally:
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
