# Startup Safety — the Four-Layer Defense

> _On 2026-06-16, Squid silently shipped a wedge: pywebview reinstall
> changed which thread `on_loaded` fired on. `move_to_corner` was now
> called from a WebKit content thread. macOS 14+ blocks NSWindow
> setters from non-main threads silently — no exception, no log, no
> crash. The window stayed at pywebview's default (100, 100). Every
> existing health check passed. Pink saw "Squid died" with no signal._

This document describes the four mechanically-enforced layers that
make the bug class structurally impossible to ship.

## Layer 1 — `@cocoa_main_thread` decorator
Module: [`src/squid_pet/threading_guards.py`](../src/squid_pet/threading_guards.py)

```python
from squid_pet.threading_guards import cocoa_main_thread

@cocoa_main_thread
def move_to_corner(corner: str) -> bool:
    nw.setFrameOrigin_(NSPoint(x, y))
    return True
```

- If called on the main thread → runs inline, return value propagates.
- If called from a worker thread → dispatches via `AppHelper.callAfter`
  and returns `None` immediately (fire-and-forget).
- Need a return value from off-thread? Use `@cocoa_main_thread_blocking`
  (5 s timeout, propagates exceptions).
- Mac-only: on non-Mac systems both decorators degrade to pass-through
  so Linux CI doesn't break on unrelated unit tests.

## Layer 2 — `python -m squid_pet --doctor`
Module: [`src/squid_pet/doctor.py`](../src/squid_pet/doctor.py)

Six-check end-to-end self-test. Verifies the **user-visible contract**
(window actually rendered at a sane position), not just "process exists":

| # | Check | What it verifies |
|---|---|---|
| 1 | process running | pid file exists; `kill -0` succeeds |
| 2 | state.json fresh | mtime within 5 s (watcher loop alive) |
| 3 | launchd loaded | `launchctl list com.pink.squid-pet` rc==0 |
| 4 | window visible | `CGWindowListCopyWindowInfo` returns a window with alpha>0 and on-screen for our pid |
| 5 | window not wedged | window NOT within 10 px of pywebview default (100, 100) |
| 6 | startup log markers | HEAD of `/tmp/squid-pet.out.log` contains all 5 boot markers |

Exit codes: `0` = healthy, `N` = check N failed (so CI can blame).
JSON output: `--doctor-json` for machine consumption.

**If Squid seems missing, the very first thing to run is `python -m squid_pet --doctor`.**

## Layer 3 — pre-commit hook
- Script: [`scripts/check_cocoa_main_thread.py`](../scripts/check_cocoa_main_thread.py)
- Config: [`.pre-commit-config.yaml`](../.pre-commit-config.yaml)
- Install once: `pip install pre-commit && pre-commit install`

AST-based detector. Refuses to commit any new `NSWindow.*` / `NSApp.*`
setter call that isn't covered by **any one** of:
1. An enclosing function decorated with `@cocoa_main_thread` /
   `@cocoa_main_thread_blocking`.
2. An enclosing function whose name appears as the first arg to
   `AppHelper.callAfter(<name>)` somewhere in the file.
3. A trailing `# noqa: cocoa-main-thread` justification on that line.

Manual scan: `python scripts/check_cocoa_main_thread.py src/squid_pet/*.py`

## Layer 4 — CI smoke test
> **Status: deferred.** Not wired up yet. The repo now lives on a private
> github.com remote, so macOS-latest runners are available -- this is a
> matter of spending the minutes, not of missing infrastructure.

Intent: macOS-latest runner boots Squid, sleeps 10 s, runs
`squid-pet --doctor-json`, asserts `healthy: true`. Catches Cocoa
thread-safety regressions and pywebview-version incompatibilities
before merge.

## What to do when a check fails

| Symptom | Suspect |
|---|---|
| Window is missing OR doctor check 5 = wedged | Thread-safety regression. Run the pre-commit hook manually on recent diffs. Look for new NSWindow setters without `@cocoa_main_thread`. |
| Doctor check 6 missing "passthrough loop started" | Likely an exception during passthrough init. Check `/tmp/squid-pet.err.log`. |
| Doctor check 2 stale | Watcher thread crashed or wedged. Check `/tmp/squid-pet.err.log` for tracebacks. |
| Doctor check 3 not loaded | LaunchAgent plist not bootstrapped. `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.pink.squid-pet.plist` |
| Doctor check 4 = no visible window | More serious than check 5. The window doesn't exist in CGWindowList at all. Check pywebview installation. |

For canonical context: kennel drawer 239 (the 2026-06-16 wedge),
commit `0d21f15` (the band-aid fix), commits `626d524` + `7ae4032`
(layers 1 and 2).
