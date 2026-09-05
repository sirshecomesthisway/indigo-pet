"""Tests for the periodic auto-wake feature (Pink-2026-08-30): once Squid
falls asleep (frontend mood == "drowsy"/"sleeping", driven by agent_idle_seconds
in index.html), nothing used to wake her again short of real Claude Code /
Codex activity or a user poke/sprint -- "asleep forever" if the user steps
away. get_state() now force-wakes her every PERIODIC_WAKE_CADENCE_SEC while
she's drowsy/sleeping, granting a PERIODIC_WAKE_AWAKE_SEC stay-awake window
(reusing the existing user_wake_until/wake_trigger_seq plumbing poke() uses)
so RoutineController un-pauses and runs a real stretch/idle/walk lap before
she's allowed to drift back to sleep under the normal agent-idle logic.

_wake() is the shared helper behind poke()/swing/sprint/periodic-wake; it
also resets the periodic clock, so a real interaction doesn't immediately
get followed by a redundant auto-wake.
"""
from __future__ import annotations

import threading
import time

import pytest

from squid_pet import watcher
from squid_pet.window import PetApi, PERIODIC_WAKE_CADENCE_SEC, PERIODIC_WAKE_AWAKE_SEC
from squid_pet.watcher import PetState


class _StubObserver:
    """poke() fires a bubble; these tests only care about the wake."""
    def on_interaction(self, kind):
        return None


def _quiet_machine(monkeypatch) -> watcher.StateMachine:
    """A real StateMachine whose agents have been quiet past the sleep
    threshold, with the two external signals compute() consults stubbed
    out (see test_state_machine.install_world for why the
    awaiting-input dir must be isolated from the real one)."""
    monkeypatch.setattr(watcher, "macos_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(watcher, "CLAUDE_AWAITING_INPUT_DIR", "/nonexistent")
    sm = watcher.StateMachine(detectors=[])
    sm._agent_idle_since = time.time() - (watcher.IDLE_THRESHOLD_SEC + 1)
    return sm


def _make_api(mood: str = "", last_wake_at: float = 0.0, state: str = "idle",
              sm=None) -> PetApi:
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
    api._observer = _StubObserver()
    if sm is not None:
        api.set_state_machine(sm)
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


# ──────────────────────────────────────────────────────────────────────
# End-to-end: a wake must reach the state machine (2026-09-04)
# ──────────────────────────────────────────────────────────────────────
# Everything above asserts the SIGNAL -- wake_trigger_seq bumped,
# user_wake_remaining set. None of it asserted the OUTCOME, and the
# outcome was broken the whole time: _wake() only ever touched PetApi's
# own two fields, so the watcher kept reporting state == "sleeping"
# straight through the awake window. Observed live 6 times in one
# afternoon (/tmp/squid-pet.out.log): "periodic auto-wake" fires,
# "mood notify: sleeping -> stretch -> (awake)", and the very next tick
# still reads state=sleeping. She stretched and went back to sleep,
# because index.html's wakeUpWithStretch() ends by restoring
# spriteUrl(currentState) -- and currentState was still "sleeping".
#
# These tests pin the seam the layer-local ones each missed.
def test_periodic_wake_actually_takes_her_out_of_the_sleeping_state(monkeypatch):
    sm = _quiet_machine(monkeypatch)
    assert sm.compute().state == "sleeping", "baseline: she is genuinely asleep"
    api = _make_api(
        mood="sleeping",
        last_wake_at=time.time() - PERIODIC_WAKE_CADENCE_SEC - 1,
        state="sleeping",
        sm=sm,
    )

    api.get_state()

    assert api._wake_trigger_seq == 1, "the wake fired"
    assert sm.compute().state == "idle", "...and the state machine agrees"


def test_periodic_wake_holds_her_awake_for_the_full_spec_window(monkeypatch):
    sm = _quiet_machine(monkeypatch)
    api = _make_api(
        mood="sleeping",
        last_wake_at=time.time() - PERIODIC_WAKE_CADENCE_SEC - 1,
        state="sleeping",
        sm=sm,
    )
    before = time.time()

    api.get_state()

    assert sm.awake_hold_until == pytest.approx(
        before + PERIODIC_WAKE_AWAKE_SEC, abs=2.0
    ), "backend hold and frontend override must expire together"


def test_poke_also_takes_her_out_of_the_sleeping_state(monkeypatch):
    """Same bug, user-visible path: poking a sleeping Squid played the
    stretch and then put sleeping.png right back."""
    sm = _quiet_machine(monkeypatch)
    assert sm.compute().state == "sleeping"
    api = _make_api(mood="sleeping", state="sleeping", sm=sm)

    api.poke()

    assert sm.compute().state == "idle"


def test_wake_survives_having_no_state_machine_yet(monkeypatch):
    """PetApi is constructed and update()d once in main() BEFORE
    watcher_thread() calls set_state_machine, and the tests above build it
    via __new__. A wake in that window must degrade, not raise."""
    api = _make_api(mood="sleeping", last_wake_at=0.0, state="sleeping")
    monkeypatch.setattr("squid_pet.window._time.time", lambda: PERIODIC_WAKE_CADENCE_SEC * 10)

    api.get_state()

    assert api._wake_trigger_seq == 1


def test_watcher_thread_wires_the_machine_it_feeds_updates_from(monkeypatch):
    """The last link in the chain. _wake() can only reach the state machine
    if watcher_thread() introduced them, and it must do so BEFORE its first
    compute -- otherwise a wake in the opening seconds is silently dropped,
    which is the same class of gap that hid this bug in the first place."""
    import threading as _threading
    from squid_pet import window as window_mod

    made = []

    class _FakeMachine:
        def __init__(self, *a, **kw):
            made.append(self)

    monkeypatch.setattr(window_mod.watcher, "StateMachine", _FakeMachine)
    api = _make_api()
    stop = _threading.Event()
    stop.set()  # loop body never runs; only the pre-loop wiring does

    window_mod.watcher_thread(api, stop)

    assert len(made) == 1
    assert api._sm is made[0], "watcher_thread must hand PetApi its own machine"
