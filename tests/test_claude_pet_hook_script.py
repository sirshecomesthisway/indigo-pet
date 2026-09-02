"""Pink-2026-08-26: exercises scripts/claude_pet_hook.py as a REAL
subprocess (it's invoked by the Claude Code CLI as an external command,
never imported), the same way it actually runs in production. Verifies
the on-disk flag-file protocol that watcher.py's
claude_sessions_awaiting_input() reads.

SQUID_PET_HOME is overridden per-test to a tmp_path so nothing here ever
touches the real ~/.squid-pet.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "claude_pet_hook.py"


def _run(payload: dict | None, home: Path) -> subprocess.CompletedProcess:
    stdin_data = "" if payload is None else json.dumps(payload)
    env = dict(os.environ)
    env["SQUID_PET_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_data, capture_output=True, text=True, timeout=10, env=env,
    )


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "squid-pet-home"


def _flag_path(home: Path, session_id: str) -> Path:
    return home / "claude_awaiting_input" / session_id


def _finished_path(home: Path, session_id: str) -> Path:
    return home / "claude_finished" / session_id


def _turn_active_path(home: Path, session_id: str) -> Path:
    return home / "claude_turn_active" / session_id


def _recap_path(home: Path, session_id: str) -> Path:
    return home / "claude_recapping" / session_id


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_notification_permission_prompt_writes_flag(home):
    r = _run({
        "session_id": "sess-1", "hook_event_name": "Notification",
        "notification_type": "permission_prompt",
        "message": "Claude needs your permission",
    }, home)
    assert r.returncode == 0, r.stderr
    fp = _flag_path(home, "sess-1")
    assert fp.exists()
    assert fp.read_text() == "permission_prompt"


def test_notification_idle_prompt_is_ignored(home):
    """Pink-2026-09-01: idle_prompt fires 60s after Claude hands control
    back and simply means "it is your turn and you are not here". Pink:
    "I don't need her to tell me what to do next, only to speak up when
    she needs me." Only permission_prompt -- an actual blocked request --
    is worth interrupting for now.

    Note this does NOT weaken the stepped-away case: a permission prompt
    raised while you are away still waves and still fires the banner. What
    is gone is the alert that fires when nothing is blocked at all."""
    r = _run({
        "session_id": "sess-2", "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
    }, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-2").exists()


def test_notification_unhandled_type_does_not_write_flag(home):
    """auth_success and any other type we haven't confirmed the meaning
    of must NOT create a flag -- only the two empirically-confirmed
    'waiting on you' types should."""
    r = _run({
        "session_id": "sess-3", "hook_event_name": "Notification",
        "notification_type": "auth_success",
    }, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-3").exists()


def test_user_prompt_submit_removes_flag(home):
    _run({"session_id": "sess-4", "hook_event_name": "Notification",
          "notification_type": "permission_prompt"}, home)
    assert _flag_path(home, "sess-4").exists()

    r = _run({"session_id": "sess-4", "hook_event_name": "UserPromptSubmit"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-4").exists()


def test_session_end_removes_flag(home):
    _run({"session_id": "sess-5", "hook_event_name": "Notification",
          "notification_type": "permission_prompt"}, home)
    assert _flag_path(home, "sess-5").exists()

    r = _run({"session_id": "sess-5", "hook_event_name": "SessionEnd"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-5").exists()


# ── Stop (Pink-2026-08-27f: replaces the busy->idle celebrate heuristic
# with the real "Claude finished responding" signal -- see
# watcher.claude_sessions_just_finished()) ──────────────────────────────
def test_stop_writes_finished_flag(home):
    r = _run({"session_id": "sess-6", "hook_event_name": "Stop",
              "last_assistant_message": "done!"}, home)
    assert r.returncode == 0, r.stderr
    fp = _finished_path(home, "sess-6")
    assert fp.exists()


def test_stop_does_not_read_last_assistant_message_into_the_flag(home):
    """Privacy stance: session_id and mtime only, never message content --
    matches the awaiting-input flag's own content-blind contract."""
    r = _run({"session_id": "sess-7", "hook_event_name": "Stop",
              "last_assistant_message": "some potentially sensitive text"}, home)
    assert r.returncode == 0, r.stderr
    fp = _finished_path(home, "sess-7")
    assert "sensitive" not in fp.read_text()


def test_stop_does_not_create_an_awaiting_input_flag(home):
    """Stop must never CREATE an awaiting-input flag -- "the turn ended" is
    not "the session is waiting on you"; only a Notification says that.

    Pink-2026-08-31: this used to assert Stop never removed one either, but
    that invariant was wrong and caused a real stuck-wave bug -- see
    test_stop_clears_a_stuck_awaiting_input_flag below for why Stop now
    clears. The no-create half is unchanged and still load-bearing.
    """
    r = _run({"session_id": "sess-8", "hook_event_name": "Stop"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-8").exists()


# ── Answering a permission prompt (Pink-2026-08-31) ────────────────────
# Answering a permission prompt -- yes OR no -- fires no hook of its own.
# With only UserPromptSubmit/SessionEnd clearing the flag, approving a
# prompt and letting Claude carry on left it set indefinitely: confirmed
# live in claude_hook.log (session 8ced357d) as a nine-minute wave at a
# session that was happily working, with approval_needed's PRIME priority
# masking every working/thinking state behind it the whole time.
def test_post_tool_use_clears_the_awaiting_input_flag(home):
    """The APPROVAL path: a tool actually executed, so whatever permission
    question was pending has been answered and the session is no longer
    blocked on the user."""
    _run({"session_id": "sess-p1", "hook_event_name": "Notification",
          "notification_type": "permission_prompt"}, home)
    assert _flag_path(home, "sess-p1").exists()

    r = _run({"session_id": "sess-p1", "hook_event_name": "PostToolUse",
              "tool_name": "Bash"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-p1").exists()


def test_stop_clears_a_stuck_awaiting_input_flag(home):
    """The DENIAL path: denying runs no tool, so PostToolUse never fires.
    Stop is what covers it -- control came back, so nothing is blocked on a
    dialog any more."""
    _run({"session_id": "sess-p2", "hook_event_name": "Notification",
          "notification_type": "permission_prompt"}, home)
    assert _flag_path(home, "sess-p2").exists()

    r = _run({"session_id": "sess-p2", "hook_event_name": "Stop"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-p2").exists()
    # Stop's own signal must still land -- clearing is additive, not a swap.
    assert _finished_path(home, "sess-p2").exists()


def test_a_later_permission_prompt_rearms_after_stop_cleared_the_flag(home):
    """Clearing on Stop must not permanently suppress the next wave: a
    permission prompt raised afterwards has to be able to re-arm the flag
    Stop cleared."""
    _run({"session_id": "sess-p3", "hook_event_name": "Stop"}, home)
    assert not _flag_path(home, "sess-p3").exists()

    _run({"session_id": "sess-p3", "hook_event_name": "Notification",
          "notification_type": "permission_prompt"}, home)
    assert _flag_path(home, "sess-p3").exists()


def test_post_tool_use_on_nonexistent_flag_is_a_noop(home):
    """PostToolUse fires on EVERY tool call, so the overwhelmingly common
    case is no flag to clear. It must be silent and cheap, never an error."""
    r = _run({"session_id": "sess-p4", "hook_event_name": "PostToolUse",
              "tool_name": "Read"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-p4").exists()


def test_post_tool_use_only_clears_its_own_session(home):
    """The reason this beats watcher.py's self-heal, which is gated on
    len(find_claude_code_processes()) <= 1 precisely because it cannot tell
    whose activity resolved whose wait: the hook payload carries the
    session_id, so a busy session can never cancel another one's wave."""
    for sid in ("sess-p5", "sess-p6"):
        _run({"session_id": sid, "hook_event_name": "Notification",
              "notification_type": "permission_prompt"}, home)

    _run({"session_id": "sess-p5", "hook_event_name": "PostToolUse",
          "tool_name": "Bash"}, home)

    assert not _flag_path(home, "sess-p5").exists()
    assert _flag_path(home, "sess-p6").exists(), (
        "another session's genuinely-pending wave must survive")


def test_multiple_stop_sessions_are_independent(home):
    _run({"session_id": "sess-A", "hook_event_name": "Stop"}, home)
    _run({"session_id": "sess-B", "hook_event_name": "Stop"}, home)
    assert _finished_path(home, "sess-A").exists()
    assert _finished_path(home, "sess-B").exists()


# ── PreCompact / PostCompact (Pink-2026-08-30: Pink noticed Squid
# flashing to a generic "thinking" during a context compaction with no
# indication why, and asked for it called out by name) ─────────────────
def test_precompact_writes_recap_flag(home):
    r = _run({"session_id": "sess-c1", "hook_event_name": "PreCompact",
              "trigger": "manual", "custom_instructions": ""}, home)
    assert r.returncode == 0, r.stderr
    fp = _recap_path(home, "sess-c1")
    assert fp.exists()
    assert fp.read_text() == "manual"


def test_precompact_auto_trigger_recorded(home):
    r = _run({"session_id": "sess-c2", "hook_event_name": "PreCompact",
              "trigger": "auto"}, home)
    assert r.returncode == 0, r.stderr
    assert _recap_path(home, "sess-c2").read_text() == "auto"


def test_postcompact_removes_recap_flag(home):
    _run({"session_id": "sess-c3", "hook_event_name": "PreCompact",
          "trigger": "manual"}, home)
    assert _recap_path(home, "sess-c3").exists()

    r = _run({"session_id": "sess-c3", "hook_event_name": "PostCompact"}, home)
    assert r.returncode == 0, r.stderr
    assert not _recap_path(home, "sess-c3").exists()


def test_postcompact_on_nonexistent_flag_is_a_noop(home):
    r = _run({"session_id": "never-recapped",
              "hook_event_name": "PostCompact"}, home)
    assert r.returncode == 0, r.stderr


def test_precompact_does_not_touch_other_flags(home):
    """PreCompact/PostCompact are independent signals in their own
    directory -- must not create or remove anything in
    claude_awaiting_input/ or claude_finished/."""
    r = _run({"session_id": "sess-c4", "hook_event_name": "PreCompact",
              "trigger": "auto"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-c4").exists()
    assert not _finished_path(home, "sess-c4").exists()


def test_session_end_also_clears_a_stuck_recap_flag(home):
    """Crash-safety: a session ending mid-compact (PostCompact never
    fires) must not leave Squid waving "recapping" forever."""
    _run({"session_id": "sess-c5", "hook_event_name": "PreCompact",
          "trigger": "manual"}, home)
    assert _recap_path(home, "sess-c5").exists()

    r = _run({"session_id": "sess-c5", "hook_event_name": "SessionEnd"}, home)
    assert r.returncode == 0, r.stderr
    assert not _recap_path(home, "sess-c5").exists()


def test_user_prompt_submit_on_nonexistent_flag_is_a_noop(home):
    r = _run({"session_id": "never-had-a-flag",
              "hook_event_name": "UserPromptSubmit"}, home)
    assert r.returncode == 0, r.stderr


def test_multiple_sessions_are_independent(home):
    _run({"session_id": "sess-A", "hook_event_name": "Notification",
          "notification_type": "permission_prompt"}, home)
    _run({"session_id": "sess-B", "hook_event_name": "Notification",
          "notification_type": "permission_prompt"}, home)
    assert _flag_path(home, "sess-A").exists()
    assert _flag_path(home, "sess-B").exists()

    _run({"session_id": "sess-A", "hook_event_name": "UserPromptSubmit"}, home)
    assert not _flag_path(home, "sess-A").exists()
    assert _flag_path(home, "sess-B").exists(), \
        "removing sess-A's flag must not touch sess-B's"


def test_malformed_json_does_not_crash(home):
    r = _run(None, home)  # empty stdin handled separately below
    assert r.returncode == 0

    env = dict(os.environ)
    env["SQUID_PET_HOME"] = str(home)
    r2 = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="not valid json {{{", capture_output=True, text=True,
        timeout=10, env=env,
    )
    assert r2.returncode == 0, r2.stderr


def test_missing_session_id_does_not_crash(home):
    r = _run({"hook_event_name": "Notification",
              "notification_type": "permission_prompt"}, home)
    assert r.returncode == 0, r.stderr
    # No flag directory content should be produced at all.
    flag_dir = home / "claude_awaiting_input"
    assert not flag_dir.exists() or list(flag_dir.iterdir()) == []


def test_unknown_hook_event_does_not_crash(home):
    r = _run({"session_id": "sess-6", "hook_event_name": "SomeFutureEvent"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-6").exists()


def test_log_file_is_written(home):
    _run({"session_id": "sess-7", "hook_event_name": "Notification",
          "notification_type": "permission_prompt"}, home)
    log = home / "claude_hook.log"
    assert log.exists()
    assert "sess-7" in log.read_text()


def test_log_file_is_truncated_when_large(home):
    """The log must not grow unbounded over months of real usage."""
    home.mkdir(parents=True, exist_ok=True)
    log = home / "claude_hook.log"
    # Pre-seed a log well past the truncation threshold.
    with open(log, "w") as f:
        for i in range(20_000):
            f.write(f"1700000000 filler line {i}\n")
    size_before = log.stat().st_size
    assert size_before > 200_000

    r = _run({"session_id": "sess-8", "hook_event_name": "Notification",
              "notification_type": "permission_prompt"}, home)
    assert r.returncode == 0, r.stderr

    size_after = log.stat().st_size
    assert size_after < size_before, "log should have been truncated"
    content = log.read_text()
    assert "sess-8" in content, "the triggering event must survive truncation"


def test_defaults_to_real_home_when_env_unset(tmp_path, monkeypatch):
    """Without SQUID_PET_HOME set, the script must fall back to
    ~/.squid-pet -- verified by checking the script's own default
    resolves relative to HOME, not by actually touching the real
    directory (that would pollute the developer's machine)."""
    env = dict(os.environ)
    env.pop("SQUID_PET_HOME", None)
    env["HOME"] = str(tmp_path)  # redirect HOME itself instead
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"session_id": "sess-9", "hook_event_name": "Notification",
                          "notification_type": "permission_prompt"}),
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert (tmp_path / ".squid-pet" / "claude_awaiting_input" / "sess-9").exists()


# ── Turn-in-flight signal (Pink-2026-09-01) ────────────────────────────
# ClaudeCodeDetector infers "thinking" from transcript mtime
# (STREAMING_STALE_SEC), but Claude Code only writes a transcript entry
# when a block COMPLETES. An extended thinking stretch writes nothing, so
# past the staleness window Squid decided nobody was home and showed idle
# while the UI said "thinking some more" -- measured at up to 29s of
# wrongly-idle in a single stretch. UserPromptSubmit/Stop bracket a turn
# exactly, with no inference and no transcript content read.
def test_user_prompt_submit_marks_the_turn_active(home):
    r = _run({"session_id": "sess-t1", "hook_event_name": "UserPromptSubmit"}, home)
    assert r.returncode == 0, r.stderr
    assert _turn_active_path(home, "sess-t1").exists()


def test_stop_ends_the_active_turn(home):
    _run({"session_id": "sess-t2", "hook_event_name": "UserPromptSubmit"}, home)
    assert _turn_active_path(home, "sess-t2").exists()

    r = _run({"session_id": "sess-t2", "hook_event_name": "Stop"}, home)
    assert r.returncode == 0, r.stderr
    assert not _turn_active_path(home, "sess-t2").exists()


def test_session_end_ends_the_active_turn(home):
    """Crash safety: a session killed mid-turn must not leave Squid
    thinking forever."""
    _run({"session_id": "sess-t3", "hook_event_name": "UserPromptSubmit"}, home)
    r = _run({"session_id": "sess-t3", "hook_event_name": "SessionEnd"}, home)
    assert r.returncode == 0, r.stderr
    assert not _turn_active_path(home, "sess-t3").exists()


def test_turn_active_sessions_are_independent(home):
    _run({"session_id": "sess-t4", "hook_event_name": "UserPromptSubmit"}, home)
    _run({"session_id": "sess-t5", "hook_event_name": "UserPromptSubmit"}, home)
    _run({"session_id": "sess-t4", "hook_event_name": "Stop"}, home)

    assert not _turn_active_path(home, "sess-t4").exists()
    assert _turn_active_path(home, "sess-t5").exists(), (
        "one session finishing must not end another's turn")


def test_turn_active_flag_holds_no_prompt_content(home):
    """Same content-blind contract as every other flag here: session_id and
    mtime only, never what the user actually typed."""
    _run({"session_id": "sess-t6", "hook_event_name": "UserPromptSubmit",
          "prompt": "something private the user typed"}, home)
    assert "private" not in _turn_active_path(home, "sess-t6").read_text()
