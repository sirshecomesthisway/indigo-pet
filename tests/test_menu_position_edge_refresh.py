"""Regression tests: _menu_snap() and _menu_recenter() must refresh the
wanderer's edge tracker after moving the window, same as next_corner()
and drag's _on_end already do.

Found in a 2026-08-17 code review: without the refresh, clicking
Position -> <corner> or Recenter from the right-click menu moves the
window but leaves the sprite rotation (and passthrough's edge-aware
click hit-test offset, which keys off the same tracked edge) stale
until the next wander tick happens to run.

PetApi.__init__ pulls in real pywebview/AppKit state, so these tests
build a minimal double via __new__ + direct attribute assignment
(same pattern as test_passthrough_state_mapping.py's controller
double) rather than constructing a real PetApi.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from squid_pet.window import PetApi


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
    fake_wanderer.refresh_edge.assert_called_once()


def test_menu_snap_does_not_refresh_edge_on_move_failure():
    fake_wanderer = MagicMock()
    api = _make_api(wanderer=fake_wanderer)
    with patch("squid_pet.window.move_to_corner", return_value=False), \
         patch("squid_pet.window.save_corner"):
        api._menu_snap("top-left")
    fake_wanderer.refresh_edge.assert_not_called()


def test_menu_snap_tolerates_missing_wanderer():
    api = _make_api(wanderer=None)
    with patch("squid_pet.window.move_to_corner", return_value=True), \
         patch("squid_pet.window.save_corner"):
        api._menu_snap("top-left")  # must not raise


def test_menu_snap_tolerates_refresh_edge_exception():
    fake_wanderer = MagicMock()
    fake_wanderer.refresh_edge.side_effect = RuntimeError("boom")
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
    fake_wanderer.refresh_edge.assert_called_once()


def test_menu_recenter_does_not_refresh_edge_on_move_failure():
    fake_wanderer = MagicMock()
    api = _make_api(wanderer=fake_wanderer)
    with patch("squid_pet.window.load_corner", return_value="bottom-right"), \
         patch("squid_pet.window.move_to_corner", return_value=False):
        api._menu_recenter()
    fake_wanderer.refresh_edge.assert_not_called()
