# click-passthrough Specification

## Purpose
Define how Squid's window selectively passes mouse events through to the
desktop and other apps. Most of the 200×300 window is transparent; only the
actual pet sprite pixels should capture clicks. Outside the sprite bbox the
window must be invisible to mouse input so users can interact with whatever
is underneath (Finder, browser, terminal, etc.).

This capability also owns the cursor-behavior layers built on top of the
same hit-test loop: the hover fade-through that lets her step aside, and
the approach tracking that makes her hop or flee when shooed.

## Requirements

### Requirement: Pre-load alpha masks for all sprites at startup

A `PassthroughController` SHALL load every PNG in
`frontend/sprites/*.png` (excluding files whose name starts with `_`) at
startup, extract the alpha channel, resize it to the displayed sprite size
(180×180), and keep one mask per sprite name in memory.

#### Scenario: Startup
- **WHEN** the controller is created
- **THEN** the in-memory mask dict contains one entry per non-underscore
  sprite PNG, keyed by sprite name -- including the frontend-derived
  sprites (`drowsy`, `stretch`, `blink`, `look-left`, `look-right`) and the
  flag-wave frames (`attention_needed`, `attention_needed_1`..`_4`)

### Requirement: Toggle `setIgnoresMouseEvents_` based on current alpha at cursor

A background daemon thread SHALL poll the global cursor location via
`NSEvent.mouseLocation()` at ~33 Hz (30 ms interval). It SHALL:

1. Map the cursor's screen coords to window-local coords using the current
   NSWindow frame.
2. Translate window-local coords to sprite-local coords using the sprite's
   offset within the window (`SPRITE_LEFT` = 10, `SPRITE_TOP` = 120, i.e.
   the 180×180 sprite flush with the window bottom), adjusted by the
   top-edge offset when the sprite is shifted up.
3. Look up the alpha value at that sprite pixel in the mask for the
   currently-displayed state.
4. Call `setIgnoresMouseEvents_(False)` if `alpha > ALPHA_THRESHOLD` (30),
   otherwise `setIgnoresMouseEvents_(True)`.
5. Avoid redundant calls by tracking the last applied value, under a lock
   shared by every reader and writer of it.

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

### Requirement: Sustained hover fades her, then lets the click through

Holding the cursor still over her clickable pixels SHALL escalate in two
stages: after `HOVER_DWELL_SEC` (1.0 s) she fades to `HOVER_FADE_ALPHA`
(0.5) but REMAINS grabbable; only after `HOVER_PASSTHROUGH_DWELL_SEC`
(2.5 s) does she also become click-through, so whatever is underneath is
reachable without shooing her first.

Dwell SHALL reset the instant the cursor leaves her bbox -- this is a
sustained-presence check, not a re-entry count. Cursor movement beyond
`HOVER_STILLNESS_TOLERANCE_PX` (16 px of total drift from the anchor where
the dwell began) SHALL restart the timer.

- **Why**: Pink-2026-09-01, both numbers. Fade and click-through used to
  arrive together at 1.0 s, so any grab slower than one second silently
  went to the app underneath -- she looked translucent and simply could not
  be picked up. Splitting them makes the fade a WARNING that she is about
  to step aside. And the 4 px stillness budget was so tight an ordinary
  hand tremor cancelled the dwell, so the feature barely fired; 16 px
  absorbs tremor while staying far from "anywhere inside the bbox" (her
  visible character is ~107 px wide).

#### Scenario: Parking the cursor on her to reach what is underneath
- **WHEN** the cursor rests on her opaque pixels for 1 second
- **THEN** she fades to 50% opacity AND is still grabbable
- **WHEN** it stays there for 2.5 seconds total
- **THEN** clicks pass through to the app underneath

#### Scenario: Sweeping across her while aiming elsewhere
- **WHEN** the cursor crosses her bbox while moving more than 16 px
- **THEN** no dwell accumulates and she neither fades nor steps aside

### Requirement: Shift is the deliberate escape hatch

While Shift is held, Squid SHALL be grabbable regardless of any other
condition -- no dwell, no fade, no fleeing. The check SHALL be a pure bit
test on the modifier flags (`NSEventModifierFlagShift`, bit 17) so it is
testable without AppKit.

#### Scenario: Grabbing a faded Squid
- **WHEN** she has already faded and gone click-through
- **AND** the user holds Shift
- **THEN** the next click lands on her, not on the app underneath

### Requirement: Repeated approaches nudge her, then send her to a corner

The controller SHALL track raw re-entries of the cursor into her clickable
bbox and translate them into movement requests:

- `NUDGE_APPROACH_THRESHOLD` (2) entries within `NUDGE_WINDOW_SEC` (0.8 s)
  SHALL request a short nudge hop, rate-limited by `NUDGE_COOLDOWN_SEC`
  (1.5 s).
- `CORNER_FLEE_THRESHOLD` (3) entries in a row SHALL request a flee to the
  corner, with no window or cooldown gating. "In a row" is bounded only by
  a lull: `CORNER_FLEE_RESET_SEC` (2.5 s) without a fresh entry resets the
  streak.

A single entry SHALL never reach either threshold, so a plain hover, click,
or drag is completely unaffected.

- **Why**: 2026-08-21 replaced an older scheme where fleeing required N
  consecutive *nudges*, each already gated behind rapid re-entries -- far
  too much had to pile up before she got out of the way. Pink-2026-09-01
  cut the flee reset from 6.0 s to 2.5 s because three deliberate grab
  retries land well inside 6 s and were being read as shooing.

#### Scenario: Bouncing the cursor onto her twice quickly
- **WHEN** the cursor enters her bbox twice within 0.8 seconds
- **THEN** a short nudge hop is requested
- **AND** no further nudge fires for 1.5 seconds

#### Scenario: Three approaches in a row
- **WHEN** the cursor enters her bbox three times with no 2.5 s lull
- **THEN** she flees to the corner immediately

#### Scenario: A single click is not a shoo
- **WHEN** the user moves onto her once and clicks
- **THEN** no nudge and no flee is requested

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
