## Requirements

### Requirement: One sprite per state, transparent background

The project SHALL provide exactly seven sprite PNGs under
`src/indigo_pet/frontend/sprites/`, named `<state>.png` for each state in
`{idle, thinking, working, grooving, celebrating, sleeping, concerned}`.
Each sprite SHALL have a fully transparent background (alpha=0 on the
background pixels) so the sprite appears to float on the desktop.

#### Scenario: Sprites are present
- **WHEN** the app starts
- **THEN** all 7 expected sprite files exist
- **AND** the alpha channel of each PNG's corner pixels is 0

#### Scenario: A new background-removal pass is required
- **WHEN** new raw artwork (with cream background) is dropped into `sprites/`
- **THEN** the maintainer runs the bundled `tools/remove_bg.py` flood-fill script, which backs up originals to `sprites/_originals_with_bg/` and writes alpha-transparent versions in place

### Requirement: Display via a single `<img>` element with state attribute

The frontend SHALL render Indigo using one `<img id="pet" class="pet">`
element. The currently displayed state SHALL be communicated by setting
`data-state` on the element. Sprite swaps SHALL include a 150 ms opacity
cross-fade.

#### Scenario: State changes from working to grooving
- **WHEN** the JS poll detects a state change
- **THEN** the `<img>` opacity drops to 0 for ~150 ms, the `src` is updated to `sprites/grooving.png`, and `data-state` is set to `grooving`
- **AND** opacity returns to 1 on the new image's `onload`

### Requirement: Per-state CSS keyframe animation

Each state SHALL have its own `@keyframes` animation, attached via the
`.pet[data-state="<state>"]` selector. The character of the animation SHALL
match the emotional intent:

| State | Animation | Intent |
|---|---|---|
| `idle` | gentle scale 1.00 ↔ 1.04 over 3s | calm breathing |
| `thinking` | rotate −4° ↔ +4° over 2.4s | head tilt |
| `working` | translateX −1.5 ↔ +1.5 px every 180 ms | rapid typing shake |
| `grooving` | bounce up/down + rotate, 0.5s loop | dance |
| `celebrating` | jump −18 px + scale 1.08, 0.8s loop | hype |
| `sleeping` | slow scale 0.94 ↔ 1.02 over 4.5s | slow breathing |
| `concerned` | tremble ±1.5 px both axes at 250 ms | anxious |

#### Scenario: Watcher emits `working`
- **WHEN** state is `working`
- **THEN** the sprite is `working.png` AND a fast horizontal shake animation is active

#### Scenario: Watcher emits `sleeping`
- **WHEN** state is `sleeping`
- **THEN** the sprite is `sleeping.png` AND the slow-breathing scale animation is active

### Requirement: Frontend polls Python every 800 ms

The frontend SHALL poll `window.pywebview.api.get_state()` every 800 ms and
update the displayed state when it changes. If a forced state is set via
double-click, `get_state()` SHALL return the forced state until cleared.

#### Scenario: User has not forced a state
- **WHEN** the watcher emits a new state
- **THEN** the displayed sprite reflects the new state within 800 ms

#### Scenario: Forced state is active
- **WHEN** the user has double-clicked to force `grooving`
- **THEN** every poll returns `grooving` regardless of watcher output until Esc clears it

### Requirement: Subtle hint and debug overlays

The window SHALL show a small hint toast at the bottom briefly after startup
explaining the controls ("drag • R-click=corner • dbl-click=state"). Pressing
`Ctrl+D` SHALL toggle a debug overlay showing the current state name in the
top-left corner.

#### Scenario: User starts Indigo
- **WHEN** the window first appears
- **THEN** a small dark hint toast fades in for ~3.5 s with the controls reminder, then fades out

#### Scenario: User toggles debug
- **WHEN** the user presses Ctrl+D
- **THEN** a small monospace overlay shows the current state in the top-left
