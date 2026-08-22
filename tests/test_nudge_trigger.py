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

from squid_pet.passthrough import (
    NudgeApproachTracker, CornerFleeApproachTracker, PassthroughController,
)


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


# ── CornerFleeApproachTracker (pure logic) ──────────────────────────────
# Independent of NudgeApproachTracker above: counts raw consecutive
# re-entries (no window/cooldown gating), reset only by a
# CORNER_FLEE_RESET_SEC lull. Replaces the old wanderer-side
# NUDGE_TO_CORNER_THRESHOLD scheme (2026-08-21) where fleeing to a corner
# required a run of already-gated *nudges*, not raw entries.

def test_corner_flee_first_two_entries_do_not_fire():
    t = CornerFleeApproachTracker(threshold=3, reset_sec=6.0)
    assert t.on_tick(True, False, now=0.0) is False   # 1st entry
    assert t.on_tick(False, True, now=0.1) is False    # left
    assert t.on_tick(True, False, now=0.2) is False    # 2nd entry


def test_corner_flee_dwelling_never_fires():
    """A plain hover (no re-entries) must never accumulate a streak,
    however long it dwells."""
    t = CornerFleeApproachTracker(threshold=3, reset_sec=6.0)
    assert t.on_tick(True, False, now=0.0) is False
    for i in range(1, 100):
        assert t.on_tick(True, True, now=i * 0.03) is False


def test_corner_flee_third_consecutive_entry_fires():
    t = CornerFleeApproachTracker(threshold=3, reset_sec=6.0)
    assert t.on_tick(True, False, now=0.0) is False    # 1st
    assert t.on_tick(False, True, now=0.1) is False
    assert t.on_tick(True, False, now=0.2) is False    # 2nd
    assert t.on_tick(False, True, now=0.3) is False
    assert t.on_tick(True, False, now=0.4) is True     # 3rd -> fire


def test_corner_flee_not_gated_by_a_window_unlike_nudge_tracker():
    """Unlike NudgeApproachTracker, entries spread well apart (but still
    within the reset lull) must still accumulate toward the threshold --
    there's no NUDGE_WINDOW_SEC-style burst-speed requirement here."""
    t = CornerFleeApproachTracker(threshold=3, reset_sec=6.0)
    assert t.on_tick(True, False, now=0.0) is False    # 1st
    assert t.on_tick(False, True, now=1.0) is False
    assert t.on_tick(True, False, now=2.5) is False    # 2nd, 2.5s later
    assert t.on_tick(False, True, now=3.0) is False
    assert t.on_tick(True, False, now=5.0) is True     # 3rd, still within reset_sec -> fire


def test_corner_flee_streak_resets_after_long_gap():
    t = CornerFleeApproachTracker(threshold=3, reset_sec=6.0)
    assert t.on_tick(True, False, now=0.0) is False    # 1st
    assert t.on_tick(False, True, now=0.1) is False
    assert t.on_tick(True, False, now=0.2) is False    # 2nd
    assert t.on_tick(False, True, now=0.3) is False
    # Long lull -- streak resets, so this counts as a fresh 1st entry.
    assert t.on_tick(True, False, now=10.0) is False
    assert t.on_tick(False, True, now=10.1) is False
    assert t.on_tick(True, False, now=10.2) is False   # would be 3rd pre-reset -> still False


def test_corner_flee_fires_again_after_firing():
    """After firing, the streak resets so it takes a fresh run of
    threshold entries to fire again."""
    t = CornerFleeApproachTracker(threshold=3, reset_sec=6.0)
    assert t.on_tick(True, False, now=0.0) is False    # 1st
    assert t.on_tick(False, True, now=0.1) is False
    assert t.on_tick(True, False, now=0.2) is False    # 2nd
    assert t.on_tick(False, True, now=0.3) is False
    assert t.on_tick(True, False, now=0.4) is True     # 3rd -> fires, streak resets

    assert t.on_tick(False, True, now=0.5) is False
    assert t.on_tick(True, False, now=0.6) is False    # 1st of a fresh streak
    assert t.on_tick(False, True, now=0.7) is False
    assert t.on_tick(True, False, now=0.8) is False    # 2nd
    assert t.on_tick(False, True, now=0.9) is False
    assert t.on_tick(True, False, now=1.0) is True     # 3rd -> fires again


def test_corner_flee_default_threshold_is_three():
    t = CornerFleeApproachTracker()
    assert t._threshold == 3
    assert t._reset_sec == 6.0


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
    ctrl._corner_flee_callback = None
    ctrl._corner_flee_tracker = CornerFleeApproachTracker()
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


def test_set_corner_flee_callback_fires_on_third_consecutive_entry():
    calls = []
    ctrl = _make_controller()
    ctrl.set_corner_flee_callback(lambda cx, cy: calls.append((cx, cy)))

    ctrl._track_nudge(True, 1.0, 1.0)   # 1st entry
    ctrl._track_nudge(False, 1.0, 1.0)
    ctrl._track_nudge(True, 2.0, 2.0)   # 2nd entry
    ctrl._track_nudge(False, 2.0, 2.0)
    ctrl._track_nudge(True, 3.0, 3.0)   # 3rd entry -> corner-flee fires

    assert calls == [(3.0, 3.0)]


def test_corner_flee_and_nudge_callbacks_are_independent():
    """Both trackers observe the same raw entry stream (_track_nudge uses
    real wall-clock time internally, and these calls execute back to
    back, well inside both the hop tracker's 0.8s window and the
    corner-flee tracker's 6.0s reset lull) and fire off their own
    thresholds -- the hop callback (threshold 2) fires before, and
    independently of, the corner-flee callback (threshold 3)."""
    hop_calls = []
    corner_calls = []
    ctrl = _make_controller()
    ctrl.set_nudge_callback(lambda cx, cy: hop_calls.append((cx, cy)))
    ctrl.set_corner_flee_callback(lambda cx, cy: corner_calls.append((cx, cy)))

    ctrl._track_nudge(True, 1.0, 1.0)   # 1st entry
    ctrl._track_nudge(False, 1.0, 1.0)
    ctrl._track_nudge(True, 2.0, 2.0)   # 2nd entry -> hop fires (nudge cooldown starts)
    assert hop_calls == [(2.0, 2.0)]
    assert corner_calls == []

    ctrl._track_nudge(False, 2.0, 2.0)
    ctrl._track_nudge(True, 3.0, 3.0)   # 3rd entry -> corner-flee fires (hop still cooling down)
    assert hop_calls == [(2.0, 2.0)]
    assert corner_calls == [(3.0, 3.0)]


def test_track_nudge_swallows_corner_flee_callback_exceptions():
    """A broken corner-flee callback must not take down the poll loop,
    and must not suppress the (working) hop callback."""
    ctrl = _make_controller()
    hop_calls = []

    def _boom(cx, cy):
        raise RuntimeError("boom")

    ctrl.set_nudge_callback(lambda cx, cy: hop_calls.append((cx, cy)))
    ctrl.set_corner_flee_callback(_boom)

    ctrl._track_nudge(True, 1.0, 1.0)
    ctrl._track_nudge(False, 1.0, 1.0)
    ctrl._track_nudge(True, 2.0, 2.0)  # hop fires normally
    ctrl._track_nudge(False, 2.0, 2.0)
    ctrl._track_nudge(True, 3.0, 3.0)  # corner-flee threshold hit -> callback raises, must not propagate

    assert hop_calls == [(2.0, 2.0)]
