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
and Squid stays still. Drowsy entry, sprint-perimeter, and stroll-modes
remain unchanged in behavior (sprint-perimeter is a one-off invocation
from the menu, not the routine; drowsy entry is in state-detection).

## Impact

Affects the canonical `autonomous-motion` capability:
- MODIFY the "Wanderer thread moves the window without user input"
  requirement to describe the routine-driven model
- ADD a new requirement: "Routine controller drives idle rhythm via
  deterministic sequence"
- Existing requirements for stroll modes, sprint-perimeter, busy-gate,
  drowsy entry stay AS-IS (semantics preserved, implementation moves
  into the wanderer-as-service or routine).
