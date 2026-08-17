"""Regression test: PetApi.update()'s LLM-bubble context enrichment
(cpu_pct/tool_age/llm_streaming/git_active passed to
Observer.on_state_change) must actually read live detector signals.

Found in a 2026-08-17 code review: the enrichment code read
self._tpa_detector, watcher._tpa_detector_singleton, and
self._git_detector -- none of which were ever assigned anywhere in the
codebase, so every enrichment silently used its defaults
(False/0.0/inf/False) forever, and the richer LLM-bubble context (so
bubbles can say "shipped"/"fetching deps" instead of generic text)
never engaged.

Fix: PetApi.set_state_machine() gives it a live StateMachine ref
(wired from watcher_thread()); enrichment reads
self._sm._tpa_detector / self._sm._git_detector instead.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from squid_pet.window import PetApi
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
    return api


def test_update_reads_tpa_detector_signals_via_state_machine():
    api = _make_api()
    fake_sm = MagicMock()
    fake_sm._tpa_detector = MagicMock(
        llm_streaming=True, cpu_percent=42.5, tool_activity_age=3.0,
    )
    fake_sm._git_detector = None
    api.set_state_machine(fake_sm)

    api.update(PetState(state="working", timestamp=1000.0))

    _, kwargs = api._observer.on_state_change.call_args
    assert kwargs["llm_streaming"] is True
    assert kwargs["cpu_pct"] == 42.5
    assert kwargs["tool_age"] == 3.0


def test_update_reads_git_detector_signal_via_state_machine():
    api = _make_api()
    fake_sm = MagicMock()
    fake_sm._tpa_detector = None
    fake_git = MagicMock()
    fake_git.is_busy.return_value = True
    fake_git.is_celebrating.return_value = False
    fake_sm._git_detector = fake_git
    api.set_state_machine(fake_sm)

    api.update(PetState(state="working", timestamp=1000.0))

    _, kwargs = api._observer.on_state_change.call_args
    assert kwargs["git_active"] is True
    fake_git.is_busy.assert_called_once_with(1000.0)


def test_update_tolerates_no_state_machine_set():
    """Before watcher_thread() calls set_state_machine() (or if it never
    does), enrichment must degrade to defaults, not raise."""
    api = _make_api()
    api.update(PetState(state="working", timestamp=1000.0))
    _, kwargs = api._observer.on_state_change.call_args
    assert kwargs["llm_streaming"] is False
    assert kwargs["cpu_pct"] == 0.0
    assert kwargs["tool_age"] == float("inf")
    assert kwargs["git_active"] is False


def test_update_tolerates_state_machine_without_detector_attrs():
    """A StateMachine-shaped object missing _tpa_detector/_git_detector
    entirely (e.g. future refactor) must not crash update()."""
    api = _make_api()

    class _BareSM:
        pass

    api.set_state_machine(_BareSM())
    api.update(PetState(state="working", timestamp=1000.0))
    _, kwargs = api._observer.on_state_change.call_args
    assert kwargs["cpu_pct"] == 0.0
    assert kwargs["git_active"] is False


def test_set_state_machine_stores_reference():
    api = _make_api()
    fake_sm = MagicMock()
    api.set_state_machine(fake_sm)
    assert api._sm is fake_sm
