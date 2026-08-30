"""Regression tests: _menu_snap() and _menu_recenter() must sync the
wanderer's edge tracker after moving the window, same as next_corner()
and drag's _on_end already do.

Found in a 2026-08-17 code review: without the sync, clicking
Position -> <corner> or Recenter from the right-click menu moves the
window but leaves the sprite rotation (and passthrough's edge-aware
click hit-test offset, which keys off the same tracked edge) stale
until the next wander tick happens to run.

Pink-2026-08-27d: the original fix called wanderer.refresh_edge()
(distance-based classification from live window origin), but that
never actually worked for corner-snapped positions -- move_to_corner's
own EDGE_MARGIN(20) was never coordinated with wanderer.py's tighter
margins tuned for wander-walk targets, so a freshly corner-snapped
window sits just outside EDGE_BAND_PX and refresh_edge() silently
returns "" (no edge), leaving the sprite un-rotated regardless. Fixed
by using force_edge() with an edge derived directly from the corner
name (window._edge_for_corner) instead of inferring it from distance.

PetApi.__init__ pulls in real pywebview/AppKit state, so these tests
build a minimal double via __new__ + direct attribute assignment
(same pattern as test_passthrough_state_mapping.py's controller
double) rather than constructing a real PetApi.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from squid_pet.window import PetApi, _edge_for_corner


def _make_api(wanderer=None):
    api = PetApi.__new__(PetApi)
    api._wanderer = wanderer
    api._lock = threading.Lock()
    api._hint_text = ""
    api._hint_seq = 0
    return api


def test_menu_snap_refreshes_edge_on_success():
    fake_wanderer = MagicMock()
    api = _make_api(wanderer=fake_wanderer)
    with patch("squid_pet.window.move_to_corner", return_value=True), \
         patch("squid_pet.window.save_corner"):
        api._menu_snap("top-left")
    fake_wanderer.force_edge.assert_called_once_with("top")


def test_menu_snap_does_not_refresh_edge_on_move_failure():
    fake_wanderer = MagicMock()
    api = _make_api(wanderer=fake_wanderer)
    with patch("squid_pet.window.move_to_corner", return_value=False), \
         patch("squid_pet.window.save_corner"):
        api._menu_snap("top-left")
    fake_wanderer.force_edge.assert_not_called()


def test_menu_snap_tolerates_missing_wanderer():
    api = _make_api(wanderer=None)
    with patch("squid_pet.window.move_to_corner", return_value=True), \
         patch("squid_pet.window.save_corner"):
        api._menu_snap("top-left")  # must not raise


def test_menu_snap_tolerates_refresh_edge_exception():
    fake_wanderer = MagicMock()
    fake_wanderer.force_edge.side_effect = RuntimeError("boom")
    api = _make_api(wanderer=fake_wanderer)
    with patch("squid_pet.window.move_to_corner", return_value=True), \
         patch("squid_pet.window.save_corner"):
        api._menu_snap("top-left")  # must not raise


def test_menu_recenter_refreshes_edge_on_success():
    fake_wanderer = MagicMock()
    api = _make_api(wanderer=fake_wanderer)
    with patch("squid_pet.window.load_corner", return_value="bottom-right"), \
         patch("squid_pet.window.move_to_corner", return_value=True):
        api._menu_recenter()
    fake_wanderer.force_edge.assert_called_once_with("bottom")


def test_menu_recenter_does_not_refresh_edge_on_move_failure():
    fake_wanderer = MagicMock()
    api = _make_api(wanderer=fake_wanderer)
    with patch("squid_pet.window.load_corner", return_value="bottom-right"), \
         patch("squid_pet.window.move_to_corner", return_value=False):
        api._menu_recenter()
    fake_wanderer.force_edge.assert_not_called()


def test_next_corner_syncs_edge_via_force_edge():
    """next_corner() (right-click cycling) has the exact same
    corner-snap-vs-wander-margin mismatch as menu snap/recenter/startup --
    same fix, same regression coverage."""
    fake_wanderer = MagicMock()
    api = _make_api(wanderer=fake_wanderer)
    api._corner = "top-right"
    with patch("squid_pet.window.move_to_corner", return_value=True), \
         patch("squid_pet.window.save_corner"):
        new_corner = api.next_corner()
    assert new_corner == "bottom-right"
    fake_wanderer.force_edge.assert_called_once_with("bottom")


def test_edge_for_corner_uses_bottom_top_priority():
    """Matches wanderer._compute_edge_at's own bottom>top>left>right
    corner tiebreak (see its docstring) -- every CORNERS entry leads
    with 'top' or 'bottom' so that's the only distinction that matters."""
    assert _edge_for_corner("top-right") == "top"
    assert _edge_for_corner("top-left") == "top"
    assert _edge_for_corner("bottom-right") == "bottom"
    assert _edge_for_corner("bottom-left") == "bottom"
    assert _edge_for_corner("unknown") == ""
