# click-passthrough Specification

## Purpose
Define how Squid's window selectively passes mouse events through to the
desktop and other apps. Most of the 200x220 window is transparent; only the
actual pet sprite pixels should capture clicks. Outside the sprite bbox the
window must be invisible to mouse input so users can interact with whatever
is underneath (Finder, browser, terminal, etc.).

## Requirements

### Requirement: Pre-load alpha masks for all sprites at startup

A `PassthroughController` SHALL load every PNG in
`frontend/sprites/*.png` (excluding files whose name starts with `_`) at
startup, extract the alpha channel, resize it to the displayed sprite size
(180×180), and keep one mask per state name in memory.

#### Scenario: Startup
- **WHEN** the controller is created
- **THEN** the in-memory mask dict contains exactly 7 entries keyed by state name (idle, thinking, working, grooving, celebrating, sleeping, concerned)

### Requirement: Toggle `setIgnoresMouseEvents_` based on current alpha at cursor

A background daemon thread SHALL poll the global cursor location via
`NSEvent.mouseLocation()` at ~33 Hz (30 ms interval). It SHALL:

1. Map the cursor's screen coords to window-local coords using the current
   NSWindow frame.
2. Translate window-local coords to sprite-local coords using the sprite's
   center offset (SPRITE_LEFT=10, SPRITE_TOP=20).
3. Look up the alpha value at that sprite pixel in the mask for the
   currently-displayed state.
4. Call `setIgnoresMouseEvents_(False)` if `alpha > ALPHA_THRESHOLD` (30),
   otherwise `setIgnoresMouseEvents_(True)`.
5. Avoid redundant calls by tracking the last applied value.

#### Scenario: Cursor over opaque sprite pixel
- **WHEN** the cursor is over a pixel of the sprite with alpha > 30
- **THEN** the NSWindow's `ignoresMouseEvents` is `False`
- **AND** clicks land on Squid (drag / right-click / dbl-click all work)

#### Scenario: Cursor over transparent area of the window
- **WHEN** the cursor is inside the window's bounding box but over a transparent pixel (alpha < 30)
- **THEN** the NSWindow's `ignoresMouseEvents` is `True`
- **AND** clicks pass through to whatever app is behind Squid

#### Scenario: Cursor outside the window
- **WHEN** the cursor is outside the window's bounding box
- **THEN** `ignoresMouseEvents` is set to `True` so the window never blocks anything

### Requirement: Update active mask on state change

The controller's `set_state(state)` method SHALL be called by `PetApi.update`
whenever the displayed state changes (either via watcher or forced override).
Subsequent hit-tests SHALL use the new mask within one polling interval (≈30 ms).

#### Scenario: State changes from idle to grooving
- **WHEN** the watcher transitions from `idle` to `grooving`
- **THEN** the hit-test alpha values are read from the `grooving.png` mask within the next 30 ms

### Requirement: Pause passthrough during active drag

Before the JS drag starts (on `mousedown`), the bridge SHALL call
`api.drag_start()`, which SHALL pause the passthrough loop and force
`ignoresMouseEvents = False`. On `mouseup`, `api.drag_end()` SHALL resume
the loop.

#### Scenario: User drags Squid across opaque and transparent regions
- **WHEN** the user is mid-drag and the cursor briefly leaves the opaque sprite area
- **THEN** the drag is NOT dropped (because passthrough remains paused until `drag_end`)

#### Scenario: User releases mouse
- **WHEN** `mouseup` fires
- **THEN** `drag_end()` resumes the polling loop and normal alpha-based passthrough resumes within 30 ms

### Requirement: Failure modes SHALL never block the user

The controller SHALL default to `ignoresMouseEvents = True` (passthrough on)
whenever alpha lookup fails (missing mask, coords out of range, exception),
so Squid never accidentally blocks a click.

#### Scenario: Mask for current state is missing
- **WHEN** `_masks[current_state]` raises `KeyError`
- **THEN** `ignoresMouseEvents` is set to `True` and a warning is logged
