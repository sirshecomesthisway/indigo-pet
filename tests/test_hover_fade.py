"""Tests for hover-fade-through (2026-08-27n): hold the cursor over Squid
for HOVER_DWELL_SEC continuously and she fades to HOVER_FADE_ALPHA and
becomes click-through, so whatever she's covering is reachable without a
deliberate nudge/drag first.

HoverDwellTracker is pure logic (no AppKit/threading), same rationale as
NudgeApproachTracker/CornerFleeApproachTracker -- see test_nudge_trigger.py.
PassthroughController wiring (_apply_fade, pause()/set_hidden() reset) uses
the same __new__ + manual-attribute pattern as
test_passthrough_last_ignore_lock.py.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from squid_pet.passthrough import (
    HOVER_STILLNESS_TOLERANCE_PX,
    CornerFleeApproachTracker,
    HoverDwellTracker,
    PassthroughController,
    shift_held,
)


# ── HoverDwellTracker (pure logic) ──────────────────────────────────────

def test_fresh_entry_does_not_dwell_immediately():
    t = HoverDwellTracker(dwell_sec=1.0)
    assert t.is_dwelling(True, now=0.0) is False


def test_dwells_once_threshold_crossed():
    t = HoverDwellTracker(dwell_sec=1.0)
    assert t.is_dwelling(True, now=0.0) is False   # entry
    assert t.is_dwelling(True, now=0.5) is False   # still under threshold
    assert t.is_dwelling(True, now=1.0) is True    # exactly at threshold
    assert t.is_dwelling(True, now=1.5) is True    # stays true while dwelling


def test_leaving_resets_dwell_timer():
    t = HoverDwellTracker(dwell_sec=1.0)
    assert t.is_dwelling(True, now=0.0) is False
    assert t.is_dwelling(True, now=1.0) is True
    # Cursor leaves the bbox.
    assert t.is_dwelling(False, now=1.1) is False
    # Re-entering must NOT immediately dwell -- timer restarted.
    assert t.is_dwelling(True, now=1.2) is False
    assert t.is_dwelling(True, now=2.2) is True


def test_reset_clears_in_progress_dwell():
    t = HoverDwellTracker(dwell_sec=1.0)
    t.is_dwelling(True, now=0.0)
    t.reset()
    # Even though "now" would have crossed threshold from the original
    # entry time, reset() means the next tick starts a fresh timer.
    assert t.is_dwelling(True, now=1.5) is False


def test_never_entering_never_dwells():
    t = HoverDwellTracker(dwell_sec=1.0)
    for i in range(50):
        assert t.is_dwelling(False, now=i * 0.03) is False


# ── PassthroughController._apply_fade / reset wiring ────────────────────

def _make_controller():
    ctrl = PassthroughController.__new__(PassthroughController)
    ctrl._get_ns_window = lambda: MagicMock()
    ctrl._masks = {}
    ctrl._current_state = "idle"
    ctrl._current_edge = ""
    ctrl._paused = False
    ctrl._hidden = False
    ctrl._stop = threading.Event()
    ctrl._lock = threading.Lock()
    ctrl._last_ignore = None
    ctrl._last_faded = None
    ctrl._hover_tracker = HoverDwellTracker()
    return ctrl


# ── Real __init__ construction (not the __new__ double above) ──────────
# Pink-2026-08-27o regression, caught live: a hasty replace_all edit
# turned PassthroughController.__init__'s `self._hover_tracker =
# HoverDwellTracker()` assignment into `self._hover_tracker.reset()` --
# AttributeError on every single app startup (PassthroughController is
# constructed fresh in window.py's on_loaded()), silently crashing
# on_loaded() partway through and leaving click-through/passthrough
# never initialized. Every other test in this file uses __new__ +
# manual attribute assignment specifically to avoid load_alpha_masks()'s
# real disk I/O -- which is exactly why none of them caught this: they
# never exercise the real __init__ body at all. This one does.
def test_real_init_constructs_without_error():
    ctrl = PassthroughController(lambda: None)
    assert isinstance(ctrl._hover_tracker, HoverDwellTracker)
    assert ctrl._last_faded is None


def test_apply_fade_updates_last_faded():
    ctrl = _make_controller()
    with patch("PyObjCTools.AppHelper.callAfter"):
        ctrl._apply_fade(True)
    assert ctrl._last_faded is True


def test_apply_fade_skips_dispatch_when_unchanged():
    ctrl = _make_controller()
    ctrl._last_faded = False
    with patch("PyObjCTools.AppHelper.callAfter") as mock_call:
        ctrl._apply_fade(False)
    mock_call.assert_not_called()


def test_pause_clears_fade_and_resets_dwell_timer():
    """Dragging a half-faded, click-through Squid would be incoherent --
    pause() (called on drag start) must restore full opacity and forget
    any in-progress dwell."""
    ctrl = _make_controller()
    with patch("PyObjCTools.AppHelper.callAfter"):
        ctrl._hover_tracker.is_dwelling(True, now=0.0)
        ctrl._apply_fade(True)
        assert ctrl._last_faded is True

        ctrl.pause()

    assert ctrl._last_faded is False
    # Dwell timer was reset -- immediately re-entering must not still
    # count as continuously dwelling from before the pause.
    assert ctrl._hover_tracker.is_dwelling(True, now=0.01) is False


def test_set_hidden_true_clears_fade_and_resets_dwell_timer():
    ctrl = _make_controller()
    with patch("PyObjCTools.AppHelper.callAfter"):
        ctrl._hover_tracker.is_dwelling(True, now=0.0)
        ctrl._apply_fade(True)
        assert ctrl._last_faded is True

        ctrl.set_hidden(True)

    assert ctrl._last_faded is False
    assert ctrl._hover_tracker.is_dwelling(True, now=0.01) is False


# ── Grab-vs-get-out-of-the-way (Pink-2026-09-01) ───────────────────────
# Pink: "she becomes opaque or starts running away from me, so the drag
# becomes a chasing-after-me." Two features read the same signal (cursor
# over the bbox) and infer opposite intents. Fade-through fired at 1.0s
# and made her click-through in the same instant, so any grab slower than
# one second silently went to the app underneath -- and the retries that
# provoked are exactly the re-entry pattern the flee tracker reads as
# "move, you're in the way".
def test_fade_arrives_before_click_through_not_with_it():
    """Fix 1. At the fade threshold she is translucent -- a warning that
    she is about to step aside -- but still grabbable."""
    t = HoverDwellTracker(dwell_sec=1.0, passthrough_dwell_sec=2.5)
    faded, click_through = t.update(True, now=0.0)
    assert (faded, click_through) == (False, False)

    faded, click_through = t.update(True, now=1.0)
    assert faded is True
    assert click_through is False, "a 1s-old hover must still be grabbable"


def test_click_through_arrives_at_the_later_threshold():
    t = HoverDwellTracker(dwell_sec=1.0, passthrough_dwell_sec=2.5)
    t.update(True, now=0.0)
    assert t.update(True, now=2.49)[1] is False
    assert t.update(True, now=2.5)[1] is True


def test_moving_over_her_restarts_the_dwell_timer():
    """Fix 3. Presence is not intent. Sweeping the cursor across her while
    aiming used to accumulate dwell exactly like parking on her did."""
    t = HoverDwellTracker(dwell_sec=1.0, move_tolerance_px=4.0)
    t.update(True, now=0.0, cx=100.0, cy=100.0)
    assert t.update(True, now=0.9, cx=100.0, cy=100.0)[0] is False
    # Cursor moves: this is aiming, not dwelling. Timer restarts.
    assert t.update(True, now=1.0, cx=140.0, cy=100.0)[0] is False
    assert t.update(True, now=1.6, cx=140.0, cy=100.0)[0] is False
    assert t.update(True, now=2.0, cx=140.0, cy=100.0)[0] is True


def test_tiny_jitter_still_counts_as_still():
    """A resting hand is never perfectly still; the tolerance has to
    absorb that or the feature never fires at all."""
    t = HoverDwellTracker(dwell_sec=1.0, move_tolerance_px=4.0)
    t.update(True, now=0.0, cx=100.0, cy=100.0)
    assert t.update(True, now=1.0, cx=102.0, cy=101.0)[0] is True


def test_require_exit_blocks_refade_until_the_cursor_leaves():
    """Fix 2. After a drop your hand is still on her; without this she
    turns translucent a second after you place her."""
    t = HoverDwellTracker(dwell_sec=1.0)
    t.require_exit()
    assert t.update(True, now=0.0)[0] is False
    assert t.update(True, now=5.0)[0] is False, "must not re-fade while held"

    t.update(False, now=5.1)          # cursor finally leaves
    t.update(True, now=5.2)           # and comes back
    assert t.update(True, now=6.2)[0] is True, "re-arms normally afterwards"


def test_deliberate_retries_do_not_stack_into_a_flee():
    """Fix 4. Three grab attempts a few seconds apart are a user trying to
    pick her up, not someone shooing her away."""
    t = CornerFleeApproachTracker(threshold=3, reset_sec=2.5)
    assert t.on_tick(True, False, now=0.0) is False    # attempt 1
    assert t.on_tick(True, False, now=3.0) is False    # attempt 2, 3s later
    assert t.on_tick(True, False, now=6.0) is False, (
        "slow, deliberate retries must not accumulate toward fleeing")


def test_rapid_re_entries_still_flee():
    """The feature itself must survive: genuinely batting at her still
    makes her get out of the way."""
    t = CornerFleeApproachTracker(threshold=3, reset_sec=2.5)
    assert t.on_tick(True, False, now=0.0) is False
    assert t.on_tick(True, False, now=0.4) is False
    assert t.on_tick(True, False, now=0.8) is True


def test_shift_is_the_escape_hatch():
    """Holding Shift means "I want YOU", whatever state she is in."""
    NSEventModifierFlagShift = 1 << 17
    assert shift_held(NSEventModifierFlagShift) is True
    assert shift_held(NSEventModifierFlagShift | (1 << 20)) is True   # +cmd
    assert shift_held(0) is False
    assert shift_held(1 << 19) is False                                # option


def test_a_resting_hand_still_dwells_at_the_default_tolerance():
    """Pink: 4px meant "a tremor cancels it". The tolerance is a drift
    budget from where the dwell began, so ordinary hand noise has to fit
    inside it comfortably or the feature never fires."""
    t = HoverDwellTracker(dwell_sec=1.0)
    t.update(True, now=0.0, cx=100.0, cy=100.0)
    assert t.update(True, now=1.0, cx=110.0, cy=107.0)[0] is True, (
        "10px of tremor must not cancel a deliberate dwell")


def test_an_aiming_sweep_still_restarts_at_the_default_tolerance():
    """The other side of the widening: it must still tell "parked on her"
    from "moving across her toward a grab"."""
    t = HoverDwellTracker(dwell_sec=1.0)
    t.update(True, now=0.0, cx=100.0, cy=100.0)
    assert t.update(True, now=1.0, cx=160.0, cy=100.0)[0] is False
    assert HOVER_STILLNESS_TOLERANCE_PX < 60.0, (
        "tolerance must stay well under a character-width sweep")
