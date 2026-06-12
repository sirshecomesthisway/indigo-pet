## ADDED Requirements

### Requirement: Persist across all macOS Spaces and fullscreen apps

Squid SHALL remain visible when the user switches Spaces, enters a
fullscreen application, or uses Mission Control. The window's
`collectionBehavior` SHALL be set to `273`
(`canJoinAllSpaces | stationary | fullScreenAuxiliary`) via
`NSWindow.setCollectionBehavior_`, called from the loaded event handler
on the main thread via `AppHelper.callAfter`.

#### Scenario: User switches Space
- **WHEN** the user switches to a different macOS Space (Control-arrow or trackpad swipe)
- **THEN** Squid is visible on the new Space at her existing screen position
- **AND** her state and any in-progress animation continues uninterrupted

#### Scenario: User enters fullscreen
- **WHEN** the user enters fullscreen mode in any application
- **THEN** Squid remains visible above the fullscreen window
- **AND** Squid is positioned within the visible frame of the fullscreen Space

### Requirement: Refuse to launch a second instance (atomic singleton)

A second invocation of `python -m indigo_pet` SHALL detect that an existing
instance is running and refuse to start, printing a clear message identifying
the running instance. The detection mechanism SHALL be atomic and race-free
under concurrent launches.

The implementation SHALL acquire an exclusive non-blocking flock on
`~/.indigo-pet/lock` (`fcntl.LOCK_EX | fcntl.LOCK_NB`) at startup. The lock
file descriptor SHALL be kept alive in module globals for the duration of
the process. The lock SHALL be released by an atexit handler on clean
shutdown, OR by the kernel's automatic fd cleanup on SIGKILL.

#### Scenario: Two launches race to start
- **WHEN** two `python -m indigo_pet` invocations start within milliseconds of each other
- **THEN** exactly one acquires the flock and continues to startup
- **AND** the other prints a clear "already running" message and exits cleanly
- **AND** no two windows ever appear on screen

#### Scenario: Hard-killed instance leaves no stale lock
- **WHEN** an instance is killed with SIGKILL
- **AND** a new instance is launched immediately afterward
- **THEN** the kernel has released the flock on fd close
- **AND** the new instance acquires the lock and starts normally

### Requirement: External CLI for control and diagnostics

A command-line tool SHALL be installed at `~/.local/bin/squid` providing
operational control without requiring direct knowledge of the Python module
or process details. The CLI SHALL support at minimum:

- `squid start` — launch if not running
- `squid stop` — terminate cleanly (with escalating SIGTERM -> SIGKILL retries up to 6 attempts)
- `squid restart` — stop then start, with up to 3 launch retries to handle
  WKWebView startup flakiness on the host
- `squid status` — report running/healthy/unhealthy + pid
- `squid logs` — tail the log file
- `squid why` — print recent state log entries explaining current state

The `squid status` command SHALL distinguish between true duplicate processes
(multiple roots) and benign parent-child pairs (pywebview spawns a WebKit
content child sharing the parent's cmdline). It SHALL count only ROOT
processes whose parent is NOT also an indigo_pet process.

For backward compatibility, the binary `~/.local/bin/indigo` SHALL exist
as a symlink to `squid`.

#### Scenario: Status command after normal launch
- **WHEN** Squid is running healthily
- **AND** the user runs `squid status`
- **THEN** the output reports "running + healthy" with the parent pid
- **AND** no duplicate warning appears despite the pywebview child process

#### Scenario: Restart survives WKWebView flake
- **WHEN** the user runs `squid restart`
- **AND** the first launch attempt hangs in WKWebView startup
- **THEN** the CLI's 10-second watchdog kills the stuck attempt
- **AND** the CLI retries up to 3 times until startup succeeds
- **AND** the final status reports running + healthy

#### Scenario: Backward-compatible binary name
- **WHEN** the user runs `indigo status` (old muscle memory)
- **THEN** the symlink resolves to `squid status`
- **AND** the output identifies the pet as "Squid"
