## 1. user-interactions capability (frontend)

- [x] 1.1 Poke detection in `frontend/index.html` mouseup handler
       (dt < POKE_MAX_MS=250ms, dist < POKE_MAX_PX=6px, e.button === 0)
- [x] 1.2 260ms setTimeout deferral for dblclick disambiguation
       (window._pokePending, cleared by dblclick handler)
- [x] 1.3 "boop!" hint pill on poke
- [x] 1.4 Dblclick handler cancels pending poke

## 2. user-interactions capability (backend)

- [x] 2.1 `PetApi.poke()` sets `_user_wake_until = now + 60s`,
       bumps `_wake_trigger_seq`, fires "boop!" hint, clears `_forced_state`
- [x] 2.2 `PetApi.debug_log(msg)` JS-exposed sink for frontend telemetry
       (kept for future debugging)
- [x] 2.3 Right-click global NSEvent monitor invokes menu (bypasses WKWebView)
- [x] 2.4 Menu items: Sprint perimeter, Pause/Resume wander, Quit
- [x] 2.5 Swing-to-wake detection in `_native_drag_loop`
       (4 reversals in 0.6s window, >=8px deltas)
- [x] 2.6 Swing fires same wake path as poke + "wheee!" hint

## 3. autonomous-motion capability (wanderer.py)

- [x] 3.1 `WanderController` with 800ms tick, threaded
- [x] 3.2 Stroll modes: STROLL_MODE_ANYWHERE, STROLL_MODE_EDGES
- [x] 3.3 `set_stroll_mode()` and `get_stroll_mode()` for live mode flip
- [x] 3.4 `_pick_edge_destination` with EDGE_BAND_PX, EDGE_HOP_PROBABILITY
- [x] 3.5 Edge-hop targets CORNER of current edge (no diagonal cuts)
- [x] 3.6 `sprint_perimeter()` walks CW around perimeter using cumulative degrees
- [x] 3.7 Sprint includes pre-stretch transition + 80ms poll interval
- [x] 3.8 Sprint ends at bottom edge (snap-to-bottom)
- [x] 3.9 `is_busy` callback gates wander loop
       (active when TPA thinking/working OR TPA running + idle <30s)
- [x] 3.10 Drowsy entry: idle >120s in idle state, plays slump animation

## 4. pet-window capability (multi-Space + singleton + CLI)

- [x] 4.1 Multi-Space: `NSWindow.setCollectionBehavior_(273)` via
       AppHelper.callAfter from on_loaded
- [x] 4.2 Atomic singleton: `fcntl.flock(fd, LOCK_EX | LOCK_NB)`
       on `~/.indigo-pet/lock` in `__main__.py`
- [x] 4.3 atexit handler releases lock; SIGKILL also releases via kernel cleanup
- [x] 4.4 CLI: `~/.local/bin/squid` (with `indigo` symlink for backwards compat)
- [x] 4.5 CLI subcommands: start, stop, restart, status, why, logs
- [x] 4.6 CLI `_launch_with_retry` retries up to 3x for WKWebView flakiness
- [x] 4.7 CLI `_force_kill` loops up to 6x until process truly dead
- [x] 4.8 CLI status: parent-child-aware dup detection
       (walks ppid chain, only counts root indigo_pet processes)

## 5. state-detection capability (drowsy + wake override)

- [x] 5.1 8th state `drowsy` added to state model
- [x] 5.2 Drowsy entry condition: agent_idle_seconds > 120 AND current state idle
- [x] 5.3 Frontend `checkDrowsyState()` runs on each poll
- [x] 5.4 `_user_wake_until: float` field on PetApi
- [x] 5.5 `get_state` returns `user_wake_remaining` (seconds, clamped >=0)
- [x] 5.6 `_wake_trigger_seq: int` incremented on every wake event
- [x] 5.7 Frontend `checkDrowsyState` early-exits when userAwake
- [x] 5.8 Frontend `wakeUpWithStretch()` plays stretch animation, then idle sprite

## 6. Documentation

- [x] 6.1 Memory file `pink-pm/indigo-pet.md` updated with all behavioral
       decisions and lessons learned
- [x] 6.2 Kennel `repo:decisions` drawer for is_busy gate semantics + WKWebView quirk
- [x] 6.3 This OpenSpec change authored as canonical record
- [ ] 6.4 Sync this change's spec deltas into `openspec/specs/` after archive
