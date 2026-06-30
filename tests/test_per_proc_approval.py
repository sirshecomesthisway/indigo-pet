"""Pink-2026-06-29 follow-up: per-PID approval-wave eligibility.

After fixing the silent kill-switch bug, Pink reported that Squid
WOULD wave but never stopped, and waved for CPs she'd already replied
to. Root cause: per_process_max_idle_seconds is purely "longest idle"
with no concept of:

1. **Busy history.** A TPA that was opened hours ago and never observed
   running anything still has a giant idle time. It shouldn't trigger
   an approval wave -- nothing is actually waiting.

2. **Snooze cap.** Once approval has been waving for 2 minutes without
   Pink interacting, give up. She's seen it and chose to defer. The
   wave should re-fire only if that TPA cycles busy -> idle again
   (i.e. she replied and got a new response that's now awaiting input).

These tests pin down the new `per_process_pending_approval_idle()`
function's semantics. They use the lower-level _PER_PID dicts directly
rather than psutil so the logic is deterministic.
"""
from __future__ import annotations
import time
from unittest.mock import MagicMock

import pytest

from squid_pet import watcher


@pytest.fixture(autouse=True)
def _clear_per_pid_state():
    """Each test gets a fresh per-PID dict."""
    watcher._PER_PID_LAST_BUSY.clear()
    if hasattr(watcher, "_PER_PID_EVER_BUSY"):
        watcher._PER_PID_EVER_BUSY.clear()
    yield
    watcher._PER_PID_LAST_BUSY.clear()
    if hasattr(watcher, "_PER_PID_EVER_BUSY"):
        watcher._PER_PID_EVER_BUSY.clear()


def _proc(pid: int, cpu: float) -> MagicMock:
    p = MagicMock(pid=pid)
    p.cpu_percent.return_value = cpu
    return p


def test_pending_approval_ignores_never_busy_pid():
    """A TPA that has never been observed busy should not trigger approval.

    Pink's case: she opens a fresh TPA window, leaves it at the prompt,
    walks away. Nothing is awaiting her input -- but with the OLD
    behavior, idle time accumulated and would have falsely fired the
    flag wave."""
    p_never_busy = _proc(pid=1001, cpu=0.0)
    # Tick once to seed it
    watcher.per_process_pending_approval_idle([p_never_busy])
    # Force the LAST_BUSY backwards in time so it looks long-idle
    watcher._PER_PID_LAST_BUSY[1001] = time.time() - 60.0
    # Still no busy history -> still not eligible
    idle = watcher.per_process_pending_approval_idle([p_never_busy])
    assert idle == 0.0, (
        "PID never observed busy should never trigger approval, "
        f"got idle={idle}"
    )


def test_pending_approval_fires_after_busy_then_idle():
    """A TPA that WAS busy and is NOW idle past threshold = pending approval."""
    p = _proc(pid=2002, cpu=20.0)            # busy
    watcher.per_process_pending_approval_idle([p])
    # Now goes idle
    p.cpu_percent.return_value = 0.0
    # Backdate LAST_BUSY so 30s have elapsed
    watcher._PER_PID_LAST_BUSY[2002] = time.time() - 30.0
    idle = watcher.per_process_pending_approval_idle([p])
    assert 29.0 < idle < 31.0, f"expected ~30s idle, got {idle}"


def test_pending_approval_snoozes_after_window():
    """After SNOOZE_WINDOW_SEC of idle, the PID drops out -- Pink has
    clearly seen the wave and chosen to defer. No further waving."""
    p = _proc(pid=3003, cpu=20.0)
    watcher.per_process_pending_approval_idle([p])         # mark busy
    p.cpu_percent.return_value = 0.0
    # Idle for FOUR minutes -- well past the 2-min snooze cap
    watcher._PER_PID_LAST_BUSY[3003] = time.time() - 240.0
    idle = watcher.per_process_pending_approval_idle([p])
    assert idle == 0.0, (
        f"PID idle > snooze window should drop out, got {idle}"
    )


def test_pending_approval_re_fires_when_tpa_becomes_busy_again():
    """The snooze is per cycle, not per PID forever. If the snoozed TPA
    transitions busy -> idle again (Pink replied, got new response),
    the wave should re-fire."""
    p = _proc(pid=4004, cpu=20.0)
    watcher.per_process_pending_approval_idle([p])
    p.cpu_percent.return_value = 0.0
    # Snoozed (4 min idle)
    watcher._PER_PID_LAST_BUSY[4004] = time.time() - 240.0
    assert watcher.per_process_pending_approval_idle([p]) == 0.0  # snoozed

    # Pink replies -> TPA runs again
    p.cpu_percent.return_value = 25.0
    watcher.per_process_pending_approval_idle([p])         # bump LAST_BUSY=now
    # New response, TPA goes idle
    p.cpu_percent.return_value = 0.0
    watcher._PER_PID_LAST_BUSY[4004] = time.time() - 15.0
    idle = watcher.per_process_pending_approval_idle([p])
    assert 14.0 < idle < 16.0, (
        f"re-fire should report fresh idle ~15s, got {idle}"
    )


def test_pending_approval_max_across_multiple_eligible_pids():
    """When multiple CPs are eligible, return the MAX idle (mirrors the
    multi-TPA rule that drove the original per_proc design)."""
    p_short = _proc(pid=5005, cpu=20.0)
    p_long  = _proc(pid=5006, cpu=20.0)
    watcher.per_process_pending_approval_idle([p_short, p_long])
    for p in (p_short, p_long):
        p.cpu_percent.return_value = 0.0
    watcher._PER_PID_LAST_BUSY[5005] = time.time() - 15.0
    watcher._PER_PID_LAST_BUSY[5006] = time.time() - 45.0
    idle = watcher.per_process_pending_approval_idle([p_short, p_long])
    assert 44.0 < idle < 46.0, f"expected ~45s (the longer), got {idle}"


def test_pending_approval_ignores_pid_below_threshold_window():
    """PID idle BELOW threshold (e.g. 3s) shouldn't count as 'pending';
    it's just a normal between-tick lull. Threshold is 10s by default,
    enforced by the caller -- but we still report the raw idle time
    accurately so the caller can decide."""
    p = _proc(pid=6006, cpu=20.0)
    watcher.per_process_pending_approval_idle([p])
    p.cpu_percent.return_value = 0.0
    watcher._PER_PID_LAST_BUSY[6006] = time.time() - 3.0
    idle = watcher.per_process_pending_approval_idle([p])
    # We DO return the raw 3s here -- the 10s threshold gate lives
    # in compute(), not in this function. This keeps the function
    # cleanly testable + the threshold tunable via config.
    assert 2.5 < idle < 3.5, f"expected ~3s raw, got {idle}"
