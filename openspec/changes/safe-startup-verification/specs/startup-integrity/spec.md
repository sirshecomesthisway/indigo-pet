# startup-integrity

## Purpose

The startup-integrity capability provides programmatic guarantees that
Squid never ships a process running without a visible, correctly-
positioned window. It defends against Cocoa thread-safety regressions,
pywebview/macOS upgrades that change event-firing behavior, and
installer or config corruption.

The capability operates at three lifecycle stages:
- **Developer time** — decorator enforcement, pre-commit grep audit
- **CI time** — automated smoke test on every PR
- **Runtime** — user-invokable `squid doctor` end-to-end self-test

## ADDED Requirements

### Requirement: Main-thread Cocoa enforcement decorator

The `squid_pet.threading_guards` module SHALL provide a
`cocoa_main_thread` decorator that auto-dispatches the wrapped function
via `PyObjCTools.AppHelper.callAfter` when invoked from any thread
other than the main thread, and executes the function inline when
invoked from the main thread.

#### Scenario: function called from main thread executes inline
- **GIVEN** a function decorated with `@cocoa_main_thread`
- **WHEN** the function is called from the main thread
- **THEN** the function body SHALL execute synchronously in the calling thread
- **AND** the return value SHALL be returned to the caller

#### Scenario: function called from worker thread dispatches via callAfter
- **GIVEN** a function decorated with `@cocoa_main_thread`
- **WHEN** the function is called from any non-main thread
- **THEN** the function body SHALL be enqueued via `AppHelper.callAfter`
- **AND** the wrapper SHALL return `None` immediately without waiting

#### Scenario: non-Mac environment runs decorated function inline
- **GIVEN** a runtime where Foundation/NSThread is not importable
- **WHEN** any `@cocoa_main_thread`-decorated function is called
- **THEN** the function SHALL execute inline regardless of thread origin
- **AND** no PyObjC dependency SHALL be required

### Requirement: Blocking variant for return-value callers

The `squid_pet.threading_guards` module SHALL provide a sibling
`cocoa_main_thread_blocking` decorator that synchronously waits for
main-thread execution to complete and returns the result, with a 5
second timeout.

#### Scenario: blocking variant returns main-thread result
- **GIVEN** a function decorated with `@cocoa_main_thread_blocking`
- **WHEN** the function is called from a non-main thread
- **THEN** the wrapper SHALL dispatch via `callAfter`
- **AND** SHALL block the caller until completion
- **AND** SHALL return the function's actual return value

#### Scenario: blocking variant times out after 5 seconds
- **GIVEN** a function decorated with `@cocoa_main_thread_blocking` whose body never completes
- **WHEN** the function is called from a non-main thread
- **THEN** the wrapper SHALL raise `TimeoutError` after 5 seconds with a message naming the wrapped function

### Requirement: `squid doctor` six-check end-to-end self-test

The `squid` launcher MUST expose a `doctor` subcommand that runs six
sequential checks verifying Squid is functioning end-to-end and
reports PASS or FAIL with diagnostics for each.

#### Scenario: healthy install reports all checks PASS
- **GIVEN** Squid is running with window visible at the saved corner
- **WHEN** the user runs `squid doctor`
- **THEN** the command SHALL print six PASS lines (process, state.json freshness, launchd job, CGWindowList window, window-in-corner, full startup log)
- **AND** SHALL exit with code 0

#### Scenario: frozen window reports CGWindowList check FAIL
- **GIVEN** Squid process is running with the window stuck at pywebview default (100, 100) instead of the saved corner
- **WHEN** the user runs `squid doctor`
- **THEN** checks 1-4 SHALL PASS (process alive, state.json fresh, launchd loaded, window exists in CGWindowList)
- **AND** check 5 (window-in-corner) SHALL FAIL with a diagnostic naming the actual versus expected coordinates
- **AND** check 6 (full startup log) SHALL FAIL listing the missing log markers
- **AND** the command SHALL exit with code 5

#### Scenario: JSON output for machine consumers
- **WHEN** the user runs `squid doctor --json`
- **THEN** the command SHALL emit one JSON object to stdout with `checks` array (each entry: `name`, `status`, `diagnostic`)
- **AND** SHALL suppress all human-readable formatting

### Requirement: Launcher healthcheck verifies window rendering

The `bin/squid start` command SHALL verify Squid's actual window
rendering (via CGWindowList) before declaring startup successful, not
merely process existence or state.json freshness.

#### Scenario: existing healthy instance is detected and start exits success
- **GIVEN** Squid is already running with a visible window at the saved corner
- **WHEN** the user runs `squid start`
- **THEN** the launcher SHALL detect the existing healthy instance via doctor checks 1, 2, and 4
- **AND** SHALL print "Squid is already running and healthy"
- **AND** SHALL exit 0 without launching a duplicate

#### Scenario: existing wedged instance is replaced cleanly
- **GIVEN** Squid process exists but the window is missing from CGWindowList
- **WHEN** the user runs `squid start`
- **THEN** the launcher SHALL invoke `launchctl bootout gui/<uid>/com.pink.squid-pet` before killing the process
- **AND** SHALL kill the process after bootout completes
- **AND** SHALL verify no Squid process remains before re-bootstrapping launchd
- **AND** SHALL NOT enter the previous 3-attempt retry loop

### Requirement: CI smoke test exercises full startup on every PR

The repository SHALL provide a GitHub Actions workflow that boots
Squid on a macOS runner, runs `squid doctor --json`, and fails the
build if any check reports FAIL.

#### Scenario: PR introducing a thread-safety regression fails CI
- **GIVEN** a PR that reverts the main-thread dispatch around `move_to_corner`
- **WHEN** the smoke-test workflow runs on the PR
- **THEN** the workflow SHALL boot Squid, wait 10 seconds, run `squid doctor --json`
- **AND** doctor SHALL FAIL checks 5 and 6
- **AND** the workflow SHALL exit non-zero
- **AND** the workflow SHALL upload `/tmp/squid-pet.{out,err}.log` as build artifacts for triage

#### Scenario: nightly run catches macOS-version regressions
- **GIVEN** the smoke-test workflow is scheduled on nightly cron against `main`
- **WHEN** a new macOS runner version introduces an incompatibility (e.g. WKWebView event-firing change)
- **THEN** the nightly run SHALL detect the regression before the next user-facing release

### Requirement: Pre-commit grep audit blocks unsafe new Cocoa calls

The repository SHALL provide a pre-commit hook that fails any commit
adding a direct NSWindow/NSApp/NSScreen mutating call without a
`@cocoa_main_thread` decoration on the enclosing function or an
`AppHelper.callAfter` wrap within five lines.

#### Scenario: new unsafe call is rejected
- **GIVEN** a working tree adding `win.setLevel_(3)` inside a regular function
- **WHEN** the developer runs `git commit`
- **THEN** the pre-commit hook SHALL fail the commit
- **AND** SHALL print the offending file:line and suggested fix (apply `@cocoa_main_thread` or wrap in `callAfter`)

#### Scenario: documented bypass is honored
- **GIVEN** a developer has a legitimate reason to call NSWindow off-main (e.g. read-only query proven safe)
- **WHEN** the call is followed on the same line by `# noqa: cocoa-main-thread`
- **THEN** the pre-commit hook SHALL skip that line
- **AND** SHALL print a notice listing the bypass for reviewer awareness
