# pet-animations Specification

## Purpose
Define Squid's visual state machine and sprite/animation contract: which
PNGs exist and what each is for, how the frontend swaps between them, the
per-state CSS keyframe animation, the multi-frame flag-wave, the polling
cadence that drives it all, and the hint/debug overlays.

## Requirements

### Requirement: Sprite inventory, transparent background

The project SHALL provide these PNGs under
`src/squid_pet/frontend/sprites/`, all with a fully transparent background
(alpha=0 on background pixels) so the sprite appears to float on the
desktop:

| Group | Files | Purpose |
|---|---|---|
| Backend states | `idle`, `thinking`, `working`, `grooving`, `celebrating`, `sleeping`, `concerned` | one per state the watcher can emit |
| Frontend-derived | `drowsy`, `stretch` | drowsy slump and the wake transition |
| Ambient life | `blink`, `look-left`, `look-right` | idle-routine expressions |
| Flag-wave | `attention_needed`, `attention_needed_1`..`_4` | `approval_needed`; the numbered frames cycle, the unnumbered one is the static fallback |
| Effects | `heart` | the LIKE gesture reaction |
| Menu bar | `idle_menubar`, `sleeping_menubar` | cropped to fill the status bar |

Files whose name begins with `_` are not sprites: `_originals_with_bg/`
holds pre-background-removal artwork and `_review/` holds candidates being
evaluated. Consumers that enumerate the directory SHALL skip them.

#### Scenario: Sprites are present
- **WHEN** the app starts
- **THEN** every sprite named in the table above exists
- **AND** the alpha channel of each PNG's corner pixels is 0

#### Scenario: A new background-removal pass is required
- **WHEN** new raw artwork (with cream background) is dropped into `sprites/`
- **THEN** the maintainer runs the bundled `tools/remove_bg.py` flood-fill script, which backs up originals to `sprites/_originals_with_bg/` and writes alpha-transparent versions in place

### Requirement: Display via a single `<img>` element with state attribute

The frontend SHALL render Squid using one `<img id="pet" class="pet">`
element. The currently displayed state SHALL be communicated by setting
`data-state` on the element. Sprite swaps SHALL include a 150 ms opacity
cross-fade.

The frontend SHALL validate the requested sprite name against its own
allow-list before swapping, and SHALL leave the current sprite in place for
an unknown name rather than locking to `idle.png`.

#### Scenario: State changes from working to grooving
- **WHEN** the JS poll detects a state change
- **THEN** the `<img>` opacity drops to 0 for ~150 ms, the `src` is updated to `sprites/grooving.png`, and `data-state` is set to `grooving`
- **AND** opacity returns to 1 on the new image's `onload`

#### Scenario: Backend state maps to a differently-named sprite
- **WHEN** the backend state is `approval_needed`
- **THEN** the displayed sprite is `attention_needed`
- **AND** `data-state` is `approval_needed` so the CSS animation still matches

### Requirement: Per-state CSS keyframe animation

Each state SHALL have its own `@keyframes` animation, attached via the
`.pet[data-state="<state>"]` selector. Every keyframe SHALL preserve the
centering `translate(-50%,-50%)`, or the sprite drifts out of the window.
The character of the animation SHALL match the emotional intent:

| State | Animation | Intent |
|---|---|---|
| `idle` | gentle scale 1.00 ↔ 1.04 over 3s | calm breathing |
| `thinking` | rotate −4° ↔ +4° over 2.4s | head tilt |
| `working` | translateX −1.5 ↔ +1.5 px every 180 ms | rapid typing shake |
| `grooving` | bounce up/down + rotate, 0.5s loop | dance |
| `celebrating` | jump −18 px + scale 1.08, 0.8s loop | hype |
| `sleeping` | slow scale 0.94 ↔ 1.02 over 4.5s | slow breathing |
| `concerned` | tremble ±1.5 px both axes at 250 ms | anxious |
| `approval_needed` | scale 1.00 ↔ 1.08, 0.6s loop (`attention-pop`) | in phase with the flag-wave |

Ambient motion layered on top of, not instead of, the state animation:
`idle-squish` (a one-shot squash-and-stretch flourish during idle),
`walk-bob-right` / `walk-bob-left` (while a wander sub-state is active), and
`look-around-right` / `look-around-left` (idle-routine glances).

#### Scenario: Watcher emits `working`
- **WHEN** state is `working`
- **THEN** the sprite is `working.png` AND a fast horizontal shake animation is active

#### Scenario: Watcher emits `sleeping`
- **WHEN** state is `sleeping`
- **THEN** the sprite is `sleeping.png` AND the slow-breathing scale animation is active

### Requirement: The flag-wave is JS-driven sprite cycling

While `approval_needed` is displayed, a JS timer SHALL cycle
`attention_needed_1` → `_2` → `_3` → `_4` at 150 ms per frame (600 ms
loop). The CSS `attention-pop` scale pulse SHALL share that 600 ms period
so the jump and the wave stay in phase. The timer SHALL be cleared when the
state leaves `approval_needed`.

The static `attention_needed.png` is the fallback if cycling does not run.
There SHALL be no glow ring; the flag carries the visual weight.

#### Scenario: A session starts waiting
- **WHEN** the displayed state becomes `approval_needed`
- **THEN** the four flag frames cycle at 150 ms each
- **AND** the sprite pulses 1.00 ↔ 1.08 once per full wave

#### Scenario: The wave is calmed
- **WHEN** the displayed state leaves `approval_needed`
- **THEN** the frame-cycling timer is cleared and no frames continue to swap

### Requirement: Frontend polls Python on an adaptive cadence

The frontend SHALL poll `window.pywebview.api.get_state()` and update the
displayed state when it changes. The interval SHALL be 80 ms while a fast
transition is in flight (sprint, wander step, wake) and 800 ms otherwise.

- **Why**: a fixed 800 ms was why "some turns looked slow" -- a rotation or
  edge change was not seen by the frontend until the next poll, up to
  800 ms later. Dropping to 80 ms only while something is actually moving
  keeps the idle CPU cost where it was.

If a forced state is set (menu Mood submenu or `force_state` file),
`get_state()` SHALL return the forced state until cleared.

#### Scenario: User has not forced a state
- **WHEN** the watcher emits a new state
- **THEN** the displayed sprite reflects the new state within 800 ms

#### Scenario: A sprint is running
- **WHEN** a fast transition is in flight
- **THEN** the poll interval is 80 ms so position and rotation stay smooth

#### Scenario: Forced state is active
- **WHEN** the user forces `grooving` from the menu's Mood submenu
- **THEN** every poll returns `grooving` regardless of watcher output until it is cleared

### Requirement: Subtle hint and debug overlays

The window SHALL show a small hint toast at the bottom briefly after startup
explaining the controls, and SHALL reuse the same toast for menu-driven
hints (corner name, pin state). Pressing `Ctrl+D` SHALL toggle a debug
overlay showing the current state name in the top-left corner.

The startup hint text SHALL describe the gestures that actually exist:
drag, poke, right-click for the menu, and double-click as the LIKE / take-
me-there gesture.

#### Scenario: User starts Squid
- **WHEN** the window first appears
- **THEN** a small dark hint toast fades in for ~3.5 s with the controls reminder, then fades out

#### Scenario: User toggles debug
- **WHEN** the user presses Ctrl+D
- **THEN** a small monospace overlay shows the current state in the top-left
