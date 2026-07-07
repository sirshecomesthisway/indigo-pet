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

from squid_pet.wanderer import WanderController, CHAR_TOP_IN_WIN, EDGE_MARGIN_PX


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


# ── corner-unlock regression (fix 2026-06-16, commit d0a704e) ──────────
# Bug: Squid was trapped on the right edge ping-ponging top-right <->
# bottom-right because _pick_edge_destination only considered the
# priority-tiebreak winner. Window-clamp drift (dock/menubar) also kept
# d_top=68px > EDGE_BAND_PX=60, so "top" was never in band at top-right.
# Fix: random.choice over all edges within CORNER_BAND_PX=120.

def test_corner_unlock_bottom_right_can_escape_to_bottom_edge(wc, monkeypatch):
    """At bottom-right corner, picker MUST eventually choose the bottom
    edge as destination (escape to bottom-left). Pre-fix this only
    happened via the deprecated priority-tiebreak path."""
    import random
    # Bottom-right corner: ox close to max_x, oy close to min_y.
    # Frame: 0..1000 x 0..800. Position (995, 5) — 5px from each edge.
    destinations = set()
    rng = random.Random(0)
    monkeypatch.setattr("squid_pet.wanderer.random.choice",
                        lambda seq: rng.choice(seq))
    for _ in range(40):
        tx, ty = wc._pick_edge_destination(995, 5, 0, 1000, 0, 800)
        destinations.add((tx, ty))
    # We must see at least one destination on the bottom-LEFT corner
    # (x near min, y near min). Pre-fix this was impossible.
    bottom_left_hits = [(x, y) for (x, y) in destinations
                        if x == 0 and y == 0]
    assert bottom_left_hits, \
        f"At bottom-right corner, picker never chose bottom-left: {destinations}"


def test_corner_unlock_top_right_can_escape_to_top_edge(wc, monkeypatch):
    """At top-right corner with 68px clamp drift on y (mimics dock),
    picker MUST eventually choose the top edge. Pre-fix: EDGE_BAND_PX=60
    excluded top entirely, so right won by default forever."""
    import random
    # Top-right with drift: max_x=1000, max_y=800, position (1000, 732)
    # so d_top = 68 (> old EDGE_BAND_PX=60 but < CORNER_BAND_PX=120).
    destinations = set()
    rng = random.Random(0)
    monkeypatch.setattr("squid_pet.wanderer.random.choice",
                        lambda seq: rng.choice(seq))
    for _ in range(40):
        tx, ty = wc._pick_edge_destination(1000, 732, 0, 1000, 0, 800)
        destinations.add((tx, ty))
    # Must see at least one destination on the top-LEFT corner
    # (x near min, y == max_y).
    top_left_hits = [(x, y) for (x, y) in destinations
                     if x == 0 and y == 800]
    assert top_left_hits, \
        f"At top-right with 68px clamp drift, picker never chose top-left: {destinations}"


def test_mid_edge_no_corner_lock_applies(wc):
    """Sanity: when squarely mid-edge (far from both corners), picker
    should walk along that edge only — corner-unlock must not produce
    cross-frame jumps."""
    # Right edge, mid-y: (1000, 400) in 0..1000 x 0..800 frame.
    # Only "right" is within CORNER_BAND_PX=120 (d_left=1000, d_top=400,
    # d_bottom=400 all >> 120).
    for _ in range(20):
        tx, ty = wc._pick_edge_destination(1000, 400, 0, 1000, 0, 800)
        assert tx == 1000, f"mid-right edge: x should stay at max_x=1000, got {tx}"
        assert ty in (0, 800), f"mid-right edge: y should be a corner, got {ty}"


# ── refresh_edge after manual move (fix 2026-06-16) ───────────────────
# Bug: drag and corner-snap bypass the wanderer's wrapped origin setter
# (they call NSWindow.setFrameOrigin_ directly), so _update_edge never
# fires and the sprite stays rotated for the old edge until next walk.
# Fix: public refresh_edge() polls live origin and triggers _update_edge.

def test_refresh_edge_picks_up_new_position(monkeypatch):
    """After a 'drag' (origin changes externally), refresh_edge should
    re-read origin and update the edge tracker without needing a walk."""
    edge_calls = []
    # Frame (0,0,1000,800) with WIN_W=200, EDGE_MARGIN=12, BOTTOM_MARGIN=-40
    # -> valid origin range: x in [12, 788], y in [-40, max_y]
    # where max_y = 800 - CHAR_TOP_IN_WIN - EDGE_MARGIN_PX  (symbolic; survives
    # tuning of CHAR_TOP_IN_WIN — Pink 2026-07-07 head-hug fix bumped it 165->145).
    _MAX_Y = 800 - CHAR_TOP_IN_WIN - EDGE_MARGIN_PX
    current_origin = [788.0, -40.0]  # start: bottom-right corner (both d=0)
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (current_origin[0], current_origin[1]),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    # First refresh: at (788,-40), d_bottom=0 and d_right=0 — priority bottom wins
    e1 = wc.refresh_edge()
    assert e1 == "bottom", f"expected bottom at (788,-40), got {e1!r}"
    assert edge_calls[-1] == "bottom"

    # Simulate user dragging Squid to mid LEFT edge: (12, 250)
    current_origin[0] = 12.0
    current_origin[1] = 250.0
    e2 = wc.refresh_edge()
    assert e2 == "left", f"after drag to (12,250), expected left, got {e2!r}"
    assert edge_calls[-1] == "left"

    # Drag her to mid TOP edge (y == max_y so d_top=0 wins).
    current_origin[0] = 400.0
    current_origin[1] = float(_MAX_Y)
    e3 = wc.refresh_edge()
    assert e3 == "top", f"after drag to (400,{_MAX_Y}), expected top, got {e3!r}"
    assert edge_calls[-1] == "top"


def test_refresh_edge_handles_missing_origin_gracefully():
    """If get_window_origin returns None (e.g. window not ready),
    refresh_edge must not crash; returns last known edge."""
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: None,
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    # Should not raise
    result = wc.refresh_edge()
    assert isinstance(result, str)
