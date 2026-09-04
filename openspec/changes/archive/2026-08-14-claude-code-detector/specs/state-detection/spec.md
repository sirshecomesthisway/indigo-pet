# state-detection delta

## ADDED Requirements

### Requirement: Detect Claude Code CLI activity

The watcher SHALL identify whether Claude Code is currently running by
scanning `psutil.process_iter()` for a process whose `cmdline()[0]`
basename is `claude` (not `Process.name()`, which was empirically found
to return the versioned install path's basename rather than `claude` on
macOS). It SHALL additionally detect live tool-subprocess activity
(any non-shell descendant process matching the existing shell-child
allowlist) and transcript write recency (the youngest mtime among
`~/.claude/projects/*/*.jsonl`, discovery-cached for 60 seconds, entries
older than 15 minutes dropped from the cache).

#### Scenario: Claude Code is running with a live tool subprocess
- **WHEN** a `claude` process exists AND has a non-shell descendant process matching the shell-child allowlist
- **THEN** `claude_code_running` is `true` AND the detector reports busy via its shell signal

#### Scenario: Claude Code transcript was just written
- **WHEN** a `claude` process exists AND the most-recently-modified transcript file under `~/.claude/projects/` has an mtime within 20 seconds of now
- **THEN** the detector reports `streaming=true`

#### Scenario: Claude Code is not running
- **WHEN** no process named `claude` exists
- **THEN** `claude_code_running` is `false`

### Requirement: Claude Code activity participates in the TPA-style working/thinking cascade

Unlike the generic git/terminal/IDE detectors (which only feed the flat
non-TPA OR-fallback), the Claude Code detector's signals SHALL be merged
(via OR) with the TPA detector's signals before the branch-4
working/thinking cascade evaluates, so Claude Code activity gets the same
`working` vs `thinking` distinction TPA gets, rather than the flat
`thinking` fallback.

#### Scenario: Claude Code running a tool, TPA absent
- **WHEN** TPA is not running AND the Claude Code detector reports a live tool subprocess
- **THEN** state is `working`

#### Scenario: Claude Code generating, no tool subprocess, TPA absent
- **WHEN** TPA is not running AND the Claude Code detector reports `streaming=true` AND no live tool subprocess
- **THEN** state is `thinking`

#### Scenario: Claude Code idle (stale transcript), TPA absent
- **WHEN** TPA is not running AND the Claude Code detector reports no live tool subprocess AND the newest transcript is older than 20 seconds
- **THEN** branch 4 does not fire from the Claude Code detector alone (falls through to the non-TPA fallback or `idle`, per whatever other detectors report)

#### Scenario: TPA behavior is unchanged when Claude Code is absent or disabled
- **WHEN** the Claude Code detector is disabled, or no `claude` process is running
- **THEN** the branch-4 cascade behaves exactly as it did before this change, driven solely by TPA's signals

### Requirement: Per-detector opt-out for Claude Code via settings

The watcher SHALL load `triggers.claude_code` (boolean) from
`~/.squid-pet/settings.json`, defaulting to `true` when absent, and SHALL
only instantiate the Claude Code detector when it is `true`.

#### Scenario: Claude Code trigger disabled
- **WHEN** `settings.json` sets `triggers.claude_code=false`
- **THEN** the Claude Code detector is not instantiated AND `claude_code_running` in `state.json` is `false`

## MODIFIED Requirements

### Requirement: Publish state to JSON file

The watcher SHALL atomically write the current `PetState` (state,
sub_state, cpu_percent, idle_seconds, tpa_running,
claude_code_running, timestamp, message) to `~/.squid-pet/state.json`
once per tick using a `.tmp` + rename pattern. `tpa_running`
SHALL retain its existing TPA-only meaning; `claude_code_running`
is a new, independent field reflecting the Claude Code detector's
process-presence signal.

#### Scenario: State changes
- **WHEN** a new state is computed
- **THEN** the file `~/.squid-pet/state.json` reflects the new state within one poll interval (1 second)

#### Scenario: File is being read by another process
- **WHEN** the writer flushes a new state while a reader is open
- **THEN** the reader observes either the old or the new state in full (never a partial write), because the writer uses `tmp.replace(STATE_FILE)`
