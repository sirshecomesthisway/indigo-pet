"""Tests for TPADetector -- ports the legacy watcher behaviors
to the new detector class. All scan helpers are injected so no real
processes / files are touched."""
from __future__ import annotations

from squid_pet.detectors import TPADetector


class _FakeProc:
    def __init__(self, pid=1234): self.pid = pid


def _make(
    procs=None, cpu=0.0, shell_active=False,
    tool_age=float("inf"), subagent_age=float("inf"),
    enabled=True,
):
    return TPADetector(
        enabled=enabled,
        find_processes_fn=lambda: list(procs or []),
        aggregate_cpu_fn=lambda p: cpu,
        has_active_shell_children_fn=lambda p: shell_active,
        most_recent_tool_activity_age_fn=lambda: tool_age,
        newest_subagent_age_fn=lambda: subagent_age,
    )


def test_no_processes_is_quiet():
    d = _make(procs=[])
    assert d.is_busy(now=1.0) is False
    assert d.is_celebrating(now=1.0) is False
    assert d.is_grooving(now=1.0) is False
    assert d.tpa_running is False


def test_cpu_busy_requires_sustained_streak():
    """A single high-CPU tick should NOT count as busy (burst suppression)."""
    d = _make(procs=[_FakeProc()], cpu=20.0, tool_age=2.0)
    assert d.is_busy(now=1.0) is False    # streak=1 -> not yet sustained
    assert d.is_busy(now=2.0) is True     # streak=2 -> sustained


def test_shell_active_fires_busy_immediately():
    """Active shell child is busy on tick 1; no streak needed."""
    d = _make(procs=[_FakeProc()], cpu=0.5, shell_active=True)
    assert d.is_busy(now=1.0) is True


def test_subagent_age_triggers_grooving():
    d = _make(procs=[_FakeProc()], subagent_age=5.0)  # < SUBAGENT_ACTIVE_WINDOW_SEC (30)
    assert d.is_grooving(now=1.0) is True
    d2 = _make(procs=[_FakeProc()], subagent_age=100.0)
    assert d2.is_grooving(now=1.0) is False


def test_celebrate_fires_after_cpu_drop():
    """Busy CPU then quiet should fire celebrate sticky for CELEBRATE_DURATION_SEC."""
    procs = [_FakeProc()]
    state = {"cpu": 20.0}
    d = TPADetector(
        enabled=True,
        find_processes_fn=lambda: procs,
        aggregate_cpu_fn=lambda p: state["cpu"],
        has_active_shell_children_fn=lambda p: False,
        most_recent_tool_activity_age_fn=lambda: 2.0,
        newest_subagent_age_fn=lambda: float("inf"),
    )
    # Build up streak
    d.is_busy(now=1.0)
    d.is_busy(now=2.0)
    assert d.sustained_busy is True
    # CPU drops to zero -> celebrate fires
    state["cpu"] = 0.0
    d.is_celebrating(now=3.0)
    assert d.is_celebrating(now=3.5) is True
    assert d.is_celebrating(now=10.0) is False  # past CELEBRATE_DURATION_SEC=4


def test_disabled_detector_always_returns_false():
    d = _make(procs=[_FakeProc()], cpu=99.0, shell_active=True,
              tool_age=0.0, subagent_age=0.0, enabled=False)
    assert d.is_busy(now=1.0) is False
    assert d.is_celebrating(now=1.0) is False
    assert d.is_grooving(now=1.0) is False


def test_diagnostic_contains_required_keys():
    d = _make(procs=[_FakeProc()], cpu=12.5)
    d.is_busy(now=1.0)
    diag = d.diagnostic()
    for key in ("name", "enabled", "tpa_running", "cpu_percent",
                "shell_active", "subagent_age", "sustained_busy"):
        assert key in diag, f"missing {key}"
    assert diag["name"] == "tpa"


def test_scan_cache_dedupes_within_same_tick():
    """Calling is_busy/is_celebrating/is_grooving with same `now` should
    trigger the scan only once."""
    calls = {"n": 0}
    procs = [_FakeProc()]
    def find(): calls["n"] += 1; return procs
    d = TPADetector(
        enabled=True,
        find_processes_fn=find,
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        most_recent_tool_activity_age_fn=lambda: 100.0,
        newest_subagent_age_fn=lambda: 100.0,
    )
    d.is_busy(now=5.0); d.is_celebrating(now=5.0); d.is_grooving(now=5.0)
    assert calls["n"] == 1
    d.is_busy(now=6.0)
    assert calls["n"] == 2
