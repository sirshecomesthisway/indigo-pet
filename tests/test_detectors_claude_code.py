"""Tests for ClaudeCodeDetector -- mirrors test_detectors_tpa.py's
style. All psutil / filesystem dependencies are injected so no real
process table or disk is touched."""
from __future__ import annotations

from pathlib import Path

from squid_pet.detectors import ClaudeCodeDetector


class _FakeProc:
    def __init__(self, pid=1234): self.pid = pid


class _FakeStat:
    def __init__(self, mtime): self.st_mtime = mtime


def _make(
    procs=None, cpu=0.0, shell_active=False,
    transcripts=None,  # dict[str path] -> mtime
    enabled=True, projects_dir=None,
):
    transcripts = transcripts or {}
    paths = [Path(p) for p in transcripts]

    def _stat(p):
        if p in transcripts:
            return _FakeStat(transcripts[p])
        raise OSError(f"no such file: {p}")

    return ClaudeCodeDetector(
        enabled=enabled,
        find_processes_fn=lambda: list(procs or []),
        aggregate_cpu_fn=lambda p: cpu,
        has_active_shell_children_fn=lambda p: shell_active,
        projects_dir=projects_dir or Path("/fake/.claude/projects"),
        glob_fn=lambda root: iter(paths),
        stat_fn=_stat,
    )


def test_no_processes_is_quiet():
    d = _make(procs=[])
    assert d.is_busy(now=1000.0) is False
    assert d.is_celebrating(now=1000.0) is False
    assert d.is_grooving(now=1000.0) is False
    assert d.claude_code_running is False


def test_shell_active_fires_busy_immediately():
    """Live tool subprocess (e.g. a Bash-tool call) is busy on tick 1 --
    no streak needed, same as TPADetector's shell_active."""
    d = _make(procs=[_FakeProc()], cpu=0.5, shell_active=True)
    assert d.is_busy(now=1000.0) is True


def test_fresh_transcript_fires_streaming_and_busy():
    now = 1000.0
    d = _make(
        procs=[_FakeProc()],
        transcripts={"/fake/.claude/projects/p/s.jsonl": now - 2.0},
    )
    assert d.is_busy(now=now) is True
    assert d.streaming is True
    assert d.transcript_age == 2.0


def test_stale_transcript_is_quiet():
    now = 1000.0
    d = _make(
        procs=[_FakeProc()],
        transcripts={"/fake/.claude/projects/p/s.jsonl": now - 300.0},
    )
    assert d.is_busy(now=now) is False
    assert d.streaming is False


def test_no_transcripts_is_quiet():
    d = _make(procs=[_FakeProc()], transcripts={})
    assert d.is_busy(now=1000.0) is False
    assert d.transcript_age == float("inf")


def test_newest_transcript_wins_across_multiple_sessions():
    now = 1000.0
    d = _make(
        procs=[_FakeProc()],
        transcripts={
            "/fake/.claude/projects/a/old.jsonl": now - 500.0,
            "/fake/.claude/projects/b/new.jsonl": now - 3.0,
        },
    )
    d.is_busy(now=now)
    assert d.transcript_age == 3.0


def test_disabled_detector_always_returns_false():
    d = _make(
        procs=[_FakeProc()], shell_active=True,
        transcripts={"/x.jsonl": 999.5}, enabled=False,
    )
    assert d.is_busy(now=1000.0) is False
    assert d.is_celebrating(now=1000.0) is False
    assert d.is_grooving(now=1000.0) is False


def test_diagnostic_contains_required_keys():
    d = _make(procs=[_FakeProc()], cpu=12.5)
    d.is_busy(now=1000.0)
    diag = d.diagnostic()
    for key in ("name", "enabled", "claude_code_running", "cpu_percent",
                "shell_active", "transcript_age", "streaming"):
        assert key in diag, f"missing {key}"
    assert diag["name"] == "claude_code"


def test_scan_cache_dedupes_within_same_tick():
    """Calling is_busy with the same `now` twice should scan once."""
    calls = {"n": 0}
    procs = [_FakeProc()]

    def find():
        calls["n"] += 1
        return procs

    d = ClaudeCodeDetector(
        enabled=True,
        find_processes_fn=find,
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        projects_dir=Path("/fake"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
    )
    d.is_busy(now=5.0)
    d.is_busy(now=5.0)
    assert calls["n"] == 1
    d.is_busy(now=6.0)
    assert calls["n"] == 2


def test_discovery_cache_reused_within_window():
    """The candidate-file glob should only be re-run once per
    DISCOVERY_CACHE_SEC, mirroring GitDetector's repo-discovery cache."""
    calls = {"n": 0}

    def glob_fn(root):
        calls["n"] += 1
        return iter([Path("/fake/.claude/projects/p/s.jsonl")])

    d = ClaudeCodeDetector(
        enabled=True,
        find_processes_fn=lambda: [_FakeProc()],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        projects_dir=Path("/fake/.claude/projects"),
        glob_fn=glob_fn,
        stat_fn=lambda p: _FakeStat(995.0),
    )
    d.is_busy(now=1000.0)
    d.is_busy(now=1010.0)
    assert calls["n"] == 1  # still within 60s cache window
    d.is_busy(now=1000.0 + ClaudeCodeDetector.DISCOVERY_CACHE_SEC + 1)
    assert calls["n"] == 2  # cache expired, re-globbed
