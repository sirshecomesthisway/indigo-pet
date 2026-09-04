# installer Specification

## Purpose
Define how Squid is installed, updated, and uninstalled on a Mac. Covers
the bootstrap script, the templated launchd plist, the CLI launcher's
lifecycle subcommands, and the first-run settings file. Does NOT cover
Windows support, signed-package distribution, or a Homebrew tap.

Synced from the archived `distribution-installer` change (2026-06-27) on
2026-09-02 -- the archive never created this main spec, so it existed only
as a delta until now. The first-run wizard requirement below is REWRITTEN
against what actually shipped.

## Requirements

### Requirement: Single-command bootstrap install

The system SHALL provide an `install.sh` script at the repository root that
can be executed via `curl | bash` or run locally. It SHALL perform all setup
steps without further user input when invoked non-interactively, in this
order: preflight → ensure uv → clone/update → venv → install package →
migrate legacy → render plist → install launcher → write settings →
boot launchd → verify alive → permission walkthrough → summary.

The script SHALL be idempotent: re-running it updates an existing install
rather than failing or duplicating state.

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
  -k`, and exit successfully without rewriting `settings.json`.

#### Scenario: Install on a system with legacy `~/.indigo-pet/`
- **WHEN** `install.sh` runs and `~/.indigo-pet/` exists but `~/.squid-pet/`
  does not
- **THEN** the script SHALL `cp -a` `~/.indigo-pet/` to `~/.squid-pet/`,
  preserving all user settings (corner, stroll mode, position), AND print a
  migration notice telling the user the old directory can be removed once
  they have verified Squid works.

#### Scenario: Launcher directory is not on PATH
- **WHEN** `~/.local/bin` is not on the user's PATH
- **THEN** the installer SHALL warn and print the exact `export PATH=...`
  line to add to `~/.zshrc`, rather than failing

### Requirement: Preflight environment validation

The installer SHALL verify required tooling is present before mutating any
filesystem state. If a prerequisite is missing, it SHALL print a specific
remediation hint and exit with a non-zero status.

#### Scenario: Not macOS
- **WHEN** `install.sh` runs where `uname` is not `Darwin`
- **THEN** it SHALL exit with a message naming the detected platform

#### Scenario: Missing macOS minimum version
- **WHEN** `install.sh` runs on macOS earlier than 12.0
- **THEN** it SHALL print that macOS 12+ is required (the NSWindow APIs
  Squid uses need it) and exit non-zero, without modifying any files

#### Scenario: Missing git
- **WHEN** `git` is not in PATH
- **THEN** it SHALL exit non-zero pointing at `xcode-select --install`

#### Scenario: Missing uv (auto-installable)
- **WHEN** `uv` is not in PATH but `brew` is
- **THEN** it SHALL run `brew install uv` automatically and continue, NOT exit
- **AND** a missing `brew` SHALL be a warning, not a failure, since it is
  only needed on that path

### Requirement: Templated launchd plist generation

The repository SHALL contain a launchd plist template with placeholder
paths at `launchagent/com.pink.squid-pet.plist.template`. The installer
SHALL substitute the resolved project directory into every `__PROJECT__`
placeholder and write the result to
`~/Library/LaunchAgents/com.pink.squid-pet.plist`.

#### Scenario: No placeholders remain in rendered plist
- **WHEN** the rendered plist is written
- **THEN** `grep '__PROJECT__' ~/Library/LaunchAgents/com.pink.squid-pet.plist`
  SHALL return no matches

#### Scenario: Existing plist is replaced
- **WHEN** a plist from a previous install is already present
- **THEN** it SHALL be replaced, and launchd re-bootstrapped
  (`bootout` + `bootstrap`), so the new paths take effect

### Requirement: First-run settings are written silently with sane defaults

When no `~/.squid-pet/settings.json` exists, the installer SHALL write one
with defaults and SHALL NOT prompt. An existing `settings.json` SHALL be
left untouched.

Defaults SHALL enable both agent detectors (`claude_code`, `codex`) and set
`project_dirs` to `~/Projects`.

- **Why**: neither agent detector has an observed misfire risk, and there is
  no cost to leaving one on when that tool is not running -- it simply
  reports nothing. Prompting for something with no wrong answer is friction.
  Power users can pass `--wizard` or edit the file directly; changes are
  picked up live with no restart (see `state-detection`'s hot reload).

#### Scenario: Fresh install
- **WHEN** no `settings.json` exists
- **THEN** one is written with both detectors enabled and `project_dirs` of
  `~/Projects`, with no prompts, TTY or not

#### Scenario: Existing settings are preserved
- **WHEN** `settings.json` already exists
- **THEN** the installer leaves it alone and says so

### Requirement: Accessibility permission walkthrough

The installer SHALL inform the user that macOS Accessibility permission
is required for the window-raising features and SHALL open the appropriate
System Settings pane to make granting it one click away. It SHALL NOT
attempt to grant the permission programmatically.

#### Scenario: Walkthrough on TTY install
- **WHEN** the installer reaches the permissions step on a TTY install
- **THEN** it SHALL print the reason and the exact binary path to add, then
  invoke `open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"`,
  then wait for Enter before continuing

#### Scenario: Walkthrough on non-TTY install
- **WHEN** the installer reaches the permissions step on a non-TTY install
- **THEN** it SHALL print the same instructions but NOT block on Enter; it
  SHALL exit successfully and rely on the user reading the printed output

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
  remain on disk

#### Scenario: Full uninstall with --all flag
- **WHEN** a user runs `uninstall.sh --yes --all`
- **THEN** all install artifacts SHALL be removed without prompts, including
  `~/.squid-pet/`, `~/Projects/squid-pet/`, and `/tmp/squid-pet.*.log`

### Requirement: In-place update via `squid update`

The CLI launcher SHALL accept an `update` subcommand that performs a
git-pull-based update with no downtime beyond a brief WKWebView restart.

#### Scenario: Update with no upstream changes
- **WHEN** a user runs `squid update` and the local branch is up-to-date with origin
- **THEN** the script SHALL print "Already up to date", NOT restart Squid,
  and exit successfully

#### Scenario: Update with upstream changes
- **WHEN** a user runs `squid update` and the local branch is behind origin
- **THEN** the script SHALL `git pull`, run `uv pip install -e . --quiet`,
  and `launchctl kickstart -k gui/$(id -u)/com.pink.squid-pet`. Squid SHALL
  be visibly running again within 5 seconds of the kickstart

#### Scenario: Update fails due to network or merge conflict
- **WHEN** `git pull` fails for any reason
- **THEN** the script SHALL print the git error verbatim, NOT touch the
  package or launchd job, and exit non-zero. The currently running Squid
  SHALL be unaffected
