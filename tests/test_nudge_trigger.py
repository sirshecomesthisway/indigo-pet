"""Tests for the nudge trigger: repeated rapid re-entries into Squid's
clickable bbox should read as "move, you're in the way" and fire a nudge,
while a single hover/click/drag touch must be completely unaffected.

NudgeApproachTracker is pure logic (no AppKit/threading) precisely so this
can be tested without a real NSWindow or the background poll thread.
PassthroughController wiring is covered by a couple of thin integration
tests using the same __new__ + manual-attribute pattern as
test_passthrough_state_mapping.py / test_passthrough_last_ignore_lock.py.
"""
from __future__ import annotations

import threading

from squid_pet.passthrough import NudgeApproachTracker, PassthroughController


# ── NudgeApproachTracker (pure logic) ───────────────────────────────────

def test_single_approach_does_not_fire():
    t = NudgeApproachTracker(threshold=2, window_sec=0.8, cooldown_sec=1.5)
    assert t.on_tick(True, False, now=0.0) is False


def test_dwelling_never_fires():
    """Cursor lands once and stays -- was_interactive stays True on every
    subsequent tick, so there's no 'fresh entry' to count, no matter how
    long it dwells. A plain hover must never trigger a nudge."""
    t = NudgeApproachTracker(threshold=2, window_sec=0.8, cooldown_sec=1.5)
    assert t.on_tick(True, False, now=0.0) is False  # first entry
    for i in range(1, 100):
        assert t.on_tick(True, True, now=i * 0.03) is False


def test_second_approach_within_window_fires():
    t = NudgeApproachTracker(threshold=2, window_sec=0.8, cooldown_sec=1.5)
    assert t.on_tick(True, False, now=0.0) is False       # 1st entry
    assert t.on_tick(False, True, now=0.1) is False        # left the bbox
    assert t.on_tick(True, False, now=0.3) is True         # 2nd entry -> fire


def test_approaches_outside_window_do_not_accumulate():
    """Two approaches spread further apart than window_sec must NOT fire --
    each one individually resets/expires rather than combining."""
    t = NudgeApproachTracker(threshold=2, window_sec=0.8, cooldown_sec=1.5)
    assert t.on_tick(True, False, now=0.0) is False
    assert t.on_tick(False, True, now=0.1) is False
    # Second entry arrives 1.0s later -- outside the 0.8s window.
    assert t.on_tick(True, False, now=1.0) is False


def test_cooldown_suppresses_immediate_retrigger():
    t = NudgeApproachTracker(threshold=2, window_sec=0.8, cooldown_sec=1.5)
    assert t.on_tick(True, False, now=0.0) is False
    assert t.on_tick(False, True, now=0.1) is False
    assert t.on_tick(True, False, now=0.3) is True          # fires, cooldown starts

    # Two more rapid approaches immediately after -- still cooling down.
    assert t.on_tick(False, True, now=0.4) is False
    assert t.on_tick(True, False, now=0.5) is False
    assert t.on_tick(False, True, now=0.6) is False
    assert t.on_tick(True, False, now=0.7) is False


def test_fires_again_after_cooldown_expires():
    t = NudgeApproachTracker(threshold=2, window_sec=0.8, cooldown_sec=1.5)
    assert t.on_tick(True, False, now=0.0) is False
    assert t.on_tick(False, True, now=0.1) is False
    assert t.on_tick(True, False, now=0.3) is True           # 1st fire, cooldown until 1.8

    assert t.on_tick(False, True, now=2.0) is False
    assert t.on_tick(True, False, now=2.1) is False           # 1st entry post-cooldown
    assert t.on_tick(False, True, now=2.2) is False
    assert t.on_tick(True, False, now=2.4) is True            # 2nd entry -> fires again


def test_default_thresholds_are_two_approaches_800ms():
    t = NudgeApproachTracker()
    assert t._threshold == 2
    assert t._window_sec == 0.8


# ── PassthroughController wiring ────────────────────────────────────────

def _make_controller():
    ctrl = PassthroughController.__new__(PassthroughController)
    ctrl._get_ns_window = lambda: None
    ctrl._masks = {}
    ctrl._current_state = "idle"
    ctrl._current_edge = ""
    ctrl._paused = False
    ctrl._hidden = False
    ctrl._stop = threading.Event()
    ctrl._lock = threading.Lock()
    ctrl._last_ignore = None
    ctrl._nudge_callback = None
    ctrl._nudge_tracker = NudgeApproachTracker()
    ctrl._was_interactive = False
    return ctrl


def test_set_nudge_callback_fires_with_cursor_position():
    calls = []
    ctrl = _make_controller()
    ctrl.set_nudge_callback(lambda cx, cy: calls.append((cx, cy)))

    ctrl._track_nudge(True, 100.0, 200.0)   # 1st entry
    ctrl._track_nudge(False, 100.0, 200.0)  # left
    ctrl._track_nudge(True, 105.0, 195.0)   # 2nd entry -> should fire

    assert calls == [(105.0, 195.0)]


def test_single_touch_never_invokes_callback():
    calls = []
    ctrl = _make_controller()
    ctrl.set_nudge_callback(lambda cx, cy: calls.append((cx, cy)))

    ctrl._track_nudge(True, 100.0, 200.0)
    # Dwelling on the same tick repeatedly (hover/click/drag in progress).
    for _ in range(20):
        ctrl._track_nudge(True, 100.0, 200.0)

    assert calls == []


def test_track_nudge_swallows_callback_exceptions():
    """A broken callback must not take down the poll loop."""
    ctrl = _make_controller()

    def _boom(cx, cy):
        raise RuntimeError("boom")

    ctrl.set_nudge_callback(_boom)
    ctrl._track_nudge(True, 0.0, 0.0)
    ctrl._track_nudge(False, 0.0, 0.0)
    ctrl._track_nudge(True, 0.0, 0.0)  # must not raise
