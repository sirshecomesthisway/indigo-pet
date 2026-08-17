"""Tests for _fire_approval_notification() and its AppleScript escaping.

Regression coverage for two related bugs found in a 2026-08-17 code
review:
  1. _fire_approval_notification existed, fully implemented and wired
     to config, but had zero call sites -- the "your turn" OS
     notification never actually fired despite the toggle being on.
     (Fixed: wired into StateMachine.compute()'s alert-fired branch.)
  2. text/sound were interpolated unescaped into a double-quoted
     AppleScript string literal -- a stray `"` in
     approval_alert_text/approval_alert_sound (user's own config.json)
     would break out of the string and inject arbitrary AppleScript.
     (Fixed: _applescript_escape().)
"""
from __future__ import annotations

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


def test_fire_approval_notification_escapes_injected_quote():
    """A malicious/malformed config value containing AppleScript-breaking
    quotes must not appear unescaped in the script handed to osascript."""
    with patch("subprocess.run") as mock_run:
        _fire_approval_notification('turn" & (do shell script "rm -rf ~") & "', "Glass")
        # Thread is daemon + fire-and-forget; give it a moment.
        import time
        time.sleep(0.2)
    assert mock_run.called
    script = mock_run.call_args[0][0][2]  # ["osascript", "-e", script]
    assert '" & (do shell script' not in script.replace('\\"', '')
    assert "\\\"" in script  # the quote survived, but escaped


def test_fire_approval_notification_calls_osascript(monkeypatch):
    captured = {}

    def fake_run(cmd, timeout=None, capture_output=None):
        captured["cmd"] = cmd
        class _R:
            pass
        return _R()

    with patch("subprocess.run", side_effect=fake_run):
        _fire_approval_notification("your turn", "Glass")
        import time
        time.sleep(0.2)
    assert captured["cmd"][0] == "osascript"
    assert "your turn" in captured["cmd"][2]
    assert "Glass" in captured["cmd"][2]


def test_fire_approval_notification_empty_sound_omits_clause():
    with patch("subprocess.run") as mock_run:
        _fire_approval_notification("your turn", "")
        import time
        time.sleep(0.2)
    script = mock_run.call_args[0][0][2]
    assert "sound name" not in script
