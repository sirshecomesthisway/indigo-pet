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


def test_notification_idle_prompt_writes_flag(home):
    r = _run({
        "session_id": "sess-2", "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
    }, home)
    assert r.returncode == 0, r.stderr
    assert _flag_path(home, "sess-2").exists()


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
          "notification_type": "idle_prompt"}, home)
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


def test_stop_does_not_touch_awaiting_input_flag(home):
    """Stop and Notification/UserPromptSubmit/SessionEnd are independent
    signals in separate directories -- a Stop event for a session must
    not create or remove anything in claude_awaiting_input/."""
    r = _run({"session_id": "sess-8", "hook_event_name": "Stop"}, home)
    assert r.returncode == 0, r.stderr
    assert not _flag_path(home, "sess-8").exists()


def test_multiple_stop_sessions_are_independent(home):
    _run({"session_id": "sess-A", "hook_event_name": "Stop"}, home)
    _run({"session_id": "sess-B", "hook_event_name": "Stop"}, home)
    assert _finished_path(home, "sess-A").exists()
    assert _finished_path(home, "sess-B").exists()


def test_user_prompt_submit_on_nonexistent_flag_is_a_noop(home):
    r = _run({"session_id": "never-had-a-flag",
              "hook_event_name": "UserPromptSubmit"}, home)
    assert r.returncode == 0, r.stderr


def test_multiple_sessions_are_independent(home):
    _run({"session_id": "sess-A", "hook_event_name": "Notification",
          "notification_type": "idle_prompt"}, home)
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
              "notification_type": "idle_prompt"}, home)
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
          "notification_type": "idle_prompt"}, home)
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
              "notification_type": "idle_prompt"}, home)
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
                          "notification_type": "idle_prompt"}),
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert (tmp_path / ".squid-pet" / "claude_awaiting_input" / "sess-9").exists()
