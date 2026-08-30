"""Tests for _fire_approval_notification() and its AppleScript escaping.

Regression coverage:
  1. (2026-08-17) _fire_approval_notification existed, fully implemented
     and wired to config, but had zero call sites -- the "your turn" OS
     notification never actually fired despite the toggle being on.
     (Fixed: wired into StateMachine.compute()'s alert-fired branch.)
  2. (2026-08-17) text/sound were interpolated unescaped into a double-
     quoted AppleScript string literal -- a stray `"` in
     approval_alert_text/approval_alert_sound (user's own config.json)
     would break out of the string and inject arbitrary AppleScript.
     (Fixed: _applescript_escape().)
  3. (2026-08-27) plain `osascript -e 'display notification'` has NO
     click-action support -- clicking "Show" just foregrounds whatever
     process ran the script, which macOS attributes generically to
     Script Editor, opening an empty window. Caught via live use.
     (Fixed: prefer terminal-notifier with -activate <bundle-id> when
     installed, falling back to the old osascript-only behavior when
     it isn't.)

shutil.which("terminal-notifier") is always mocked here so these tests
are deterministic regardless of whether it's actually installed on the
machine running them.
"""
from __future__ import annotations

import time
from unittest.mock import patch

from squid_pet.watcher import _applescript_escape, _fire_approval_notification


def test_escape_handles_plain_text():
    assert _applescript_escape("your turn") == "your turn"


def test_escape_quotes_double_quote():
    assert _applescript_escape('say "hi"') == 'say \\"hi\\"'


def test_escape_quotes_backslash_before_quote_char():
    """Backslash must be escaped FIRST, or an attacker-controlled quote
    could ride in on a backslash the naive single-pass replace already
    emitted."""
    assert _applescript_escape('\\"') == '\\\\\\"'


# ── osascript fallback path (terminal-notifier NOT installed) ──────────

def test_fire_approval_notification_escapes_injected_quote():
    """A malicious/malformed config value containing AppleScript-breaking
    quotes must not appear unescaped in the script handed to osascript."""
    with patch("shutil.which", return_value=None), \
         patch("subprocess.run") as mock_run:
        _fire_approval_notification('turn" & (do shell script "rm -rf ~") & "', "Glass")
        time.sleep(0.2)  # thread is daemon + fire-and-forget
    assert mock_run.called
    script = mock_run.call_args[0][0][2]  # ["osascript", "-e", script]
    assert '" & (do shell script' not in script.replace('\\"', '')
    assert "\\\"" in script  # the quote survived, but escaped


def test_fire_approval_notification_calls_osascript_when_notifier_absent(monkeypatch):
    captured = {}

    def fake_run(cmd, timeout=None, capture_output=None):
        captured["cmd"] = cmd
        class _R:
            pass
        return _R()

    with patch("shutil.which", return_value=None), \
         patch("subprocess.run", side_effect=fake_run):
        _fire_approval_notification("your turn", "Glass")
        time.sleep(0.2)
    assert captured["cmd"][0] == "osascript"
    assert "your turn" in captured["cmd"][2]
    assert "Glass" in captured["cmd"][2]
    assert "Claude Code" in captured["cmd"][2]


def test_fire_approval_notification_empty_sound_omits_clause():
    with patch("shutil.which", return_value=None), \
         patch("subprocess.run") as mock_run:
        _fire_approval_notification("your turn", "")
        time.sleep(0.2)
    script = mock_run.call_args[0][0][2]
    assert "sound name" not in script


# ── terminal-notifier path (installed) ──────────────────────────────────

def test_fire_approval_notification_uses_terminal_notifier_when_available():
    with patch("shutil.which", return_value="/usr/local/bin/terminal-notifier"), \
         patch("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
               return_value="com.apple.Terminal"), \
         patch("subprocess.run") as mock_run:
        _fire_approval_notification("your turn", "Glass")
        time.sleep(0.2)

    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "/usr/local/bin/terminal-notifier"
    assert "-title" in cmd and "Squid" in cmd
    assert "-message" in cmd
    assert cmd[cmd.index("-message") + 1] == "Claude Code: your turn"
    assert "-sound" in cmd and "Glass" in cmd
    assert "-activate" in cmd and "com.apple.Terminal" in cmd


def test_fire_approval_notification_terminal_notifier_no_bundle_found():
    """No -activate flag when the hosting terminal app can't be
    identified -- still a valid, working notification, just without a
    useful click action."""
    with patch("shutil.which", return_value="/usr/local/bin/terminal-notifier"), \
         patch("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
               return_value=None), \
         patch("subprocess.run") as mock_run:
        _fire_approval_notification("your turn", "Glass")
        time.sleep(0.2)

    cmd = mock_run.call_args[0][0]
    assert "-activate" not in cmd


def test_fire_approval_notification_terminal_notifier_does_not_need_applescript_escaping():
    """terminal-notifier args go through subprocess argv (a list), not a
    shell/AppleScript string -- no escaping needed, and none should be
    applied (a literal quote should survive verbatim)."""
    with patch("shutil.which", return_value="/usr/local/bin/terminal-notifier"), \
         patch("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
               return_value=None), \
         patch("subprocess.run") as mock_run:
        _fire_approval_notification('say "hi"', "Glass")
        time.sleep(0.2)

    cmd = mock_run.call_args[0][0]
    assert cmd[cmd.index("-message") + 1] == 'Claude Code: say "hi"'


def test_fire_approval_notification_falls_back_if_terminal_notifier_errors():
    """If terminal-notifier is present but fails to run, fall back to
    osascript rather than silently dropping the notification."""
    def fake_run(cmd, timeout=None, capture_output=None):
        if cmd[0] == "/usr/local/bin/terminal-notifier":
            raise OSError("boom")
        class _R:
            pass
        return _R()

    with patch("shutil.which", return_value="/usr/local/bin/terminal-notifier"), \
         patch("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
               return_value=None), \
         patch("subprocess.run", side_effect=fake_run) as mock_run:
        _fire_approval_notification("your turn", "Glass")
        time.sleep(0.2)

    assert mock_run.call_args_list[-1][0][0][0] == "osascript"
