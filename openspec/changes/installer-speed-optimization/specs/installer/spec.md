# installer Specification Delta — speed optimization

This delta MODIFIES the existing installer requirements from
`distribution-installer` to add performance criteria, and ADDS new
requirements for profiling, lockfile-driven installs, and progress
reporting.

## MODIFIED Requirements

### Requirement: Single-command bootstrap install

The system SHALL provide an `install.sh` script at the repository root that
performs all setup steps without further user input when invoked
non-interactively, AND SHALL complete within the following wall-time
budgets:

| Scenario | Target | Failure threshold |
|---|---|---|
| Cold install (fresh Mac, no uv cache) | <= 5 minutes | > 10 minutes is a regression |
| Warm install (re-run on existing setup) | <= 30 seconds | > 60 seconds is a regression |
| `squid update` | <= 60 seconds | > 120 seconds is a regression |
| `uninstall.sh --yes && install.sh` | <= 2 minutes | > 5 minutes is a regression |

#### Scenario: Warm install completes in <30 seconds

- **GIVEN** a Mac with `~/Projects/squid-pet/.venv` populated, `~/.cache/uv`
  warm, and `uv.lock` matching `pyproject.toml`
- **WHEN** the user runs `./install.sh` again
- **THEN** the script SHALL complete in under 30 seconds wall-time
- **AND** `clone_or_update` SHALL skip `git pull` when HEAD == origin/main
- **AND** `install_package` SHALL use `uv sync --frozen` (skip resolution)
- **AND** `ensure_uv` SHALL skip its step header when uv is already on PATH
- **AND** the daemon SHALL be kickstarted (not reinstalled) if the plist
  hasn't changed

#### Scenario: Cold install completes in <5 minutes

- **GIVEN** a fresh Mac with no `~/.cache/uv`, no `~/Projects/squid-pet`,
  and `uv` already on PATH
- **WHEN** the user runs `./install.sh`
- **THEN** the script SHALL complete in under 5 minutes wall-time on a
  reasonable corporate VPN connection (>5 Mbps to internal GHE + artifactory)
- **AND** progress SHALL be visible at all times (spinner or step headers,
  no >10s silent gaps)

## ADDED Requirements

### Requirement: Profile mode for install instrumentation

The system SHALL support a `--profile` flag on `install.sh` that captures
per-stage wall times and writes them to both stdout and a timestamped
file under `/tmp/`.

#### Scenario: --profile flag produces a profile report

- **WHEN** the user runs `./install.sh --profile`
- **THEN** each stage function SHALL be wrapped with timing
- **AND** the final output SHALL include a sorted-descending ASCII table
  of `STAGE_NAME` -> `WALL_TIME_SECONDS`
- **AND** the same table SHALL be appended to
  `/tmp/squid-pet-install-profile-<UTC_TIMESTAMP>.txt`
- **AND** `--profile` MAY be combined with any other flag (`--wizard`,
  `--non-interactive`)

#### Scenario: Profile data shows the bottleneck

- **GIVEN** a captured profile from a fresh install
- **THEN** the report SHALL identify which stage consumed the most time
- **AND** the report SHALL contain a "% of total" column to surface the dominant cost

### Requirement: Lockfile-driven installs

The system SHALL ship a `uv.lock` file at the repository root, and
`install.sh` SHALL prefer `uv sync --frozen` over `uv pip install -e .`
when the lockfile is present.

#### Scenario: uv.lock exists and matches pyproject.toml

- **WHEN** `install_package` runs
- **AND** `uv.lock` exists and is in sync with `pyproject.toml`
- **THEN** the script SHALL invoke `uv sync --frozen` (no resolution)
- **AND** SHALL NOT invoke `uv pip install -e .`

#### Scenario: uv.lock is missing or out of date

- **WHEN** `install_package` runs
- **AND** `uv.lock` is absent OR `uv sync --frozen` exits non-zero with
  "out of date" message
- **THEN** the script SHALL fall back to `uv pip install -e .` with the
  internal artifactory index args
- **AND** SHALL print a warning telling the maintainer to regenerate
  `uv.lock` with `uv lock`

#### Scenario: .python-version pins Python version

- **GIVEN** a `.python-version` file at the repository root
- **WHEN** `uv sync` runs
- **THEN** uv SHALL use the pinned Python version
- **AND** SHALL print a clear error pointing at `uv python install <ver>`
  if that version is missing

### Requirement: Parallel independent stages

The system SHALL run independent install stages concurrently when doing
so reduces wall time without complicating error handling.

#### Scenario: clone_or_update and ensure_uv run in parallel

- **WHEN** the install pipeline starts
- **THEN** `clone_or_update` and `ensure_uv` SHALL be launched in
  parallel with `&` + `wait`
- **AND** each SHALL buffer its output to a per-stage log file
- **AND** on success, the buffered outputs SHALL be replayed sequentially
  to stdout (in stage order, not interleave-order, so the install log
  reads naturally)
- **AND** on failure of either stage, the failing stage's log SHALL be
  cat'd to stderr before `die`

#### Scenario: User Ctrl-C during parallel stages

- **GIVEN** parallel stages are in flight
- **WHEN** the user sends SIGINT (Ctrl-C)
- **THEN** the trap handler SHALL kill all background jobs
- **AND** the script SHALL exit cleanly without leaving orphan processes

### Requirement: Progress visibility for slow stages

Any stage that typically takes >5 seconds SHALL print an upfront
"this can take ~Ns" message AND show a spinner while it runs (when stdout
is a TTY).

#### Scenario: install_package shows spinner with ETA

- **WHEN** `install_package` is invoked on a TTY
- **THEN** an upfront line SHALL print: "installing packages (~2-5 min
  cold, <30s warm)"
- **AND** a bash spinner SHALL run alongside the background pip/uv
  process
- **AND** the spinner SHALL be suppressed if stdout is not a TTY
- **AND** on completion, the spinner SHALL clear its line cleanly

#### Scenario: Total install duration printed in summary

- **WHEN** `print_summary` runs at the end of installation
- **THEN** the summary SHALL include a line: "install took: Xm Ys"

### Requirement: Install history log

The system SHALL append each install completion to
`~/.squid-pet/logs/install-history.log` so users can spot regressions
over time.

#### Scenario: Successful install appends history entry

- **WHEN** `install.sh` completes successfully
- **THEN** a line SHALL be appended to
  `~/.squid-pet/logs/install-history.log` with format:
  `<ISO8601_TIMESTAMP>\t<DURATION_SECONDS>\t<cold|warm>\t<commit_sha>`

#### Scenario: History log surfaces regression

- **GIVEN** at least 3 entries in `install-history.log`
- **THEN** users can compare durations across installs to detect a
  regression
- **AND** `docs/INSTALL.md` SHALL document where to look + what's normal
