"""Tests for RoutineController's idle-chatter timer: fires chatter_cb()
roughly every CHATTER_MIN/MAX_INTERVAL_SEC, independent of which
IDLE_ROUTINE action (rest/look-around/walk-*) is currently in flight, and
only while genuinely idle (not paused/busy/dragging/mood-active).

(piped through to an idle_chatter bubble by PetApi._fire_idle_chatter in
window.py.)
"""
from __future__ import annotations

import pytest

from squid_pet.routine import (
    RoutineController, CHATTER_MIN_INTERVAL_SEC, CHATTER_MAX_INTERVAL_SEC,
)


@pytest.fixture
def rc_factory():
    def _make(chatter_cb=None, state="idle", mood=""):
        return RoutineController(
            wanderer=None,  # not exercised unless an action actually fires
            get_state=lambda: state,
            is_drag_active=lambda: False,
            is_busy=lambda: False,
            get_mood=lambda: mood,
            chatter_cb=chatter_cb,
        )
    return _make


def test_chatter_does_not_fire_before_first_interval_elapses(rc_factory, monkeypatch):
    calls = []
    rc = rc_factory(chatter_cb=lambda: calls.append(1))
    t = [1000.0]
    monkeypatch.setattr("squid_pet.routine.time.time", lambda: t[0])
    monkeypatch.setattr("squid_pet.routine.random.uniform", lambda lo, hi: 45.0)

    rc._tick()  # arms the timer for t=1045.0, does not fire yet
    assert calls == []
    assert rc._next_chatter_at == 1045.0

    t[0] = 1044.9
    rc._tick()
    assert calls == []


def test_chatter_fires_once_interval_elapses(rc_factory, monkeypatch):
    calls = []
    rc = rc_factory(chatter_cb=lambda: calls.append(1))
    t = [1000.0]
    monkeypatch.setattr("squid_pet.routine.time.time", lambda: t[0])
    monkeypatch.setattr("squid_pet.routine.random.uniform", lambda lo, hi: 45.0)

    rc._tick()  # arms for 1045.0
    t[0] = 1045.0
    rc._tick()
    assert calls == [1]


def test_chatter_interval_is_within_configured_range(rc_factory, monkeypatch):
    """Sanity: the actual (non-monkeypatched) random.uniform call must
    draw from [CHATTER_MIN_INTERVAL_SEC, CHATTER_MAX_INTERVAL_SEC]."""
    rc = rc_factory(chatter_cb=lambda: None)
    t = [1000.0]
    monkeypatch.setattr("squid_pet.routine.time.time", lambda: t[0])

    rc._tick()
    assert rc._next_chatter_at is not None
    delta = rc._next_chatter_at - 1000.0
    assert CHATTER_MIN_INTERVAL_SEC <= delta <= CHATTER_MAX_INTERVAL_SEC


def test_chatter_fires_while_a_walk_action_is_in_flight(rc_factory, monkeypatch):
    """The whole point of decoupling from _fire('rest'): chatter must be
    able to land while she's mid-walk, not just resting."""
    calls = []
    rc = rc_factory(chatter_cb=lambda: calls.append(1))
    t = [1000.0]
    monkeypatch.setattr("squid_pet.routine.time.time", lambda: t[0])
    monkeypatch.setattr("squid_pet.routine.random.uniform", lambda lo, hi: 45.0)

    # Simulate being mid walk-short action window.
    rc._action_done_at = 1000.0 + 9999.0  # far in the future -- still "in flight"
    rc._idle_since = 1000.0 - 100.0        # ramp already elapsed

    rc._tick()  # arms chatter timer at 1045.0; action window still open, no advance
    t[0] = 1045.0
    rc._tick()
    assert calls == [1]


def test_chatter_countdown_resets_when_no_longer_idle(rc_factory, monkeypatch):
    calls = []
    rc = rc_factory(chatter_cb=lambda: calls.append(1), state="idle")
    t = [1000.0]
    monkeypatch.setattr("squid_pet.routine.time.time", lambda: t[0])
    monkeypatch.setattr("squid_pet.routine.random.uniform", lambda lo, hi: 45.0)

    rc._tick()
    assert rc._next_chatter_at == 1045.0

    rc._get_state = lambda: "working"  # no longer idle -- should_pause() now True
    rc._tick()
    assert rc._next_chatter_at is None

    rc._get_state = lambda: "idle"
    t[0] = 1046.0  # well past the old deadline
    rc._tick()
    assert calls == [], "must not fire immediately off a stale deadline"
    assert rc._next_chatter_at == 1046.0 + 45.0


def test_chatter_does_not_fire_while_mood_active(rc_factory, monkeypatch):
    calls = []
    rc = rc_factory(chatter_cb=lambda: calls.append(1), mood="drowsy")
    t = [1000.0]
    monkeypatch.setattr("squid_pet.routine.time.time", lambda: t[0])
    for _ in range(5):
        t[0] += 60.0
        rc._tick()
    assert calls == []
