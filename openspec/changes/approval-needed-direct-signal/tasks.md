# Implementation Tasks

> **Status:** All tasks below are COMPLETE. This change proposal is a
> retroactive spec update for work shipped on 2026-06-29 across three
> commits (`8c6395b`, the snooze fix, and `a37c05a`). The checkboxes are
> ticked to record what was built; the proposal exists so the spec
> reflects reality.

## 1. Squid-side: direct awaiting-input signal

- [x] 1.1 Add `_AWAITING_INPUT_DIR = ~/.tpa/awaiting_input` constant to `watcher.py`
- [x] 1.2 Implement `tpa_pids_awaiting_input() -> list[int]` (alive-PID scan, dead-PID eviction, non-digit filename ignore, missing-dir tolerance)
- [x] 1.3 Wire `tpa_pids_awaiting_input()` into `StateMachine.compute()` as the highest-priority TPA-active trigger
- [x] 1.4 Ensure direct signal fires `approval_needed` with `state_reason` `"awaiting_input flag from TPA pid(s) ..."` and message from `approval_alert_text` config
- [x] 1.5 Reset `_approval_alert_fired` latch on ticks where no alert fires so the next genuine alert pings the OS notification

## 2. Squid-side: per-PID fallback hardening

- [x] 2.1 Add `_PER_PID_EVER_BUSY: set[int]` so opened-and-abandoned TPA windows do not trigger the fallback
- [x] 2.2 Add `_PENDING_APPROVAL_SNOOZE_SEC = 120.0` and implement `per_process_pending_approval_idle()` returning 0.0 for PIDs idle past the cap
- [x] 2.3 Re-arm a snoozed PID automatically when it cycles busy then idle
- [x] 2.4 Switch `StateMachine.compute()` from `per_process_max_idle_seconds` to `per_process_pending_approval_idle`
- [x] 2.5 Keep `per_process_max_idle_seconds` as a thin compatibility shim (not deleted, but unused by the state machine)
- [x] 2.6 Evict dead PIDs from both `_PER_PID_LAST_BUSY` and `_PER_PID_EVER_BUSY` at each call

## 3. Squid-side: kill-switch visibility

- [x] 3.1 Read `approval_alert_enabled`, `approval_alert_threshold_sec`, `approval_alert_sound`, `approval_alert_text` from `~/.squid-pet/config.json` in `compute()`
- [x] 3.2 Add `approval_alert` block (`enabled`, `threshold_sec`, `per_proc_max_idle_sec`) to `squid why --json` output
- [x] 3.3 Add `APPROVAL ALERT: ON/OFF (threshold=Xs, per_proc_max_idle=Ys)` line to `squid why` human output
- [x] 3.4 Print a yellow warning in the human output when the alert is OFF but `per_proc_max_idle >= threshold`

## 4. TPA-side patch (in `~/.tpa/cpts_patch/sitecustomize.py`)

- [x] 4.1 Add `_AWAITING_INPUT_DIR` + `_AWAITING_INPUT_FLAG = <dir>/<pid>` constants
- [x] 4.2 Implement `_patch_awaiting_input(module)` that wraps `get_input_with_combined_completion`
- [x] 4.3 On entry to the wrapped function: `os.makedirs` + `open(flag, "w").write(str(os.getpid()))`
- [x] 4.4 On every exit path (normal return, Keyboarderrupt, EOFError, exception): `os.unlink(flag)` inside a `finally` block
- [x] 4.5 Mark module with `_cpts_awaiting_input_patched` to make the patch idempotent across import-hook re-fires
- [x] 4.6 Wire `_patch_awaiting_input` into `_maybe_patch` so the import hook applies it whenever `prompt_toolkit_completion` is in `sys.modules`

## 5. Tests (TDD: red first, then green)

- [x] 5.1 Create `tests/test_awaiting_input_signal.py` with 7 tests (missing dir, empty dir, alive PID, dead-PID eviction, multi-PID, non-numeric filename ignore, end-to-end `compute()` override)
- [x] 5.2 Create `tests/test_per_proc_approval.py` with 6 tests (never-busy filter, busy-then-idle fires, snooze cap, re-arm after re-busy, max across multi-PID, raw-idle below threshold)
- [x] 5.3 Add 2 tests to `tests/test_why_cli.py` for the kill-switch CLI surfacing (human + JSON)
- [x] 5.4 Verify all new tests fail before implementation (red phase)
- [x] 5.5 Verify full suite is 282/282 passing after implementation (was 269 before this change)

## 6. End-to-end validation

- [x] 6.1 Restart squid daemon, confirm state machine picks up new logic
- [x] 6.2 Verify with no flag: state is `working`/`thinking`/etc. (no approval)
- [x] 6.3 Verify with manually-written `awaiting_input/<my_pid>`: state flips to `approval_needed` within ~1 tick
- [x] 6.4 Verify deleting the flag: state returns to non-approval within ~1 tick (no snooze, no debounce)
- [x] 6.5 Verify the new state-reason format is human-readable in `squid why`

##Documentation / spec sync

- [x] 7.1 Write proposal.md, design.md, specs/state-detection/spec.md delta, and tasks.md (this file)
- [ ] 7.2 Validate the change with `openspec validate approval-needed-direct-signal --strict`
- [ ] 7.3 Archive the change with `openspec archive approval-needed-direct-signal` once Pink approves the spec language
