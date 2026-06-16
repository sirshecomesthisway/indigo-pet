# installer Specification

## Purpose

Define how Squid is installed, updated, and uninstalled on a company-issued
Mac. Covers the bootstrap script, the templated launchd plist, the CLI
launcher's lifecycle subcommands, and the first-run configuration wizard.
Does NOT cover Windows support, signed-package distribution, or Homebrew tap
(separate specs).

## ADDED Requirements

### Requirement: Single-command bootstrap install

The system SHALL provide a `install.sh` script at the repository root that
can be executed via `curl | bash` or run locally. It SHALL perform all setup
steps without further user input when invoked non-interactively.

#### Scenario: Fresh install on a Mac with no prior Squid
- **WHEN** a user runs `curl -fsSL <repo>/install.sh | bash` on a Mac with
  no prior Squid install and no `~/.indigo-pet/`
- **THEN** within 120 seconds: the repo is cloned to `~/Projects/squid-pet`,
  a venv is created, the package is installed, the launchd plist is rendered
  + loaded, the CLI launcher is at `~/.local/bin/squid`, Squid's window is
  visible on screen, and `~/.squid-pet/state.json` is being updated.

#### Scenario: Re-running install on a system with Squid already installed
- **WHEN** a user re-runs `install.sh` on a system where Squid is already
  installed and running
- **THEN** the script SHALL detect the existing install, perform `git pull`
  + `uv pip install -e .` to upgrade, restart Squid via `launchctl kickstart
  -k`, and exit successfully without prompting for first-run config.

#### Scenario: Install on a system with legacy `~/.indigo-pet/`
- **WHEN** `install.sh` runs and `~/.indigo-pet/` exists but `~/.squid-pet/`
  does not
- **THEN** the script SHALL `cp -a` `~/.indigo-pet/` to `~/.squid-pet/`,
  preserving all user settings (corner, stroll mode, position), AND print a
  migration notice telling the user the old directory can be removed once
  they have verified Squid works.

### Requirement: Preflight environment validation

The installer SHALL verify required tooling is present before mutating any
filesystem state. If a prerequisite is missing, it SHALL print a specific
remediation hint and exit with a non-zero status.

#### Scenario: Missing macOS minimum version
- **WHEN** `install.sh` runs on macOS earlier than 12.0
- **THEN** it SHALL print "Squid requires macOS 12 (Monterey) or later"
  and exit with status 1, without modifying any files.

#### Scenario: Missing required tooling
- **WHEN** `install.sh` runs and any of `git`, `brew`, `curl` are not in PATH
- **THEN** it SHALL print the specific missing tool and the install command
  for it, and exit with status 1.

#### Scenario: Missing uv (auto-installable)
- **WHEN** `install.sh` runs and `uv` is not in PATH but `brew` is
- **THEN** it SHALL run `brew install uv` automatically and continue, NOT exit.

### Requirement: Templated launchd plist generation

The repository SHALL contain a launchd plist template with placeholder
paths. The installer SHALL substitute the user's actual `$HOME` and project
directory into the placeholders to produce a per-user plist.

#### Scenario: Plist template substitution
- **WHEN** `install.sh` renders the plist on a user with `$HOME=/Users/alice`
- **THEN** every occurrence of `__HOME__` in
  `launchagent/com.pink.squid-pet.plist.template` SHALL be replaced with
  `/Users/alice` and every `__PROJECT__` with the resolved project path,
  AND the result SHALL be written to
  `~/Library/LaunchAgents/com.pink.squid-pet.plist`.

#### Scenario: No placeholders remain in rendered plist
- **WHEN** the rendered plist is written
- **THEN** `grep '__HOME__\|__PROJECT__' ~/Library/LaunchAgents/com.pink.squid-pet.plist`
  SHALL return no matches.

### Requirement: First-run configuration wizard

The installer SHALL prompt for initial preferences (starting corner, stroll
mode, show-on-all-spaces) when no `~/.squid-pet/settings.json` exists AND
stdin is an interactive TTY. The installer SHALL skip the wizard silently
and use defaults when stdin is not a TTY (e.g., piped from curl).

#### Scenario: Interactive first run with all defaults accepted
- **WHEN** a TTY user runs install for the first time and presses Enter at
  each wizard prompt
- **THEN** `~/.squid-pet/settings.json` SHALL be written with
  `{"corner": "top-right", "stroll_mode": "edges", "all_spaces": true}`.

#### Scenario: Non-interactive install via curl pipe
- **WHEN** the installer is run via `curl | bash` (stdin is not a TTY)
- **THEN** the wizard SHALL be skipped silently, and `settings.json` SHALL
  be written with the same defaults (or NOT written, falling back to in-app
  defaults — either is acceptable).

### Requirement: Accessibility permission walkthrough

The installer SHALL inform the user that macOS Accessibility permission
is required and SHALL open the appropriate System Settings pane to make
granting it one click away. It SHALL NOT attempt to grant the permission
programmatically.

#### Scenario: Walkthrough on TTY install
- **WHEN** the installer reaches the permissions step on a TTY install
- **THEN** it SHALL print: the reason Accessibility is needed, the exact
  path of the binary to add (`~/Projects/squid-pet/.venv/bin/python`), then
  invoke `open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"`,
  then wait for the user to press Enter before continuing.

#### Scenario: Walkthrough on non-TTY install
- **WHEN** the installer reaches the permissions step on a non-TTY install
- **THEN** it SHALL print the same instructions but NOT block on Enter; it
  SHALL exit successfully and rely on the user reading the printed output.

### Requirement: Clean uninstall

The system SHALL provide an `uninstall.sh` script that removes all install
artifacts in reverse dependency order. By default it SHALL preserve user
state (`~/.squid-pet/`, project directory). It SHALL be invokable via
`squid uninstall` for discoverability.

#### Scenario: Default uninstall preserves user data
- **WHEN** a user runs `squid uninstall` (or `~/Projects/squid-pet/uninstall.sh`)
  and answers Y to default prompts
- **THEN** the launchd job SHALL be unloaded, the plist removed, the CLI
  launcher removed, but `~/.squid-pet/` and `~/Projects/squid-pet/` SHALL
  remain on disk.

#### Scenario: Full uninstall with --all flag
- **WHEN** a user runs `uninstall.sh --yes --all`
- **THEN** all install artifacts SHALL be removed without prompts, including
  `~/.squid-pet/`, `~/Projects/squid-pet/`, and `/tmp/squid-pet.*.log`.

### Requirement: In-place update via `squid update`

The CLI launcher SHALL accept an `update` subcommand that performs
git-pull-based update with zero downtime perceptible beyond a brief WKWebView
restart.

#### Scenario: Update with no upstream changes
- **WHEN** a user runs `squid update` and the local branch is up-to-date with origin
- **THEN** the script SHALL print "Already up to date", NOT restart Squid,
  and exit successfully.

#### Scenario: Update with upstream changes
- **WHEN** a user runs `squid update` and the local branch is behind origin
- **THEN** the script SHALL `git pull`, run `uv pip install -e . --quiet`,
  and `launchctl kickstart -k gui/$(id -u)/com.pink.squid-pet`. Squid SHALL
  be visibly running again within 5 seconds of the kickstart.

#### Scenario: Update fails due to network or merge conflict
- **WHEN** `git pull` fails for any reason
- **THEN** the script SHALL print the git error verbatim, NOT touch the
  package or launchd job, and exit non-zero. The currently running Squid
  SHALL be unaffected.
