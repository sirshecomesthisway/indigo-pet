# Safe Startup Verification

## Why

Tonight (2026-06-16) Squid shipped a latent thread-safety bug that became
a 100% reproducer after a pywebview reinstall. `move_to_corner()` was
called from a WebKit callback thread; on macOS 14+ that silently blocks
indefinitely. Result: the process held its singleton lock, its watcher
thread kept updating state.json, but the window was frozen invisibly at
pywebview's default (100, 100). Every existing health check passed.
Pink (the only user) saw "Squid died" with no signal of what went wrong.

If this had landed on 500 internal engineers via `distribution-installer`,
every one of them would have hit the same wedge. Tooling caught nothing:
- launcher reported "healthy" then "hung in WKWebView" (false diagnosis)
- launchd KeepAlive made it worse (kill → respawn → re-wedge → lock fight)
- no automated test exercises actual window rendering
- no developer-time enforcement against Cocoa-from-non-main-thread

This change builds four layers of defense so the bug class cannot recur
silently, in dev or in users' hands.

## Goal

Make it structurally impossible for Squid to ship a process that runs
without a visible, correctly-positioned window — whether from a Cocoa
thread-safety regression, a pywebview/macOS upgrade, an installer
glitch, or a config corruption.

## Non-goals

- Crash reporting / telemetry back to a server (no phoning home)
- Automatic recovery from broken WebKit content processes (out of our control)
- Cross-process IPC for liveness signaling (state.json is sufficient)
- Replacing pywebview with a different webview library
- Windows-host equivalents (deferred to windows-port change)

## What changes

1. **`@cocoa_main_thread` decorator** (`src/squid_pet/threading_guards.py`)
   - Auto-dispatches the wrapped function via `AppHelper.callAfter` if
     called off the main thread, runs inline if on main thread.
   - Sibling `@cocoa_main_thread_blocking` for cases needing a return value.
   - Applied to every function that touches NSWindow/NSApp/NSScreen/NSView.
   - Migrated ~6 existing callsites in window.py, menu.py, passthrough.py.

2. **`squid doctor` command** (`src/squid_pet/cli.py` or launcher)
   - Six-check end-to-end self-test (process / state.json freshness /
     launchd job / window in CGWindowList / window in expected corner /
     full startup log present).
   - Exit 0 = healthy, non-zero = specific check failure with diagnostics.
   - Suggested fix for each failure mode, including links to kennel
     drawer / spec section explaining the root cause.

3. **Improved launcher healthcheck** (`bin/squid` start subcommand)
   - Replaces current "process exists + responds to signal" check with
     `squid doctor`'s actual window-rendering checks.
   - Stops launchd job BEFORE force-killing the process, preventing the
     kill→respawn→relock fight Pink hit tonight.

4. **CI smoke test** (`.github/workflows/smoke-test.yml`)
   - macOS-latest GitHub Actions runner boots Squid, waits 8s, runs
     `squid doctor`, asserts PASS.
   - Runs on every PR + nightly main.
   - Catches Cocoa thread-safety regressions and pywebview-version
     incompatibilities before merge.

5. **Pre-commit grep audit** (`.pre-commit-config.yaml` or simple shell hook)
   - Fails commit if any NSWindow/NSApp/NSScreen setter is added without
     `@cocoa_main_thread` decoration or `AppHelper.callAfter` wrap nearby.

## Success criteria

- `squid doctor` reports PASS in under 1 second on a healthy install.
- Reverting commit `0d21f15` and running `squid doctor` produces a FAIL
  with diagnostic naming `move_to_corner` as the root cause.
- A new contributor cannot land a PR that introduces a direct
  off-main-thread NSWindow call (pre-commit + CI both reject).
- Distribution-installer adds `squid doctor` to its post-install
  verification step.
