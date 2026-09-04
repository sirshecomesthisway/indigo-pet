# pet-reactions Specification

## Purpose
Define the ephemeral visual reaction effects Squid renders in response to
user gestures -- today, the heart spawned by the double-click LIKE gesture.
Reactions are pure decoration: they never change sprite state, never
participate in click-passthrough, and always clean themselves up.

## Requirements
### Requirement: Ephemeral visual reactions to user gestures

The pet SHALL render ephemeral visual reaction effects in response to specific
user gestures. Reaction effects are short-lived (under 2 seconds), rendered
as absolutely-positioned DOM elements above the sprite layer, and do NOT
affect the sprite state or click-passthrough behavior.

REVISION (2026-06-08): Heart trigger moved from single-click (poke) to
double-click (LIKE gesture). Rationale: single-click wakes Squid quickly;
double-click is the deliberate affection gesture that deserves the visual
reward. A double-click on a drowsy Squid wakes her AND shows a heart in
one gesture (poke API still fires on dblclick).

#### Scenario: Double-click spawns one blinking heart and wakes Squid
- **WHEN** the user double-clicks Squid on an opaque pixel
- **THEN** any pending single-click poke is cancelled
- **AND** the poke API is invoked (sets the 60 s wake override and publishes
  the observer's poke bubble)
- **AND** `HEART_COUNT` (1) `sprites/heart.png` element appears centered
  above Squid's sprite, 38×35 px and pixel-rendered
- **AND** the heart pops in (scale 0.3 → 1.4), settles to 1.0, pulses once
  to 1.15, and fades out over 1200 ms
- **AND** the heart does NOT translate vertically (no rise) so it cannot be
  clipped by the window's 120 px of headroom
- **AND** the heart is removed from the DOM on `animationend`

#### Scenario: Heart anchors correctly at the top screen edge
- **WHEN** Squid is docked at the top edge (sprite flipped, face pointing down)
- **THEN** the heart anchors to the sprite's bottom rather than its top
- **AND** the stacking order stays face → heart → bubble

#### Scenario: Single click does NOT spawn a heart
- **WHEN** the user single-clicks Squid (no follow-up dblclick within 260 ms)
- **THEN** the poke API fires for wake behavior
- **AND** zero hearts appear (heart is reserved for the dblclick LIKE gesture)

#### Scenario: Hearts never block user interaction
- **WHEN** any heart element exists in the DOM
- **THEN** that element has `pointer-events: none`
- **AND** the user can still drag, click, or right-click Squid through the heart

#### Scenario: Cap on concurrent hearts prevents runaway spawn
- **WHEN** `HEART_MAX_LIVE` (12) hearts already exist on screen
- **AND** the user double-clicks again
- **THEN** no new hearts spawn for that gesture
- **AND** the gesture itself still fires normally (wake, ack, take-me-there)

#### Scenario: Drag misclassified as poke does not spawn heart
- **WHEN** a click is classified as a single poke (no dblclick follow-up)
- **THEN** zero hearts appear (heart only fires on dblclick)

#### Scenario: Hearts do not fire on swing-to-wake
- **WHEN** the user performs a swing gesture during drag that triggers wake
- **THEN** the observer's shake bubble appears as designed
- **AND** zero hearts appear (the gesture already has its own feedback)

#### Scenario: Hearts ride along with Squid during drag
- **WHEN** hearts are mid-animation
- **AND** the user drags Squid to a new screen position
- **THEN** the hearts move with her, because they are positioned inside the
  window and the whole window is what moves
- **AND** the hearts complete their pop+fade animation in the new position

#### Scenario: A failed spawn never breaks the gesture
- **WHEN** heart spawning raises for any reason
- **THEN** the error is logged to the console and routed to the Python log
  via `debug_log`
- **AND** the wake, acknowledge, and take-me-there parts of the gesture are
  unaffected
