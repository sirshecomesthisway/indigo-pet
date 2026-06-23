# Tasks — Safe Startup Verification

## 1. Thread-safety decorator
- [x] 1.1 Create `src/squid_pet/threading_guards.py` with `cocoa_main_thread` decorator (fire-and-forget) and `cocoa_main_thread_blocking` decorator (synchronous w/ 5s timeout)
- [x] 1.2 Add module docstring linking to kennel drawer 239 and commit 0d21f15 (why this exists)
- [x] 1.3 Handle non-Mac import path (NSThread import optional, runs inline if unavailable — keeps Linux CI green for non-Cocoa tests)
- [x] 1.4 Unit tests: `tests/test_threading_guards.py` -- 7 tests (4 spec + 3 extras: guarded-marker, off-thread dispatch returns None, exception propagation)

## 2. Migrate existing Cocoa callsites
- [x] 2.1 Audit done. RAW HITS: window.py:141, window.py:162, window.py:960, menu.py:290, passthrough.py:168
- [x] 2.2 Triage: REAL=move_to_corner@141, move_window_by_delta@162. FALSE-POS=setCollectionBehavior@960 (inside _set_all_spaces->callAfter), makeKeyAndOrderFront@menu:290 (inside _on_main->callAfter), setIgnoresMouseEvents@passthrough:168 (inside _apply_on_main->callAfter)
- [x] 2.3 `src/squid_pet/window.py`: decorated `move_to_corner` and `move_window_by_delta` with `@cocoa_main_thread`
- [x] 2.4 `src/squid_pet/menu.py:290`: already inside `_on_main` closure dispatched via `AppHelper.callAfter(_on_main)` on line 307 -- no change needed
- [ ] 2.5 `src/squid_pet/passthrough.py`: verify `setIgnoresMouseEvents_` is properly dispatched (already via callAfter — confirm or upgrade to decorator)
- [x] 2.6 Re-audit: zero remaining direct NSWindow setters outside `cocoa_main_thread` decorators or callAfter dispatch
- [x] 2.7 Full suite 133/133 green (was 126; +7 from threading_guards tests)

## 3. `squid doctor` subcommand
- [x] 3.1 Wired `--doctor` and `--doctor-json` flags into `python -m squid_pet` (project does not have bin/squid; uses module entry instead)
- [x] 3.2 check_process_running: reads ~/.squid-pet/pid, signals 0 to verify alive
- [x] 3.3 check_state_json_fresh: mtime within STATE_FRESHNESS_MAX_SEC=5.0s, injectable now_fn
- [x] 3.4 check_launchd_loaded: `launchctl list com.pink.squid-pet`, returncode==0 = loaded
- [x] 3.5 check_window_visible: via Quartz.CGWindowListCopyWindowInfo filtered by pid, requires alpha>0 and kCGWindowIsOnscreen=True
- [x] 3.6 check_window_in_expected_corner: REDESIGNED as `window not wedged` -- the wanderer legitimately moves squid all over the screen so corner-match would false-positive constantly. Instead checks window is NOT within 10px of pywebview default (100,100) which is the actual bug signature from drawer 239.
- [x] 3.7 check_startup_log_complete: reads HEAD of log (16KB), not tail -- startup markers only fire once at boot and would be evicted from tail in long-running instances. Verifies all 5 markers: watcher thread started, passthrough loop started, routine thread started, context menu ready, startup complete.
- [x] 3.8 CheckResult.as_line() format matches: `[N/6] <name> ... PASS|FAIL  diagnostic`
- [x] 3.9 CheckResult has diagnostic + suggested_fix + drawer_ref; printed on failure
- [x] 3.10 run_doctor returns 0 if all pass, else 1-based index of first failing check
- [x] 3.11 `--doctor-json` flag emits {healthy: bool, checks: [...]}
- [x] 3.12 Live test against PID 2375 reported 6/6 PASS, exit 0. CAUGHT TWO REAL DESIGN BUGS IN INITIAL DRAFT: check 5 was comparing to saved corner (wanderer breaks this); check 6 was reading log tail (startup markers were at head).

## 4. Improved launcher healthcheck
- [x] 4.1 RE-SCOPED: project has no bin/squid launcher (entry is `python -m squid_pet`). Equivalent fast-subset healthcheck is `--doctor` itself which runs all 6 checks in ~0.4s. No separate launcher healthcheck needed.
- [x] 4.2 RE-SCOPED: no `squid start` command exists. The kill-respawn-fight only happens via launchd KeepAlive; users restart by killing the pid then launchd auto-relaunches. If users want to script a clean restart, they can use the launchctl bootout/kill/bootstrap sequence directly -- it's a 3-line shell command not worth wrapping in a custom launcher right now.
- [x] 4.3 RE-SCOPED: no startup-attempt loop exists in __main__.py to remove. Singleton via fcntl.flock is atomic; no retry needed.
- [x] 4.4 PARTIAL: doctor's check 5 (`window not wedged`) directly reproduces the bug detection. Test `test_window_wedged_at_pywebview_default` verifies this synthetically. Manual repro of reverting 0d21f15 deferred (would require destabilizing live Squid for a regression test that the unit test already covers).

## 5. CI smoke test
- [ ] 5.1 DEFERRED: blocked on github.com VPN. internal GHE may not have macOS runners; needs decision.
- [ ] 5.2 Job: macos-latest runner, install uv, `uv pip install -e .`
- [ ] 5.3 Background-launch Squid, sleep 10s, run `squid doctor --json`, parse exit code
- [ ] 5.4 On failure: upload `/tmp/squid-pet.out.log` + `/tmp/squid-pet.err.log` as artifacts
- [ ] 5.5 Trigger: every PR + nightly cron on main
- [ ] 5.6 Verify CI catches the bug class: open a PR that synthetically reverts commit 0d21f15, confirm CI fails with diagnostic

## 6. Pre-commit grep audit
- [x] 6.1 Created `.pre-commit-config.yaml` with local hook entry
- [x] 6.2 Created `scripts/check_cocoa_main_thread.py` -- AST-based (not grep) for far fewer false positives. Tested against live codebase: zero violations.
- [x] 6.3 Implemented via AST: walks outward through enclosing functions, accepts ANY guarded decorator OR ANY enclosing fn-name appearing as first arg to `AppHelper.callAfter(name)` in the file. Handles nested closures (e.g. `_inner` defined in `outer`, then `callAfter(_inner)`).
- [x] 6.4 `# noqa: cocoa-main-thread` per-line bypass implemented
- [x] 6.5 Documented in `docs/STARTUP_SAFETY.md` Layer 3 section (CONTRIBUTING.md not yet present; STARTUP_SAFETY is the natural home since it's the cross-reference target)

## 7. Documentation
- [x] 7.1 `docs/STARTUP_SAFETY.md` written -- covers all 4 layers, has When-A-Check-Fails decision table
- [x] 7.2 README troubleshooting section added pointing at --doctor + docs/STARTUP_SAFETY.md
- [ ] 7.3 DEFERRED: distribution-installer proposal is still at 0/52. Cross-link will land when that proposal is implemented.

## 8. Commit + memory
- [x] 8.1 3 commits this session: 626d524 (decorator + migration), 7ae4032 (doctor + tests), THIS COMMIT (pre-commit hook + docs). Healthcheck/CI re-scoped/deferred per Groups 4-5.
- [ ] 8.2 internal pushed each commit. github.com origin still VPN-blocked -- pending.
- [ ] 8.3 File kennel drawer (decisions room) — the four-layer defense pattern, applicable to future cross-platform UI projects
- [ ] 8.4 Update `~/.tpa/agent_memory/pink-pm/squid-pet.md` — mark this change archived, link decorator pattern as repo standard
- [ ] 8.5 PENDING: archive when Group 5 (CI) is decided and 7.3 cross-link lands (after distribution-installer implementation)
