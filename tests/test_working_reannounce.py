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
    """Pink-2026-08-22: shell_cmd enrichment was TPA-only and is
    gone (see window.py's _maybe_reannounce_working) -- on_still_working
    is now always called with None, but the throttle/reannounce timing
    itself is unchanged and still generic."""
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


def test_non_working_state_never_reannounces():
    api = _make_api()
    api.update(PetState(state="idle", timestamp=1000.0))
    api.update(PetState(state="idle", timestamp=1000.0 + WORKING_REANNOUNCE_SEC + 1))
    api._observer.on_still_working.assert_not_called()
