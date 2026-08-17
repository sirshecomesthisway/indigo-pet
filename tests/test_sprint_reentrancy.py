"""Regression tests: sprint_perimeter() must not allow two concurrent
sprints. Found in a 2026-08-17 code review: it unconditionally spawned
a new _do_sprint_perimeter thread with no check on self._sprint_mode,
and _sprint_mode itself was an unlocked plain bool only set True deep
inside the thread (after a SPRINT_WAKE_WAIT_SEC sleep) -- a double
right-click of "Sprint the perimeter!" before the first sprint finished
spawned two threads racing on window origin / wrapper-rotation state.

Fix: the check-and-set moved to sprint_perimeter() itself, atomic under
a lock, synchronous before the thread spawns.
"""
from __future__ import annotations

import threading
import time

import pytest

from squid_pet.wanderer import WanderController


@pytest.fixture
def wc():
    return WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (100.0, 100.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )


def test_sprint_mode_starts_false(wc):
    assert wc._sprint_mode is False


def test_second_sprint_call_is_ignored_while_first_in_flight(wc, monkeypatch):
    """Block the sprint thread mid-flight (right after it sets
    _sprint_mode) and verify a second call is a pure no-op -- no second
    thread spawned, _sprint_mode stays True (owned by the first call)."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    entered = threading.Event()
    release = threading.Event()

    def blocking_wake():
        entered.set()
        release.wait(timeout=5)

    monkeypatch.setattr(wc, "_trigger_wake", blocking_wake)

    thread_count_before = threading.active_count()
    wc.sprint_perimeter()
    assert entered.wait(timeout=2), "first sprint never started"
    assert wc._sprint_mode is True

    # Second call while the first is still blocked inside _trigger_wake.
    wc.sprint_perimeter()
    # No new sprint thread should have been spawned.
    assert threading.active_count() == thread_count_before + 1

    release.set()
    # Wait for the (now unblocked, fast-forwarded) sprint to finish
    # cleanly, so it doesn't leak a running thread into later tests.
    deadline = time.time() + 5
    while wc._sprint_mode and time.time() < deadline:
        time.sleep(0.02)
    assert wc._sprint_mode is False


def test_sprint_mode_resets_after_completion(wc, monkeypatch):
    """A sprint that runs to completion must clear _sprint_mode so a
    later call can proceed. Fast-forward the real multi-second sprint
    animation (SPRINT_WAKE_WAIT_SEC + 4 legs of rotation/walk sleeps)
    by no-op'ing time.sleep within the wanderer module -- the sprint
    logic itself is what's under test, not real wall-clock timing."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc.sprint_perimeter()
    deadline = time.time() + 5
    while wc._sprint_mode and time.time() < deadline:
        time.sleep(0.02)
    assert wc._sprint_mode is False, (
        "sprint_mode did not reset -- a later sprint_perimeter() call "
        "would be permanently ignored"
    )


def test_sprint_mode_resets_when_origin_lookup_fails(monkeypatch):
    """Early-return path (get_window_origin returns None) must still
    clear _sprint_mode -- this is exactly the path that used to never
    set it True in the old code, so moving the set earlier risked
    leaking it stuck True forever if the reset wasn't also centralized."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc2 = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: None,  # triggers the early return
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc2.sprint_perimeter()
    deadline = time.time() + 5
    while wc2._sprint_mode and time.time() < deadline:
        time.sleep(0.02)
    assert wc2._sprint_mode is False
