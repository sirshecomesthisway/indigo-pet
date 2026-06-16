# state-detection delta

## ADDED Requirements

### Requirement: Detect git activity for project directories

The watcher SHALL detect git activity by reading filesystem mtimes of
`.git/HEAD`, `.git/index`, and `.git/refs/heads/` files within configured
`project_dirs`. The detector SHALL NOT read file contents and SHALL NOT
shell out to the `git` binary.

#### Scenario: Just committed
- **WHEN** any tracked repo's `.git/HEAD` mtime is within 5 seconds of now
- **THEN** the git detector reports `is_celebrating=True` for the next 4 seconds

#### Scenario: Just staged files
- **WHEN** any tracked repo's `.git/index` mtime is within 5 seconds of now AND `.git/HEAD` mtime is not within that window
- **THEN** the git detector reports `is_busy=True`

#### Scenario: Just pushed
- **WHEN** any tracked repo's file under `.git/refs/heads/` was modified within 5 seconds of now AND `.git/HEAD` has not been modified in that window
- **THEN** the git detector reports `is_celebrating=True` for the next 4 seconds

#### Scenario: Repo discovery bounded
- **WHEN** the git detector scans `project_dirs` for `.git/HEAD` files
- **THEN** the scan SHALL be capped at depth 4 and at 50 repos total, AND the result list SHALL be cached for 60 seconds before re-scanning

### Requirement: Detect terminal activity via active shell children

The watcher SHALL detect terminal activity by scanning `psutil.process_iter()`
for shells (`zsh`, `bash`, `fish`) that have a non-shell child process
running for more than 3 seconds. The detector SHALL NOT read shell history,
command strings, or environment variables.

#### Scenario: Active long-running command
- **WHEN** at least one shell process has a non-shell child with `(now - child.create_time) > 3`
- **THEN** the terminal detector reports `is_busy=True`

#### Scenario: Idle terminal
- **WHEN** no shell process has an active long-running non-shell child
- **THEN** the terminal detector reports `is_busy=False`

### Requirement: Detect IDE activity via process CPU and project file mtimes

The watcher SHALL detect IDE activity by combining two signals: (1) CPU% of
processes whose names match the `triggers.ide_processes` allowlist, and
(2) recent file modifications within `triggers.project_dirs`. The detector
SHALL NOT read file contents.

#### Scenario: IDE busy with recent save
- **WHEN** an IDE process is using CPU >= 3% AND any file under `project_dirs` was modified within 5 seconds of now
- **THEN** the IDE detector reports `is_busy=True`

#### Scenario: IDE quiet but autosaving
- **WHEN** an IDE process is using CPU < 3% AND any file under `project_dirs` was modified within 5 seconds of now
- **THEN** the IDE detector reports `is_busy=True`

#### Scenario: Creative burst across many files
- **WHEN** more than 5 distinct files under `project_dirs` were modified within 30 seconds of now
- **THEN** the IDE detector reports `is_grooving=True`

#### Scenario: IDE running but no file activity
- **WHEN** an IDE process is using CPU >= 3% but no project file was modified within 5 seconds
- **THEN** the IDE detector reports `is_busy=False` (treats this as background indexing, not active work)

### Requirement: Per-detector opt-out via settings

The watcher SHALL load `triggers.{tpa, git, terminal, ide}` boolean
flags from `~/.squid-pet/settings.json` and SHALL only instantiate detectors
whose flag is `true`. Missing keys SHALL default to `true`.

#### Scenario: All triggers enabled (default)
- **WHEN** `settings.json` has no `triggers` key (or all four flags are missing/true)
- **THEN** all four detectors (TPA, Git, Terminal, IDE) SHALL be instantiated

#### Scenario: All triggers disabled
- **WHEN** `settings.json` sets all four `triggers.*` flags to `false`
- **THEN** zero detectors SHALL be instantiated AND the state SHALL be `sleeping` (if user is idle) or `idle` (otherwise), with no crash

#### Scenario: Selective opt-out
- **WHEN** `settings.json` sets `triggers.tpa=false` but leaves the other three at true
- **THEN** only Git, Terminal, and IDE detectors SHALL be instantiated AND TPA-derived fields (`tpa_running`, `cpu_percent`) in state.json SHALL be `false` and `0.0` respectively

### Requirement: Privacy contract — no file content reads, no network

Every activity detector SHALL be implemented using only: `psutil` process
introspection (name, pid, ppid, create_time, cpu_percent, cmdline), `os.stat`
for mtime checks, and `os.listdir` for path discovery. No detector SHALL
read file contents (no `open()`, `read()`, `Path.read_text()`). No detector
SHALL make network calls.

#### Scenario: No network sockets opened
- **WHEN** Squid is running and `lsof -i -p $(pgrep -f 'python -m squid_pet')` is executed
- **THEN** the output SHALL show zero network sockets owned by the Squid process

#### Scenario: No file contents accessed for detection
- **WHEN** an audit traces all `read()` / `open()` syscalls made by detector code paths during one tick
- **THEN** the only files opened SHALL be `~/.squid-pet/{state.json,settings.json}` (Squid's own files); no project file, no `.git/HEAD` contents, no log file contents SHALL be opened

## MODIFIED Requirements

### Requirement: Emit exactly one state per tick using priority cascade

The watcher SHALL emit exactly one of seven states each tick, selected by a
priority cascade. Higher-priority conditions SHALL override lower-priority
ones. State signals (`is_busy`, `is_celebrating`, `is_grooving`) SHALL be
aggregated by OR-ing across all enabled detectors before the cascade runs.

The priority order is:

1. `sleeping`
2. `celebrating` (sticky hold, 4 seconds)
3. `idle` (when ALL detectors report inactive AND no user input)
4. `grooving`
5. `concerned`
6. `working`
7. `thinking`
8. `idle` (default fallback)

#### Scenario: User is idle for 5+ minutes
- **WHEN** `idle_seconds >= 300`
- **THEN** state is `sleeping` regardless of any detector signals

#### Scenario: Any detector reports celebrating
- **WHEN** at least one enabled detector returns `is_celebrating=True` AND user is not idle
- **THEN** state is `celebrating` AND this state is held for the next 4 seconds

#### Scenario: Any detector reports grooving
- **WHEN** at least one enabled detector returns `is_grooving=True` AND no higher-priority state matched
- **THEN** state is `grooving`

#### Scenario: TPA errors logged (legacy TPA-only signal)
- **WHEN** the TPA detector reports `is_concerned=True` (errors.log recent AND TPA CPU < 5%) AND no higher-priority state matched
- **THEN** state is `concerned`

#### Scenario: Any detector reports busy
- **WHEN** at least one enabled detector returns `is_busy=True` AND no higher-priority state matched
- **THEN** state is `working`

#### Scenario: TPA thinking (legacy TPA-only signal)
- **WHEN** the TPA detector reports TPA CPU >= 5% AND no recent log writes AND no higher-priority state matched
- **THEN** state is `thinking`

#### Scenario: No detectors active
- **WHEN** all enabled detectors return False for busy/celebrating/grooving AND user is not idle 5+min
- **THEN** state is `idle`
