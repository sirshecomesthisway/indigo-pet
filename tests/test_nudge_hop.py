"""Tests for WanderController.request_nudge() -- the single quick hop away
from the cursor fired by passthrough.NudgeApproachTracker on repeated rapid
approaches (see test_nudge_trigger.py for the trigger-side logic).

Follows the same fixture/monkeypatch pattern as test_sprint_reentrancy.py
and test_stroll_mode.py: a real WanderController wired with fake callables,
time.sleep no-op'd so the (fast, but still stepped) hop animation resolves
instantly instead of over real wall-clock time.
"""
from __future__ import annotations

import math

import pytest

from squid_pet.wanderer import (
    WanderController, NUDGE_HOP_DISTANCE_PX, WIN_W, CHAR_TOP_IN_WIN,
    EDGE_MARGIN_PX, BOTTOM_MARGIN_PX, TOP_MARGIN_PX, NUDGE_STUCK_THRESHOLD_PX,
    NUDGE_TO_CORNER_THRESHOLD, NUDGE_COUNT_RESET_SEC,
)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


@pytest.fixture
def recorder():
    return []


@pytest.fixture
def wc(recorder):
    return WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: recorder.append((x, y)),
        # Huge frame so clamping never kicks in for these tests.
        get_visible_frame=lambda: (-10_000.0, -10_000.0, 20_000.0, 20_000.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )


def test_hop_moves_away_from_cursor_to_the_right(wc, recorder, monkeypatch):
    """Cursor to the right of her center -> she hops left (away)."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc._do_request_nudge(cursor_x=5000.0, cursor_y=CHAR_TOP_IN_WIN / 2)
    assert recorder, "no origin updates recorded"
    tx, ty = recorder[-1]
    assert tx < 0.0, f"expected hop to the LEFT (away from cursor), got tx={tx}"


def test_hop_moves_away_from_cursor_above(wc, recorder, monkeypatch):
    """Cursor above her (larger Cocoa y) -> she hops down (away)."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc._do_request_nudge(cursor_x=WIN_W / 2, cursor_y=5000.0)
    tx, ty = recorder[-1]
    assert ty < 0.0, f"expected hop DOWN (away from cursor above), got ty={ty}"


def test_hop_distance_matches_constant_when_unclamped(wc, recorder, monkeypatch):
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
    final = recorder[-1]
    assert _dist((0.0, 0.0), final) == pytest.approx(NUDGE_HOP_DISTANCE_PX, rel=1e-6)


def test_hop_target_clamped_to_visible_frame(monkeypatch):
    """A frame that's narrow (but tall enough to leave vertical room given
    TOP_MARGIN_PX's guaranteed-on-screen floor) must clamp the hop
    horizontally -- she can't be nudged off-screen."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: recorder.append((x, y)),
        get_visible_frame=lambda: (0.0, 0.0, 300.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)  # would hop far right
    tx, ty = recorder[-1]
    max_x = 0.0 + 300.0 - WIN_W - EDGE_MARGIN_PX
    assert tx <= max_x + 1e-6, f"hop not clamped: tx={tx} > max_x={max_x}"


def test_cursor_exactly_on_center_does_not_crash(wc, recorder, monkeypatch):
    """Zero-distance vector must fall back to a random direction, not
    raise a division-by-zero."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc._do_request_nudge(cursor_x=WIN_W / 2, cursor_y=CHAR_TOP_IN_WIN / 2)
    assert recorder  # completed without raising


def test_no_hop_while_dragging(recorder, monkeypatch):
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: True,   # dragging
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: recorder.append((x, y)),
        get_visible_frame=lambda: (-10_000.0, -10_000.0, 20_000.0, 20_000.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
    assert recorder == [], "must not move the window while a drag is active"


def test_request_nudge_is_noop_during_sprint(wc, monkeypatch):
    """Public entry point must not even spawn the hop thread mid-sprint."""
    called = []
    monkeypatch.setattr(wc, "_do_request_nudge", lambda cx, cy: called.append((cx, cy)))
    wc._sprint_mode = True
    wc.request_nudge(100.0, 100.0)
    assert called == [], "request_nudge must be a no-op while sprinting"


def test_request_nudge_spawns_hop_when_idle(wc, monkeypatch):
    import threading
    import time as _time

    called = threading.Event()
    monkeypatch.setattr(
        wc, "_do_request_nudge",
        lambda cx, cy: called.set(),
    )
    wc.request_nudge(100.0, 100.0)
    assert called.wait(timeout=2), "request_nudge did not invoke the hop"


# ── Corner fallback (2026-08-19) ────────────────────────────────────────
# At a corner she's pinned on BOTH axes at once. If the cursor approaches
# from the screen interior (the common case), "away from cursor" points
# further into the corner on both axes -- the clamp cancels that to zero
# movement, so the plain away-from-cursor hop silently does nothing. The
# fallback detects near-zero movement and redirects away from the corner.
#
# WanderController defaults to stroll_mode="edges" (matches the menu's
# default and what most users have selected), so by default the fallback
# must stay ON the edge she's pinned to (bottom, in these tests) rather
# than cutting across open middle space toward the wander range's center.
# See the "anywhere" mode tests below for the un-pinned, freeform version.

def _corner_frame():
    """A frame + bottom-right-corner origin using the SAME margin
    constants production code uses, so the numbers stay meaningful if
    those constants are retuned later."""
    vx, vy, vw, vh = 0.0, 0.0, 1000.0, 800.0
    min_x = vx + EDGE_MARGIN_PX
    max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
    min_y = vy + BOTTOM_MARGIN_PX
    max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
    return (vx, vy, vw, vh), (min_x, max_x, min_y, max_y)


def _make_corner_wc(recorder, stroll_mode="edges"):
    (vx, vy, vw, vh), (min_x, max_x, min_y, max_y) = _corner_frame()
    origin = (max_x, min_y)  # pinned at the bottom-right corner
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: recorder.append((x, y)),
        get_visible_frame=lambda: (vx, vy, vw, vh),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc.set_stroll_mode(stroll_mode)
    return wc, (min_x, max_x, min_y, max_y)


def test_corner_fallback_edges_mode_slides_along_pinned_edge(monkeypatch):
    """Default stroll_mode ('edges'): the fallback must stay exactly on
    the bottom edge (ty pinned to min_y, the corner's own y) and only move
    along x, away from the corner -- NOT walk her up into open middle
    space the way the raw range-center direction alone would."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    wc, (min_x, max_x, min_y, max_y) = _make_corner_wc(recorder, "edges")
    # Cursor approaching from the screen interior: left of and above her --
    # "away from cursor" would point right+down, straight into the corner.
    wc._do_request_nudge(cursor_x=500.0, cursor_y=400.0)
    assert recorder, "no origin updates recorded"
    tx, ty = recorder[-1]
    assert ty == min_y, f"edges mode must keep her pinned to the bottom edge, ty={ty}"
    assert tx < max_x, f"expected leftward movement away from the corner, tx={tx}"
    moved = math.hypot(tx - max_x, ty - min_y)
    assert moved > NUDGE_STUCK_THRESHOLD_PX, (
        f"corner fallback did not produce real movement: moved={moved}"
    )


def test_corner_fallback_anywhere_mode_targets_range_center_precisely(monkeypatch):
    """stroll_mode='anywhere': no edge to stay pinned to, so the fallback
    is the raw away-from-corner-toward-range-center direction -- verify
    the exact landing point."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    wc, (min_x, max_x, min_y, max_y) = _make_corner_wc(recorder, "anywhere")
    wc._do_request_nudge(cursor_x=500.0, cursor_y=400.0)
    tx, ty = recorder[-1]

    range_cx = (min_x + max_x) / 2
    range_cy = (min_y + max_y) / 2
    fdx, fdy = range_cx - max_x, range_cy - min_y
    fdist = math.hypot(fdx, fdy)
    expected_tx = max_x + (fdx / fdist) * NUDGE_HOP_DISTANCE_PX
    expected_ty = min_y + (fdy / fdist) * NUDGE_HOP_DISTANCE_PX

    assert tx == pytest.approx(expected_tx, abs=1e-6)
    assert ty == pytest.approx(expected_ty, abs=1e-6)


def test_edges_mode_pins_normal_hop_to_current_edge_not_just_corners(monkeypatch):
    """Not just the corner-fallback case: an ORDINARY hop while she's on a
    single edge (not cornered) must also stay pinned to that edge in
    'edges' mode."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 1000.0, 800.0
    min_x = vx + EDGE_MARGIN_PX
    max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
    min_y = vy + BOTTOM_MARGIN_PX
    max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
    origin = (min_x, 300.0)  # mid LEFT edge, well clear of top/bottom corners
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: recorder.append((x, y)),
        get_visible_frame=lambda: (vx, vy, vw, vh),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc.set_stroll_mode("edges")
    # Cursor far to her LEFT -> "away from cursor" naturally points RIGHT,
    # off the edge into open interior space (she's not already blocked by
    # the ordinary boundary clamp in that direction, unlike a cursor
    # positioned so "away" points further past min_x, which the plain
    # clamp alone would already stop regardless of stroll_mode). Edges
    # mode must override that and keep her pinned to min_x anyway.
    wc._do_request_nudge(cursor_x=min_x - 1000.0, cursor_y=300.0)
    tx, ty = recorder[-1]
    assert tx == min_x, f"edges mode must keep her pinned to the left edge, tx={tx}"


def test_anywhere_mode_does_not_pin_normal_hop_to_edge(monkeypatch):
    """Sanity/contrast: the SAME setup under 'anywhere' mode must NOT be
    pinned -- confirms the pinning in the test above is really gated on
    stroll_mode, not some other side effect (e.g. just the ordinary
    boundary clamp)."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 1000.0, 800.0
    min_x = vx + EDGE_MARGIN_PX
    origin = (min_x, 300.0)
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: recorder.append((x, y)),
        get_visible_frame=lambda: (vx, vy, vw, vh),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc.set_stroll_mode("anywhere")
    wc._do_request_nudge(cursor_x=min_x - 1000.0, cursor_y=300.0)
    tx, ty = recorder[-1]
    assert tx > min_x, f"anywhere mode must NOT pin to the edge, tx={tx}"


def test_non_cornered_hop_does_not_trigger_fallback(wc, recorder, monkeypatch):
    """Sanity: away from a screen edge (huge frame, no clamp pressure),
    the fallback must never engage -- behavior stays exactly the plain
    away-from-cursor hop."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
    final = recorder[-1]
    assert _dist((0.0, 0.0), final) == pytest.approx(NUDGE_HOP_DISTANCE_PX, rel=1e-6)


# ── Nudge-streak escalation: flee to nearest corner (2026-08-19) ───────────
# After more than NUDGE_TO_CORNER_THRESHOLD nudges in a row, a plain short
# hop stops being enough -- she flees all the way to whichever corner of
# her wander range is nearest, instead of hopping the same fixed short
# distance each time.

def _range_bounds(vx, vy, vw, vh):
    """Same min_x/max_x/min_y/max_y formula production code uses."""
    min_x = vx + EDGE_MARGIN_PX
    max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
    min_y = vy + BOTTOM_MARGIN_PX
    max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
    return min_x, max_x, min_y, max_y


def test_nudges_under_threshold_stay_plain_hops(wc, recorder, monkeypatch):
    """The first NUDGE_TO_CORNER_THRESHOLD nudges in a row must each still
    be an ordinary short hop, not a corner flee. Each hop always returns
    her to the origin (0,0) before the next call since set_window_origin
    is only a recorder here, not real state -- so every call's *final*
    recorded point should be ~NUDGE_HOP_DISTANCE_PX from (0,0)."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    for _ in range(NUDGE_TO_CORNER_THRESHOLD):
        recorder.clear()
        wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
        tx, ty = recorder[-1]
        assert _dist((0.0, 0.0), (tx, ty)) == pytest.approx(NUDGE_HOP_DISTANCE_PX, rel=1e-6)


def test_nudge_past_threshold_flees_to_nearest_corner(wc, recorder, monkeypatch):
    """The (NUDGE_TO_CORNER_THRESHOLD + 1)th nudge in a row must land
    exactly on the nearest corner of her wander range, not a short hop."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    vx, vy, vw, vh = -10_000.0, -10_000.0, 20_000.0, 20_000.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    for _ in range(NUDGE_TO_CORNER_THRESHOLD):
        wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
    recorder.clear()
    wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
    tx, ty = recorder[-1]
    corners = [(min_x, min_y), (min_x, max_y), (max_x, min_y), (max_x, max_y)]
    expected = min(corners, key=lambda c: (c[0] - 0.0) ** 2 + (c[1] - 0.0) ** 2)
    assert (tx, ty) == pytest.approx(expected, rel=1e-6)


def test_streak_resets_after_corner_flee(wc, recorder, monkeypatch):
    """Right after a corner flee, the streak must restart -- the next
    nudge is an ordinary short hop again, not another flee."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    for _ in range(NUDGE_TO_CORNER_THRESHOLD + 1):
        wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)  # last one flees
    recorder.clear()
    wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
    tx, ty = recorder[-1]
    assert _dist((0.0, 0.0), (tx, ty)) == pytest.approx(NUDGE_HOP_DISTANCE_PX, rel=1e-6)


def test_streak_resets_after_long_gap(wc, recorder, monkeypatch):
    """A lull longer than NUDGE_COUNT_RESET_SEC between nudges must reset
    the streak, so a nudge arriving after a long gap counts as the 1st of
    a fresh bout, not a continuation toward the corner-flee threshold."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    clock = {"t": 0.0}
    monkeypatch.setattr("squid_pet.wanderer.time.time", lambda: clock["t"])

    for _ in range(NUDGE_TO_CORNER_THRESHOLD):
        wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
        clock["t"] += 0.01  # well within the reset window

    clock["t"] += NUDGE_COUNT_RESET_SEC + 1.0  # long lull
    recorder.clear()
    wc._do_request_nudge(cursor_x=5000.0, cursor_y=0.0)
    tx, ty = recorder[-1]
    assert _dist((0.0, 0.0), (tx, ty)) == pytest.approx(NUDGE_HOP_DISTANCE_PX, rel=1e-6), (
        "streak should have reset after the long gap, so this nudge stays a plain hop"
    )


def test_corner_flee_lands_on_edge_in_edges_stroll_mode(monkeypatch):
    """In default 'edges' stroll mode, the corner target must itself be a
    valid on-edge point -- fleeing to a wander-range corner should never
    need _project_nudge_to_stroll_mode to stay pinned, since a corner IS
    where two edges meet."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 1000.0, 800.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    origin = (min_x, 300.0)  # mid left edge
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: recorder.append((x, y)),
        get_visible_frame=lambda: (vx, vy, vw, vh),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc.set_stroll_mode("edges")
    for _ in range(NUDGE_TO_CORNER_THRESHOLD + 1):
        wc._do_request_nudge(cursor_x=500.0, cursor_y=300.0)
    tx, ty = recorder[-1]
    assert tx == min_x
    assert ty in (min_y, max_y)
