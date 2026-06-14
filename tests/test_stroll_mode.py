"""Tests for stroll-path mode (restored 2026-06-13 after unify-idle-rhythm regression).

Validates:
- Default mode is "edges" (matches pre-regression behavior)
- set_stroll_mode accepts only valid values
- get_stroll_mode reflects current state
- Invalid mode logs warning + leaves mode unchanged
- Picker honors stroll_mode (edges -> always edge picker;
  anywhere -> band-based picker)
"""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest

from indigo_pet.wanderer import WanderController


@pytest.fixture
def wc():
    """A WanderController wired with mock callbacks. We only use it to
    exercise the stroll-mode API and the picker dispatch; we never run
    actual walks."""
    return WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (100.0, 100.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )


def test_default_stroll_mode_is_edges(wc):
    """Restored default matches pre-regression behavior."""
    assert wc.get_stroll_mode() == "edges"


def test_set_stroll_mode_anywhere(wc):
    wc.set_stroll_mode("anywhere")
    assert wc.get_stroll_mode() == "anywhere"


def test_set_stroll_mode_edges(wc):
    wc.set_stroll_mode("anywhere")  # flip first
    wc.set_stroll_mode("edges")
    assert wc.get_stroll_mode() == "edges"


def test_set_stroll_mode_invalid_is_ignored(wc, capsys):
    original = wc.get_stroll_mode()
    wc.set_stroll_mode("sideways")  # bogus
    assert wc.get_stroll_mode() == original
    captured = capsys.readouterr()
    assert "invalid" in captured.out.lower()


def test_set_stroll_mode_same_value_is_noop(wc, capsys):
    """Flipping to current mode shouldn't log a transition message."""
    wc.set_stroll_mode("edges")  # already edges by default
    out = capsys.readouterr().out
    # No "stroll mode: ... -> ..." transition log when value unchanged
    assert "stroll mode:" not in out


def test_picker_honors_edges_mode(wc, monkeypatch):
    """When _stroll_mode is "edges", picker MUST route through
    _pick_edge_destination regardless of band ("short"/"medium"/"edge")."""
    wc.set_stroll_mode("edges")
    edge_called_with = []

    def mock_edge_picker(ox, oy, min_x, max_x, min_y, max_y):
        edge_called_with.append(("edge", ox, oy))
        return (50.0, 100.0)

    monkeypatch.setattr(wc, "_pick_edge_destination", mock_edge_picker)

    for band in ("short", "medium", "edge"):
        edge_called_with.clear()
        wc._pick_target_for_band(band, 200, 200, 0, 1000, 0, 800)
        assert len(edge_called_with) == 1, \
            f"edges mode + band={band!r} should call edge picker, got {edge_called_with}"


def test_picker_honors_anywhere_mode_for_polar_bands(wc, monkeypatch):
    """When _stroll_mode is "anywhere", "short"/"medium" use polar pick,
    only explicit "edge" band uses edge picker."""
    wc.set_stroll_mode("anywhere")
    edge_calls = []
    monkeypatch.setattr(wc, "_pick_edge_destination",
                        lambda *a, **kw: edge_calls.append(1) or (0.0, 0.0))

    # short and medium should NOT call edge picker
    wc._pick_target_for_band("short", 200, 200, 0, 1000, 0, 800)
    wc._pick_target_for_band("medium", 200, 200, 0, 1000, 0, 800)
    assert edge_calls == [], \
        f"anywhere + short/medium should NOT call edge picker, got {edge_calls}"

    # but explicit "edge" band SHOULD
    wc._pick_target_for_band("edge", 200, 200, 0, 1000, 0, 800)
    assert edge_calls == [1], \
        f"anywhere + 'edge' band SHOULD call edge picker, got {edge_calls}"


def test_picker_polar_target_stays_in_frame(wc):
    """Anywhere mode: returned (x, y) MUST be clamped to visible frame."""
    wc.set_stroll_mode("anywhere")
    for _ in range(20):  # randomized -- run a few times
        tx, ty = wc._pick_target_for_band("short", 500, 400, 0, 1000, 0, 800)
        assert 0 <= tx <= 1000, f"x={tx} out of frame"
        assert 0 <= ty <= 800, f"y={ty} out of frame"


def test_valid_stroll_modes_class_constant():
    """Public API contract: VALID_STROLL_MODES is the source of truth."""
    assert WanderController.VALID_STROLL_MODES == ("anywhere", "edges")
