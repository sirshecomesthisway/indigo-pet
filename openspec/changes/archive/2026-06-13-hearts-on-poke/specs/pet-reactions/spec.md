## ADDED Requirements

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
- **AND** the poke API is invoked (sets 60 s wake override, shows "boop!" hint)
- **AND** one heart emoji appears centered above Squid's sprite
- **AND** the heart pops in (scale 0.3 -> 1.3), settles, pulses once, and fades over 900 ms
- **AND** the heart does NOT translate vertically (no rise) so it cannot be clipped by the 220 px window
- **AND** the heart is removed from the DOM after its animation completes

#### Scenario: Single click does NOT spawn a heart
- **WHEN** the user single-clicks Squid (no follow-up dblclick within 260 ms)
- **THEN** the poke API fires for wake / boop behavior
- **AND** zero hearts appear (heart is reserved for the dblclick LIKE gesture)

#### Scenario: Hearts never block user interaction
- **WHEN** any heart element exists in the DOM
- **THEN** that element has `pointer-events: none`
- **AND** the user can still drag, click, or right-click Squid through the heart

#### Scenario: Cap on concurrent hearts prevents runaway spawn
- **WHEN** the user pokes Squid repeatedly such that the cap (HEART_MAX_LIVE) hearts already exist on screen
- **AND** the user pokes again
- **THEN** no new hearts spawn for that poke
- **AND** the poke itself still fires the wake override (functional poke is unaffected)

#### Scenario: Drag misclassified as poke does not spawn heart
- **WHEN** a click is classified as a single poke (no dblclick follow-up)
- **THEN** zero hearts appear (heart only fires on dblclick)

#### Scenario: Hearts do not fire on swing-to-wake
- **WHEN** the user performs a swing gesture during drag that triggers wake
- **THEN** the "wheee!" hint appears as designed
- **AND** zero hearts appear (the gesture already has its own visual feedback)

#### Scenario: Hearts ride along with Squid during drag
- **WHEN** hearts are mid-animation
- **AND** the user drags Squid to a new screen position
- **THEN** the hearts follow Squid because they are children of the sprite container
- **AND** the hearts complete their rise+fade animation in the new position
