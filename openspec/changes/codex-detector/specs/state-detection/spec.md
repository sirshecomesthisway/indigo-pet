# state-detection delta

## ADDED Requirements

### Requirement: Detect Codex CLI activity

The watcher SHALL identify whether Codex is currently running by
scanning `psutil.process_iter()` for a process whose `cmdline()[0]`
basename is `codex` or `codex-tui` (Codex's npm distribution spawns the
native binary as a child of a JS shim; the shim itself SHALL NOT match).
It SHALL additionally detect live tool-subprocess activity (any
non-shell descendant process matching the shell-child allowlist), a
recent write under configured `project_dirs`, and session-transcript
write recency (the youngest mtime among `~/.codex/sessions/**/*.jsonl`,
nested by date, discovery-cached for 60 seconds, entries older than 15
minutes dropped from the cache).

#### Scenario: Codex is running with a live tool subprocess
- **WHEN** a `codex` or `codex-tui` process exists AND has a non-shell descendant process matching the shell-child allowlist
- **THEN** `codex_running` is `true` AND the detector reports busy via its shell signal

#### Scenario: Codex wrote a file in-process
- **WHEN** a `codex` process exists AND a file under `project_dirs` was modified within 10 seconds of now
- **THEN** the detector reports `file_active=true`

#### Scenario: Codex session transcript was just written
- **WHEN** a `codex` process exists AND the most-recently-modified transcript file under `~/.codex/sessions/` (any date subdirectory) has an mtime within 20 seconds of now
- **THEN** the detector reports `streaming=true`

#### Scenario: Codex is not running
- **WHEN** no process matching `codex`/`codex-tui` exists
- **THEN** `codex_running` is `false`

### Requirement: Codex activity participates in the TPA-style working/thinking cascade

The Codex detector's signals SHALL be merged (via OR) with the Code
Puppy and Claude Code detectors' signals before the branch-4
working/thinking cascade evaluates, so Codex activity gets the same
`working` vs `thinking` distinction the other two get, rather than the
flat `thinking` fallback used by git/terminal/IDE.

#### Scenario: Codex running a tool, no other agent running
- **WHEN** neither TPA nor Claude Code is running AND the Codex detector reports a live tool subprocess OR a recent file write
- **THEN** state is `working`

#### Scenario: Codex generating, no shell/file evidence, no other agent running
- **WHEN** neither TPA nor Claude Code is running AND the Codex detector reports `streaming=true` AND neither shell nor file evidence
- **THEN** state is `thinking`

#### Scenario: Multiple agents running simultaneously
- **WHEN** both Claude Code and Codex are running and either reports hard working evidence (shell or file)
- **THEN** state is `working`, and `state_reason` names whichever detector's signal fired (priority order, not exclusivity)

#### Scenario: Existing behavior unchanged when Codex is absent or disabled
- **WHEN** the Codex detector is disabled, or no `codex`/`codex-tui` process is running
- **THEN** the branch-4 cascade behaves exactly as it did before this change

### Requirement: Per-detector opt-out for Codex via settings

The watcher SHALL load `triggers.codex` (boolean) from
`~/.squid-pet/settings.json`, defaulting to `true` when absent, and
SHALL only instantiate the Codex detector when it is `true`.

#### Scenario: Codex trigger disabled
- **WHEN** `settings.json` sets `triggers.codex=false`
- **THEN** the Codex detector is not instantiated AND `codex_running` in `state.json` is `false`

## MODIFIED Requirements

### Requirement: Claude Code activity participates in the TPA-style working/thinking cascade

In addition to the live-tool-subprocess and transcript-streaming
signals, the Claude Code detector SHALL also report `file_active=true`
when a file under `project_dirs` was modified within 10 seconds of
now, gated on a `claude` process being alive. This SHALL feed the same
`working` branch as the live-tool-subprocess signal, since in-process
tool calls (e.g. Edit/Write) never spawn a subprocess and would
otherwise only ever be visible via the coarser streaming signal.

#### Scenario: Claude Code writes a file in-process
- **WHEN** a `claude` process exists AND a file under `project_dirs` was modified within 10 seconds of now AND no live tool subprocess is detected
- **THEN** state is `working` (not `thinking`)

### Requirement: Publish state to JSON file

The watcher SHALL atomically write the current `PetState` (state,
sub_state, cpu_percent, idle_seconds, tpa_running,
claude_code_running, codex_running, timestamp, message) to
`~/.squid-pet/state.json` once per tick using a `.tmp` + rename
pattern. `tpa_running` and `claude_code_running` retain their
existing meanings; `codex_running` is a new, independent field
reflecting the Codex detector's process-presence signal.

#### Scenario: State changes
- **WHEN** a new state is computed
- **THEN** the file `~/.squid-pet/state.json` reflects the new state within one poll interval (1 second)

#### Scenario: File is being read by another process
- **WHEN** the writer flushes a new state while a reader is open
- **THEN** the reader observes either the old or the new state in full (never a partial write), because the writer uses `tmp.replace(STATE_FILE)`
