## ADDED Requirements

### Requirement: Poke gesture wakes Squid temporarily

A single-click on an opaque pixel of Squid SHALL be classified as a "poke"
gesture when both: the mousedown-to-mouseup duration is under 250 milliseconds,
AND the cursor movement during that interval is under 6 pixels. The poke
SHALL be deferred by 260 milliseconds before firing to allow disambiguation
from a double-click; a double-click within that window cancels the pending poke.

A confirmed poke SHALL:
- Set a user-wake override that suppresses drowsy entry for 60 seconds.
- Bump a wake-trigger sequence number consumed by the frontend.
- Display a "boop!" hint pill in the corner.
- Clear any forced-state override.

#### Scenario: Single click while drowsy
- **WHEN** Squid is in the drowsy state
- **AND** the user single-clicks an opaque pixel of her sprite
- **THEN** 260 milliseconds later, the wake override is set to now + 60 seconds
- **AND** the "boop!" hint pill appears
- **AND** the frontend plays the wake-stretch transition and returns to the idle sprite
- **AND** Squid stays awake for the next 60 seconds even if TPA remains idle

#### Scenario: Double-click supersedes pending poke
- **WHEN** the user double-clicks Squid within 260 milliseconds of the first click
- **THEN** the pending single-click poke is cancelled
- **AND** the dblclick LIKE gesture fires instead (see next requirement)

### Requirement: Double-click is the LIKE gesture (heart + wake)

A double-click on Squid SHALL be treated as a LIKE gesture distinct from a
single-click poke. The dblclick handler SHALL:
- Cancel any pending single-click poke timer.
- Invoke `api.poke()` (which sets the 60 s wake override and shows the "boop!" hint).
- Spawn a single blinking heart above Squid's sprite (see `pet-reactions` capability).

The previous foundational behavior of dblclick (cycling forced state through
the 7 sprite states for debug) is REMOVED. State cycling is no longer
exposed via dblclick; if needed for debug it lives in the right-click menu.

#### Scenario: Double-click on drowsy Squid
- **WHEN** Squid is in the drowsy state
- **AND** the user double-clicks her
- **THEN** the wake override is set to now + 60 seconds
- **AND** the "boop!" hint pill appears
- **AND** one heart emoji blinks above her head
- **AND** Squid plays the wake-stretch transition back to the idle sprite

#### Scenario: Double-click on awake Squid
- **WHEN** Squid is in any non-drowsy state
- **AND** the user double-clicks her
- **THEN** the wake override is set (no visible state change since she is already awake)
- **AND** one heart emoji blinks above her head
- **AND** the sprite state does NOT cycle (no force_state invocation)

### Requirement: Swing-to-wake gesture during drag

A vigorous up-down shaking motion during a drag SHALL be detected by the
native drag loop and treated as a wake gesture equivalent to a poke. The
algorithm SHALL count y-direction reversals of at least 8 pixels each within
a sliding 0.6-second window, and fire once when the count reaches 4 reversals
(equivalent to two complete up-down swings). The detection SHALL fire at
most once per drag.

#### Scenario: Shake Squid awake mid-drag
- **WHEN** the user is dragging Squid
- **AND** the user moves the cursor up-down-up-down with at least 8 pixel deltas
       within 0.6 seconds
- **THEN** the wake override is set to now + 60 seconds
- **AND** the "wheee!" hint pill appears
- **AND** subsequent reversals within the same drag do NOT re-fire

#### Scenario: Gentle drag does not trigger swing-wake
- **WHEN** the user drags Squid smoothly to a new screen position
- **THEN** no reversal count accumulates beyond the threshold
- **AND** no wake override is set
- **AND** no "wheee!" hint appears

### Requirement: Right-click opens a menu independent of click-passthrough

A right-click anywhere on Squid SHALL open a context menu, even when the
sprite is in a click-passthrough region under the cursor. Detection SHALL
use a global NSEvent monitor (not WKWebView contextmenu events) so the
menu always opens.

The menu SHALL include at minimum:
- "Sprint perimeter" — triggers `WanderController.sprint_perimeter()`
- "Pause wander" / "Resume wander" — toggles wander activity
- "Quit" — clean shutdown

#### Scenario: Right-click on transparent edge of sprite
- **WHEN** the user right-clicks within Squid's window bounds but in a
       click-passthrough (alpha=0) pixel
- **THEN** the menu still opens
- **AND** the menu is positioned at the click location

#### Scenario: Sprint via menu
- **WHEN** the user selects "Sprint perimeter" from the menu
- **THEN** Squid begins a clockwise perimeter sprint starting with a stretch
       transition and ending at the bottom edge
