"""Tests for CodexDetector -- mirrors test_detectors_claude_code.py's
style (the two detectors are structurally identical, see CodexDetector's
docstring for why they're independent classes rather than sharing a
base). All psutil / filesystem dependencies are injected so no real
process table or disk is touched."""
from __future__ import annotations

from pathlib import Path

from squid_pet.detectors import CodexDetector


class _FakeProc:
    def __init__(self, pid=1234): self.pid = pid


class _FakeStat:
    def __init__(self, mtime): self.st_mtime = mtime


def _make(
    procs=None, cpu=0.0, shell_active=False,
    transcripts=None,  # dict[str path] -> mtime
    enabled=True, sessions_dir=None, file_ages=None,
):
    transcripts = transcripts or {}
    paths = [Path(p) for p in transcripts]

    def _stat(p):
        if p in transcripts:
            return _FakeStat(transcripts[p])
        raise OSError(f"no such file: {p}")

    return CodexDetector(
        enabled=enabled,
        find_processes_fn=lambda: list(procs or []),
        aggregate_cpu_fn=lambda p: cpu,
        has_active_shell_children_fn=lambda p: shell_active,
        sessions_dir=sessions_dir or Path("/fake/.codex/sessions"),
        glob_fn=lambda root: iter(paths),
        stat_fn=_stat,
        # Hermetic by default: no real disk walk. Tests exercising
        # file_active pass file_ages explicitly.
        recent_file_ages_fn=lambda: list(file_ages or []),
    )


def test_no_processes_is_quiet():
    d = _make(procs=[])
    assert d.is_busy(now=1000.0) is False
    assert d.is_celebrating(now=1000.0) is False
    assert d.is_grooving(now=1000.0) is False
    assert d.codex_running is False


def test_shell_active_fires_busy_immediately():
    """Live tool subprocess (e.g. a shell command) is busy on tick 1 --
    no streak needed, same as TPADetector's shell_active."""
    d = _make(procs=[_FakeProc()], cpu=0.5, shell_active=True)
    assert d.is_busy(now=1000.0) is True


def test_fresh_transcript_fires_streaming_and_busy():
    now = 1000.0
    d = _make(
        procs=[_FakeProc()],
        transcripts={"/fake/.codex/sessions/2026/08/14/s.jsonl": now - 2.0},
    )
    assert d.is_busy(now=now) is True
    assert d.streaming is True
    assert d.transcript_age == 2.0


def test_stale_transcript_is_quiet():
    now = 1000.0
    d = _make(
        procs=[_FakeProc()],
        transcripts={"/fake/.codex/sessions/2026/08/14/s.jsonl": now - 300.0},
    )
    assert d.is_busy(now=now) is False
    assert d.streaming is False


def test_no_transcripts_is_quiet():
    d = _make(procs=[_FakeProc()], transcripts={})
    assert d.is_busy(now=1000.0) is False
    assert d.transcript_age == float("inf")


def test_newest_transcript_wins_across_nested_date_dirs():
    """Codex nests sessions by date (YYYY/MM/DD/*.jsonl) rather than
    ClaudeCodeDetector's flat two-level layout -- the recursive glob
    pattern must still find the youngest one."""
    now = 1000.0
    d = _make(
        procs=[_FakeProc()],
        transcripts={
            "/fake/.codex/sessions/2026/08/01/old.jsonl": now - 500.0,
            "/fake/.codex/sessions/2026/08/14/new.jsonl": now - 3.0,
        },
    )
    d.is_busy(now=now)
    assert d.transcript_age == 3.0


def test_recent_file_write_fires_busy_and_file_active():
    """apply_patch-style tool calls never spawn a subprocess, so
    shell_active alone misses them -- same gap and fix as
    ClaudeCodeDetector's file_active signal."""
    d = _make(procs=[_FakeProc()], file_ages=[2.0])
    assert d.is_busy(now=1000.0) is True
    assert d.file_active is True


def test_stale_file_write_is_quiet():
    d = _make(procs=[_FakeProc()], file_ages=[])
    assert d.is_busy(now=1000.0) is False
    assert d.file_active is False


def test_file_active_requires_process_running():
    d = _make(procs=[], file_ages=[1.0])
    assert d.is_busy(now=1000.0) is False
    assert d.file_active is False


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
    for key in ("name", "enabled", "codex_running", "cpu_percent",
                "shell_active", "file_active", "transcript_age", "streaming"):
        assert key in diag, f"missing {key}"
    assert diag["name"] == "codex"


def test_scan_cache_dedupes_within_same_tick():
    """Calling is_busy with the same `now` twice should scan once."""
    calls = {"n": 0}
    procs = [_FakeProc()]

    def find():
        calls["n"] += 1
        return procs

    d = CodexDetector(
        enabled=True,
        find_processes_fn=find,
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        sessions_dir=Path("/fake"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        recent_file_ages_fn=lambda: [],
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
        return iter([Path("/fake/.codex/sessions/2026/08/14/s.jsonl")])

    d = CodexDetector(
        enabled=True,
        find_processes_fn=lambda: [_FakeProc()],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        sessions_dir=Path("/fake/.codex/sessions"),
        glob_fn=glob_fn,
        stat_fn=lambda p: _FakeStat(995.0),
        recent_file_ages_fn=lambda: [],
    )
    d.is_busy(now=1000.0)
    d.is_busy(now=1010.0)
    assert calls["n"] == 1  # still within 60s cache window
    d.is_busy(now=1000.0 + CodexDetector.DISCOVERY_CACHE_SEC + 1)
    assert calls["n"] == 2  # cache expired, re-globbed


# ── CPU fix 2 (2026-09-03): one child-tree walk per tick ──────────────
class _WalkCountingProc:
    def __init__(self, children):
        self.pid = 4321
        self._children = children
        self.walks = 0

    def children(self, recursive=False):
        self.walks += 1
        return self._children


class _WalkChild:
    def __init__(self, name, cmdline):
        self._name, self._cmdline = name, cmdline

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmdline


def test_uninjected_shell_signals_walk_the_child_tree_once_per_tick():
    """Codex runs the same shell wiring as Claude Code, so it pays the
    same double walk -- both agents live meant four traversals a second."""
    proc = _WalkCountingProc([_WalkChild("rg", ["rg", "-n", "TODO"])])
    d = CodexDetector(
        find_processes_fn=lambda: [proc],
        aggregate_cpu_fn=lambda p: 0.0,
        sessions_dir=Path("/fake/.codex/sessions"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        recent_file_ages_fn=lambda: [],
    )

    d.is_busy(now=1000.0)

    assert proc.walks == 1
    assert d.shell_active is True
    assert d.shell_cmdline == ["rg", "-n", "TODO"]
