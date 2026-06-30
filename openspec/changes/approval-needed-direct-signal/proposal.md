## Why

Squid currently has no spec-level definition of the `approval_needed` state
(the flag-wave that fires when TPA is sitting at the prompt waiting
for Pink's reply). Three sequential 2026-06-29 commits implemented and
hardened it, but the contract for what triggers it, what overrides it,
and how it clears was never written down. Today the trigger is a DIRECT
signal from TPA itself (`~/.tpa/awaiting_input/<pid>`), with a CPU
heuristic as fallback for older TPA versions — that distinction needs to
live in the spec so future work doesn't accidentally re-introduce
guessing behavior.

## What Changes

- Add `approval_needed` as the 9th emotional state and the highest-priority
  TPA-active state in the cascade, above `thinking`/`working`.
- Add a DIRECT signal contract: presence of any file at
  `~/.tpa/awaiting_input/<pid>` (alive PID) fires `approval_needed`
  instantly, with no threshold and no snooze.
- Add a FALLBACK trigger for TPA versions without the patch: per-process
  idle time (with ever-busy filter + 2-minute snooze) fires when the
  direct signal is absent.
- Add a kill switch: `approval_alert_enabled` in `~/.squid-pet/config.json`
  disables both triggers when false.
- Add the dead-PID eviction rule (crashed CPs do not leave a stuck signal).
- **BREAKING (internal only):** `per_process_max_idle_seconds` is no longer
  the input to `compute()`. Replaced by
  `per_process_pending_approval_idle()` (filters never-busy PIDs, applies
  snooze cap). Old function retained for backward compatibility but
  unused by the state machine.

## Capabilities

### New Capabilities
<!-- None — this is all state-detection behavior. -->

### Modified Capabilities
- `state-detection`: adds the `approval_needed` state, the direct
  awaiting_input signal contract, the per-process idle fallback contract,
  the kill switch, and dead-PID eviction. Updates the priority cascade.

## Impact

- **Code:** `src/squid_pet/watcher.py` — new `tpa_pids_awaiting_input()`,
  new `per_process_pending_approval_idle()`, reworked approval branch in
  `StateMachine.compute()`.
- **External patch:** `~/.tpa/cpts_patch/sitecustomize.py` —
  wraps `tpa.command_line.prompt_toolkit_completion.
  get_input_with_combined_completion` to write/delete the flag file.
  Lives outside the repo but is part of the same architectural contract.
- **Config:** `approval_alert_enabled` (bool, default true),
  `approval_alert_threshold_sec` (float, default 10.0),
  `approval_alert_sound` (string, default "Glass"),
  `approval_alert_text` (string, default "your turn").
- **CLI:** `squid why` surfaces `approval_alert.{enabled, threshold_sec,
  per_proc_max_idle_sec}` and prints a warning when the alert is OFF but
  the per-proc idle exceeds threshold.
- **Tests:** 13 new tests across `test_awaiting_input_signal.py` (7),
  `test_per_proc_approval.py` (6), and 2 added to `test_why_cli.py`.
  Suite: 282 passing.
- **Backward compat:** Existing TPA processes without the sitecustomize
  patch fall through to the CPU heuristic and continue to work.
