"""StateMachine cascade tests for ClaudeCodeDetector -- verifies it gets
promoted into the rich working/thinking distinction (branch 4) instead
of falling through the flat generic OR-fallback (branch 5, which always
labels 'thinking' regardless of source -- see claude-code-detector
proposal.md for why that was the bug being fixed).
"""
from __future__ import annotations

from pathlib import Path

from squid_pet import watcher
from squid_pet.watcher import StateMachine
from squid_pet.detectors import ClaudeCodeDetector


def install_world(monkeypatch, idle=0.0):
    """Stub the non-detector-owned signals StateMachine still reads
    directly (idle time, the TPA presence check for approval_needed/
    schema purposes)."""
    monkeypatch.setattr(watcher, "macos_idle_seconds", lambda: idle)
    monkeypatch.setattr(watcher, "find_tpa_processes", lambda: [])


def _claude_machine(monkeypatch, *, shell_active=False, transcript_age_sec=float("inf"),
                     file_ages=None):
    install_world(monkeypatch)
    now_ref = {"v": 1_000_000.0}

    def _stat(p):
        class _S:
            st_mtime = now_ref["v"] - transcript_age_sec
        return _S()

    claude = ClaudeCodeDetector(
        find_processes_fn=lambda: ["fake-claude-proc"],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: shell_active,
        projects_dir=Path("/fake/.claude/projects"),
        glob_fn=lambda root: iter(
            [] if transcript_age_sec == float("inf")
            else [Path("/fake/.claude/projects/p/s.jsonl")]
        ),
        stat_fn=_stat,
        # Hermetic: no real disk walk under ~/Projects (default would
        # pick up this very repo's own recent edits mid-test-run).
        recent_file_ages_fn=lambda: list(file_ages or []),
    )
    sm = StateMachine(detectors=[claude])
    # StateMachine.compute() uses time.time() internally for `now`, but
    # our fake stat_fn keys off a fixed reference instead -- patch
    # time.time so the transcript-age math lines up with now_ref.
    monkeypatch.setattr(watcher.time, "time", lambda: now_ref["v"])
    return sm


def test_claude_only_shell_active_yields_working(monkeypatch):
    sm = _claude_machine(monkeypatch, shell_active=True)
    st = sm.compute()
    assert st.state == "working"
    assert st.claude_code_running is True
    assert st.tpa_running is False


def test_claude_only_file_write_yields_working(monkeypatch):
    """Edit/Write-style tool calls don't spawn a subprocess -- the
    file-write signal is what catches this (regression test for the
    2026-08-14 user report: Squid stayed 'thinking' while Claude was
    actively editing files)."""
    sm = _claude_machine(monkeypatch, shell_active=False, file_ages=[2.0])
    st = sm.compute()
    assert st.state == "working"
    assert st.state_reason == "file write detected (claude_code)"


def test_claude_only_fresh_transcript_no_shell_yields_thinking(monkeypatch):
    sm = _claude_machine(monkeypatch, shell_active=False, transcript_age_sec=2.0)
    st = sm.compute()
    assert st.state == "thinking"
    assert st.state_reason == "claude streaming"
    assert st.claude_code_running is True


def test_claude_only_stale_transcript_no_shell_falls_to_idle(monkeypatch):
    sm = _claude_machine(monkeypatch, shell_active=False, transcript_age_sec=300.0)
    st = sm.compute()
    assert st.state == "idle"


def test_claude_only_no_transcript_no_shell_falls_to_idle(monkeypatch):
    sm = _claude_machine(monkeypatch, shell_active=False, transcript_age_sec=float("inf"))
    st = sm.compute()
    assert st.state == "idle"


def test_claude_shell_active_wins_over_streaming(monkeypatch):
    """Both signals fire -- working (hard evidence) beats thinking."""
    sm = _claude_machine(monkeypatch, shell_active=True, transcript_age_sec=1.0)
    st = sm.compute()
    assert st.state == "working"


def test_claude_not_running_is_idle(monkeypatch):
    install_world(monkeypatch)
    claude = ClaudeCodeDetector(
        find_processes_fn=lambda: [],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        projects_dir=Path("/fake/.claude/projects"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
    )
    sm = StateMachine(detectors=[claude])
    st = sm.compute()
    assert st.state == "idle"
    assert st.claude_code_running is False


def test_claude_detector_absent_falls_to_idle(monkeypatch):
    """No claude_code detector in the list at all -- claude_code_running
    stays False and the cascade falls through to plain idle."""
    install_world(monkeypatch)
    sm = StateMachine(detectors=[])
    st = sm.compute()
    assert st.state == "idle"
    assert st.claude_code_running is False
    assert sm._claude_detector is None
