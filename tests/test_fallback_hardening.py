"""Pink-2026-06-30: harden the approval fallback to stop false-firing
for legacy TPA processes that don't have the awaiting_input patch.

Two new behaviors:

1. **Auto-detect patched CPs.** If a PID has EVER been observed
   writing its awaiting_input flag, mark it as patched and SKIP the
   fallback for it. The direct signal is the only path of truth for
   patched CPs -- no CPU heuristic, no false fires from GC noise.

2. **Require sustained busy.** A single tick over the CPU threshold
   no longer adds a PID to _PER_PID_EVER_BUSY. We require N=3
   consecutive busy ticks. A real LLM call easily sustains 3 seconds
   of CPU; Python GC blips and prompt_toolkit redraws do not.
"""
from __future__ import annotations
import os
import time
from unittest.mock import MagicMock

import pytest

from squid_pet import watcher


@pytest.fixture(autouse=True)
def _clear_per_pid_state():
    watcher._PER_PID_LAST_BUSY.clear()
    watcher._PER_PID_EVER_BUSY.clear()
    watcher._PER_PID_BUSY_STREAK.clear()
    watcher._PER_PID_EVER_WROTE_FLAG.clear()
    yield
    watcher._PER_PID_LAST_BUSY.clear()
    watcher._PER_PID_EVER_BUSY.clear()
    watcher._PER_PID_BUSY_STREAK.clear()
    watcher._PER_PID_EVER_WROTE_FLAG.clear()


@pytest.fixture
def tmp_awaiting_dir(tmp_path, monkeypatch):
    d = tmp_path / "awaiting_input"
    d.mkdir()
    monkeypatch.setattr(watcher, "_AWAITING_INPUT_DIR", str(d))
    return d


def _proc(pid: int, cpu: float) -> MagicMock:
    p = MagicMock(pid=pid)
    p.cpu_percent.return_value = cpu
    return p


# ── Sustained-busy requirement ─────────────────────────────────────

def test_single_busy_tick_does_NOT_mark_ever_busy():
    """One blip above the threshold isn't enough -- could be GC, redraw,
    or any random Python bookkeeping. Need sustained activity."""
    p = _proc(pid=7001, cpu=20.0)  # one blip
    watcher.per_process_pending_approval_idle([p])
    # Now goes idle immediately
    p.cpu_percent.return_value = 0.0
    watcher._PER_PID_LAST_BUSY[7001] = time.time() - 30.0
    idle = watcher.per_process_pending_approval_idle([p])
    assert idle == 0.0, (
        "single busy blip should not register as 'ever busy'; "
        f"got fallback idle={idle}"
    )


def test_three_consecutive_busy_ticks_marks_ever_busy():
    """N=3 sustained ticks is real activity, qualifies for fallback."""
    p = _proc(pid=7002, cpu=20.0)
    # Three consecutive busy ticks
    for _ in range(3):
        watcher.per_process_pending_approval_idle([p])
    # Goes idle
    p.cpu_percent.return_value = 0.0
    watcher._PER_PID_LAST_BUSY[7002] = time.time() - 30.0
    idle = watcher.per_process_pending_approval_idle([p])
    assert 29.0 < idle < 31.0, (
        f"3 sustained busy ticks should qualify; got idle={idle}"
    )


def test_busy_streak_breaks_on_idle_tick():
    """If a PID has 2 busy ticks then 1 idle tick, the streak resets.
    Next busy tick starts the count over."""
    p = _proc(pid=7003, cpu=20.0)
    watcher.per_process_pending_approval_idle([p])  # tick 1: busy
    watcher.per_process_pending_approval_idle([p])  # tick 2: busy
    p.cpu_percent.return_value = 0.0
    watcher.per_process_pending_approval_idle([p])  # tick 3: idle -> reset
    p.cpu_percent.return_value = 20.0
    watcher.per_process_pending_approval_idle([p])  # tick 4: busy (streak=1)
    # Should NOT be in EVER_BUSY yet -- streak interrupted
    p.cpu_percent.return_value = 0.0
    watcher._PER_PID_LAST_BUSY[7003] = time.time() - 30.0
    idle = watcher.per_process_pending_approval_idle([p])
    assert idle == 0.0, f"interrupted streak shouldn't qualify; got {idle}"


# ── Auto-detect patched CPs ────────────────────────────────────────

def test_patched_tpa_is_skipped_by_fallback(tmp_awaiting_dir):
    """Once a PID has written its awaiting_input flag at any point,
    the fallback should NEVER fire for it again. The direct signal
    is the only source of truth for patched CPs."""
    my_pid = os.getpid()

    # Step 1: simulate TPA writing its flag (we've seen it speak the protocol)
    (tmp_awaiting_dir / str(my_pid)).write_text("hi")
    watcher.tpa_pids_awaiting_input()  # this should record it

    # Step 2: flag goes away (Pink typed)
    (tmp_awaiting_dir / str(my_pid)).unlink()

    # Step 3: make the PID look very busy then idle past threshold
    p = _proc(pid=my_pid, cpu=20.0)
    for _ in range(5):  # well above sustained-busy bar
        watcher.per_process_pending_approval_idle([p])
    p.cpu_percent.return_value = 0.0
    watcher._PER_PID_LAST_BUSY[my_pid] = time.time() - 60.0

    idle = watcher.per_process_pending_approval_idle([p])
    assert idle == 0.0, (
        "patched TPA must be skipped by fallback; "
        f"got idle={idle}, ever_wrote_flag={watcher._PER_PID_EVER_WROTE_FLAG}"
    )


def test_unpatched_tpa_still_uses_fallback(tmp_awaiting_dir):
    """A PID that has NEVER written its flag is presumed legacy.
    Fallback still applies (with sustained-busy gate)."""
    legacy_pid = 8888
    # No flag write happens.

    p = _proc(pid=legacy_pid, cpu=20.0)
    for _ in range(3):
        watcher.per_process_pending_approval_idle([p])
    p.cpu_percent.return_value = 0.0
    watcher._PER_PID_LAST_BUSY[legacy_pid] = time.time() - 30.0
    idle = watcher.per_process_pending_approval_idle([p])
    assert 29.0 < idle < 31.0, (
        f"unpatched PID should still use fallback; got {idle}"
    )


def test_dead_patched_pid_evicted_from_known_set(tmp_awaiting_dir):
    """When a patched PID dies, drop it from _PER_PID_EVER_WROTE_FLAG so
    a future PID with the same number isn't accidentally trusted."""
    dead_pid = 999999
    (tmp_awaiting_dir / str(dead_pid)).write_text("hi")
    watcher.tpa_pids_awaiting_input()  # records + evicts file (PID dead)
    # The set should also have been pruned
    assert dead_pid not in watcher._PER_PID_EVER_WROTE_FLAG, (
        "dead patched PID should be evicted from the trust set"
    )
