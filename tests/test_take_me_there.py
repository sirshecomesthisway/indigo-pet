"""PetApi.take_me_there(): a double-click raises the window responsible for
whatever she is currently showing.

Pink-2026-09-01: started as "take me to the session that's waving", then
Pink asked for the same on every active sprite -- "except idle/drowsy/
sleeping, because they mean she's doing nothing".

Same __new__ + manual-attribute pattern as test_acknowledge_approval.py,
so no real window is ever raised.
"""
from __future__ import annotations

import threading

import pytest

from squid_pet.window import PetApi
from squid_pet.watcher import PetState


def _api(state: str, focus_fn=None):
    api = PetApi.__new__(PetApi)
    api._lock = threading.Lock()
    api._latest = PetState(state=state)
    if focus_fn is not None:
        api._focus_fn = focus_fn
    return api


@pytest.mark.parametrize("state", ["working", "thinking", "approval_needed",
                                   "celebrating", "grooving"])
def test_active_states_are_taken_to_their_window(state):
    seen = []
    api = _api(state, focus_fn=lambda s: seen.append(s) or "matched")
    result = api.take_me_there()
    assert result == {"status": "matched", "state": state}
    assert seen == [state], "the state itself picks which signal dir is read"


@pytest.mark.parametrize("state", ["idle", "sleeping"])
def test_resting_states_take_you_nowhere(state):
    api = _api(state, focus_fn=lambda s: "resting")
    assert api.take_me_there()["status"] == "resting"


def test_a_failed_raise_never_breaks_the_gesture():
    """The poke and heart already happened; a broken osascript must not
    surface as an error to the user."""
    def _boom(_state):
        raise RuntimeError("osascript exploded")
    api = _api("working", focus_fn=_boom)
    assert api.take_me_there()["status"] == "error"


def test_inert_without_a_focus_callable():
    """Tests (and any construction path that skips __init__) simply never
    raise a window."""
    api = _api("working")
    assert api.take_me_there()["status"] == "skipped"
