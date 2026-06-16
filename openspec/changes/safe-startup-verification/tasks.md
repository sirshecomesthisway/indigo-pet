# Tasks — Safe Startup Verification

## 1. Thread-safety decorator
- [ ] 1.1 Create `src/squid_pet/threading_guards.py` with `cocoa_main_thread` decorator (fire-and-forget) and `cocoa_main_thread_blocking` decorator (synchronous w/ 5s timeout)
- [ ] 1.2 Add module docstring linking to kennel drawer 239 and commit 0d21f15 (why this exists)
- [ ] 1.3 Handle non-Mac import path (NSThread import optional, runs inline if unavailable — keeps Linux CI green for non-Cocoa tests)
- [ ] 1.4 Unit tests: `tests/test_threading_guards.py` — main-thread fast path, off-thread dispatch path, blocking timeout, blocking exception propagation

## 2. Migrate existing Cocoa callsites
- [ ] 2.1 Audit: run grep pattern from drawer 239, generate VIOLATION list with file:line
- [ ] 2.2 Triage each violation: real vs false-positive (false-positive = whole enclosing function already dispatched via callAfter)
- [ ] 2.3 `src/squid_pet/window.py`: decorate `move_to_corner`, drag handlers, any newly-found violations
- [ ] 2.4 `src/squid_pet/menu.py:290`: decorate or wrap `makeKeyAndOrderFront_`
- [ ] 2.5 `src/squid_pet/passthrough.py`: verify `setIgnoresMouseEvents_` is properly dispatched (already via callAfter — confirm or upgrade to decorator)
- [ ] 2.6 Re-run audit, confirm zero violations
- [ ] 2.7 Run full test suite, confirm 121/121 still green

## 3. `squid doctor` subcommand
- [ ] 3.1 Add `doctor` subcommand to launcher (`bin/squid`) — Python entry point
- [ ] 3.2 Implement check 1 — process running (`pgrep -f "python -m squid_pet"`)
- [ ] 3.3 Implement check 2 — `~/.squid-pet/state.json` mtime < 5s ago
- [ ] 3.4 Implement check 3 — launchd job `com.pink.squid-pet` in `launchctl list`
- [ ] 3.5 Implement check 4 — CGWindowList shows window for PID with alpha>0 and on_screen=True
- [ ] 3.6 Implement check 5 — window bounds within tolerance of saved corner position
- [ ] 3.7 Implement check 6 — last N lines of `/tmp/squid-pet.out.log` contain all expected startup markers
- [ ] 3.8 Pretty-print output: `[N/6] <name> ... PASS|FAIL  (diagnostic)`
- [ ] 3.9 Each failure: diagnostic message + suggested fix + drawer link
- [ ] 3.10 Exit codes: 0 = all pass, N = first failing check number
- [ ] 3.11 `--json` flag for machine-readable output (CI consumes this)
- [ ] 3.12 Integration test against running instance

## 4. Improved launcher healthcheck
- [ ] 4.1 Refactor `bin/squid start` healthcheck to invoke doctor checks 1, 2, 4 (the fast subset)
- [ ] 4.2 On unhealthy detection: `launchctl bootout` FIRST, then `kill`, then verify gone, then `launchctl bootstrap`
- [ ] 4.3 Remove the "3 startup attempts" loop — replaced by single attempt + clear failure with doctor output
- [ ] 4.4 Manual repro test: revert commit 0d21f15 locally, run `squid start`, confirm clear failure (not 8x REFUSING TO START)

## 5. CI smoke test
- [ ] 5.1 Create `.github/workflows/smoke-test.yml`
- [ ] 5.2 Job: macos-latest runner, install uv, `uv pip install -e .`
- [ ] 5.3 Background-launch Squid, sleep 10s, run `squid doctor --json`, parse exit code
- [ ] 5.4 On failure: upload `/tmp/squid-pet.out.log` + `/tmp/squid-pet.err.log` as artifacts
- [ ] 5.5 Trigger: every PR + nightly cron on main
- [ ] 5.6 Verify CI catches the bug class: open a PR that synthetically reverts commit 0d21f15, confirm CI fails with diagnostic

## 6. Pre-commit grep audit
- [ ] 6.1 Add `.pre-commit-config.yaml` if not present
- [ ] 6.2 Local hook: shell script that runs the drawer-239 grep against staged files
- [ ] 6.3 Fail if a new direct NSWindow/NSApp/NSScreen setter appears without `@cocoa_main_thread` or `callAfter` within 5 lines
- [ ] 6.4 Documented bypass: `# noqa: cocoa-main-thread` comment with justification required
- [ ] 6.5 Document in `docs/CONTRIBUTING.md` (new file or section)

## 7. Documentation
- [ ] 7.1 `docs/STARTUP_SAFETY.md` — explain the three layers (decorator, doctor, CI), why they exist, what to do when a check fails
- [ ] 7.2 Update `README.md` — add "if Squid seems missing, run `squid doctor`"
- [ ] 7.3 Cross-link from `distribution-installer` proposal — installer's post-install step invokes `squid doctor`

## 8. Commit + memory
- [ ] 8.1 Commit logical batches (decorator, migration, doctor, healthcheck, CI, pre-commit, docs as 6-7 separate commits)
- [ ] 8.2 Push both remotes
- [ ] 8.3 File kennel drawer (decisions room) — the four-layer defense pattern, applicable to future cross-platform UI projects
- [ ] 8.4 Update `~/.tpa/agent_memory/pink-pm/squid-pet.md` — mark this change archived, link decorator pattern as repo standard
- [ ] 8.5 `openspec archive safe-startup-verification` once all checks green
