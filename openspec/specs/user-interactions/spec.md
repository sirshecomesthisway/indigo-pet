# user-interactions Specification

## Purpose
Define every way the user touches Squid and what she does back: the poke
and LIKE gestures, swing-to-wake during a drag, the right-click menu and
menu-bar item, and the `approval_needed` flag-wave -- the one state that
asks the user for something, and the gestures that answer it.

## Requirements
### Requirement: Poke gesture wakes Squid temporarily

A single-click on an opaque pixel of Squid SHALL be classified as a "poke"
gesture when both: the mousedown-to-mouseup duration is under 250 milliseconds,
AND the cursor movement during that interval is under 6 pixels. The poke
SHALL be deferred by 260 milliseconds before firing to allow disambiguation
from a double-click; a double-click within that window cancels the pending poke.

A confirmed poke SHALL:
- Set a user-wake override that suppresses drowsy entry for 60 seconds.
- Bump a wake-trigger sequence number consumed by the frontend.
- Publish the observer's `poke` bubble line.
- Clear any forced-state override.

The poke SHALL NOT show a "boop!" hint pill.
- **Why**: 2026-06-13, from Pink's screenshot showing the hint pill and the
  observer bubble stacked on top of each other saying the same thing. The
  bubble owns the poke reaction now; one reaction per gesture.

#### Scenario: Single click while drowsy
- **WHEN** Squid is in the drowsy state
- **AND** the user single-clicks an opaque pixel of her sprite
- **THEN** 260 milliseconds later, the wake override is set to now + 60 seconds
- **AND** the observer's poke bubble appears
- **AND** the frontend plays the wake-stretch transition and returns to the idle sprite
- **AND** Squid stays awake for the next 60 seconds even if the agent remains idle

#### Scenario: Double-click supersedes pending poke
- **WHEN** the user double-clicks Squid within 260 milliseconds of the first click
- **THEN** the pending single-click poke is cancelled
- **AND** the dblclick LIKE gesture fires instead (see next requirement)

### Requirement: Double-click is the LIKE gesture (heart + wake + go there)

A double-click on Squid SHALL be treated as a LIKE gesture distinct from a
single-click poke. The dblclick handler SHALL, in order:
- Cancel any pending single-click poke timer.
- Invoke `api.poke()` (which sets the 60 s wake override).
- If the current state is `approval_needed`, invoke
  `api.acknowledge_approval()` and display the bubble returned on that RPC
  response directly, rather than waiting for the next poll to pick it up.
- Invoke `api.take_me_there()` unconditionally; the backend decides whether
  there is anywhere to go.
- Spawn a single blinking heart above Squid's sprite (see `pet-reactions`).

Any failure in the acknowledge or take-me-there steps SHALL be swallowed so
the poke and heart still happen.

The previous foundational behavior of dblclick (cycling forced state through
the sprite states for debug) is REMOVED. State cycling lives in the
right-click menu's mood submenu.

#### Scenario: Double-click on drowsy Squid
- **WHEN** Squid is in the drowsy state
- **AND** the user double-clicks her
- **THEN** the wake override is set to now + 60 seconds
- **AND** one heart blinks above her head
- **AND** Squid plays the wake-stretch transition back to the idle sprite
- **AND** `take_me_there()` returns `resting` and nothing is raised

#### Scenario: Double-click on awake Squid
- **WHEN** Squid is in any non-drowsy state
- **AND** the user double-clicks her
- **THEN** the wake override is set (no visible state change since she is already awake)
- **AND** one heart blinks above her head
- **AND** the sprite state does NOT cycle (no force_state invocation)

#### Scenario: Ack bubble does not lose a race with the watcher
- **WHEN** a dblclick calms a wave
- **AND** the watcher's next tick computes `working` before the frontend's
  next poll
- **THEN** the ack bubble is still shown, because it came back on the RPC
  response rather than through `pending_bubble`

### Requirement: The approval_needed flag-wave asks for the user

When Claude Code reports a session is blocked on the user (see
`state-detection`'s hook signal channel), Squid SHALL enter
`approval_needed`, which overrides every other computed state.

While waving, the system SHALL:
- Cycle the four `attention_needed_1..4` sprite frames at 150 ms each
  (600 ms loop) with a synchronized `attention-pop` scale pulse.
- Fire ONE macOS notification banner per wave cycle -- never once per tick
  -- titled `Squid` with body `<agent>: <approval_alert_text>` (default
  text `your turn`, default sound `Glass`, both from `config.json`).
- Prefer `terminal-notifier` with `-activate <bundle-id>` over
  `osascript -e 'display notification'` when it is installed.

The notification banner SHALL name the agent that is actually waiting.
- **Why**: Pink-2026-08-26 found the name hardcoded to a single agent, so
  a Claude Code session produced a banner announcing a different agent
  entirely -- one that was never running.

Config values interpolated into AppleScript SHALL be escaped before use.

#### Scenario: Notification fires once, not every tick
- **WHEN** a session's awaiting-input flag stays present for 30 ticks
- **THEN** exactly one banner is fired
- **AND** the latch resets only once no session is awaiting, so the next
  genuine wait gets a fresh ping

#### Scenario: Clicking Show opens the right terminal
- **WHEN** `terminal-notifier` is installed
- **AND** the user clicks Show on the banner
- **THEN** the terminal app hosting that Claude Code session is raised
- **Why**: plain `osascript` notifications have no click action -- Show
  foregrounded whatever ran the AppleScript, which macOS attributes to
  Script Editor, opening an empty window. Confirmed live.

### Requirement: Acknowledging a wave calms it without erasing the signal

`acknowledge_approval()` SHALL be a no-op returning `not-waving` unless the
current state is `approval_needed`. When it is waving, it SHALL:
- Publish the observer's `like` bubble line AND return it on the response.
- Schedule the actual calm on a background timer
  `ACKNOWLEDGE_DISMISS_DELAY_SEC` (1.0 s) later, so the wave visibly
  continues for a beat after the bubble appears.
- Calm by SNOOZING the session (backdating its tracked birth time past
  `_CLAUDE_SESSION_SNOOZE_SEC`, 120 s), never by deleting the
  awaiting-input flag file.

- **Why**: deleting the flag would erase the ground truth that `squid why`
  and the menu's waving-count read to tell "seen and deferred" apart from
  "nothing pending". Snoozing re-arms naturally: replying removes the flag
  and evicts the entry, and the session's next genuine wait gets a fresh
  birth time. The 1 s delay is because firing the bubble and the wave-stop
  in the same instant made them compete for attention; settling a beat
  later reads as her noticing.

#### Scenario: Dblclick while waving
- **WHEN** state is `approval_needed`
- **AND** the user double-clicks her
- **THEN** the like bubble appears immediately
- **AND** she keeps waving for ~1 second, then settles
- **AND** the awaiting-input flag file still exists on disk

#### Scenario: A calmed session asks again later
- **WHEN** a snoozed session's flag disappears (the user replied) and later
  reappears for new work
- **THEN** Squid waves again

### Requirement: Double-click takes the user to the responsible window

`take_me_there()` SHALL raise the terminal window responsible for whatever
Squid is currently showing, resolving session -> project dir -> process ->
tty -> terminal tab. Each active state maps to the flag directory naming
the session that caused it:

| State | Signal directory |
|---|---|
| `working`, `thinking` | `claude_turn_active/` |
| `approval_needed` | `claude_awaiting_input/` |
| `celebrating` | `claude_task_complete/` |
| `grooving` | `claude_finished/` |

Where several sessions have flags, the FRESHEST SHALL win -- whichever most
recently caused the state is the one being reacted to.

Resting states (`idle`, `drowsy`, `sleeping`) SHALL be absent from the map
by design and SHALL return status `resting`.
- **Why**: Pink-2026-09-01, "except idle/drowsy/sleeping, because they mean
  she's doing nothing". With nothing running there is no window a
  double-click could honestly raise, and yanking one forward would be worse
  than doing nothing.

When the session's flag has aged out of its freshness window but the sprite
still shows the state, the resolver SHALL fall back to any live Claude Code
process's tty rather than giving up.

#### Scenario: Double-click a waving Squid
- **WHEN** state is `approval_needed` and a session flag names session `S`
- **THEN** the terminal tab running `S` is raised to the front

#### Scenario: Double-click a sleeping Squid
- **WHEN** state is `sleeping`
- **THEN** `take_me_there()` returns `resting`
- **AND** the gesture is just a poke and a heart

### Requirement: Swing-to-wake gesture during drag

A vigorous up-down shaking motion during a drag SHALL be detected by the
native drag loop and treated as a wake gesture equivalent to a poke. The
algorithm SHALL count y-direction reversals of at least 8 pixels each within
a sliding 0.6-second window, and fire once when the count reaches 4 reversals
(equivalent to two complete up-down swings). The detection SHALL fire at
most once per drag.

A confirmed swing SHALL set the 60 s wake override and publish the
observer's `shake` bubble line. It SHALL NOT show a "wheee!" hint pill
(deduped 2026-06-13, same reason as the poke's "boop!").

#### Scenario: Shake Squid awake mid-drag
- **WHEN** the user is dragging Squid
- **AND** the user moves the cursor up-down-up-down with at least 8 pixel deltas
       within 0.6 seconds
- **THEN** the wake override is set to now + 60 seconds
- **AND** the observer's shake bubble appears
- **AND** subsequent reversals within the same drag do NOT re-fire

#### Scenario: Gentle drag does not trigger swing-wake
- **WHEN** the user drags Squid smoothly to a new screen position
- **THEN** no reversal count accumulates beyond the threshold
- **AND** no wake override is set
- **AND** no bubble appears

### Requirement: Right-click opens a menu independent of click-passthrough

A right-click anywhere on Squid SHALL open a native context menu, even when
the sprite is in a click-passthrough region under the cursor. Detection
SHALL use a global NSEvent monitor (not WKWebView contextmenu events) so the
menu always opens. If `show_context_menu` is unavailable, the frontend MAY
fall back to the legacy corner-cycle behavior.

The menu SHALL include at minimum:
- Calm Squid (dismiss wave) — promoted to the top level while waving,
  otherwise inside the Bubbles submenu; disabled when nothing is waving
- Hide / Show Squid
- Position ▸ four corner snaps, Pin, Recenter, Stroll: anywhere / edges only
- Bubbles ▸ Mute, LLM bubbles, Approval alert, Calm Squid
- Pause Squid ▸ 5 / 15 / 30 / 60 minutes, Resume now
- Mood ▸ force each state, Clear override — this is where debug state
  cycling lives now that dblclick no longer cycles
- Sprint the perimeter! — triggers `WanderController.sprint_perimeter()`
- Open Squid log
- Restart Squid
- Quit Squid... — confirms via an alert whose default button is Cancel

#### Scenario: Right-click on transparent edge of sprite
- **WHEN** the user right-clicks within Squid's window bounds but in a
       click-passthrough (alpha=0) pixel
- **THEN** the menu still opens
- **AND** the menu is positioned at the click location

#### Scenario: Sprint via menu
- **WHEN** the user selects "Sprint the perimeter!" from the menu
- **THEN** Squid begins a clockwise perimeter sprint starting with a stretch
       transition and ending at the bottom edge

#### Scenario: Quit is guarded
- **WHEN** the user selects "Quit Squid..."
- **THEN** an alert appears whose default (return-key) button is Cancel

### Requirement: Menu-bar status item mirrors the menu

Squid SHALL install an `NSStatusItem` in the macOS menu bar whose icon is
a menubar-tinted sprite -- `idle_menubar.png` while the window is visible,
`sleeping_menubar.png` while it is hidden -- falling back to an emoji glyph
if sprite loading fails. Clicking it with either button SHALL open the same
menu as right-click, rebuilt on open so its checkboxes are current.

The icon SHALL NOT change on mute: the menu label already carries that
state, and a third sprite swap in the menu bar is visually noisy.

#### Scenario: Squid is hidden
- **WHEN** the user hides Squid from the menu
- **THEN** the window is not visible
- **AND** the menu-bar item remains, so she can be brought back
