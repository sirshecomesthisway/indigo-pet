# state-detection delta

## ADDED Requirements

### Requirement: Detect awaiting-input signal from TPA

The watcher SHALL scan `~/.tpa/awaiting_input/` for per-PID flag
files written by TPA's `sitecustomize.py` monkey-patch of
`tpa.command_line.prompt_toolkit_completion.get_input_with_combined_completion`.
A flag file SHALL be named exactly `<pid>` (digits only). The watcher
SHALL treat presence of a flag file whose PID is alive as a binary
signal that the corresponding TPA process is awaiting user input RIGHT
NOW. Filenames that are not all-digit SHALL be ignored. A missing
directory SHALL be treated as "no signal" (no error).

#### Scenario: Flag file present for alive PID
- **WHEN** `~/.tpa/awaiting_input/<pid>` exists AND `pid` is alive
- **THEN** `tpa_pids_awaiting_input()` includes `pid` in its return value

#### Scenario: Flag file present for dead PID
- **WHEN** `~/.tpa/awaiting_input/<pid>` exists AND `pid` is NOT alive
- **THEN** the flag file SHALL be deleted by the watcher (read-time eviction)
- **AND** `tpa_pids_awaiting_input()` does NOT include `pid` in its return value

#### Scenario: Directory does not exist
- **WHEN** `~/.tpa/awaiting_input/` does not exist on the filesystem
- **THEN** `tpa_pids_awaiting_input()` returns `[]` without raising

#### Scenario: Non-numeric filenames in the directory
- **WHEN** the directory contains `.DS_Store`, `README.md`, or other non-digit names
- **THEN** those files SHALL be ignored (not parsed, not deleted)

### Requirement: Emit `approval_needed` as highest-priority TPA-active state

The watcher SHALL emit an `approval_needed` state — a 9th state added to
the existing 8 — when TPA is asking the user for input. This
state SHALL override `working`, `thinking`, `grooving`, `celebrating`,
and `concerned` in the priority cascade. It SHALL NOT override
`sleeping` (user idle) and SHALL NOT override the `force_state` test/demo
override. The state SHALL be paired with a human-readable message
(default `"your turn"`, configurable via `approval_alert_text`) and a
`state_reason` string explaining why it fired.

#### Scenario: Direct signal fires approval
- **WHEN** `tpa_pids_awaiting_input()` returns at least one PID
- **AND** `approval_alert_enabled` is `true` in `~/.squid-pet/config.json`
- **THEN** `state` is `approval_needed`
- **AND** `state_reason` starts with `"awaiting_input flag from TPA pid(s) "` followed by the PIDs

#### Scenario: Direct signal clears immediately
- **WHEN** all `awaiting_input/<pid>` files have been deleted (Pink typed)
- **THEN** the next tick produces a state OTHER than `approval_needed`
- **AND** no threshold or snooze delays the transition

#### Scenario: User is idle outranks approval
- **WHEN** `idle_seconds >= 300` (user away)
- **AND** at least one awaiting-input flag is present
- **THEN** `state` is `sleeping`, NOT `approval_needed`

### Requirement: Fallback CPU-heuristic trigger when direct signal absent

The watcher SHALL provide a fallback trigger for TPA versions
that do not write the awaiting-input flag. The fallback SHALL use the
`per_process_pending_approval_idle()` function, which returns the
maximum idle duration across TPA processes that satisfy ALL of:

1. The PID has been observed busy (CPU >= 5%) for at least 3 consecutive
   ticks (sustained-busy gate; filters Python GC and prompt_toolkit
   redraw blips). Any single idle tick resets the streak.
2. The PID has NOT been observed writing its awaiting-input flag at any
   point in the watcher's lifetime. Once a PID writes the flag, the
   watcher trusts the direct signal exclusively for that PID and SHALL
   skip the fallback for it.
3. The PID is currently idle (CPU < 5%).
4. The idle duration is no greater than 120 seconds (snooze cap).

The fallback SHALL fire `approval_needed` when the returned idle time is
at or above `approval_alert_threshold_sec` (default 10.0) AND the
direct signal is absent.

#### Scenario: Fallback fires when TPA busy then idle
- **WHEN** no awaiting-input flag is present
- **AND** at least one TPA process was observed busy for 3+ consecutive ticks and has been idle for 15s
- **AND** `approval_alert_enabled` is `true`
- **THEN** `state` is `approval_needed`
- **AND** `state_reason` ends with `"per-proc idle, fallback)"`

#### Scenario: Single busy tick does not arm the fallback
- **WHEN** a TPA process has exactly one tick above the CPU threshold and then goes idle
- **THEN** the fallback does NOT fire (the streak is below the sustained-busy gate)

#### Scenario: Interrupted streak does not arm the fallback
- **WHEN** a TPA process has 2 busy ticks, 1 idle tick, then 1 more busy tick
- **THEN** the streak resets at the idle tick and the fallback does NOT fire

#### Scenario: Patched TPA is skipped by fallback
- **WHEN** a TPA process has been observed writing its `awaiting_input/<pid>` flag at any point in the watcher's lifetime
- **AND** later the flag is absent and the TPA's CPU pattern would otherwise qualify it for the fallback
- **THEN** the fallback does NOT fire for that PID (direct signal is exclusive)

#### Scenario: Never-busy TPA does not trigger fallback
- **WHEN** no awaiting-input flag is present
- **AND** a TPA process has been idle since launch but never observed busy
- **THEN** the fallback does NOT fire (the never-busy PID is filtered out)

#### Scenario: Snoozed TPA does not re-trigger fallback
- **WHEN** no awaiting-input flag is present
- **AND** a TPA process has been idle for 240 seconds (past the 120s snooze cap)
- **THEN** the fallback does NOT fire for that PID
- **AND** it re-arms only if the PID next transitions through 3 sustained busy ticks then idle

#### Scenario: Fallback retriggers after re-busy cycle
- **WHEN** a snoozed PID is observed busy for 3 consecutive ticks at any subsequent ticks
- **AND** then goes idle past the threshold
- **THEN** the fallback fires `approval_needed` with the fresh idle time

#### Scenario: Direct signal takes precedence over fallback
- **WHEN** at least one awaiting-input flag is present
- **AND** the CPU heuristic would also have fired
- **THEN** the `state_reason` reflects the direct signal (NOT the fallback)

### Requirement: Approval-alert kill switch

The watcher SHALL read `approval_alert_enabled` (boolean, default
`true`) from `~/.squid-pet/config.json`. When the value is `false`,
NEITHER the direct signal NOR the fallback SHALL fire
`approval_needed`. The state cascade SHALL behave as if both triggers
were absent.

#### Scenario: Kill switch disables direct signal
- **WHEN** `approval_alert_enabled` is `false`
- **AND** at least one awaiting-input flag is present
- **THEN** `state` is computed from the unmodified cascade (`working`/`thinking`/etc.)
- **AND** `state` is NOT `approval_needed`

#### Scenario: Kill switch disables fallback
- **WHEN** `approval_alert_enabled` is `false`
- **AND** a TPA process has been idle past the threshold with the fallback's other gates satisfied
- **THEN** `state` is NOT `approval_needed`

### Requirement: Surface approval-alert state in `squid why`

The `squid why` CLI SHALL include the current `approval_alert_enabled`
value, the configured `approval_alert_threshold_sec`, and the live
`per_proc_max_idle_sec` in both human-readable and `--json` outputs.
When the alert is disabled but the per-proc idle is at or above the
threshold, the CLI SHALL print a visible warning indicating that an
alert would be firing but the kill switch is off.

#### Scenario: Human output shows toggle
- **WHEN** the user runs `squid why`
- **THEN** the output contains a line that says `APPROVAL ALERT: ON` or `APPROVAL ALERT: OFF`
- **AND** the same line contains the threshold value and the current per-proc idle value

#### Scenario: JSON output exposes structured fields
- **WHEN** the user runs `squid why --json`
- **THEN** the JSON includes `approval_alert.enabled` (bool), `approval_alert.threshold_sec` (number), `approval_alert.per_proc_max_idle_sec` (number)

#### Scenario: Disabled alert warning when idle exceeds threshold
- **WHEN** `approval_alert_enabled` is `false`
- **AND** `per_proc_max_idle_sec >= approval_alert_threshold_sec`
- **THEN** the human output prints a yellow warning that an alert is being suppressed

## MODIFIED Requirements

### Requirement: Emit exactly one state per tick using priority cascade

The watcher SHALL emit exactly one of nine states each tick, selected by a
priority cascade. Higher-priority conditions SHALL override lower-priority
ones. The order is:

1. `force_state` override (test/demo file at `~/.squid-pet/force_state`)
2. `sleeping`
3. `celebrating` (sticky hold)
4. `idle` (when TPA not running)
5. `approval_needed` (direct signal OR fallback, both gated by kill switch)
6. `grooving`
7. `concerned`
8. `working`
9. `thinking`
10. `idle` (default fallback)

#### Scenario: User is idle for 5+ minutes
- **WHEN** `idle_seconds >= 300`
- **THEN** state is `sleeping` regardless of any other signal

#### Scenario: CPU drops from busy to near-zero
- **WHEN** the previous tick was busy (CPU >= 5%) AND current CPU < 1.0%
- **THEN** state is `celebrating` AND this state is held for the next 4 seconds

#### Scenario: Approval signal beats working/thinking
- **WHEN** at least one TPA awaiting-input flag is present
- **AND** `approval_alert_enabled` is `true`
- **AND** the user is not idle and not in a sticky celebrate
- **THEN** state is `approval_needed` (even if CPU would have selected `working` or `thinking`)

#### Scenario: A subagent is active
- **WHEN** a `.pkl` file under `~/.tpa/subagent_sessions/` was modified within the last 30 seconds AND user is not idle AND not in a sticky celebrate AND no approval signal is present
- **THEN** state is `grooving`

#### Scenario: A recent error was logged
- **WHEN** `~/.tpa/logs/errors.log` was modified within the last 60 seconds AND CPU < 5% AND no higher-priority state matched
- **THEN** state is `concerned`

#### Scenario: TPA is busy and writing logs
- **WHEN** CPU >= 5% AND a session log file was modified within the last 5 seconds AND no approval signal is present
- **THEN** state is `working`

#### Scenario: TPA is busy but no recent log writes (LLM call)
- **WHEN** CPU >= 5% AND no session log was modified within the last 5 seconds AND no approval signal is present
- **THEN** state is `thinking`

#### Scenario: No signals match
- **WHEN** none of the above conditions are met
- **THEN** state is `idle`
