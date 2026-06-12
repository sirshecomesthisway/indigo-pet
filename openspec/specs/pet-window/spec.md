# pet-window Specification

## Purpose
Define how Squid is presented as a desktop companion window: a frameless,
transparent, always-on-top pywebview window with a stable JS/Python bridge.
Covers window creation, positioning, drag-without-Accessibility, state
controls (dblclick/Esc), the JS API surface, multi-Space persistence,
singleton enforcement, and the external CLI for launch/control.
## Requirements
### Requirement: Render as a frameless, transparent, always-on-top window

The pet window SHALL be created via pywebview with `frameless=True`,
`transparent=True`, `on_top=True`, `resizable=False`, and dimensions
200×220 pixels. It SHALL load `frontend/index.html` as a `file://` URL.

#### Scenario: Window is created
- **WHEN** `python -m indigo_pet` starts
- **THEN** a 200×220 frameless, transparent, always-on-top window appears on the main display
- **AND** no title bar, traffic-light buttons, or window chrome are visible

#### Scenario: Other windows come to the foreground
- **WHEN** the user activates another application
- **THEN** Indigo remains visible above that application

### Requirement: Position via direct NSWindow control

The window SHALL be positioned by calling `NSWindow.setFrameOrigin_` with
coordinates derived from `NSScreen.mainScreen().visibleFrame()`. The
pywebview `x, y` parameters MAY be used for initial placement but a final
snap via NSWindow SHALL run inside the `loaded` event to guarantee correct
position on multi-display setups.

#### Scenario: Snap to a corner of the visible frame
- **WHEN** `move_to_corner("top-right")` is called
- **THEN** the window's top-right pixel sits exactly `EDGE_MARGIN` (20 px) inside the visible frame's top-right corner
- **AND** the menu bar and dock are not overlapped

#### Scenario: Right-click cycles corners
- **WHEN** the user right-clicks Indigo
- **THEN** the window snaps to the next corner in the order top-right → bottom-right → bottom-left → top-left → top-right
- **AND** the new corner is persisted to `~/.indigo-pet/position.json`

#### Scenario: App restarts after a corner snap
- **WHEN** Indigo is restarted
- **THEN** she appears at the last-saved corner

### Requirement: Drag the window without macOS Accessibility permission

The window SHALL be draggable by clicking and holding the left mouse button
on any opaque pixel of the sprite. Movement SHALL be implemented in JS
(`mousedown`/`mousemove`/`mouseup` with `event.screenX/Y` deltas) calling
the Python bridge method `api.move_window_by(dx, dy)`, which SHALL apply
the delta via `NSWindow.setFrameOrigin_`.

Neither `pywebview.easy_drag` nor `-webkit-app-region: drag` SHALL be used,
and no macOS Accessibility permission prompt SHALL be triggered.

#### Scenario: User drags Indigo across the screen
- **WHEN** the user presses left mouse, moves 100 px right and 50 px down, and releases
- **THEN** the window's screen position shifts by (+100, +50)
- **AND** no permission prompt appears

#### Scenario: User clicks without moving
- **WHEN** the user presses left mouse and releases at the same position within 250 ms
- **THEN** the window does not move
- **AND** this is treated as a tap (currently no-op; reserved for future click action)

### Requirement: Cycle and force states via double-click and Esc

The user SHALL be able to preview each state by double-clicking the window,
which forces the next state in the canonical order. Pressing `Esc` SHALL
release the forced state and resume the auto-detected state from the
watcher.

#### Scenario: Double-click cycles through states
- **WHEN** the user double-clicks Indigo
- **THEN** the displayed state advances to the next in the order: idle → thinking → working → grooving → celebrating → sleeping → concerned → idle
- **AND** the watcher's emitted state is overridden until Esc

#### Scenario: User presses Esc
- **WHEN** the user presses Escape while the window has keyboard focus
- **THEN** the forced state is cleared
- **AND** the next poll displays the watcher's current detected state

### Requirement: Expose a JS↔Python bridge with a stable API

The Python `PetApi` class SHALL expose, at minimum, the following methods
to JavaScript via pywebview's `js_api`:

| Method | Purpose |
|---|---|
| `get_state()` | Return current `PetState` dict (with any forced override applied) |
| `force_state(name)` | Pin pet to a specific state name |
| `clear_force()` | Release the forced state |
| `next_corner()` | Snap to the next corner; return its name |
| `move_window_by(dx, dy)` | Move the window by the given screen-pixel delta |
| `drag_start()` / `drag_end()` | Notify Python that a drag is starting / ending so passthrough can pause |
| `quit()` | Close the window |

#### Scenario: JavaScript reads current state
- **WHEN** `window.pywebview.api.get_state()` is awaited
- **THEN** the returned object includes `state`, `cpu_percent`, `idle_seconds`, `tpa_running`, `timestamp`, and `message` fields

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

A second invocation of `python -m indigo_pet` SHALL detect that an existing
instance is running and refuse to start, printing a clear message identifying
the running instance. The detection mechanism SHALL be atomic and race-free
under concurrent launches.

The implementation SHALL acquire an exclusive non-blocking flock on
`~/.indigo-pet/lock` (`fcntl.LOCK_EX | fcntl.LOCK_NB`) at startup. The lock
file descriptor SHALL be kept alive in module globals for the duration of
the process. The lock SHALL be released by an atexit handler on clean
shutdown, OR by the kernel's automatic fd cleanup on SIGKILL.

#### Scenario: Two launches race to start
- **WHEN** two `python -m indigo_pet` invocations start within milliseconds of each other
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
or process details. The CLI SHALL support at minimum:

- `squid start` — launch if not running
- `squid stop` — terminate cleanly (with escalating SIGTERM -> SIGKILL retries up to 6 attempts)
- `squid restart` — stop then start, with up to 3 launch retries to handle
  WKWebView startup flakiness on the host
- `squid status` — report running/healthy/unhealthy + pid
- `squid logs` — tail the log file
- `squid why` — print recent state log entries explaining current state

The `squid status` command SHALL distinguish between true duplicate processes
(multiple roots) and benign parent-child pairs (pywebview spawns a WebKit
content child sharing the parent's cmdline). It SHALL count only ROOT
processes whose parent is NOT also an indigo_pet process.

For backward compatibility, the binary `~/.local/bin/indigo` SHALL exist
as a symlink to `squid`.

#### Scenario: Status command after normal launch
- **WHEN** Squid is running healthily
- **AND** the user runs `squid status`
- **THEN** the output reports "running + healthy" with the parent pid
- **AND** no duplicate warning appears despite the pywebview child process

#### Scenario: Restart survives WKWebView flake
- **WHEN** the user runs `squid restart`
- **AND** the first launch attempt hangs in WKWebView startup
- **THEN** the CLI's 10-second watchdog kills the stuck attempt
- **AND** the CLI retries up to 3 times until startup succeeds
- **AND** the final status reports running + healthy

#### Scenario: Backward-compatible binary name
- **WHEN** the user runs `indigo status` (old muscle memory)
- **THEN** the symlink resolves to `squid status`
- **AND** the output identifies the pet as "Squid"

