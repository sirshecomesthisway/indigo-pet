"""
Indigo's micro-pulse: tiny in-place idle motions that keep her feeling alive
even when the wanderer is gated (pinned, or actively-working-and-still).

Inspired by Claude Code's BUDDY pet IDLE_SEQUENCE pattern -- rhythm beats
randomness. The pulse runs on a fixed-cadence schedule, NOT random:

    every PULSE_INTERVAL_SEC: pick the next action from MICRO_PULSE,
                              run it, advance the cursor.

The pulse ONLY uses substates that don't move the window. It never walks.
The wanderer still owns roaming. The pulse just makes her breathe:

  - "look-around-{facing}"  -- already a supported substate; head turn

Coexistence rule: if the wanderer is currently animating (sub_state already
set to walking/looking by the wander loop), the pulse skips this beat and
waits for the next one. Never fights the wanderer for the sub_state slot.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, Optional


# Cadence. Pink can tune these.
PULSE_INTERVAL_SEC = 22.0             # one pulse beat every ~22s
PULSE_JITTER_SEC = 4.0                # +/- jitter per beat so it's not metronomic
LOOK_AROUND_DURATION_SEC = 1.4        # how long the look lasts (matches wanderer)
IDLE_BEFORE_PULSE_SEC = 6.0           # don't pulse during the first few idle seconds
PAUSE_WHEN_CP_IDLE_SEC = 60.0         # pause pulses when drowsy/sleeping (mood territory)

# Fixed routine -- the heartbeat. Loops indefinitely.
# Currently all "look-around"; trivially extensible to blink / yawn / stretch
# once the frontend has matching substates. The point is the RHYTHM, not the
# action variety. Pink will start to recognize: "she always glances around
# about 20s after I pause."
MICRO_PULSE: list[str] = [
    "look-around",
    "look-around",
    "look-around",
    "look-around",
]


class PulseController:
    """Background thread that fires in-place idle motions on a fixed cadence.

    Runs INDEPENDENT of the wanderer's busy/pin gating. Only gates on:
      - state must be 'idle' (don't fidget mid-thinking/working/celebrating)
      - user not actively dragging
      - wanderer not currently animating (don't stomp its sub_state)
    """

    def __init__(
        self,
        get_state: Callable[[], str],
        is_drag_active: Callable[[], bool],
        get_wander_sub_state: Callable[[], str],
        set_sub_state: Callable[[str], None],
        get_agent_idle_seconds: Callable[[], float] = None,  # for mood-based pause
    ):
        self._get_state = get_state
        self._is_drag_active = is_drag_active
        self._get_wander_sub_state = get_wander_sub_state
        self._set_sub_state = set_sub_state
        self._get_agent_idle = get_agent_idle_seconds or (lambda: 0.0)

        self._stop = threading.Event()
        self._enabled = True
        self._cursor = 0
        self._idle_since: Optional[float] = None
        self._next_pulse_at: Optional[float] = None

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True, name="indigo-pulse")
        t.start()
        print("[indigo-pet] pulse thread started", flush=True)

    def stop(self) -> None:
        self._stop.set()

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[indigo-pet] pulse error: {e}", flush=True)
            time.sleep(0.5)

    def _tick(self) -> None:
        if not self._enabled:
            return

        # Mood pause: drowsy/sleeping pets don't fidget. Skip pulses past
        # the drowsy threshold. The wanderer already has the same gate.
        if self._get_agent_idle() >= PAUSE_WHEN_CP_IDLE_SEC:
            self._idle_since = None
            self._next_pulse_at = None
            return

        state = self._get_state()
        now = time.time()

        # Reset the idle clock whenever she's not idle.
        if state != "idle" or self._is_drag_active():
            self._idle_since = None
            self._next_pulse_at = None
            return

        if self._idle_since is None:
            self._idle_since = now

        if now - self._idle_since < IDLE_BEFORE_PULSE_SEC:
            return

        if self._next_pulse_at is None:
            jitter = random.uniform(-PULSE_JITTER_SEC, PULSE_JITTER_SEC)
            self._next_pulse_at = now + PULSE_INTERVAL_SEC + jitter

        if now < self._next_pulse_at:
            return

        # Coexistence: if the wanderer is currently animating, skip this
        # beat (but reschedule so we try again in another ~22s).
        cur_sub = self._get_wander_sub_state()
        if cur_sub:
            self._next_pulse_at = now + PULSE_INTERVAL_SEC
            return

        # Time to pulse. Advance cursor, run the next action.
        action = MICRO_PULSE[self._cursor % len(MICRO_PULSE)]
        self._cursor += 1
        self._do_action(action)

        # Schedule the next beat.
        jitter = random.uniform(-PULSE_JITTER_SEC, PULSE_JITTER_SEC)
        self._next_pulse_at = time.time() + PULSE_INTERVAL_SEC + jitter

    def _do_action(self, action: str) -> None:
        """Run a single micro-action. Aborts cleanly on state change."""
        if action == "look-around":
            facing = random.choice(["left", "right"])
            print(f"[indigo-pet] pulse: look-around-{facing}", flush=True)
            self._set_sub_state(f"looking-around-{facing}")
            end_at = time.time() + LOOK_AROUND_DURATION_SEC
            while time.time() < end_at and not self._stop.is_set():
                if self._get_state() != "idle" or self._is_drag_active():
                    break
                time.sleep(0.05)
            self._set_sub_state("")
        # Future actions land here: "blink", "yawn", "stretch", "head-tilt"
        # Each just sets a sub_state for a duration and clears it.
