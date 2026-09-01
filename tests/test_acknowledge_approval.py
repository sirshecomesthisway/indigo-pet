"""Tests for PetApi.acknowledge_approval(): a dblclick while she's waving
the approval_needed flag is an "I saw you" gesture. The bubble shows
immediately (see PetApi._pending_bubble / the RPC return value), but the
actual calm -- the snooze that makes the wave stop, same mechanic as the
right-click 'Calm Squid' menu action (PetApi._calm_squid /
_menu_calm_squid) -- is deliberately deferred by
ACKNOWLEDGE_DISMISS_DELAY_SEC via a background timer, so the wave keeps
visibly waving for that beat instead of stopping in the same instant the
bubble appears.

Follows the same __new__ + manual-attribute + MagicMock-observer pattern
as test_working_reannounce.py. threading.Timer is faked so tests don't
actually wait out the real delay or leave background threads running.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from squid_pet import window as window_mod
from squid_pet.window import PetApi, ACKNOWLEDGE_DISMISS_DELAY_SEC
from squid_pet.watcher import PetState


def _make_api():
    api = PetApi.__new__(PetApi)
    api._lock = threading.Lock()
    api._latest = PetState()
    api._observer = MagicMock()
    api._pending_bubble = None
    api._hint_text = None
    api._hint_seq = 0
    return api


class _FakeTimer:
    """Records (delay, fn) instead of actually scheduling a thread; the
    test fires it manually by calling .fn()."""
    instances: list["_FakeTimer"] = []

    def __init__(self, delay, fn):
        self.delay = delay
        self.fn = fn
        self.daemon = False
        self.started = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True


def _patch_timer(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr(window_mod.threading, "Timer", _FakeTimer)
    return _FakeTimer


def test_noop_when_not_waving(monkeypatch):
    from squid_pet import watcher as _w
    snooze = MagicMock(return_value=3)
    monkeypatch.setattr(_w, "snooze_all_awaiting_now", snooze)
    fake = _patch_timer(monkeypatch)

    api = _make_api()
    api._latest = PetState(state="working")

    result = api.acknowledge_approval()

    assert result == {"status": "not-waving", "bubble": None}
    snooze.assert_not_called()
    api._observer.on_interaction.assert_not_called()
    assert api._pending_bubble is None
    assert fake.instances == []


def test_bubble_shows_immediately_but_calm_is_deferred(monkeypatch):
    from squid_pet import watcher as _w
    snooze = MagicMock(return_value=1)
    monkeypatch.setattr(_w, "snooze_all_awaiting_now", snooze)
    fake = _patch_timer(monkeypatch)

    api = _make_api()
    api._latest = PetState(state="approval_needed")
    api._observer.on_interaction.return_value = "gotcha!"

    result = api.acknowledge_approval()

    # Bubble + return value land immediately.
    assert result == {"status": "calmed", "bubble": "gotcha!"}
    api._observer.on_interaction.assert_called_once_with("like")
    assert api._pending_bubble == "gotcha!"

    # The actual calm (snooze) must NOT have run yet -- it's scheduled,
    # not executed, so she's still genuinely waving at this point.
    snooze.assert_not_called()
    assert api._hint_text is None
    assert len(fake.instances) == 1
    timer = fake.instances[0]
    assert timer.delay == ACKNOWLEDGE_DISMISS_DELAY_SEC
    assert timer.fn == api._calm_squid
    assert timer.started is True

    # Simulate the timer firing after the delay.
    timer.fn()
    snooze.assert_called_once()
    assert api._hint_text == "shh -- calmed 1 wave"


def test_calm_squid_hint_pluralizes_and_handles_zero(monkeypatch):
    from squid_pet import watcher as _w

    monkeypatch.setattr(_w, "snooze_all_awaiting_now", MagicMock(return_value=0))
    api = _make_api()
    assert api._calm_squid() == 0
    assert api._hint_text == "nothing to calm"

    monkeypatch.setattr(_w, "snooze_all_awaiting_now", MagicMock(return_value=2))
    api2 = _make_api()
    assert api2._calm_squid() == 2
    assert api2._hint_text == "shh -- calmed 2 waves"


def test_calm_squid_swallows_snooze_failure(monkeypatch):
    from squid_pet import watcher as _w

    def _boom():
        raise RuntimeError("disk error")

    monkeypatch.setattr(_w, "snooze_all_awaiting_now", _boom)
    api = _make_api()
    assert api._calm_squid() == 0
    assert api._hint_text == "calm failed"


def test_acknowledge_does_not_publish_bubble_when_observer_returns_none(monkeypatch):
    from squid_pet import watcher as _w
    monkeypatch.setattr(_w, "snooze_all_awaiting_now", MagicMock(return_value=1))
    fake = _patch_timer(monkeypatch)

    api = _make_api()
    api._latest = PetState(state="approval_needed")
    api._observer.on_interaction.return_value = None

    result = api.acknowledge_approval()

    assert api._pending_bubble is None
    assert result == {"status": "calmed", "bubble": None}
    # Calm is still scheduled even when there's no bubble to show.
    assert len(fake.instances) == 1
