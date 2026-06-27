## 1. Add RoutineController

- [x] 1.1 Create `squid_pet/routine.py` with `RoutineController` class
- [x] 1.2 Define `IDLE_ROUTINE` constant (8 entries: rest, look, walk-short, rest, walk-medium, look, rest, walk-edge)
- [x] 1.3 Implement `_loop` with circular index, jittered duration per action
- [x] 1.4 Implement `start()`, `stop()`, `pause()`, `resume()` methods (daemon thread)
- [x] 1.5 Implement `_fire(action)` dispatcher (rest→noop, look→wanderer.request_look_around, walk-*→wanderer.request_walk(band))
- [x] 1.6 Implement `_sleep_interruptible(dur)` that wakes on stop event
- [x] 1.7 Implement mood gate: `_is_mood_active()` returns True for drowsy/sleeping/stretch; expose `notify_mood_entered(mood)` hook that sets `_wake_from_sleeping_pending = True` on sleeping; `_loop` consumes flag to reset `_idx = 0` on first dispatch after gate clears

## 2. Refactor wanderer.py to service mode

- [x] 2.1 Delete `_tick_loop` and the thread it spawned
- [x] 2.2 Delete all internal RNG scheduling (`random.uniform` for next_tick, `random.choice` for action picks)
- [x] 2.3 Expose `request_walk(distance_band: str)` — accepts "short", "medium", "edge"
- [x] 2.4 Expose `request_look_around()` — fires frontend look-around mini-anim
- [x] 2.5 Add `_pick_target_for_band(band)` — short=local cluster, medium=anywhere, edge=use existing edge picker
- [x] 2.6 Add `_duration_for_band(band)` mapping band → ms (short ~1500, medium ~3000, edge ~4500)
- [x] 2.7 Keep `sprint_perimeter` unchanged (still invoked by menu)
- [x] 2.8 Remove `get_stroll_mode`/`set_stroll_mode` (no callers after this change)
- [x] 2.9 Remove `PAUSE_WHEN_CP_IDLE_SEC = 60.0` from wanderer.py (no longer needed; routine uses frontend mood as gate). Also remove from pulse.py before deleting that file.

## 3. Delete pulse.py

- [x] 3.1 Remove `squid_pet/pulse.py`
- [x] 3.2 Remove pulse import + bootstrap from `__main__.py` (no pulse refs remain in `__main__.py`)

## 4. Wire RoutineController into __main__

- [x] 4.1 Construct `WanderController` (no longer starts thread) — wired in `window.on_loaded`
- [x] 4.2 Construct `RoutineController(wanderer, is_busy=lambda:False, is_drowsy via get_mood, ...)`
- [x] 4.3 Call `routine.start()` after window loads — verified via "routine thread started" log
- [x] 4.4 atexit: `routine.stop()` before window close — wired in `on_closing()`
- [x] 4.5 Wire frontend mood → routine: `PetApi.notify_mood(mood)` exposed on JS bridge; frontend `enterDrowsy()` / `enterSleeping()` / `wakeUpWithStretch` all call `notifyMoodToBackend(...)`; `RoutineController` reads via `api.get_frontend_mood`

## 5. Update menu

- [x] 5.1 Existing "Pause/Resume" submenu retained — pauses the whole pet via `_wander_paused_until` (RoutineController honors `is_pinned` predicate which OR's the pause-until window)
- [x] 5.2 Menu label change: "Pause wandering" → "Pause Squid" (verified in menu.py L150)

## 6. Validation

- [x] 6.1 Routine cycle verified via logs (2026-06-13 15:09): `routine[0]: rest (17.9s)` → `routine[1]: look-around (1.8s)` → `routine[2]: walk-short (6.2s)` → `routine[3]: rest (10.1s)` — order matches IDLE_ROUTINE
- [x] 6.2 Routine pauses on `state != "idle"` — confirmed via gate predicate (`_should_pause` returns True when state is "thinking" / "working"); is_busy=lambda:False per 2026-06-08 Pink decision (she roams during TPA work too)
- [x] 6.3 TPA idle 120s → drowsy mood suppresses routine — VERIFIED 2026-06-27 via live logs (mood notify (awake) -> drowsy fires, no `edge ->` walk events during the drowsy window)
- [x] 6.4 Poke during drowsy → wake override fires, routine resumes from current index — VERIFIED 2026-06-27 via live logs: `poke -> 60s awake override + observer bubble` immediately followed by `drowsy -> stretch -> (awake)` mood chain, then `edge -> X` walk events resumed
- [ ] 6.5 Menu Pause → cycle frozen; Menu Resume → continues from same index — MANUAL UI TEST
- [x] 6.6 No animation overlap: routine fires one action per cycle, waits the full duration window via `_action_done_at` before advancing — verified by serial action log
- [ ] 6.7 Sprint-perimeter menu still works — selector + handler intact in menu.py L54+161; MANUAL UI TEST to confirm routine pauses cleanly during sprint
- [x] 6.8 Drowsy nap test (120s) — VERIFIED 2026-06-27 via live logs (multiple (awake)->drowsy transitions today, routine ticks paused during drowsy as expected)
- [x] 6.9 Full sleep test (180s+ after 2026-06-25 threshold change) — VERIFIED 2026-06-27 via live logs: full chain (awake) -> drowsy -> sleeping -> stretch -> (awake) cycled repeatedly, `_wake_from_sleeping_pending` reset path exercised end-to-end
- [x] 6.10 No "stationary but awake" gap — confirmed: `PAUSE_WHEN_CP_IDLE_SEC` removed from wanderer; routine fires whenever state=idle regardless of TPA-busy

**Bug found + fixed during validation (2026-06-13):** initial implementation reset `_action_done_at = None` on every pause-tick, which caused `_idx` to stay at 0 forever because frequent `state=thinking` flips wiped the wait window. Fixed in `routine.py:_tick` — pause-gate now only resets `_idle_since`, preserving the action progress window. Verified by post-fix log showing full `[0]→[1]→[2]→[3]` cycle.

## 7. Spec sync

- [x] 7.1 Validate change: `openspec validate unify-idle-rhythm` — passes
- [ ] 7.2 Archive: `openspec archive unify-idle-rhythm` → syncs delta to canonical autonomous-motion (pending Pink go-ahead after she eyeball-verifies 6.3-6.5, 6.7-6.9 in real use)


## 8. Auto-wake from sleeping (dumb-wake, added 2026-06-23 per Pink)

Goal: Squid should never look dead. After 10 minutes asleep, force one
~3 minute wake cycle so the routine fires and she does some movement,
then naturally re-enter sleeping for another 10 min. Cycle = ~13 min.

- [x] 8.1 Add `AUTO_WAKE_AFTER_SLEEPING_SEC = 600` + `AUTO_WAKE_DURATION_SEC = 180` constants to `watcher.py`
- [x] 8.2 Add `_sleeping_since` + `_force_awake_until` instance vars to `StateMachine.__init__`
- [x] 8.3 Modify the sleeping branch in `_compute_inner` to suppress sleeping during the wake window
- [x] 8.4 Clear both flags when user returns (idle drops below threshold)
- [x] 8.5 Unit test: auto-wake opens window after 10 min sleeping
- [x] 8.6 Unit test: sleeping returns after wake window expires
- [x] 8.7 Unit test: user return clears bookkeeping
- [x] 8.8 All 27 state_machine tests pass; full suite 126 green
- [ ] 8.9 MANUAL: leave Mac idle for 13 minutes -- observe Squid sleep -> auto-wake -> rhythm cycle -> back to sleep
