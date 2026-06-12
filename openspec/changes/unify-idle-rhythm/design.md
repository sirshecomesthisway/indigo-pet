# Design: unify-idle-rhythm

## Architecture

```
BEFORE                              AFTER
------                              -----
+----------+    +-----------+       +--------------------+
|  Pulse   |    | Wanderer  |       | RoutineController  |
| (fixed   |    | (RNG      |       | (deterministic     |
|  cadence)|    |  schedule)|       |  IDLE_ROUTINE)     |
+----+-----+    +-----+-----+       +---+----------------+
     |                |                 |
     |  fires         |  fires          |  request_walk(band)
     | "look-around"  | "walk to (x,y)" |  request_look_around()
     v                v                 v
   frontend         frontend          +--------------------+
   (animation       (window           | Wanderer (service) |
    conflict        position          |  - request_walk    |
    risk)           via Cocoa)        |  - request_look... |
                                      |  - sprint_perimeter|
                                      +---+----------------+
                                          |
                                          v
                                        frontend + window
```

## Modules

### NEW: `routine.py` (~120 lines)

```python
class RoutineController:
    MOODS_THAT_PAUSE = {"drowsy", "sleeping", "stretch"}

    def __init__(self, wanderer, is_busy: Callable[[], bool],
                 get_mood: Callable[[], str]):
        self.wanderer = wanderer
        self.is_busy = is_busy
        self.get_mood = get_mood             # returns "" / "drowsy" / "sleeping" / "stretch"
        self._idx = 0
        self._stop = threading.Event()
        self._wake_from_sleeping_pending = False

    def start(self): ...   # spawn daemon thread

    def stop(self): ...

    def notify_mood_entered(self, mood: str):
        '''Hook called by watcher when frontend mood transitions.'''
        if mood == "sleeping":
            self._wake_from_sleeping_pending = True

    def _is_mood_active(self) -> bool:
        return self.get_mood() in self.MOODS_THAT_PAUSE

    def _loop(self):
        while not self._stop.is_set():
            if self.is_busy() or self._is_mood_active():
                time.sleep(1.0)
                continue
            # Just resumed — if we passed through sleeping, restart cycle
            if self._wake_from_sleeping_pending:
                self._idx = 0
                self._wake_from_sleeping_pending = False
            action, lo, hi = IDLE_ROUTINE[self._idx]
            dur = random.uniform(lo, hi)
            self._fire(action)
            self._sleep_interruptible(dur)
            self._idx = (self._idx + 1) % len(IDLE_ROUTINE)

    def _fire(self, action):
        if action == "rest":
            return                       # do nothing
        if action == "look-around":
            self.wanderer.request_look_around()
        elif action.startswith("walk-"):
            band = action.split("-", 1)[1]   # "short" / "medium" / "edge"
            self.wanderer.request_walk(band)
```

### MODIFIED: `wanderer.py` (~50 lines net delta)

Remove the autonomous tick loop and all `random.*` scheduling calls.
Keep position-picking and animation logic but expose them as methods:

```python
class WanderController:
    # REMOVED: __init__ thread, _tick_loop, all internal RNG schedulers
    # REMOVED: get_stroll_mode/set_stroll_mode (routine picks band, not mode)

    def request_walk(self, distance_band: str):
        target = self._pick_target_for_band(distance_band)
        self._animate_to(target, duration_ms=self._duration_for_band(distance_band))

    def request_look_around(self):
        self._fire_frontend("look-around")

    def sprint_perimeter(self):  # unchanged, used by menu
        ...
```

### REMOVED: `pulse.py`

Functionality fully subsumed by `RoutineController`. Delete the file and
its bootstrap in `__main__.py`.

### MODIFIED: `__main__.py`

Replace pulse + wanderer init with:
```python
wanderer = WanderController(window, ...)   # no longer starts thread
routine = RoutineController(wanderer, is_busy=watcher.is_busy,
                            is_drowsy=watcher.is_drowsy)
routine.start()
```

## Decisions

### D1. Use circular index with jittered durations (not random.choice)
**Choice:** Deterministic ordering, random durations within bands.
**Alternative:** weighted-random action selection (BUDDY-style).
**Why:** Determinism is debuggable — you can predict what comes next from
the current index. Behavior variety comes from the SEQUENCE structure plus
jittered timing, which is enough for "feels alive" without RNG fingerprints
in logs.

### D2. Keep `is_busy()` gate at routine level, not wanderer level
**Choice:** Routine checks busy/drowsy before firing the next action;
wanderer service is "dumb" and always honors requests immediately.
**Why:** Separation of concerns — wanderer becomes a primitive that the
routine can drive. Future: a different controller (e.g., reactive
explorer) could reuse the wanderer service.

### D3. Rest action does nothing (not a frontend ping)
**Choice:** `rest` action just sleeps; no message to frontend.
**Alternative:** send a "settling" cue so frontend can play subtle
breath/blink animation.
**Why:** Frontend already runs an idle-breath loop autonomously while in
`idle` state. Sending explicit `rest` events would just duplicate that.

### D4. Drop `stroll_mode` (anywhere vs edges-only)
**Choice:** Routine specifies `walk-short`/`walk-medium`/`walk-edge`
explicitly; no global mode flag.
**Why:** Mode flag was a coarse-grained knob; the routine sequence is the
right place to encode "now walk to an edge". The menu's "Pause/Resume
wander" still works via `routine.stop()`/`start()`. Removing
`set_stroll_mode` is a breaking API change — but the only caller is the
menu, which is updated as part of this change.

### D5. NOT changing sprint-perimeter, drowsy entry, or wake override
These behaviors remain in their current homes (wanderer.sprint_perimeter,
state-detection drowsy logic, user-interactions wake override). They are
orthogonal to the idle rhythm.

### D6. Pause on drowsy AND sleeping AND stretch, not just drowsy
**Choice:** Single `_is_mood_active()` predicate returns True for any
non-empty mood. Routine pauses for all three.
**Why:** Drowsy and sleeping share the "don't fidget" intent; stretch is
a transition animation that must complete before any other action fires.
Lumping them simplifies the gate and matches existing frontend behavior
where mood layer takes over the sprite during all three.

### D7. Reset index on wake-from-sleeping only (not wake-from-drowsy)
**Choice:** `_wake_from_sleeping_pending` flag set when mood enters
`sleeping`; consumed by next post-pause dispatch to reset `_idx = 0`.
Wake-from-drowsy preserves the saved index.
**Why:** Maps to natural pet behavior. Drowsy (2-5 min nap) feels like
"she stirred and continued"; sleeping (5+ min) feels like "she just
woke up and is reorienting". Starting at `rest` (action 0) after a long
sleep gives a calm beat (rest → look-around → walk) that matches the
visual cue of just having stretched.

### D8. Use frontend mood as source of truth, not backend agent_idle timer
**Choice:** Routine reads mood via `get_mood()` callback that observes
the frontend's `_mood` value. No backend `PAUSE_WHEN_CP_IDLE_SEC` timer.
**Why:** Fixes the existing 60-120s "stationary but awake" bug
(`pulse.py:33` and `wanderer.py:51` use 60s; frontend uses 120s for
drowsy). Frontend mood is already the authoritative state for visual
behavior; making motion track it eliminates the inconsistency.

## Risks

| Risk | Mitigation |
|---|---|
| Routine feels too predictable after watching a full cycle (~110-130s) | Tune jitter bands; add ~2 alternate routine sequences and switch nightly via date-mod |
| Removing stroll_mode breaks anything I forgot | Grep for `set_stroll_mode`/`stroll_mode` — confirmed only 2 callers (menu + frontend status query) |
| Walk-edge action needs different pick logic than walk-short/-medium | Wanderer's `_pick_edge_destination` already exists from previous work; just rewire |

## Out of scope

- TPA-log-tailing tier-1 observer (separate future change)
- Speech bubbles / chat track (separate future change)
- Public release prep (separate future change)
