"""Tests for focus.py -- "take me to the window that's waiting".

Matching is by TTY, which is exact: a claude process's controlling
terminal and Terminal.app's `tty of tab` are the same string. The
osascript call is injected so nothing here raises a real window.
"""
from __future__ import annotations

from squid_pet import focus


def test_script_targets_the_exact_tty():
    script = focus.build_terminal_focus_script("/dev/ttys000")
    assert '"/dev/ttys000"' in script
    assert "tty of tb as text" in script
    assert "set selected tab of w to tb" in script


def test_script_escapes_quotes():
    """Defence in depth: a tty string is never attacker-controlled, but a
    string built into AppleScript should not be able to break out of its
    literal regardless."""
    script = focus.build_terminal_focus_script('x" & do shell script "id')
    assert script.count('is "') == 1
    assert '\\"' in script


def test_matches_the_tab_when_hosted_by_terminal_app(monkeypatch):
    monkeypatch.setattr(focus, "waiting_session_tty", lambda: "/dev/ttys003")
    monkeypatch.setattr("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
                        lambda: focus.TERMINAL_APP_BUNDLE_ID)
    seen = []
    result = focus.focus_waiting_session(run=lambda s: (seen.append(s), "matched")[1])
    assert result == "matched"
    assert "/dev/ttys003" in seen[0]


def test_falls_back_to_the_app_for_other_terminals(monkeypatch):
    """iTerm2 and VS Code have no addressable tab here -- raise the app and
    say so honestly, rather than claiming a match."""
    monkeypatch.setattr(focus, "waiting_session_tty", lambda: "/dev/ttys003")
    monkeypatch.setattr("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
                        lambda: "com.googlecode.iterm2")
    seen = []
    assert focus.focus_waiting_session(run=lambda s: seen.append(s) or "") == "app-only"
    assert "com.googlecode.iterm2" in seen[0]


def test_falls_back_when_the_tty_is_unknown(monkeypatch):
    monkeypatch.setattr(focus, "waiting_session_tty", lambda: None)
    monkeypatch.setattr("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
                        lambda: focus.TERMINAL_APP_BUNDLE_ID)
    seen = []
    assert focus.focus_waiting_session(run=lambda s: seen.append(s) or "") == "app-only"
    assert "activate" in seen[0]


def test_reports_none_when_no_terminal_can_be_identified(monkeypatch):
    monkeypatch.setattr(focus, "waiting_session_tty", lambda: None)
    monkeypatch.setattr("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
                        lambda: None)
    assert focus.focus_waiting_session(run=lambda s: "") == "none"


def test_a_tab_that_does_not_match_reports_app_only(monkeypatch):
    """The script's own "app-only" answer must survive back to the caller,
    so the UI never claims it took you somewhere it did not."""
    monkeypatch.setattr(focus, "waiting_session_tty", lambda: "/dev/ttys009")
    monkeypatch.setattr("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
                        lambda: focus.TERMINAL_APP_BUNDLE_ID)
    assert focus.focus_waiting_session(run=lambda s: "app-only\n") == "app-only"


def test_picks_the_freshest_wave(tmp_path, monkeypatch):
    """With two waiting, the one that just started waving is the one the
    user is reacting to."""
    import os
    d = tmp_path / "awaiting"; d.mkdir()
    (d / "older").write_text("permission_prompt")
    (d / "newer").write_text("permission_prompt")
    os.utime(d / "older", (1000, 1000))
    os.utime(d / "newer", (2000, 2000))
    monkeypatch.setattr("squid_pet.watcher.CLAUDE_AWAITING_INPUT_DIR", str(d))

    assert focus.freshest_waiting_session() == "newer"


def test_tty_comes_from_the_waiting_session_not_just_any_process(monkeypatch):
    """The multi-session fix: resolve through the session that is waving,
    not whichever claude process is found first."""
    monkeypatch.setattr(focus, "freshest_waiting_session", lambda: "sess-x")
    monkeypatch.setattr("squid_pet.watcher.claude_session_tty",
                        lambda sid: "/dev/ttys007" if sid == "sess-x" else None)
    assert focus.waiting_session_tty() == "/dev/ttys007"


# ── Any active state, not just the wave (Pink-2026-09-01) ──────────────
# Pink: apply the same logic to the other sprites -- double-click takes
# you to the window for whatever she is doing -- except idle/drowsy/
# sleeping, "because they mean she's doing nothing".
#
# Each active state already has a flag directory naming the session
# responsible, so the same session -> project -> process -> tty -> tab
# chain works for all of them.
import pytest


@pytest.mark.parametrize("state,dir_attr", [
    ("working",         "CLAUDE_TURN_ACTIVE_DIR"),
    ("thinking",        "CLAUDE_TURN_ACTIVE_DIR"),
    ("approval_needed", "CLAUDE_AWAITING_INPUT_DIR"),
    ("celebrating",     "CLAUDE_TASK_COMPLETE_DIR"),
    ("grooving",        "CLAUDE_FINISHED_DIR"),
])
def test_each_active_state_reads_its_own_signal_dir(state, dir_attr):
    from squid_pet import watcher
    assert focus.STATE_SIGNAL_DIRS[state] is getattr(watcher, dir_attr), (
        f"{state} must resolve through the flag dir that names who caused it")


@pytest.mark.parametrize("state", ["idle", "sleeping", "drowsy", "stretch"])
def test_resting_states_have_nowhere_to_take_you(state):
    """Pink's exclusion: these mean she is doing nothing, so a double-click
    must not yank a window to the front."""
    assert state not in focus.STATE_SIGNAL_DIRS
    assert focus.focus_for_state(state, run=lambda s: "matched") == "resting"


def test_unknown_state_is_treated_as_resting():
    assert focus.focus_for_state("some-future-state", run=lambda s: "x") == "resting"


def test_working_focuses_the_session_that_is_mid_turn(tmp_path, monkeypatch):
    d = tmp_path / "turn"; d.mkdir()
    (d / "sess-w").write_text("turn")
    monkeypatch.setitem(focus.STATE_SIGNAL_DIRS, "working", str(d))
    monkeypatch.setattr("squid_pet.watcher.claude_session_tty",
                        lambda sid: "/dev/ttys011" if sid == "sess-w" else None)
    monkeypatch.setattr("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
                        lambda: focus.TERMINAL_APP_BUNDLE_ID)
    seen = []
    result = focus.focus_for_state("working",
                                   run=lambda s: (seen.append(s), "matched")[1])
    assert result == "matched"
    assert "/dev/ttys011" in seen[0]


def test_an_active_state_with_no_flag_still_raises_the_app(tmp_path, monkeypatch):
    """Celebrating can outlive its marker's freshness window. Better to
    raise the right app than to do nothing at all."""
    monkeypatch.setitem(focus.STATE_SIGNAL_DIRS, "celebrating",
                        str(tmp_path / "empty"))
    monkeypatch.setattr("squid_pet.watcher.find_claude_code_processes", lambda: [])
    monkeypatch.setattr("squid_pet.watcher.find_terminal_app_bundle_for_claude_code",
                        lambda: "com.googlecode.iterm2")
    assert focus.focus_for_state("celebrating", run=lambda s: "") == "app-only"
