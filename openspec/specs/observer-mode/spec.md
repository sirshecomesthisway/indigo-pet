# observer-mode Specification

## Purpose
Define Squid's voice: the Observer, a passive comment layer that turns
state changes, user gestures, mood changes, and the ambient idle beat into
one short line at a time, rendered as a speech bubble above her sprite.
Covers the `BUBBLE_LINES` vocabulary, the optional LLM enrichment layer
that can replace a line with a context-aware one, and the mute toggle.

## Requirements
### Requirement: Observer subsystem publishes one-line reactions in response to triggers

The system SHALL include an Observer component that consumes state-change and
interaction events and publishes a single short reaction string per event.
The Observer is a passive comment layer: it never modifies pet state, never
intercepts the agent (Claude Code or Codex), and never produces multi-line
or paragraph output.

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
- **WHEN** the user interacts with Squid in a registered way (single-click poke, double-click LIKE, swing-to-wake shake, right-click → Sprint, mood-change notify)
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
`BUBBLE_LINES: dict[str, str | list[str]]` in `src/squid_pet/observer.py`.
Each value MAY be a single string or a list of strings; when a list, the
Observer SHALL pick uniformly at random per call. Editing this dictionary
SHALL be the sole code change required to evolve Squid's baseline voice for
any already-wired trigger. The optional LLM enrichment layer below may
replace a published line at runtime, but never bypasses this dict: the
`BUBBLE_LINES` pick is always what is published first.

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

### Requirement: Non-transition trigger channels

Beyond state transitions and gestures, the Observer SHALL expose:

- `on_mood_change(old, new)` — the frontend mood layer (drowsy, stretch,
  sleeping). It SHALL fire only on the ENTRY edge, never per tick, and
  SHALL stay silent on `sleeping` so the sprite speaks for itself.
  `stretch` maps to the `waking` pool.
- `on_idle_chatter()` — the ambient idle beat driven by
  `RoutineController` every ~26-34 s. It SHALL return an
  `idle_idea_prompt` line `IDEA_PROMPT_CHANCE` (0.2) of the time and an
  ordinary `idle_chatter` line otherwise.

`idle_idea_prompt` SHALL be a separate pool rather than more
`idle_chatter` entries.
- **Why**: Pink-2026-09-01. The rate is the entire difference between a pet
  with her own ideas and one that pesters you. At the 26-34 s chatter
  cadence, a squid asking "what should we build?" stops reading as company
  and starts reading as nagging; blended in at 1-in-5 it lands once or
  twice per awake-idle stretch.

#### Scenario: Drowsy entry speaks once
- **WHEN** the frontend mood changes idle → drowsy
- **THEN** one drowsy line is published
- **AND** no further line is published while she stays drowsy

#### Scenario: Falling asleep is silent
- **WHEN** the frontend mood changes to `sleeping`
- **THEN** the Observer returns None

### Requirement: Optional LLM enrichment may replace a generated line

The Observer MAY hold an `LLMClient`. When one is available AND the
`llm_bubbles` config flag is currently true, a dispatched trigger SHALL
publish its `BUBBLE_LINES` pick immediately and then ask the model, on a
background daemon thread, for a context-aware alternative. If a reply
arrives and respects the `MAX_BUBBLE_CHARS` (32) cap, it SHALL overwrite
the pending bubble via the publish callback; otherwise the original line
stands.

The flag SHALL be read through a callback on EVERY dispatch, not captured
at construction, so the menu toggle takes effect without restarting Squid.

The client SHALL be constructed regardless of the current flag value
(construction is cheap and does no network I/O), and reports itself
unavailable when no credential loads. Enrichment SHALL be bounded by a
per-call timeout (`HTTP_TIMEOUT_SEC`, 10 s), a per-process minimum gap
between calls (`MIN_CALL_GAP_SEC`, 5 s), and a daily call cap
(`llm_bubbles_daily_cap`, default 500, persisted in
`~/.squid-pet/llm_usage.json` and reset on date rollover). The cap SHALL be
enforced silently: over it, `ask()` returns None and the rule-based line
stands, with no user-visible error.

The credential SHALL be read from the user's OWN
`~/.tpa/puppy.cfg` at runtime, with no embedded token and no
fallback path that could reuse one person's token for another's session.
- **Note**: this is the one surviving TPA dependency. Everything
  else about TPA was removed on 2026-08-27; the LLM bubbles feature
  still borrows its config file and its internal-hosted model endpoints, so
  LLM bubbles are unavailable to anyone without that file.

Enrichment SHALL never block the bubble: a slow, failed, or over-length
reply is simply dropped.

#### Scenario: LLM is disabled mid-session
- **WHEN** the user unchecks "LLM bubbles" in the menu
- **THEN** the next trigger publishes only its `BUBBLE_LINES` line
- **AND** no restart is required

#### Scenario: LLM reply is too long
- **WHEN** the model returns more than 32 characters
- **THEN** the reply is dropped with a logged warning
- **AND** the originally-published line remains displayed

#### Scenario: LLM is unavailable
- **WHEN** no client is configured or it reports unavailable
- **THEN** the Observer behaves exactly as the `BUBBLE_LINES`-only contract
  describes

### Requirement: Mute toggle suppresses all observer output

The system SHALL support a persistent mute flag that, when set, suppresses
every observer-emitted bubble without affecting any other pet behavior
(mood detection, wandering, animations, interactions).

#### Scenario: Mute flag short-circuits emit paths
- **WHEN** the mute flag is True in `~/.squid-pet/config.json`
- **AND** any trigger (state transition or interaction) fires
- **THEN** the Observer returns None
- **AND** `_pending_bubble` is NOT modified
- **AND** Squid continues to walk, sleep, react to pokes, etc. exactly as before

#### Scenario: Mute toggle via right-click menu persists across restart
- **WHEN** the user clicks "Mute Squid" in the right-click menu
- **THEN** the flag flips and is persisted to `~/.squid-pet/config.json`
- **AND** the menu item shows the new state (checkbox or label) on next open
- **AND** the new state is honored on the next Squid restart

#### Scenario: Unmuting clears any in-flight pending bubble
- **WHEN** the mute flag flips from True to False
- **THEN** any non-null `_pending_bubble` is set to None
- **AND** no stale bubble queued during muted operation is displayed

