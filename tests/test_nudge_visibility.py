"""Regression tests for the 2026-08-19 fix: nudge hops were physically
moving the window in every backend state (by design -- see
WanderController.request_nudge's docstring) but the walking-cue never
showed unless she happened to already be in the "idle" backend state,
because PetApi.get_state() only forwarded sub_state to the frontend when
state=="idle". A user reported this made nudge look completely broken
while she was "working" or "sleeping" (mood, suppressed separately in the
frontend -- see index.html's applySubState, not covered by this file).

Fix: "nudge-{facing}" is a distinct sub_state name from the ambient
"walking-{facing}"/"looking-around-{facing}" wander sub-states, and is
exempt from the state=="idle" gate. Ambient wander sub-states keep the
original gating (they SHOULD stay invisible while busy/thinking/etc --
only nudge, a direct reaction to being bumped, is exempt).
"""
from __future__ import annotations

import threading
import time as _time
from unittest.mock import MagicMock

from squid_pet.window import PetApi
from squid_pet.watcher import PetState


def _make_api(state: str) -> PetApi:
    api = PetApi.__new__(PetApi)
    api._lock = threading.Lock()
    api._latest = PetState(state=state)
    api._forced_state = None
    api._wander_sub_state = ""
    api._wander_edge = ""
    api._hint_text = ""
    api._hint_seq = 0
    api._pinned = False
    api._wrapper_deg_override = None
    api._wake_trigger_seq = 0
    api._user_wake_until = 0.0
    api._sprint_fast_transition = False
    api._pending_bubble = None
    api._frontend_mood = ""
    api._last_wake_at = 0.0
    return api


def test_nudge_sub_state_exposed_while_working():
    api = _make_api(state="working")
    api._wander_sub_state = "nudge-left"
    d = api.get_state()
    assert d.get("sub_state") == "nudge-left"


def test_nudge_sub_state_exposed_while_thinking():
    api = _make_api(state="thinking")
    api._wander_sub_state = "nudge-right"
    d = api.get_state()
    assert d.get("sub_state") == "nudge-right"


def test_ambient_walking_sub_state_still_suppressed_while_working():
    """Preserve existing behavior: AMBIENT wander sub-states (not nudge)
    must stay invisible outside state=='idle' -- she shouldn't visibly
    wander-walk while busy."""
    api = _make_api(state="working")
    api._wander_sub_state = "walking-left"
    d = api.get_state()
    assert "sub_state" not in d or d.get("sub_state") != "walking-left"


def test_ambient_looking_around_still_suppressed_while_thinking():
    api = _make_api(state="thinking")
    api._wander_sub_state = "looking-around-right"
    d = api.get_state()
    assert "sub_state" not in d or d.get("sub_state") != "looking-around-right"


def test_ambient_walking_sub_state_still_shown_while_idle():
    """Sanity: the idle case (the only case that worked before this fix)
    must still work."""
    api = _make_api(state="idle")
    api._wander_sub_state = "walking-right"
    d = api.get_state()
    assert d.get("sub_state") == "walking-right"


def test_nudge_sub_state_exposed_while_idle_too():
    api = _make_api(state="idle")
    api._wander_sub_state = "nudge-left"
    d = api.get_state()
    assert d.get("sub_state") == "nudge-left"
