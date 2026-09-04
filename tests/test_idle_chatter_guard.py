"""Idle small talk belongs to idle, and nowhere else.

RoutineController's chatter timer fires every ~26-34s regardless of
state. A guard was added for "working" (2026-08-31) after idle chatter
stepped on the working reannounce beat -- but it was never generalised,
so every other non-idle state still got idle-flavoured lines.

Pink hit this on 2026-09-03: Squid finished a task and announced it with
"8 arms, 0 tasks", which reads as "nothing to do" at the exact moment
something had just been completed. Every state already has its own
phrase pool in observer.py; the idle pool was simply talking over them.

Same __new__ + MagicMock-observer pattern as test_working_reannounce.py.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from squid_pet.watcher import PetState
from squid_pet.window import PetApi


def _make_api(state: str):
    api = PetApi.__new__(PetApi)
    api._lock = threading.Lock()
    api._latest = PetState(state=state)
    api._observer = MagicMock()
    api._observer.on_idle_chatter.return_value = "8 arms, 0 tasks"
    api._pending_bubble = None
    return api


@pytest.mark.parametrize(
    "state",
    ["celebrating", "working", "thinking", "grooving", "approval_needed"],
)
def test_idle_chatter_stays_quiet_outside_idle(state):
    api = _make_api(state)

    api._fire_idle_chatter()

    api._observer.on_idle_chatter.assert_not_called()
    assert api._pending_bubble is None


def test_idle_chatter_still_fires_when_actually_idle():
    """Guard rail: silencing the other states must not mute her entirely."""
    api = _make_api("idle")

    api._fire_idle_chatter()

    api._observer.on_idle_chatter.assert_called_once()
    assert api._pending_bubble == "8 arms, 0 tasks"


def test_sleeping_does_not_chatter():
    """She is asleep; the sleeping pool owns that moment on transition."""
    api = _make_api("sleeping")

    api._fire_idle_chatter()

    api._observer.on_idle_chatter.assert_not_called()
