"""Regression tests: _last_ignore must be read/written under
PassthroughController._lock everywhere, matching every sibling field
(_paused, _hidden, _current_state, _current_edge).

Found in a 2026-08-17 code review: _apply_ignore()'s compare-and-set on
_last_ignore ran with no lock while every sibling field's setter was
guarded. The background poll loop also read _last_ignore unlocked (twice
for diagnostics, once to decide the hysteresis want_ignore branch) --
concurrent writers (pause()/set_hidden(), called from the drag thread
or main thread) could race with the poll thread's reads/writes,
potentially leaving _last_ignore inconsistent with the real NSWindow
state and causing a later genuine state change to be silently skipped.

PassthroughController.__init__ calls load_alpha_masks() (real disk I/O)
and expects a callable NSWindow getter -- these tests build a minimal
double via __new__ + direct attribute assignment, same pattern as
test_passthrough_state_mapping.py.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from squid_pet.passthrough import PassthroughController


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
    return ctrl


def test_apply_ignore_updates_last_ignore():
    ctrl = _make_controller()
    with patch("PyObjCTools.AppHelper.callAfter"):
        ctrl._apply_ignore(True)
    assert ctrl._last_ignore is True


def test_apply_ignore_skips_dispatch_when_unchanged():
    ctrl = _make_controller()
    with patch("PyObjCTools.AppHelper.callAfter") as mock_call_after:
        ctrl._apply_ignore(True)
        assert mock_call_after.call_count == 1
        ctrl._apply_ignore(True)  # no-op: already True
        assert mock_call_after.call_count == 1  # not called again


def test_apply_ignore_no_ns_window_does_not_raise():
    ctrl = _make_controller()
    ctrl._get_ns_window = lambda: None
    ctrl._apply_ignore(True)  # must not raise
    assert ctrl._last_ignore is None  # unchanged -- nothing to apply to


def test_concurrent_apply_ignore_calls_leave_consistent_state():
    """Stress test: many threads calling _apply_ignore with alternating
    values concurrently must never raise and must always leave
    _last_ignore matching SOME value that was actually requested (not a
    torn/inconsistent read). This is the scenario the lock protects --
    pause()/set_hidden() (arbitrary callers) racing the poll loop.
    """
    ctrl = _make_controller()
    errors = []

    def hammer(value: bool):
        try:
            for _ in range(50):
                ctrl._apply_ignore(value)
        except Exception as e:
            errors.append(e)

    with patch("PyObjCTools.AppHelper.callAfter"):
        threads = [
            threading.Thread(target=hammer, args=(i % 2 == 0,))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

    assert not errors, f"concurrent _apply_ignore raised: {errors}"
    assert ctrl._last_ignore in (True, False)


def test_loop_hysteresis_read_is_locked_snapshot(monkeypatch):
    """The _loop() hysteresis branch must read _last_ignore through a
    locked snapshot, not the bare attribute, so it can't observe a
    torn/concurrent write mid-decision. We can't easily drive the real
    _loop() (needs AppKit/NSEvent), so this asserts the fix at the
    source level: the snapshot variable name appears immediately after
    an acquisition of self._lock in the hysteresis block."""
    import inspect
    from squid_pet import passthrough
    src = inspect.getsource(passthrough.PassthroughController._loop)
    assert "_last_ignore_snapshot" in src
    # The snapshot must be taken inside a `with self._lock:` block.
    idx = src.index("_last_ignore_snapshot = self._last_ignore")
    preceding = src[:idx]
    assert preceding.rstrip().endswith("with self._lock:")
