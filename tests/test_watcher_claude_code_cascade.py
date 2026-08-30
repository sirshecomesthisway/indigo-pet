"""StateMachine cascade tests for ClaudeCodeDetector -- verifies it gets
promoted into the rich working/thinking distinction (branch 4) instead
of falling through the flat generic OR-fallback (branch 5, which always
labels 'thinking' regardless of source -- see claude-code-detector
proposal.md for why that was the bug being fixed).
"""
from __future__ import annotations

import os
from pathlib import Path

from squid_pet import watcher
from squid_pet.watcher import StateMachine
from squid_pet.detectors import ClaudeCodeDetector


def install_world(monkeypatch, idle=0.0, finished_dir="/nonexistent"):
    """Stub the non-detector-owned signals StateMachine still reads
    directly: idle time, the Claude Code awaiting-input directory, and
    the Claude Code just-finished directory (each must be isolated from
    the real ~/.squid-pet/claude_*/, or a live flag on the developer's
    own machine overrides every test here via approval_needed/
    celebrating)."""
    monkeypatch.setattr(watcher, "macos_idle_seconds", lambda: idle)
    monkeypatch.setattr(watcher, "CLAUDE_AWAITING_INPUT_DIR", "/nonexistent")
    monkeypatch.setattr(watcher, "CLAUDE_FINISHED_DIR", finished_dir)


def _claude_machine(monkeypatch, *, shell_active=False, transcript_age_sec=float("inf"),
                     file_ages=None, idle=0.0):
    install_world(monkeypatch, idle=idle)
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


# ── Regression: 2026-08-27 -- "celebrating" was never reachable ────────
# ClaudeCodeDetector.is_celebrating() (added 2026-08-22) correctly
# detected the busy->idle edge internally the whole time -- is_busy()
# calls _scan() on every tick, which arms the detector's own
# _celebrate_until. But _compute_inner() never actually CALLED
# claude.is_celebrating(now) to read that result, so "celebrating" was
# silently unreachable via the real StateMachine cascade from the moment
# it was built. The original detector-only unit tests
# (test_detectors_claude_code.py) called is_celebrating() directly on
# the detector, so they passed and gave false confidence; this file's
# cascade-level tests never checked "celebrating" as an outcome, so
# nothing caught it until a direct StateMachine-level reproduction.
#
# Pink-2026-08-27f: that busy->idle edge itself turned out to be the
# wrong trigger -- a >20s gap with no tool call is a normal mid-task
# reasoning stretch, not a real completion, and fired a false "finished
# with claude!" bubble live while Claude was still working. Replaced with
# Claude Code's Stop hook (scripts/claude_pet_hook.py writes
# <CLAUDE_FINISHED_DIR>/<session_id>) -- these tests drive that flag file
# directly instead of toggling shell_active.
#
# Pink-2026-08-30: Stop hook fires on EVERY turn completion, not "the
# whole task is done" -- confirmed live as celebrating firing mid-task on
# routine turns. Moved off CELEBRATING (now reserved for actual
# milestones -- git commits, etc.) onto GROOVING (a lighter, per-turn
# "still making progress" beat) -- see watcher.py's cascade comments.
# These tests updated accordingly; the reachability regression they
# originally guarded against (the branch being silently dead) is the same
# concern, just for GROOVING now.
def _write_finished_flag(finished_dir: Path, session_id: str, mtime: float) -> None:
    finished_dir.mkdir(parents=True, exist_ok=True)
    path = finished_dir / session_id
    path.write_text("stop")
    os.utime(path, (mtime, mtime))


def test_claude_stop_hook_flag_fires_grooving(monkeypatch, tmp_path):
    finished_dir = tmp_path / "claude_finished"
    install_world(monkeypatch, finished_dir=str(finished_dir))
    monkeypatch.setattr(
        "squid_pet.config.get",
        lambda k, default=None: 20 if k == "celebrate_hold_sec" else default,
    )
    now_ref = {"v": 1_000_000.0}
    state = {"shell_active": True}

    claude = ClaudeCodeDetector(
        find_processes_fn=lambda: ["fake-claude-proc"],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: state["shell_active"],
        projects_dir=Path("/fake/.claude/projects"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        recent_file_ages_fn=lambda: [],
    )
    sm = StateMachine(detectors=[claude])
    monkeypatch.setattr(watcher.time, "time", lambda: now_ref["v"])

    st1 = sm.compute()
    assert st1.state == "working", "sanity: must actually be busy first"

    # A merely-quiet moment (shell_active dropping) must NOT celebrate
    # OR groove on its own anymore -- this is the exact false-positive
    # the fix closes.
    state["shell_active"] = False
    now_ref["v"] += 1.0
    st_quiet = sm.compute()
    assert st_quiet.state not in ("celebrating", "grooving"), (
        "a shell_active drop alone must not fire celebrating/grooving -- "
        "that was the false-positive bug (busy->idle heuristic, not a "
        "real Stop event)"
    )

    # Now the Stop hook actually fires.
    _write_finished_flag(finished_dir, "sess-1", now_ref["v"])
    now_ref["v"] += 1.0
    st2 = sm.compute()
    assert st2.state == "grooving", (
        f"Stop-hook flag must fire grooving (reserved for a lighter "
        f"per-turn beat, not celebrating -- see watcher.py's cascade "
        f"comments, Pink-2026-08-30); got {st2.state!r} "
        f"(reason={st2.state_reason!r})"
    )
    assert st2.state_reason == "claude grooving"


def test_claude_grooving_holds_for_the_configured_window(monkeypatch, tmp_path):
    finished_dir = tmp_path / "claude_finished"
    install_world(monkeypatch, finished_dir=str(finished_dir))
    monkeypatch.setattr(
        "squid_pet.config.get",
        lambda k, default=None: 20 if k == "celebrate_hold_sec" else default,
    )
    now_ref = {"v": 1_000_000.0}

    claude = ClaudeCodeDetector(
        find_processes_fn=lambda: ["fake-claude-proc"],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        projects_dir=Path("/fake/.claude/projects"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        recent_file_ages_fn=lambda: [],
    )
    sm = StateMachine(detectors=[claude])
    monkeypatch.setattr(watcher.time, "time", lambda: now_ref["v"])

    _write_finished_flag(finished_dir, "sess-1", now_ref["v"])
    assert sm.compute().state == "grooving"

    now_ref["v"] += 18  # still within the 20s celebrate_hold_sec window
    assert sm.compute().state == "grooving"

    now_ref["v"] += 5  # now past the hold
    assert sm.compute().state == "idle"


# ── Regression: 2026-08-27g -- sleeping used to win unconditionally ────
# A user caught this looking wrong live: squid showed sleeping while
# Claude Code was actively working in the background, because the user
# had stepped away from the keyboard for 5+ minutes mid-session.
# Sleeping is about USER presence; working/thinking are about AGENT
# activity -- when the agent is genuinely busy, that should win.
def test_agent_busy_suppresses_sleeping_via_shell_active(monkeypatch):
    sm = _claude_machine(
        monkeypatch, shell_active=True, idle=watcher.IDLE_THRESHOLD_SEC + 1,
    )
    st = sm.compute()
    assert st.state != "sleeping", (
        "an actively busy agent must suppress sleeping even when the "
        "user has been idle 5+ minutes"
    )
    assert st.state == "working"


def test_agent_busy_suppresses_sleeping_via_streaming(monkeypatch):
    """Same as above but via the streaming signal (thinking/generating,
    no tool call in flight) rather than shell_active -- both feed the
    same working_evidence_merged/streaming_merged signals the sleeping
    suppression reuses."""
    sm = _claude_machine(
        monkeypatch, transcript_age_sec=1.0, idle=watcher.IDLE_THRESHOLD_SEC + 1,
    )
    st = sm.compute()
    assert st.state != "sleeping"
    assert st.state == "thinking"


def test_sleeping_still_wins_when_agent_genuinely_idle(monkeypatch):
    """Contrast case: user away AND agent not busy -> sleeping still
    fires normally. The suppression is specifically busy-gated, not a
    blanket disable."""
    sm = _claude_machine(
        monkeypatch, shell_active=False, idle=watcher.IDLE_THRESHOLD_SEC + 1,
    )
    st = sm.compute()
    assert st.state == "sleeping"
