# state-detection Specification

## Purpose
Define how Squid infers what the user's AI coding agent (Claude Code or
Codex) is doing on the user's Mac and publishes that state to the
frontend. Covers process detection, idle time measurement, the Claude
Code hook signal channel, the priority cascade for one-state-per-tick
emission, JSON state file publication, per-detector opt-out, and the
drowsy/user-wake-override layer for prolonged idle periods.

Pink-2026-08-22/27: the project originally watched TPA (a
separate, internal CLI coding agent) as a general busy/thinking/
working/celebrating/grooving/concerned source, plus a TPA-driven
approval_needed/flag-wave signal. TPA was never actually
installed/run on this machine, so none of it ever fired anything in
practice, and it has been fully removed -- including
`tpa_running`, which no longer exists in the schema.

Pink-2026-09-01: the agent-facing signals that used to be CPU/mtime
heuristics are now Claude Code's own hook events. See the hook signal
channel Requirement below; `user-interactions` owns the gesture side of
`approval_needed`.

## Requirements
### Requirement: Detect Claude Code / Codex activity

The watcher SHALL identify whether Claude Code or Codex is currently
running by matching each process's `cmdline()[0]` basename against the
known CLI binary names (`claude`; `codex`/`codex-tui` excluding headless
subcommands). For each running agent, the watcher SHALL detect: a live
non-shell descendant process (`shell_active`), a project file under
`project_dirs` modified within `FILE_ACTIVE_WINDOW_SEC` (10 s)
(`file_active`), and a session transcript written within its
streaming-stale window (`streaming`).

`file_active` exists because in-process tool calls (Edit/Write,
`apply_patch`) never spawn a subprocess, so without it real editing work
was only ever visible through the coarser streaming signal and reported as
`thinking` rather than `working`.

#### Scenario: Claude Code is running with an active tool call
- **WHEN** a `claude` process exists AND has a live shell-child matching the
  known tool-name list
- **THEN** `claude_code_running` is `true` AND `shell_active` is `true`

#### Scenario: Neither agent is running
- **WHEN** no `claude` or `codex`/`codex-tui` process exists
- **THEN** `claude_code_running` and `codex_running` are both `false`

### Requirement: Claude Code hook events are the authoritative signal channel

Signals that Claude Code can report about itself SHALL be taken from its
official hook events rather than inferred from CPU, process, or file
mtime heuristics. `scripts/claude_pet_hook.py` SHALL be wired into
`~/.claude/settings.json` and SHALL translate hook events into per-session
flag files under `~/.squid-pet/`, one directory per signal, each file
named by `session_id`:

| Hook event | Flag directory | Meaning |
|---|---|---|
| `Notification` (`permission_prompt`) | `claude_awaiting_input/` | this session is blocked on you |
| `UserPromptSubmit` | writes `claude_turn_active/`, clears `claude_awaiting_input/` | you replied; a turn opened |
| `PostToolUse` | clears `claude_awaiting_input/` | tools ran, so any prompt was resolved |
| `Stop` | writes `claude_finished/`, clears `claude_turn_active/` + `claude_awaiting_input/` | Claude handed control back |
| `PreCompact` | `claude_recapping/` | Claude is summarizing its own context |
| `SessionEnd` | clears this session's flags | session gone |

The marker `scripts/squid_task_complete.py` SHALL write
`claude_task_complete/<session_id>` and is NOT a hook: it is written only
when Claude judges a whole task (not one turn) finished.

Flag file CONTENT SHALL never be read -- existence plus mtime is the
entire contract. Each directory SHALL be swept of entries older than its
stale window (2h for the hook-driven signals, 1h for `claude_turn_active`)
so a crashed session cannot leave a permanent flag.

`Notification` with `notification_type == idle_prompt` SHALL be ignored.

- **Why**: Pink-2026-08-27/09-01. Every heuristic replaced here produced a
  confirmed-live false positive: CPU-idle guessing fired the wave when
  nothing was waiting, and a busy->idle mtime edge fired "finished with
  claude!" mid-task. A hook fires exactly when the thing it names happens.

#### Scenario: A session asks for permission
- **WHEN** Claude Code fires `Notification` with `notification_type` of
  `permission_prompt` for session `S`
- **THEN** `~/.squid-pet/claude_awaiting_input/S` exists
- **AND** the next watcher tick reports `approval_needed`

#### Scenario: idle_prompt is not a wave
- **WHEN** Claude Code fires `Notification` with `notification_type` of
  `idle_prompt`
- **THEN** no flag is written AND Squid does not wave
- **Why**: idle_prompt means "you have been quiet", not "I am blocked".
  Waving for it made the wave mean nothing.

### Requirement: Stale awaiting-input flags self-heal

When at most ONE Claude Code process is alive and the watcher's own
independently-derived state is `working` or `thinking`, the watcher SHALL
delete any `claude_awaiting_input/` flag older than
`SELF_HEAL_MIN_FLAG_AGE_SEC` (3s), then re-scan the directory from disk
before evaluating the approval branch.

Self-heal SHALL NOT run when 2+ Claude Code processes are alive.

- **Why**: Pink-2026-08-27/08-30, both caught live. Claude can resume work
  without `UserPromptSubmit` ever firing (approval granted some other way,
  auto-mode proceeding), leaving the wave stuck on "your turn" while you
  watch it work -- so our own activity evidence is proof the wait
  resolved. But with two sessions, session B being busy says nothing about
  session A's genuine wait, and the coarse version silently ate A's wave.
  The hook payload carries no PID, so single-session is the only case it
  can be trusted in. The 3s floor guarantees a real flag is displayed at
  least once. The re-scan is not optional: individual unlinks can fail.

#### Scenario: One session, flag went stale
- **WHEN** exactly one `claude` process is alive
- **AND** an `claude_awaiting_input/` flag is 10 seconds old
- **AND** the cascade computes `working`
- **THEN** the flag is deleted AND state stays `working`

#### Scenario: Two sessions, one genuinely waiting
- **WHEN** two `claude` processes are alive
- **AND** session A has an awaiting-input flag while session B is working
- **THEN** A's flag is NOT deleted
- **AND** state is `approval_needed`

### Requirement: Measure macOS user idle time

The watcher SHALL read macOS HID idle time via CoreGraphics
(`CGEventSourceSecondsSinceLastEventType`), falling back to the
`ioreg -c IOHIDSystem` command (parsing `HIDIdleTime`, nanoseconds) when
the Quartz bindings are unavailable. No Accessibility permission SHALL be
required for either read, and a failed binding resolution SHALL be
remembered rather than retried every tick.

`idle_seconds` SHALL be published in `state.json` and reported by
`squid why` as a diagnostic only. Since 2026-09-04 it SHALL NOT decide
any state -- see "Emit exactly one state per tick".

#### Scenario: User is active
- **WHEN** the user has produced input within the last second
- **THEN** `idle_seconds` is `< 2.0`

#### Scenario: CoreGraphics is unavailable
- **WHEN** the Quartz bindings cannot be imported, or the call fails
- **THEN** the watcher falls back to the ioreg read
- **AND** returns `0.0` if that fails too -- a failed read SHALL read as
  "the user is right here", never as grounds to doze
- **Why**: Pink-2026-09-03. The ioreg read forks a subprocess every
  second. Measured inside the running daemon that fork cost 134 ms of CPU
  per tick -- half of Squid's entire tick -- because the process being
  forked carries Cocoa, WebKit and 11 threads; a small harness had
  suggested 36 ms. CoreGraphics answers the same question in 0.0014 ms,
  and sampled side by side the two agreed to within the time ioreg itself
  takes to run.

#### Scenario: User has stepped away
- **WHEN** there has been no mouse or keyboard input for 5+ minutes
- **THEN** `idle_seconds >= 300.0`

### Requirement: Emit exactly one state per tick using priority cascade

The watcher SHALL emit exactly one state each tick, selected by a priority
cascade. Higher-priority conditions SHALL override lower-priority ones.
The order is:

1. `sleeping` (agents quiet past `IDLE_THRESHOLD_SEC`; suppressed while
   an agent is actively busy or a celebration is pending -- see below)
2. `celebrating` (sticky hold)
3. `grooving` (Claude Code's `Stop` flag, or any other detector's groove)
4. `thinking` / "recapping" (Claude Code's `PreCompact` flag)
5. `working` / `thinking` (Claude Code / Codex rich cascade)
6. `thinking` (generic non-agent-detector busy fallback)
7. `idle` (default fallback)

Two overrides SHALL then be layered on top of whatever the cascade picked,
in this order:

8. `approval_needed` -- overrides every cascade result (see
   `user-interactions` for the gesture contract)
9. `~/.squid-pet/force_state` -- a debug/demo file override that wins over
   everything including `approval_needed`

#### Scenario: The agents have been quiet for IDLE_THRESHOLD_SEC
- **WHEN** the state machine has not left an active state (`thinking`,
  `working`, `grooving`, `celebrating`, `concerned`) for
  `IDLE_THRESHOLD_SEC` (315 s) -- the same clock published as
  `agent_idle_seconds`
- **AND** Claude Code's/Codex's merged busy signal (shell_active OR
  file_active OR streaming) is false
- **AND** no celebration is pending
- **THEN** state is `sleeping` regardless of any other lower-priority signal
- **AND** this holds whether or not the user is at the keyboard

#### Scenario: The user is away but the agents only just stopped
- **WHEN** the quiet clock is below `IDLE_THRESHOLD_SEC`
- **THEN** state is NOT `sleeping`, however long the user has been away
- **Why**: Pink-2026-09-04. Sleeping used to be gated on macOS HID idle:
  five minutes without a keystroke. That is the wrong question for a pet
  whose job is watching agents -- sitting at the keyboard writing docs
  while nothing had run for an hour kept her wide awake, and a run
  finishing thirty seconds before the user left for lunch put her to
  sleep. The frontend had staged drowsy off the agents' own quiet clock
  since well before this; the backend was the inconsistent half.

#### Scenario: The agents are quiet but one is actively busy
- **WHEN** the quiet clock has passed `IDLE_THRESHOLD_SEC`
- **AND** Claude Code's or Codex's merged busy signal (shell_active OR
  file_active OR streaming) is true
- **THEN** state is NOT `sleeping` -- the cascade falls through to
  evaluate `working`/`thinking` normally
- **Why**: Pink-2026-08-27g. Sleeping used to override everything
  unconditionally, including active agent work -- confirmed live as a
  user-visible bug: squid showed sleeping while Claude Code was actively
  working in the background. The quiet clock only resets once a tick has
  been *classified* active, so a freshly-busy agent is still one tick
  away from resetting it; this guard covers that gap.

#### Scenario: Claude marks the whole task complete
- **WHEN** `~/.squid-pet/claude_task_complete/<session_id>` was written
  within `celebrate_hold_sec` (default 20s, hot-reloadable)
- **THEN** state is `celebrating`
- **AND** a per-turn latch records that this turn already celebrated
- **Why**: Pink-2026-08-30. `Stop` fires identically on EVERY turn, mid-task
  or truly final (confirmed live: one conversation rewrote the Stop flag a
  dozen+ times), so neither raw freshness nor a settle-window timer on it
  can separate "made progress" from "actually done" -- ordinary reply
  latency covers both cases identically, so a timer just delays the same
  false positive. Only Claude itself knows, so only Claude writes it.

#### Scenario: Claude Code's Stop hook fires
- **WHEN** `~/.squid-pet/claude_finished/<session_id>` is fresh
- **AND** no `shell_active`/`file_active` evidence has appeared since
- **AND** this turn has not already celebrated
- **THEN** state is `grooving`, never `celebrating`, no matter how much
  time has passed
- **Why**: grooving is the lighter per-turn "still making progress" beat.
  Promotion to celebrating requires the explicit marker above, never
  elapsed time.

#### Scenario: Stop fires on a turn that already celebrated
- **WHEN** the turn's celebrate latch is set
- **AND** the `Stop` flag then becomes fresh
- **THEN** state is NOT demoted to `grooving`
- **Why**: observed live as CELEBRATING 16:00:25-16:00:45 (a commit)
  followed immediately by GROOVING at 16:00:45 (the Stop) for one single
  response -- a demotion at the exact moment the work finished.

#### Scenario: A non-agent detector celebrates mid-turn
- **WHEN** another detector (e.g. GitDetector seeing a fresh commit)
  reports celebrating
- **AND** a turn is currently in flight (`claude_turn_active/` is fresh)
- **AND** neither `claude_task_complete` nor Codex's own edge fired
- **THEN** the celebrate is HELD, not emitted
- **AND** it is released when the turn closes, holding for
  `celebrate_hold_sec` from that moment
- **Why**: Pink-2026-09-01. A commit lands well before the turn ends;
  Pink saw her celebrate 20s before the reply appeared, then groove the
  moment the answer arrived. `claude_task_complete` and Codex's edge are
  NOT deferred -- those already mean "the work is done", not "a file
  changed".

#### Scenario: Claude Code is compacting its context
- **WHEN** `~/.squid-pet/claude_recapping/<session_id>` is fresh
  (`PreCompact` fired, `PostCompact` has not cleared it)
- **THEN** state is `thinking` with reason `claude recapping`
- **AND** this wins over the working/thinking cascade below even if stale
  file-write evidence from just before the compact is still fresh
- **Why**: no tool calls happen during a compaction, so leftover evidence
  would otherwise report `working` for something that is not agent work.

#### Scenario: Claude Code or Codex is actively running a tool
- **WHEN** `shell_active` OR `file_active` is true for Claude Code or Codex
- **THEN** state is `working`
- **AND** a working hold is armed for `working_hold_sec` (default 25s)

#### Scenario: Both agents are running at once
- **WHEN** both Claude Code and Codex are alive and either reports hard
  working evidence (shell or file)
- **THEN** state is `working`
- **AND** `state_reason` names whichever detector's signal fired, by
  priority order rather than exclusivity

#### Scenario: Sticky working across an LLM-generation gap
- **WHEN** the working hold is still open
- **AND** the merged streaming signal is true
- **THEN** state stays `working` rather than dropping to `thinking`
- **Why**: post-e2e-polish 2026-06-27 Fix 7. Between two tool calls there
  is a gap with no shell child and no file write; without the hold she
  flickered working -> thinking -> working within one task.

#### Scenario: Claude Code or Codex is generating (no tool call in flight)
- **WHEN** neither agent's `shell_active`/`file_active` is true, but its
  transcript was written within the streaming-stale window
- **THEN** state is `thinking`

#### Scenario: A turn is open but nothing has been written recently
- **WHEN** an agent is running
- **AND** no working, sticky-working, or streaming evidence matches
- **AND** `claude_turn_active/` is fresh
- **THEN** state is `thinking` with reason `claude turn in flight`
- **Why**: a long reasoning stretch produces no transcript write at all,
  so the streaming signal goes stale and she used to fall through to
  `idle` mid-thought. Ranked last so it only ever decides what would
  otherwise be idle.

#### Scenario: No signals match
- **WHEN** none of the above conditions are met
- **THEN** state is `idle`

#### Scenario: Force-state override for testing and demos
- **WHEN** `~/.squid-pet/force_state` exists and holds a non-empty state name
- **THEN** that name is the emitted state, overriding every other branch
  including `approval_needed`
- **AND** deleting the file (or writing empty) resumes normal computation
- **Note**: this is the only way to reach `concerned`, which has no
  detector -- see the non-goal below.

### Requirement: Per-detector opt-out via settings

Each detector SHALL carry an `enabled` flag sourced from the `triggers`
subsection of `~/.squid-pet/settings.json` (`triggers.claude_code`,
`triggers.codex`, ...), defaulting to `true` when the key is absent. A
disabled detector SHALL NOT be instantiated: it contributes no busy,
celebrating, or grooving signal, and its `*_running` field reports `false`.
Unknown keys in `triggers` SHALL be ignored, not error.

The watcher SHALL hot-reload the detector list when `settings.json`'s
mtime changes, checked once per tick via a single `stat()`. A
StateMachine constructed with an explicit detector list (tests, custom
embeddings) SHALL keep that list immutable and skip reloading.

#### Scenario: Codex is disabled while Squid is running
- **WHEN** the user sets Codex's `enabled` to `false` in `settings.json`
- **THEN** the next tick reloads detectors without a restart
- **AND** `codex_running` is `false` even while a `codex` process is alive

### Requirement: `concerned` has no detector -- documented non-goal

No detector SHALL implement `concerned`. It had no Claude Code or Codex
equivalent when TPA was removed, and inventing one would mean
guessing at failure from signals that do not carry it. The sprite,
animation, and alpha mask remain so `force_state` can still reach it for
testing and demos.

#### Scenario: Nothing naturally emits concerned
- **WHEN** any combination of real agent signals is present
- **THEN** the emitted state is never `concerned`

### Requirement: Publish state to JSON file

The watcher SHALL atomically write the current `PetState` -- `state`,
`sub_state`, `idle_seconds`, `agent_idle_seconds`, `claude_code_running`,
`codex_running`, `timestamp`, `message`, `concern_reason`,
`concern_severity`, `state_reason` -- to `~/.squid-pet/state.json` once
per tick using a `.tmp` + rename pattern.

`agent_idle_seconds` is generic despite its name: seconds since the state
machine last left an active state. The `cp` prefix predates Claude Code
support and is kept because the frontend's drowsy-entry logic reads the
field every tick.

#### Scenario: State changes
- **WHEN** a new state is computed
- **THEN** the file `~/.squid-pet/state.json` reflects the new state within one poll interval (1 second)

#### Scenario: File is being read by another process
- **WHEN** the writer flushes a new state while a reader is open
- **THEN** the reader observes either the old or the new state in full (never a partial write), because the writer uses `tmp.replace(STATE_FILE)`

### Requirement: Run as a daemon thread alongside the window

The watcher SHALL run as a daemon thread inside the main `squid_pet` process
with a configurable poll interval (default 1.0 second), and SHALL stop cleanly
when the window's `closing` event fires.

#### Scenario: Window is closed
- **WHEN** the user closes the pet window
- **THEN** the watcher thread observes the stop event within one poll interval and exits cleanly

### Requirement: Drowsy state for prolonged agent idle

An additional emotional state, `drowsy`, SHALL be added to the state model. The
drowsy state SHALL be entered by the frontend (not the backend state
machine) when:
- The backend state has been `idle` continuously, AND
- `agent_idle_seconds` (time since the state machine last left an active
  state) exceeds 300 seconds (raised from 120s on 2026-08-17 so the
  idle-routine wander cycle -- which stays at its original ~91s-average
  pacing -- gets to repeat 2-3+ times before drowsy, rather than cutting
  one lengthened lap off mid-way), AND
- No `user_wake_override` is currently active

The frontend SHALL play a slump animation when entering drowsy, and the
drowsy sprite SHALL persist until the backend reports `sleeping`, the
agent resumes activity, a wake gesture fires, or the periodic auto-wake
fires (see below).

Drowsy is intentionally a frontend-driven derivation rather than a backend
state to avoid coupling the watcher's state machine to user-gesture timing.

Drowsy is the SHORT stage before sleep: the backend takes over at
`IDLE_THRESHOLD_SEC` (315 s), so the slump plays for roughly fifteen
seconds. Two constants therefore have to hold, and
`tests/test_window_constants_agree.py` pins both:

- `watcher.IDLE_THRESHOLD_SEC` > the frontend's `DROWSY_IDLE_SEC`. At or
  below it the backend reports `sleeping` first, the frontend's mood
  machine never advances (it only steps while the backend state is
  `idle` or `sleeping`), and the slump is unreachable.
- the frontend's `SLEEPING_IDLE_SEC` == `watcher.IDLE_THRESHOLD_SEC`. An
  active mood BLOCKS state-driven sprite swaps, so a later mark in the
  frontend would strand her on the drowsy sprite while `state.json`
  already said `sleeping`.

#### Scenario: Enter drowsy after prolonged idle
- **WHEN** the backend state is `idle`
- **AND** agent_idle_seconds is 301 or greater
- **AND** user_wake_remaining is 0
- **THEN** the frontend plays the slump animation
- **AND** the displayed sprite is the drowsy sprite

#### Scenario: Drowsy gives way to sleep
- **WHEN** the frontend mood is `drowsy`
- **AND** agent_idle_seconds reaches `SLEEPING_IDLE_SEC` (315 s), at which
  point the backend state is `sleeping`
- **THEN** the frontend mood becomes `sleeping` and the sprite swaps
- **Why**: Pink-2026-09-04. When sleeping moved onto the agents' quiet
  clock the two layers landed on the same axis for the first time. The
  frontend gate had read `state === "idle"` alone, so the backend
  crossing over froze the mood at drowsy -- and because an active mood
  blocks state-driven swaps, she would have sat on the drowsy sprite for
  the rest of the quiet stretch.

#### Scenario: Drowsy reverts when the agent becomes active
- **WHEN** Squid is in the drowsy state
- **AND** Claude Code or Codex starts a new tool call or generation
- **THEN** the backend state transitions to `thinking` or `working`
- **AND** the frontend swaps to the corresponding sprite

### Requirement: User-wake override channel suppresses drowsy

`PetApi` SHALL maintain a `_user_wake_until: float` epoch timestamp. The
`get_state()` response SHALL include a derived `user_wake_remaining`
field equal to `max(0, _user_wake_until - now)` in seconds.

The frontend SHALL treat `user_wake_remaining > 0` as a signal to:
- Suppress drowsy entry from the idle state
- Fire a wake-stretch transition if currently drowsy

The override SHALL be set by poke and swing-to-wake gestures. The override
SHALL NOT modify `agent_idle_seconds` (that field continues to reflect actual
state-machine activity).

`PetApi` SHALL also maintain a `_wake_trigger_seq: int` counter incremented
on every wake event. The frontend MAY use this counter to detect
"new wake event since last poll" without needing to compare timestamps.

#### Scenario: Poke during drowsy
- **WHEN** Squid is in the drowsy state
- **AND** the user pokes her
- **THEN** _user_wake_until is set to now + 60 seconds
- **AND** the next get_state response returns user_wake_remaining near 60
- **AND** the frontend fires the wake-stretch transition
- **AND** Squid does NOT re-enter drowsy for the next 60 seconds even if
       agent_idle_seconds remains above 300

#### Scenario: Override expires after 60 seconds
- **WHEN** 60 seconds have elapsed since the last wake gesture
- **AND** agent_idle_seconds is still above 300
- **AND** no new gesture has fired
- **THEN** user_wake_remaining returns 0
- **AND** the frontend re-enters drowsy on the next poll

### Requirement: Periodic auto-wake from prolonged drowsy or sleeping

While the frontend mood is `drowsy` or `sleeping`, `get_state()` SHALL
force a wake cycle every `PERIODIC_WAKE_CADENCE_SEC` (900s / 15 min),
holding the wake override for `PERIODIC_WAKE_AWAKE_SEC` (180s / 3 min).
Any wake -- gesture or periodic -- SHALL reset the periodic clock so the
next one is a full cadence away.

- **Why**: without it she stays asleep forever absent real agent activity,
  which reads as "the app died" rather than "nothing is happening".

#### Scenario: Fifteen minutes asleep with no agent activity
- **WHEN** Squid has been drowsy or sleeping for 15 minutes
- **AND** no gesture and no agent activity has occurred
- **THEN** a wake cycle fires (stretch transition + ~3 min awake window)
- **AND** she returns to drowsy/sleeping after that window

#### Scenario: A poke resets the periodic clock
- **WHEN** the user pokes her 14 minutes into a sleep
- **THEN** the next periodic wake is scheduled 15 minutes from the poke,
  not one minute later
