## 1. Project scaffolding

- [x] 1.1 Create `pyproject.toml` with `pywebview`, `psutil`, `Pillow` dependencies (managed by uv)
- [x] 1.2 Create `src/indigo_pet/` package with `__init__.py` and `__main__.py`
- [x] 1.3 Set up uv-managed `.venv` and lockfile

## 2. State detection (`watcher.py`)

- [x] 2.1 Define `PetState` dataclass with the 7 fields
- [x] 2.2 Implement `macos_idle_seconds()` via `ioreg -c IOHIDSystem`
- [x] 2.3 Implement `find_tpa_processes()` with cmdline filter
- [x] 2.4 Implement `aggregate_cpu()` (non-blocking, since-last-call)
- [x] 2.5 Implement `newest_file_age_in_dir()` helper
- [x] 2.6 Implement `StateMachine.compute()` priority cascade (8 branches)
- [x] 2.7 Implement `was_busy` + `celebrate_until` memory for celebrating
- [x] 2.8 Implement atomic `write_state()` via `tmp.replace`
- [x] 2.9 Wire watcher into `window.py` as a daemon thread

## 3. Pet window (`window.py`)

- [x] 3.1 Create pywebview window (frameless, transparent, on_top, 200×220)
- [x] 3.2 Implement `_get_ns_window()` PyObjC helper
- [x] 3.3 Implement `_visible_frame()` via `NSScreen.visibleFrame`
- [x] 3.4 Implement `corner_origin()` math (top-right / bottom-right / bottom-left / top-left)
- [x] 3.5 Implement `move_to_corner()` via `NSWindow.setFrameOrigin_`
- [x] 3.6 Implement `move_window_by_delta()` (Cocoa Y-flip)
- [x] 3.7 Persist corner to `~/.indigo-pet/position.json`
- [x] 3.8 Implement `PetApi` with `get_state` / `next_corner` / `move_window_by` / `force_state` / `clear_force` / `drag_start` / `drag_end` / `quit`
- [x] 3.9 Hook `window.events.loaded` to snap to saved corner
- [x] 3.10 Hook `window.events.closing` to stop daemon threads

## 4. Click passthrough (`passthrough.py`)

- [x] 4.1 Implement `load_alpha_masks()` (PIL split alpha, resize to 180×180)
- [x] 4.2 Implement `PassthroughController` with `set_state` / `pause` / `resume` / `start` / `stop`
- [x] 4.3 Implement `_apply_ignore()` with idempotent check
- [x] 4.4 Implement `_loop()` polling `NSEvent.mouseLocation()` at 30 ms
- [x] 4.5 Map cursor → window-local → sprite-local with Y flip
- [x] 4.6 Read mask alpha and toggle `setIgnoresMouseEvents_`
- [x] 4.7 Wire `PassthroughController` into `window.py` `on_loaded`
- [x] 4.8 Notify controller from `PetApi.update` and `PetApi.force_state`
- [x] 4.9 Pause on `drag_start`, resume on `drag_end`

## 5. Frontend (`frontend/index.html`)

- [x] 5.1 Single `<img id="pet">` element with absolute centering
- [x] 5.2 Seven CSS `@keyframes` (one per state) tied to `[data-state="…"]`
- [x] 5.3 150 ms opacity cross-fade between sprites
- [x] 5.4 800 ms `setInterval` polling `api.get_state()`
- [x] 5.5 JS `mousedown` / `mousemove` / `mouseup` drag → `api.move_window_by`
- [x] 5.6 `contextmenu` → `api.next_corner`
- [x] 5.7 `dblclick` → cycle states and call `api.force_state`
- [x] 5.8 `Escape` → `api.clear_force`
- [x] 5.9 Hint toast on startup + Ctrl+D debug overlay

## 6. Sprites (`frontend/sprites/`)

- [x] 6.1 Source artwork for all 7 states (pink chibi octopus, ~180×180 effective)
- [x] 6.2 Flood-fill background-removal pass; back up originals to `_originals_with_bg/`
- [x] 6.3 Verify alpha=0 on corner pixels for every sprite

## 7. Documentation & ops

- [x] 7.1 OpenSpec init + foundational spec (this change)
- [x] 7.2 README with install + run instructions (refreshed -- new architecture diagram with routine/wanderer/passthrough/menu, indigo CLI section, LaunchAgent section, tests section, sprite tooling section, OpenSpec footer)
- [x] 7.3 LaunchAgent plist for auto-start on login (committed at launchagent/com.pink.indigo-pet.plist + idempotent install.sh with install/status/remove subcommands; plist already loaded and running pid 67206 since 2026-06-12)
- [x] 7.4 tools/remove_bg.py recreated and checked in (was deleted from /tmp during cleanup; flood-fill from 4 corners with RGB tolerance; CLI supports --tolerance, --backup-to, --recursive, --verify; round-trip tested on backed-up original)
- [x] 7.5 Unit tests at tests/test_state_machine.py -- 24 tests, ~0.1s; covers all 9 priority branches, busy_streak burst-suppression (3 tests), agent_idle_seconds tracking (3 tests), celebration window (2 tests), concerned hard-vs-transient (3 tests), PetState dataclass shape; pytest added as dev dependency in pyproject.toml [dependency-groups.dev]; [tool.pytest.ini_options] configured with pythonpath=[src]
