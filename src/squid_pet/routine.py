"""
RoutineController: Squid's unified idle rhythm.

REPLACES the separate `pulse.py` (fixed-cadence heartbeat) + `wanderer.py`
internal RNG scheduler. Both are subsumed into ONE deterministic-but-
jittered sequence (`IDLE_ROUTINE`) that owns the entire idle behavior.

Architecture (see openspec/changes/2026-06-12-...unify-idle-rhythm):

    RoutineController
        │
        │   loop:                            # daemon thread, 1Hz check
        │     if busy/mood-active: sleep 1s, continue
        │     if just woke from sleeping: _idx = 0, clear flag
        │     action, lo, hi = IDLE_ROUTINE[_idx]
        │     dur = uniform(lo, hi)
        │     fire(action)                   # rest=noop / look / walk-{band}
        │     sleep_interruptible(dur)
        │     _idx = (_idx + 1) % len(IDLE_ROUTINE)
        │
        ├──> wanderer.request_walk("short"/"medium"/"edge")    # stateless service
        └──> wanderer.request_look_around()

Gate predicate `_is_mood_active()` returns True when frontend mood is
`drowsy` / `sleeping` / `stretch`. Routine pauses on any of those.

Wake-from-sleeping is special: resets _idx = 0 so Squid begins a fresh
cycle starting with `rest` (matches real-pet behavior after a long nap).
Wake-from-drowsy preserves the saved index (she stirred and continued).
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, Optional


# ── Tunables ────────────────────────────────────────────────────────────
IDLE_BEFORE_ROUTINE_SEC = 6.0    # don't start cycling immediately after wake
MOOD_POLL_INTERVAL_SEC = 1.0     # how often we re-check the gate while paused


# ── The heartbeat ───────────────────────────────────────────────────────
# Each tuple: (action_name, min_duration_s, max_duration_s).
# Iterated circularly; each tick samples a uniform duration in the band.
# Total cycle length ≈ 73-108s, averaging ~91s.
#
# 2026-08-17: briefly stretched every phase ~2.2x (to a ~200s average
# cycle) to make "the routine feel too short" less noticeable, but that
# just elongated the gap between actions -- individual walks/rests felt
# sluggish, which is a worse experience than a snappier, more-frequently-
# repeating cycle. Reverted to the original pacing here; instead pushed
# DROWSY_IDLE_SEC out to 300s (frontend/index.html) so the *same* short
# cycle gets to repeat 2-3+ times before she winds down, rather than
# making each individual cycle longer.
#
# Action vocabulary:
#   "rest"          : do nothing for the duration (Squid stays still;
#                     frontend's autonomous idle-breath loop is enough)
#   "look-around"   : fire wanderer.request_look_around() (head turn anim)
#   "walk-short"    : ~60-180px hop to a nearby cluster
#   "walk-medium"   : ~120-280px traverse, anywhere in visible frame
#   "walk-edge"     : walk to a screen-edge point (uses existing picker)
IDLE_ROUTINE: list[tuple[str, float, float]] = [
    ("rest",         15.0, 18.0),
    ("look-around",   1.5,  2.5),
    ("walk-short",    6.0, 10.0),
    ("rest",          8.0, 12.0),
    ("walk-medium",  12.0, 18.0),
    ("look-around",   1.0,  2.0),
    ("rest",         44.0, 66.0),
    ("walk-edge",    22.0, 35.0),
]


class RoutineController:
    """Drives the idle rhythm. Pause-aware (busy + mood gates)."""

    # Frontend moods that suspend the routine entirely.
    MOODS_THAT_PAUSE = frozenset({"drowsy", "sleeping", "stretch"})

    def __init__(
        self,
        wanderer,                                        # WanderController (service mode)
        get_state: Callable[[], str],                    # "idle" / "thinking" / etc.
        is_drag_active: Callable[[], bool],
        is_busy: Callable[[], bool],                     # TPA is actively working
        get_mood: Callable[[], str],                     # "" / drowsy / sleeping / stretch
        is_pinned: Callable[[], bool] = None,            #  from menu
        is_user_paused: Callable[[], bool] = None,       # menu pause-N-min
    ):
        self.wanderer = wanderer
        self._get_state = get_state
        self._is_drag_active = is_drag_active
        self._is_busy = is_busy
        self._get_mood = get_mood
        self._is_pinned = is_pinned or (lambda: False)
        self._is_user_paused = is_user_paused or (lambda: False)

        self._stop = threading.Event()
        self._enabled = True
        self._idx = 0
        self._wake_from_sleeping_pending = False
        self._prev_mood = ""
        self._idle_since: Optional[float] = None
        self._action_done_at: Optional[float] = None  # when current action's duration window ends

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True,
                             name="squid-routine")
        t.start()
        print("[squid-pet] routine thread started", flush=True)

    def stop(self) -> None:
        self._stop.set()

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    # ── public diagnostics ─────────────────────────────────────────────
    def get_status(self) -> dict:
        """For menu / debug display."""
        action = IDLE_ROUTINE[self._idx][0] if 0 <= self._idx < len(IDLE_ROUTINE) else "?"
        return {
            "enabled": self._enabled,
            "idx": self._idx,
            "current_action": action,
            "mood": self._get_mood(),
            "wake_from_sleeping_pending": self._wake_from_sleeping_pending,
        }

    # ── gate predicates ────────────────────────────────────────────────
    def _is_mood_active(self) -> bool:
        return self._get_mood() in self.MOODS_THAT_PAUSE

    def _should_pause(self) -> bool:
        """Aggregate gate. True = skip dispatch, just poll."""
        if not self._enabled:
            return True
        if self._is_pinned():
            return True
        if self._is_user_paused():
            return True
        if self._is_busy():
            return True
        if self._is_mood_active():
            return True
        if self._get_state() != "idle":
            return True
        if self._is_drag_active():
            return True
        return False

    # ── main loop ──────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[squid-pet] routine error: {e}", flush=True)
            # Outer cadence: short sleep, then check whether to dispatch.
            self._stop.wait(MOOD_POLL_INTERVAL_SEC)

    def _tick(self) -> None:
        # Mood-transition observer: catch sleeping entry even if we're
        # currently mid-action (the next pause/resume cycle consumes the flag).
        mood = self._get_mood()
        if mood == "sleeping" and self._prev_mood != "sleeping":
            self._wake_from_sleeping_pending = True
            print("[squid-pet] routine: noted sleeping entry, "
                  "will reset on wake", flush=True)
        self._prev_mood = mood

        # Gate check: if anything says "pause", do nothing this tick.
        # CRITICAL: do NOT reset _action_done_at -- if we're mid-action
        # window, a brief pause (state=thinking on hover) must not wipe
        # progress, or _idx never advances past 0. Only reset _idle_since
        # so the 6s breath plays again when state returns to idle.
        if self._should_pause():
            self._idle_since = None
            return

        # If we're inside an action's duration window, just wait.
        now = time.time()
        if self._action_done_at is not None and now < self._action_done_at:
            return

        # Just exited a wait window → advance.
        if self._action_done_at is not None and now >= self._action_done_at:
            self._idx = (self._idx + 1) % len(IDLE_ROUTINE)
            self._action_done_at = None

        # Idle ramp: don't fire the very first action immediately after
        # state becomes idle. Gives a tiny breath before Squid moves.
        if self._idle_since is None:
            self._idle_since = now
        if now - self._idle_since < IDLE_BEFORE_ROUTINE_SEC:
            return

        # Consume the wake-from-sleeping reset, if any.
        if self._wake_from_sleeping_pending:
            self._idx = 0
            self._wake_from_sleeping_pending = False
            print("[squid-pet] routine: woke from sleeping → reset _idx=0",
                  flush=True)

        # Fire current action.
        action, lo, hi = IDLE_ROUTINE[self._idx]
        dur = random.uniform(lo, hi)
        print(f"[squid-pet] routine[{self._idx}]: {action} ({dur:.1f}s)",
              flush=True)
        self._fire(action)
        self._action_done_at = time.time() + dur

    def _fire(self, action: str) -> None:
        """Dispatch one action to the wanderer service (or no-op for rest)."""
        if action == "rest":
            return  # frontend idle-breath loop handles "alive" feel
        if action == "look-around":
            self.wanderer.request_look_around()
            return
        if action.startswith("walk-"):
            band = action.split("-", 1)[1]  # "short" / "medium" / "edge"
            self.wanderer.request_walk(band)
            return
        print(f"[squid-pet] routine: unknown action '{action}' (skipped)",
              flush=True)
