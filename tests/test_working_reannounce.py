"""Tests for PetApi._maybe_reannounce_working(): while state STAYS
"working" across ticks (no transition, so Observer.on_state_change never
fires again), PetApi should periodically surface what she's currently
watching via Observer.on_still_working -- throttled to
WORKING_REANNOUNCE_SEC and silent when there's nothing new to say.

Follows the same __new__ + manual-attribute + MagicMock-observer pattern
as test_petapi_llm_context_enrichment.py.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from squid_pet.window import PetApi, WORKING_REANNOUNCE_SEC
from squid_pet.watcher import PetState


def _make_api():
    api = PetApi.__new__(PetApi)
    api._lock = threading.Lock()
    api._latest = PetState()
    api._last_state_for_bubble = ""
    api._forced_state = None
    api._passthrough = None
    api._sm = None
    api._observer = MagicMock()
    api._observer.on_state_change.return_value = None
    api._pending_bubble = None
    api._last_working_bubble_at = 0.0
    api._last_working_bubble_text = ""
    return api


def test_entry_into_working_resets_reannounce_clock():
    api = _make_api()
    api._observer.on_state_change.return_value = "running pytest"
    api.update(PetState(state="working", timestamp=1000.0))
    assert api._last_working_bubble_at == 1000.0
    assert api._last_working_bubble_text == "running pytest"


def test_same_state_within_throttle_window_does_not_reannounce():
    api = _make_api()
    api.update(PetState(state="working", timestamp=1000.0))
    api._observer.on_still_working.reset_mock()

    api.update(PetState(state="working", timestamp=1000.0 + WORKING_REANNOUNCE_SEC - 1))
    api._observer.on_still_working.assert_not_called()


def test_same_state_past_throttle_window_reannounces():
    """api._sm is None in this synthetic fixture (see _make_api), so
    _current_shell_cmdline() has no detector to read and legitimately
    returns None here -- see test_petapi_llm_context_enrichment.py /
    the detector-level tests for real shell_cmdline population. This
    test is only about the throttle/reannounce timing itself."""
    api = _make_api()
    api._observer.on_still_working.return_value = "running pytest"

    api.update(PetState(state="working", timestamp=1000.0))
    api.update(PetState(state="working", timestamp=1000.0 + WORKING_REANNOUNCE_SEC + 1))

    api._observer.on_still_working.assert_called_once_with(None)
    assert api._pending_bubble == "running pytest"


def test_reannounce_skips_publish_when_bubble_unchanged():
    api = _make_api()
    api._observer.on_state_change.return_value = "running pytest"
    api._observer.on_still_working.return_value = "running pytest"  # same as entry bubble

    api.update(PetState(state="working", timestamp=1000.0))
    api._pending_bubble = None  # simulate frontend having already cleared it

    api.update(PetState(state="working", timestamp=1000.0 + WORKING_REANNOUNCE_SEC + 1))
    assert api._pending_bubble is None, "unchanged text must not be republished"


def test_reannounce_skips_publish_when_nothing_concrete():
    """No shell command available -- on_still_working returns None, and
    that must NOT fall back to republishing a stale/generic line."""
    api = _make_api()
    api._observer.on_still_working.return_value = None

    api.update(PetState(state="working", timestamp=1000.0))
    api.update(PetState(state="working", timestamp=1000.0 + WORKING_REANNOUNCE_SEC + 1))

    assert api._pending_bubble is None


def test_current_shell_cmdline_reads_claude_detector():
    """Pink-2026-08-27k: _current_shell_cmdline() is the new wiring that
    replaced the hardcoded shell_cmd=None -- reads the live Claude Code
    detector's own shell_cmdline (populated by
    watcher.latest_shell_child_cmdline via ClaudeCodeDetector._scan)."""
    api = _make_api()
    api._sm = MagicMock()
    api._sm._claude_detector = MagicMock(shell_cmdline=["pytest", "-v"])
    api._sm._codex_detector = MagicMock(shell_cmdline=None)
    assert api._current_shell_cmdline() == ["pytest", "-v"]


def test_current_shell_cmdline_falls_back_to_codex_detector():
    api = _make_api()
    api._sm = MagicMock()
    api._sm._claude_detector = MagicMock(shell_cmdline=None)
    api._sm._codex_detector = MagicMock(shell_cmdline=["git", "push"])
    assert api._current_shell_cmdline() == ["git", "push"]


def test_current_shell_cmdline_none_when_no_state_machine():
    api = _make_api()
    assert api._sm is None
    assert api._current_shell_cmdline() is None


def test_non_working_state_never_reannounces():
    api = _make_api()
    api.update(PetState(state="idle", timestamp=1000.0))
    api.update(PetState(state="idle", timestamp=1000.0 + WORKING_REANNOUNCE_SEC + 1))
    api._observer.on_still_working.assert_not_called()
