## ADDED Requirements

### Requirement: Render as a frameless, transparent, always-on-top window

The pet window SHALL be created via pywebview with `frameless=True`,
`transparent=True`, `on_top=True`, `resizable=False`, and dimensions
200×220 pixels. It SHALL load `frontend/index.html` as a `file://` URL.

#### Scenario: Window is created
- **WHEN** `python -m squid_pet` starts
- **THEN** a 200×220 frameless, transparent, always-on-top window appears on the main display
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

#### Scenario: Snap to a corner of the visible frame
- **WHEN** `move_to_corner("top-right")` is called
- **THEN** the window's top-right pixel sits exactly `EDGE_MARGIN` (20 px) inside the visible frame's top-right corner
- **AND** the menu bar and dock are not overlapped

#### Scenario: Right-click cycles corners
- **WHEN** the user right-clicks Squid
- **THEN** the window snaps to the next corner in the order top-right → bottom-right → bottom-left → top-left → top-right
- **AND** the new corner is persisted to `~/.squid-pet/position.json`

#### Scenario: App restarts after a corner snap
- **WHEN** Squid is restarted
- **THEN** she appears at the last-saved corner

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
- **AND** this is treated as a tap (currently no-op; reserved for future click action)

### Requirement: Cycle and force states via double-click and Esc

The user SHALL be able to preview each state by double-clicking the window,
which forces the next state in the canonical order. Pressing `Esc` SHALL
release the forced state and resume the auto-detected state from the
watcher.

#### Scenario: Double-click cycles through states
- **WHEN** the user double-clicks Squid
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
