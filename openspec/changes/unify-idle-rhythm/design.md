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
    def __init__(self, wanderer, is_busy: Callable[[], bool],
                 is_drowsy: Callable[[], bool]):
        self.wanderer = wanderer
        self.is_busy = is_busy
        self.is_drowsy = is_drowsy
        self._idx = 0
        self._stop = threading.Event()

    def start(self): ...   # spawn daemon thread

    def stop(self): ...

    def _loop(self):
        while not self._stop.is_set():
            if self.is_busy() or self.is_drowsy():
                time.sleep(1.0)
                continue
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
