## 1. Add RoutineController

- [ ] 1.1 Create `indigo_pet/routine.py` with `RoutineController` class
- [ ] 1.2 Define `IDLE_ROUTINE` constant (8 entries: rest, look, walk-short, rest, walk-medium, look, rest, walk-edge)
- [ ] 1.3 Implement `_loop` with circular index, jittered duration per action
- [ ] 1.4 Implement `start()`, `stop()`, `pause()`, `resume()` methods (daemon thread)
- [ ] 1.5 Implement `_fire(action)` dispatcher (rest→noop, look→wanderer.request_look_around, walk-*→wanderer.request_walk(band))
- [ ] 1.6 Implement `_sleep_interruptible(dur)` that wakes on stop event

## 2. Refactor wanderer.py to service mode

- [ ] 2.1 Delete `_tick_loop` and the thread it spawned
- [ ] 2.2 Delete all internal RNG scheduling (`random.uniform` for next_tick, `random.choice` for action picks)
- [ ] 2.3 Expose `request_walk(distance_band: str)` — accepts "short", "medium", "edge"
- [ ] 2.4 Expose `request_look_around()` — fires frontend look-around mini-anim
- [ ] 2.5 Add `_pick_target_for_band(band)` — short=local cluster, medium=anywhere, edge=use existing edge picker
- [ ] 2.6 Add `_duration_for_band(band)` mapping band → ms (short ~1500, medium ~3000, edge ~4500)
- [ ] 2.7 Keep `sprint_perimeter` unchanged (still invoked by menu)
- [ ] 2.8 Remove `get_stroll_mode`/`set_stroll_mode` (no callers after this change)

## 3. Delete pulse.py

- [ ] 3.1 Remove `indigo_pet/pulse.py`
- [ ] 3.2 Remove pulse import + bootstrap from `__main__.py`

## 4. Wire RoutineController into __main__

- [ ] 4.1 Construct `WanderController` (no longer starts thread)
- [ ] 4.2 Construct `RoutineController(wanderer, is_busy=watcher.is_busy, is_drowsy=...)`
- [ ] 4.3 Call `routine.start()` after window loads
- [ ] 4.4 atexit: `routine.stop()` before window close

## 5. Update menu

- [ ] 5.1 "Pause/Resume wander" menu item now toggles `routine.pause()/resume()` instead of `wanderer.stop()/start()`
- [ ] 5.2 Menu label change: "Pause wander" → "Pause Squid"

## 6. Validation (manual, run app)

- [ ] 6.1 Start Squid → first cycle plays rest→look→walk-short→rest→walk-medium→look→rest→walk-edge over ~110-130s
- [ ] 6.2 Trigger TPA busy (run a prompt) → routine pauses mid-cycle, Squid stops moving
- [ ] 6.3 TPA goes idle for 120s → routine continues but drowsy state suppresses motion
- [ ] 6.4 Poke during drowsy → wake override fires, routine resumes from current index
- [ ] 6.5 Menu Pause → cycle frozen; Menu Resume → continues from same index
- [ ] 6.6 No animation overlap: never see "look-around" fire while a walk is in-flight
- [ ] 6.7 Sprint-perimeter menu still works (routine pauses, sprint runs, routine resumes)

## 7. Spec sync

- [ ] 7.1 Validate change: `openspec validate unify-idle-rhythm`
- [ ] 7.2 Archive: `openspec archive unify-idle-rhythm` → syncs delta to canonical autonomous-motion
