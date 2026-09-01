"""StateMachine cascade tests for CodexDetector -- mirrors
test_watcher_claude_code_cascade.py. Verifies Codex gets promoted into
the rich working/thinking distinction (branch 4) instead of falling
through the flat generic OR-fallback.
"""
from __future__ import annotations

from pathlib import Path

from squid_pet import watcher
from squid_pet.watcher import StateMachine
from squid_pet.detectors import CodexDetector


def install_world(monkeypatch, idle=0.0):
    """Isolate every Claude Code signal dir from the real ~/.squid-pet/
    claude_*/ -- without this, a real live flag on the developer's own
    machine (from this very session's own Notification/Stop hook
    activity) overrides every test here via approval_needed/celebrating.
    Confirmed live: this file was missing the claude_finished isolation
    that test_watcher_claude_code_cascade.py already had, and a real
    Stop-hook flag from this session leaked in and flipped a test.
    Pink-2026-08-30: same leak via CLAUDE_TASK_COMPLETE_DIR -- worse here,
    since these tests also fake time.time() to a small fixed epoch, so a
    real on-disk marker's real 2026 mtime produced a NEGATIVE age that
    trivially passed any freshness check, always reading as fresh.

    Pink-2026-09-01: and once more via CLAUDE_TURN_ACTIVE_DIR. Caught by
    the suite the moment the turn-in-flight signal went live: a real flag
    from the developer's own in-flight turn made
    test_codex_only_stale_transcript_no_shell_falls_to_idle report
    thinking. Every new session-flag dir has to be added here."""
    monkeypatch.setattr(watcher, "macos_idle_seconds", lambda: idle)
    monkeypatch.setattr(watcher, "CLAUDE_AWAITING_INPUT_DIR", "/nonexistent")
    monkeypatch.setattr(watcher, "CLAUDE_FINISHED_DIR", "/nonexistent")
    monkeypatch.setattr(watcher, "CLAUDE_TASK_COMPLETE_DIR", "/nonexistent")
    monkeypatch.setattr(watcher, "CLAUDE_TURN_ACTIVE_DIR", "/nonexistent")


def _codex_machine(monkeypatch, *, shell_active=False, transcript_age_sec=float("inf"),
                    file_ages=None):
    install_world(monkeypatch)
    now_ref = {"v": 1_000_000.0}

    def _stat(p):
        class _S:
            st_mtime = now_ref["v"] - transcript_age_sec
        return _S()

    codex = CodexDetector(
        find_processes_fn=lambda: ["fake-codex-proc"],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: shell_active,
        sessions_dir=Path("/fake/.codex/sessions"),
        glob_fn=lambda root: iter(
            [] if transcript_age_sec == float("inf")
            else [Path("/fake/.codex/sessions/2026/08/14/s.jsonl")]
        ),
        stat_fn=_stat,
        recent_file_ages_fn=lambda: list(file_ages or []),
    )
    sm = StateMachine(detectors=[codex])
    monkeypatch.setattr(watcher.time, "time", lambda: now_ref["v"])
    return sm


def test_codex_only_shell_active_yields_working(monkeypatch):
    sm = _codex_machine(monkeypatch, shell_active=True)
    st = sm.compute()
    assert st.state == "working"
    assert st.codex_running is True


def test_codex_only_file_write_yields_working(monkeypatch):
    """apply_patch-style edits don't spawn a subprocess -- the
    file-write signal is what should catch this, same fix as
    ClaudeCodeDetector's."""
    sm = _codex_machine(monkeypatch, shell_active=False, file_ages=[2.0])
    st = sm.compute()
    assert st.state == "working"
    assert st.state_reason == "file write detected (codex)"


def test_codex_only_fresh_transcript_no_shell_yields_thinking(monkeypatch):
    sm = _codex_machine(monkeypatch, shell_active=False, transcript_age_sec=2.0)
    st = sm.compute()
    assert st.state == "thinking"
    assert st.state_reason == "codex streaming"
    assert st.codex_running is True


def test_codex_only_stale_transcript_no_shell_falls_to_idle(monkeypatch):
    sm = _codex_machine(monkeypatch, shell_active=False, transcript_age_sec=300.0)
    st = sm.compute()
    assert st.state == "idle"


def test_codex_shell_active_wins_over_streaming(monkeypatch):
    sm = _codex_machine(monkeypatch, shell_active=True, transcript_age_sec=1.0)
    st = sm.compute()
    assert st.state == "working"


def test_codex_not_running_is_idle(monkeypatch):
    install_world(monkeypatch)
    codex = CodexDetector(
        find_processes_fn=lambda: [],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        sessions_dir=Path("/fake/.codex/sessions"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
    )
    sm = StateMachine(detectors=[codex])
    st = sm.compute()
    assert st.state == "idle"
    assert st.codex_running is False


def test_codex_detector_absent_falls_to_idle(monkeypatch):
    """No codex detector in the list at all -- codex_running stays False
    and the cascade falls through to plain idle."""
    install_world(monkeypatch)
    sm = StateMachine(detectors=[])
    st = sm.compute()
    assert st.state == "idle"
    assert st.codex_running is False
    assert sm._codex_detector is None


def test_claude_and_codex_both_running_shell_evidence_merges(monkeypatch):
    """Both detectors wired at once (an engineer who uses both tools) --
    either one's hard evidence should drive 'working', and neither
    detector's absence-defaults leak into the other's."""
    from squid_pet.detectors import ClaudeCodeDetector
    install_world(monkeypatch)
    claude = ClaudeCodeDetector(
        find_processes_fn=lambda: [],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        projects_dir=Path("/fake/.claude/projects"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        recent_file_ages_fn=lambda: [],
    )
    codex = CodexDetector(
        find_processes_fn=lambda: ["fake-codex-proc"],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: True,
        sessions_dir=Path("/fake/.codex/sessions"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        recent_file_ages_fn=lambda: [],
    )
    sm = StateMachine(detectors=[claude, codex])
    st = sm.compute()
    assert st.state == "working"
    assert st.state_reason == "shell child active (codex)"
    assert st.claude_code_running is False
    assert st.codex_running is True
