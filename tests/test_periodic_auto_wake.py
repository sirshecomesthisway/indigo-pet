"""Tests for the periodic auto-wake feature (Pink-2026-08-30): once Squid
falls asleep (frontend mood == "drowsy"/"sleeping", driven by agent_idle_seconds
in index.html), nothing used to wake her again short of real Claude Code /
Codex activity or a user poke/sprint -- "asleep forever" if the user steps
away. get_state() now force-wakes her every PERIODIC_WAKE_CADENCE_SEC while
she's drowsy/sleeping, granting a PERIODIC_WAKE_AWAKE_SEC stay-awake window
(reusing the existing user_wake_until/wake_trigger_seq plumbing poke() uses)
so RoutineController un-pauses and runs a real stretch/idle/walk lap before
she's allowed to drift back to sleep under the normal agent_idle logic.

_wake() is the shared helper behind poke()/swing/sprint/periodic-wake; it
also resets the periodic clock, so a real interaction doesn't immediately
get followed by a redundant auto-wake.
"""
from __future__ import annotations

import threading

from squid_pet.window import PetApi, PERIODIC_WAKE_CADENCE_SEC, PERIODIC_WAKE_AWAKE_SEC
from squid_pet.watcher import PetState


def _make_api(mood: str = "", last_wake_at: float = 0.0, state: str = "idle") -> PetApi:
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
    api._frontend_mood = mood
    api._last_wake_at = last_wake_at
    return api


def test_wake_helper_bumps_trigger_and_sets_override_and_clock(monkeypatch):
    api = _make_api()
    monkeypatch.setattr("squid_pet.window._time.time", lambda: 5000.0)

    api._wake(60.0)

    assert api._wake_trigger_seq == 1
    assert api._user_wake_until == 5060.0
    assert api._last_wake_at == 5000.0


def test_no_periodic_wake_while_awake(monkeypatch):
    api = _make_api(mood="", last_wake_at=0.0)
    monkeypatch.setattr("squid_pet.window._time.time", lambda: PERIODIC_WAKE_CADENCE_SEC * 10)

    api.get_state()

    assert api._wake_trigger_seq == 0


def test_no_periodic_wake_before_cadence_elapses(monkeypatch):
    api = _make_api(mood="sleeping", last_wake_at=1000.0)
    monkeypatch.setattr(
        "squid_pet.window._time.time",
        lambda: 1000.0 + PERIODIC_WAKE_CADENCE_SEC - 1,
    )

    api.get_state()

    assert api._wake_trigger_seq == 0


def test_periodic_wake_fires_after_cadence_while_sleeping(monkeypatch):
    api = _make_api(mood="sleeping", last_wake_at=1000.0)
    fire_at = 1000.0 + PERIODIC_WAKE_CADENCE_SEC
    monkeypatch.setattr("squid_pet.window._time.time", lambda: fire_at)

    d = api.get_state()

    assert api._wake_trigger_seq == 1
    assert d["wake_trigger_seq"] == 1
    assert d["user_wake_remaining"] == PERIODIC_WAKE_AWAKE_SEC
    assert api._last_wake_at == fire_at


def test_periodic_wake_fires_while_drowsy(monkeypatch):
    api = _make_api(mood="drowsy", last_wake_at=1000.0)
    monkeypatch.setattr(
        "squid_pet.window._time.time",
        lambda: 1000.0 + PERIODIC_WAKE_CADENCE_SEC,
    )

    api.get_state()

    assert api._wake_trigger_seq == 1


def test_periodic_wake_does_not_refire_within_new_window(monkeypatch):
    api = _make_api(mood="sleeping", last_wake_at=1000.0)
    t = [1000.0 + PERIODIC_WAKE_CADENCE_SEC]
    monkeypatch.setattr("squid_pet.window._time.time", lambda: t[0])

    api.get_state()
    assert api._wake_trigger_seq == 1

    t[0] += 5.0  # well within the fresh cadence window, still "sleeping"
    api.get_state()
    assert api._wake_trigger_seq == 1, "must not fire again until next cadence"
