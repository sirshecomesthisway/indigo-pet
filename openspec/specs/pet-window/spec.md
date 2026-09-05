# pet-window Specification

## Purpose
Define how Squid is presented as a desktop companion window: a frameless,
transparent, always-on-top pywebview window with a stable JS/Python bridge.
Covers window creation, positioning, drag-without-Accessibility, the JS API
surface, multi-Space persistence, singleton enforcement, and the external
CLI for launch/control.

Gestures themselves (poke, LIKE, swing, right-click menu) are specified in
`user-interactions`; this capability owns only the window and the bridge
they call through.

## Requirements
### Requirement: Render as a frameless, transparent, always-on-top window

The pet window SHALL be created via pywebview with `frameless=True`,
`transparent=True`, `on_top=True`, `resizable=False`, and dimensions
200×300 pixels (`WINDOW_WIDTH` × `WINDOW_HEIGHT`). It SHALL load
`frontend/index.html` as a `file://` URL.

The 180×180 sprite sits flush with the window bottom, leaving 120 px of
headroom above it for the heart and the speech bubble.
- **Why**: height was 220 px originally; hearts spawned above her head were
  clipped by the window edge. `passthrough.py` mirrors both constants and
  must be changed with it.

#### Scenario: Window is created
- **WHEN** `python -m squid_pet` starts
- **THEN** a 200×300 frameless, transparent, always-on-top window appears on the main display
- **AND** no title bar, traffic-light buttons, or window chrome are visible

#### Scenario: Other windows come to the foreground
- **WHEN** the user activates another application
- **THEN** Squid remains visible above that application

### Requirement: Position via direct NSWindow control

The window SHALL be positioned by calling `NSWindow.setFrameOrigin_` with
coordinates derived from `NSScreen.mainScreen().visibleFrame()`. The
pywebview `x, y` parameters MAY be used for initial placement but a final
snap via NSWindow SHALL run inside the `loaded` event to guarantee correct
position on multi-display setups.

Corner snapping SHALL use the same `_char_bounds()` tight character bounds
the wander system is tuned to reach, NOT a cosmetic inset from the visible
frame.
- **Why**: Pink-2026-08-27o. With the old 20 px `EDGE_MARGIN`, a
  corner-snapped Squid sat 60-70 px further from the true screen edge than
  she did after WANDERING to that same corner, which read as her not
  hugging the edge. It was never a rotation problem; the position itself
  was ~65 px short.

Startup position SHALL resolve in priority order: `position.json` (last
saved location) → `settings.json` → default.

#### Scenario: Snap to a corner of the visible frame
- **WHEN** `move_to_corner("top-right")` is called
- **THEN** the window lands at the identical position wandering to that
  corner would produce, tentacles touching the edge
- **AND** the menu bar and dock are not overlapped

#### Scenario: App restarts after a corner snap
- **WHEN** Squid is restarted
- **THEN** she appears at the last-saved position from `~/.squid-pet/position.json`

### Requirement: Drag the window without macOS Accessibility permission

The window SHALL be draggable by clicking and holding the left mouse button
on any opaque pixel of the sprite. Movement SHALL be implemented in JS
(`mousedown`/`mousemove`/`mouseup` with `event.screenX/Y` deltas) calling
the Python bridge method `api.move_window_by(dx, dy)`, which SHALL apply
the delta via `NSWindow.setFrameOrigin_`.

Neither `pywebview.easy_drag` nor `-webkit-app-region: drag` SHALL be used,
and no macOS Accessibility permission prompt SHALL be triggered.

#### Scenario: User drags Squid across the screen
- **WHEN** the user presses left mouse, moves 100 px right and 50 px down, and releases
- **THEN** the window's screen position shifts by (+100, +50)
- **AND** no permission prompt appears

#### Scenario: User clicks without moving
- **WHEN** the user presses left mouse and releases at the same position within 250 ms
- **THEN** the window does not move
- **AND** the click is classified as a poke (see `user-interactions`)

### Requirement: Esc releases a forced state

Pressing `Esc` while the window has keyboard focus SHALL release any forced
state and resume the auto-detected state from the watcher. `Ctrl+D` SHALL
toggle the debug overlay.

Double-click SHALL NOT cycle or force states. That behavior was removed in
2026-06-08; forcing a specific state for debug lives in the right-click
menu's Mood submenu, and dblclick is the LIKE gesture (see
`user-interactions`).

#### Scenario: User presses Esc
- **WHEN** the user presses Escape while the window has keyboard focus
- **THEN** the forced state is cleared
- **AND** the next poll displays the watcher's current detected state

### Requirement: Expose a JS↔Python bridge with a stable API

The Python `PetApi` class SHALL expose, at minimum, the following methods
to JavaScript via pywebview's `js_api`:

| Method | Purpose |
|---|---|
| `get_state()` | Return current `PetState` dict plus derived fields (below) |
| `force_state(name)` / `clear_force()` | Pin / release a specific state |
| `next_corner()` | Snap to the next corner; return its name |
| `move_window_by(dx, dy)` | Move the window by the given screen-pixel delta |
| `drag_start()` / `drag_end()` | Bracket a drag so passthrough can pause |
| `poke()` | Poke gesture: 60 s wake override, clear force, observer bubble |
| `acknowledge_approval()` | Calm the flag-wave; returns `{status, bubble}` |
| `take_me_there()` | Raise the terminal window behind the current state |
| `show_context_menu()` | Open the native right-click menu |
| `clear_bubble()` | Ack a displayed bubble so it is not replayed |
| `set_wander_edge(edge)` / `set_wander_sub_state(s)` / `get_wander_sub_state()` | Wander → frontend animation cues |
| `notify_mood(mood)` / `get_frontend_mood()` | Frontend-derived mood (drowsy etc.) reported back |
| `is_hidden()` / `is_muted()` / `is_approval_alert_enabled()` / `is_squid_waving()` | Menu + frontend state queries |
| `signal_ready()` | Frontend boot handshake |
| `debug_log(msg)` | Route a frontend log line into the Python log |
| `quit()` | Close the window |

`get_state()` SHALL return the full `PetState` dict (see `state-detection`)
with the forced-state override applied, plus these derived fields:
`edge`, `sub_state` overlay, `hint_text`, `hint_seq`, `pinned`,
`wrapper_deg`, `wake_trigger_seq`, `user_wake_remaining`,
`sprint_fast_transition`, `pending_bubble`.

The `sub_state` overlay SHALL be gated to `state == "idle"` for ambient
wander sub-states, EXCEPT `nudge-*` sub-states, which apply in any state.
- **Why**: 2026-08-19. A nudge hop can fire in any backend state, but
  without the exemption the window physically moved while the frontend was
  never told to show the walking cue -- nudging looked like it silently did
  nothing whenever she was not idle.

#### Scenario: JavaScript reads current state
- **WHEN** `window.pywebview.api.get_state()` is awaited
- **THEN** the returned object includes `state`, `idle_seconds`, `agent_idle_seconds`, `claude_code_running`, `codex_running`, `timestamp`, `message`, `state_reason`, `user_wake_remaining`, and `pending_bubble`

### Requirement: Persist across all macOS Spaces and fullscreen apps

Squid SHALL remain visible when the user switches Spaces, enters a
fullscreen application, or uses Mission Control. The window's
`collectionBehavior` SHALL be set to `273`
(`canJoinAllSpaces | stationary | fullScreenAuxiliary`) via
`NSWindow.setCollectionBehavior_`, called from the loaded event handler
on the main thread via `AppHelper.callAfter`.

#### Scenario: User switches Space
- **WHEN** the user switches to a different macOS Space (Control-arrow or trackpad swipe)
- **THEN** Squid is visible on the new Space at her existing screen position
- **AND** her state and any in-progress animation continues uninterrupted

#### Scenario: User enters fullscreen
- **WHEN** the user enters fullscreen mode in any application
- **THEN** Squid remains visible above the fullscreen window
- **AND** Squid is positioned within the visible frame of the fullscreen Space

### Requirement: Refuse to launch a second instance (atomic singleton)

A second invocation of `python -m squid_pet` SHALL detect that an existing
instance is running and refuse to start, printing a clear message identifying
the running instance. The detection mechanism SHALL be atomic and race-free
under concurrent launches.

The implementation SHALL acquire an exclusive non-blocking flock on
`~/.squid-pet/lock` (`fcntl.LOCK_EX | fcntl.LOCK_NB`) at startup. The lock
file descriptor SHALL be kept alive in module globals for the duration of
the process. The lock SHALL be released by an atexit handler on clean
shutdown, OR by the kernel's automatic fd cleanup on SIGKILL.

#### Scenario: Two launches race to start
- **WHEN** two `python -m squid_pet` invocations start within milliseconds of each other
- **THEN** exactly one acquires the flock and continues to startup
- **AND** the other prints a clear "already running" message and exits cleanly
- **AND** no two windows ever appear on screen

#### Scenario: Hard-killed instance leaves no stale lock
- **WHEN** an instance is killed with SIGKILL
- **AND** a new instance is launched immediately afterward
- **THEN** the kernel has released the flock on fd close
- **AND** the new instance acquires the lock and starts normally

### Requirement: External CLI for control and diagnostics

A command-line tool SHALL be installed at `~/.local/bin/squid` providing
operational control without requiring direct knowledge of the Python module
or process details. Lifecycle SHALL be delegated to launchd rather than
managed by the CLI itself:

- `squid start` — load + run the LaunchAgent
- `squid stop` — `launchctl bootout` the LaunchAgent
- `squid restart` — `launchctl kickstart -k` (atomic bounce); falls back to
  `start` when the agent is not loaded
- `squid status` — running? watcher ticking? (cross-checks `state.json`)
- `squid logs [-f]` — recent stdout+stderr; `-f` to follow
- `squid update` — git pull + reinstall + restart
- `squid uninstall [...]` — shell into the project's `uninstall.sh`
- `squid why` — explain the current state and which detectors fired
- `squid doctor` — run the six-check self-diagnostic (see `startup-integrity`)

- **Why**: the CLI used to own the process itself, with escalating
  SIGTERM→SIGKILL retries and a 10-second watchdog wrapping launch attempts
  to survive WKWebView startup flake. launchd already does supervision and
  restart-on-crash properly; `launchctl` is now the single source of truth
  and the retry scaffolding is gone.

`squid status` SHALL query launchctl for the managed pid and then cross-check
that `state.json` is fresh, so a live process with a dead watcher reports
unhealthy rather than running.

#### Scenario: Status command after normal launch
- **WHEN** Squid is running healthily
- **AND** the user runs `squid status`
- **THEN** the output reports the launchd-managed pid and a fresh `state.json`

#### Scenario: Process alive but watcher wedged
- **WHEN** launchctl reports a running pid
- **AND** `state.json` has not been written for well over one poll interval
- **THEN** `squid status` reports unhealthy rather than running
