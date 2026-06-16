## ADDED Requirements

### Requirement: Detect TPA process activity

The watcher SHALL identify whether TPA is currently running by scanning
`psutil.process_iter()` for processes whose command line contains either
`tpa` or `tpa`. Bash-wrapper processes SHALL be filtered out by
requiring `python` to also appear in the cmdline or the executable name to be
`tpa`.

#### Scenario: TPA is running
- **WHEN** at least one matching Python process exists
- **THEN** `tpa_running` is `true` AND the aggregated CPU% across matches is reported

#### Scenario: TPA is not running
- **WHEN** no matching process exists
- **THEN** `tpa_running` is `false` AND `cpu_percent` is `0.0`

### Requirement: Measure macOS user idle time

The watcher SHALL read macOS HID idle time via the `ioreg -c IOHIDSystem`
command, parse the `HIDIdleTime` value (nanoseconds), and convert to seconds.
No PyObjC or Accessibility permission SHALL be required for this read.

#### Scenario: User is active
- **WHEN** the user has produced input within the last second
- **THEN** `idle_seconds` is `< 2.0`

#### Scenario: User has stepped away
- **WHEN** there has been no mouse or keyboard input for 5+ minutes
- **THEN** `idle_seconds >= 300.0`

### Requirement: Emit exactly one state per tick using priority cascade

The watcher SHALL emit exactly one of seven states each tick, selected by a
priority cascade. Higher-priority conditions SHALL override lower-priority
ones. The order is:

1. `sleeping`
2. `celebrating` (sticky hold)
3. `idle` (when TPA not running)
4. `grooving`
5. `concerned`
6. `working`
7. `thinking`
8. `idle` (default fallback)

#### Scenario: User is idle for 5+ minutes
- **WHEN** `idle_seconds >= 300`
- **THEN** state is `sleeping` regardless of any other signal

#### Scenario: CPU drops from busy to near-zero
- **WHEN** the previous tick was busy (CPU >= 5%) AND current CPU < 1.0%
- **THEN** state is `celebrating` AND this state is held for the next 4 seconds

#### Scenario: A subagent is active
- **WHEN** a `.pkl` file under `~/.tpa/subagent_sessions/` was modified within the last 30 seconds AND user is not idle AND not in a sticky celebrate
- **THEN** state is `grooving`

#### Scenario: A recent error was logged
- **WHEN** `~/.tpa/logs/errors.log` was modified within the last 60 seconds AND CPU < 5% AND no higher-priority state matched
- **THEN** state is `concerned`

#### Scenario: TPA is busy and writing logs
- **WHEN** CPU >= 5% AND a session log file was modified within the last 5 seconds
- **THEN** state is `working`

#### Scenario: TPA is busy but no recent log writes (LLM call)
- **WHEN** CPU >= 5% AND no session log was modified within the last 5 seconds
- **THEN** state is `thinking`

#### Scenario: No signals match
- **WHEN** none of the above conditions are met
- **THEN** state is `idle`

### Requirement: Publish state to JSON file

The watcher SHALL atomically write the current `PetState` (state, sub_state,
cpu_percent, idle_seconds, tpa_running, timestamp, message) to
`~/.squid-pet/state.json` once per tick using a `.tmp` + rename pattern.

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
