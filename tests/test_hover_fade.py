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

from squid_pet.passthrough import HoverDwellTracker, PassthroughController


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
