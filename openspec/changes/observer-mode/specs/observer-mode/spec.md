## ADDED Requirements

### Requirement: Observer subsystem publishes one-line reactions in response to triggers

The system SHALL include an Observer component that consumes state-change and
interaction events and publishes a single short reaction string per event.
The Observer is a passive comment layer: it never modifies pet state, never
intercepts the agent (TPA), and never produces multi-line or paragraph
output.

#### Scenario: State transition fires a reaction
- **WHEN** the StateMachine computes a state change from `old` to `new` where `old != new`
- **AND** there is a registered line for the transition (e.g. any → thinking)
- **THEN** the Observer returns a single string ≤ 32 characters
- **AND** the string is written to `PetApi._pending_bubble`, overwriting any prior pending bubble

#### Scenario: No-op for same-state ticks
- **WHEN** the StateMachine computes a state where `old == new`
- **THEN** the Observer returns None
- **AND** the pending bubble slot is NOT modified

#### Scenario: User interaction fires a reaction
- **WHEN** the user interacts with Squid in a registered way (single-click poke, double-click LIKE, right-click → Sprint, mood-change notify)
- **THEN** PetApi calls `observer.on_interaction(kind)` with the trigger key
- **AND** if registered, a single string ≤ 32 characters is published to `_pending_bubble`

#### Scenario: Unknown trigger key is handled gracefully
- **WHEN** code calls the Observer with a trigger key not present in `BUBBLE_LINES`
- **THEN** the Observer returns None
- **AND** no exception is raised

#### Scenario: Lines that exceed the 32-char ceiling are dropped defensively
- **WHEN** `BUBBLE_LINES` contains an entry longer than 32 characters
- **THEN** the Observer returns None for that key
- **AND** a warning is logged so the dict can be corrected
- **AND** the pet does NOT crash or hang

### Requirement: BUBBLE_LINES dictionary is the canonical voice contract

The Observer's vocabulary SHALL live in a single module-level constant
`BUBBLE_LINES: dict[str, str | list[str]]` in `src/indigo_pet/observer.py`.
Each value MAY be a single string or a list of strings; when a list, the
Observer SHALL pick uniformly at random per call. Editing this dictionary
SHALL be the sole code change required to evolve Squid's voice for any
already-wired trigger.

#### Scenario: Random pick from a list
- **WHEN** a trigger key maps to a list of N alternative lines
- **THEN** each call returns one entry chosen via `random.choice`
- **AND** repeated calls eventually exercise every alternative

#### Scenario: Single-string entry
- **WHEN** a trigger key maps to a single string (not a list)
- **THEN** every call for that trigger returns that exact string

### Requirement: Speech bubble renders ephemerally above the sprite

The frontend SHALL render the pending bubble as an absolutely-positioned DOM
element above the sprite layer, animate it in, hold it briefly, animate it
out, and then acknowledge the backend.

#### Scenario: New bubble appears, holds, fades, acknowledges
- **WHEN** the frontend poll observes `state.pending_bubble` is non-null AND no bubble is currently displayed
- **THEN** a `#bubble` element is rendered with the line text
- **AND** the element animates in (scale 0.7 → 1.0 over 150 ms)
- **AND** it holds at full opacity for ~2500 ms
- **AND** it fades out over 400 ms
- **AND** after fade-out completes, the frontend calls `api.clear_bubble()`

#### Scenario: New bubble during display swaps text (latest-wins)
- **WHEN** a bubble is mid-display AND a new non-null `pending_bubble` differs from the displayed text
- **THEN** the bubble text is swapped in place immediately
- **AND** the hold timer restarts (full 2500 ms for the new line)
- **AND** the prior text is not preserved or queued

#### Scenario: Bubble does NOT block user interaction
- **WHEN** a bubble is visible
- **THEN** `pointer-events: none` is set on the bubble element
- **AND** the user can still drag, click, double-click, or right-click Squid through the bubble area

#### Scenario: Bubble does NOT affect click-passthrough computations
- **WHEN** a bubble is visible
- **THEN** the passthrough controller's PIL alpha mask continues to use only sprite pixels
- **AND** the bubble's pixels are NOT treated as opaque for cursor-over checks

### Requirement: Mute toggle suppresses all observer output

The system SHALL support a persistent mute flag that, when set, suppresses
every observer-emitted bubble without affecting any other pet behavior
(mood detection, wandering, animations, interactions).

#### Scenario: Mute flag short-circuits emit paths
- **WHEN** the mute flag is True in `~/.indigo-pet/config.json`
- **AND** any trigger (state transition or interaction) fires
- **THEN** the Observer returns None
- **AND** `_pending_bubble` is NOT modified
- **AND** Squid continues to walk, sleep, react to pokes, etc. exactly as before

#### Scenario: Mute toggle via right-click menu persists across restart
- **WHEN** the user clicks "Mute Squid" in the right-click menu
- **THEN** the flag flips and is persisted to `~/.indigo-pet/config.json`
- **AND** the menu item shows the new state (checkbox or label) on next open
- **AND** the new state is honored on the next Squid restart

#### Scenario: Unmuting clears any in-flight pending bubble
- **WHEN** the mute flag flips from True to False
- **THEN** any non-null `_pending_bubble` is set to None
- **AND** no stale bubble queued during muted operation is displayed
