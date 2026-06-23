# Proposal: Unify idle rhythm under a single routine controller

## Why

Squid currently has TWO competing autonomous-motion subsystems:

1. **`pulse.py`** — fixed-cadence heartbeat, fires `MICRO_PULSE` items every
   ~22s. But `MICRO_PULSE = ["look-around", "look-around", "look-around",
   "look-around"]` is a stub: rhythm exists in CADENCE only, not in
   BEHAVIOR variety.
2. **`wanderer.py`** — independent RNG scheduler: `random.uniform` and
   `random.choice` everywhere (lines 394, 404, 428, 546) to pick next
   wander destination, idle pause duration, edge-hop probability, etc.

The two systems do not coordinate. Pulse can fire `look-around` while the
wanderer is mid-walk, producing animation overlap. The wanderer's RNG
scheduling means consecutive wander steps can fire too close together
(jitter feels manic) or too far apart (Squid feels dead). Pink endorsed
the rhythmic-routine concept in the project memory under "future roadmap
- BUDDY patterns to steal".

## What changes

Introduce `routine.py` with a `RoutineController` that owns the idle
rhythm. It defines a deterministic-but-jittered sequence of actions:

```python
IDLE_ROUTINE = [
    ("rest",          15.0, 18.0),
    ("look-around",    1.5,  2.5),
    ("walk-short",     6.0, 10.0),
    ("rest",           8.0, 12.0),
    ("walk-medium",   12.0, 18.0),
    ("look-around",    1.0,  2.0),
    ("rest",          20.0, 30.0),
    ("walk-edge",     10.0, 16.0),
]
```

Each tuple is `(action_name, min_duration_s, max_duration_s)`. The
controller iterates the list circularly; each tick picks a uniform random
duration within the band, fires the action, sleeps the duration, advances
to the next item.

The **wanderer** is demoted to a stateless service exposing:
- `request_walk(distance_band: str)` — distance_band in `{"short", "medium", "edge"}`
- `request_look_around()` — fire the look-around mini-animation

No internal scheduling, no RNG. Routine calls in. Pulse goes away (its
role is subsumed by routine).

`is_busy()` gate semantics are preserved: when busy, the routine pauses
and Squid stays still.

**Mood gate (drowsy / sleeping / stretch):** the routine ALSO pauses
when the frontend mood is anything other than plain idle. Specifically:
- `drowsy` (agent_idle >= 120s) → routine pauses, saved index preserved
- `sleeping` (agent_idle >= 300s) → routine pauses, saved index preserved
- `stretch` (transient wake animation, ~1.6s) → routine pauses

On resume from `drowsy`, the routine continues at the saved index
(picking up where it left off). On resume from `sleeping`, the routine
RESETS to index 0 so Squid begins a fresh cycle (mirrors waking from a
real nap vs a deep sleep). The stretch transition is always a buffer
that finishes before any routine action fires.

Sprint-perimeter is a one-off invocation from the menu, not the routine,
so it is unchanged. Drowsy and sleeping mood-state thresholds live in
`state-detection` and remain unchanged in behavior.

**Bug fix bundled into this change:** the current code has two
inconsistent TPA-idle thresholds for pausing motion — `pulse.py` and
`wanderer.py` both use `PAUSE_WHEN_CP_IDLE_SEC = 60.0`, but the frontend
doesn't enter `drowsy` until 120s. This produces a 60-120s window where
Squid is stationary but still looks awake. The new routine controller
SHALL pause based on frontend mood (the source of truth) rather than a
separate backend timer, eliminating the gap.

## Impact

Affects the canonical `autonomous-motion` capability:
- MODIFY the "Wanderer thread moves the window without user input"
  requirement to describe the routine-driven model
- ADD a new requirement: "Routine controller drives idle rhythm via
  deterministic sequence"
- Existing requirements for stroll modes, sprint-perimeter, busy-gate,
  drowsy entry stay AS-IS (semantics preserved, implementation moves
  into the wanderer-as-service or routine).

## Auto-wake from sleeping (added 2026-06-23)

Pink's request: Squid should never look permanently dead. After 10
minutes asleep, the watcher SHALL force one wake cycle (~3 minutes) so
the routine fires and Squid moves a little. Then she naturally returns
to sleeping for another 10 minutes if the user is still away. Net: a
roughly 13-minute sleep-wake cycle instead of an hours-long static
sleeping sprite.

This is the "dumb" version: the wake fires regardless of whether the
user is actually at the Mac. Trade-off accepted: occasional wasted
animation during true AFK, in exchange for guaranteed liveliness during
long focus sessions where Pink is working but not generating HID events
the watcher can see.

Implementation lives in `state-detection` capability (watcher.py); the
routine controller's existing wake-from-sleeping reset logic (sets
`_idx = 0` on transition) handles the rhythm restart automatically when
mood flips from `sleeping` back to `""`.

