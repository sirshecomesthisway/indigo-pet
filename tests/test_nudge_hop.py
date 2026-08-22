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
    STUCK_ESCAPE_STICKY_SEC,
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
    monkeypatch.setattr(wc, "_do_request_nudge", lambda cx, cy, my_gen: called.append((cx, cy)))
    wc._sprint_mode = True
    wc.request_nudge(100.0, 100.0)
    assert called == [], "request_nudge must be a no-op while sprinting"


def test_request_nudge_spawns_hop_when_idle(wc, monkeypatch):
    import threading
    import time as _time

    called = threading.Event()
    monkeypatch.setattr(
        wc, "_do_request_nudge",
        lambda cx, cy, my_gen: called.set(),
    )
    wc.request_nudge(100.0, 100.0)
    assert called.wait(timeout=2), "request_nudge did not invoke the hop"


# ── Hop/flee preemption (2026-08-21) ────────────────────────────────────
# Regression: rapid continuous nudging produced only tiny net movement
# because request_nudge/request_flee_to_corner spawned a fresh daemon
# thread on every trigger with no synchronization against a still-running
# previous hop/flee animation -- two threads raced writing the window
# origin, each computing its target from its own stale snapshot of "where
# she is". Every hop/flee animation now carries a generation number
# (WanderController._bump_nudge_generation) and bails out the instant a
# newer request supersedes it.

def test_public_nudge_calls_bump_the_generation_counter(wc):
    """request_nudge (and request_flee_to_corner) must claim a fresh
    generation on every call -- that's what lets a later call preempt an
    earlier one still animating."""
    gen0 = wc._nudge_generation
    wc.request_nudge(100.0, 100.0)
    wc.request_nudge(100.0, 100.0)
    wc.request_flee_to_corner(100.0, 100.0)
    assert wc._nudge_generation == gen0 + 3


def test_animate_hop_bails_out_when_superseded_by_newer_generation(monkeypatch):
    """A hop/flee animation must stop the instant a newer request bumps
    the generation counter, instead of continuing to write origin updates
    that fight a newer animation for the window position."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: recorder.append((x, y)),
        get_visible_frame=lambda: (-10_000.0, -10_000.0, 20_000.0, 20_000.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    my_gen = wc._bump_nudge_generation()
    orig_set_origin = wc._set_origin

    def _set_origin_then_supersede(x, y):
        orig_set_origin(x, y)
        if len(recorder) == 1:
            wc._bump_nudge_generation()  # simulate a newer request arriving mid-animation

    wc._set_origin = _set_origin_then_supersede
    wc._animate_hop(0.0, 0.0, 1000.0, 0.0, my_gen)
    assert len(recorder) == 1, (
        f"animation must stop as soon as it's superseded, got {len(recorder)} steps"
    )
    tx, ty = recorder[-1]
    assert tx != 1000.0, "must not have reached the (now stale) full target"


def test_animate_hop_runs_to_completion_when_not_superseded(monkeypatch):
    """Sanity/contrast: with no newer request arriving, the animation
    completes normally and reaches the full target."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: recorder.append((x, y)),
        get_visible_frame=lambda: (-10_000.0, -10_000.0, 20_000.0, 20_000.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    my_gen = wc._bump_nudge_generation()
    wc._animate_hop(0.0, 0.0, 1000.0, 0.0, my_gen)
    assert recorder, "no origin updates recorded"
    tx, ty = recorder[-1]
    assert tx == pytest.approx(1000.0)


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
    """Default stroll_mode ('edges'): the fallback must land on SOME edge
    (not cut diagonally into open middle space). Cursor here approaches
    from the screen interior (up-left of her), and the horizontal offset
    from her center is slightly larger than the vertical one -- so
    horizontal counts as the dominant/blocked push axis (2026-08-21: see
    _corner_escape_target) and she flees along the OTHER axis instead,
    staying pinned to the right edge (tx unchanged) and moving up
    (further from the corner's own min_y)."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    wc, (min_x, max_x, min_y, max_y) = _make_corner_wc(recorder, "edges")
    # Cursor approaching from the screen interior: left of and above her --
    # "away from cursor" would point right+down, straight into the corner.
    wc._do_request_nudge(cursor_x=500.0, cursor_y=400.0)
    assert recorder, "no origin updates recorded"
    tx, ty = recorder[-1]
    assert tx == max_x, f"edges mode must keep her pinned to the right edge, tx={tx}"
    assert ty > min_y, f"expected upward movement away from the corner, ty={ty}"
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


def test_stuck_hop_at_top_right_corner_slides_down_right_edge_not_left(monkeypatch):
    """Regression (2026-08-21, Pink report): pushing the cursor onto her
    repeatedly from the LEFT while she's already pinned in the TOP-RIGHT
    corner used to send the stuck-fallback hop LEFT along the top edge
    (the old fallback re-projected via _compute_edge_at's fixed
    bottom>top>left>right priority, which always resolves to "top" at a
    top-right corner regardless of push direction). The very next push
    from the same side then had real room again since she was no longer
    exactly cornered, hopping her back RIGHT onto the corner -- an
    endless left-right ping-pong that never turned the corner. The stuck
    fallback must instead slide DOWN the right edge, matching
    _flee_to_corner's own axis-aware reasoning (see
    WanderController._corner_escape_target)."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 1000.0, 800.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    origin = (max_x, max_y)  # already at the top-right corner
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
    ox, oy = origin
    # Cursor touches her from the LEFT, level with her window-center --
    # exactly the "keep pushing her left to right" scenario reported.
    cursor_x, cursor_y = ox - 50.0, oy + CHAR_TOP_IN_WIN / 2
    wc._do_request_nudge(cursor_x=cursor_x, cursor_y=cursor_y)
    tx, ty = recorder[-1]
    assert tx == max_x, f"must stay pinned to the right edge, not drift left (tx={tx})"
    assert ty < max_y, f"must move DOWN the right edge, not stay put (ty={ty})"


def test_stuck_hop_streak_is_sticky_against_cursor_jitter(monkeypatch):
    """Regression (2026-08-21, Pink report follow-up): real repeated
    pushes from roughly the same side never land at EXACTLY the same
    cursor y each time -- a few pixels of natural jitter flip dy's sign
    push to push. Without stickiness, each stuck hop re-decided its
    escape axis fresh from that noisy sign and wobbled up/down the right
    edge instead of making progress (605->535->465->395->465->535->605
    ..., verified before this fix). Consecutive stuck hops within
    STUCK_ESCAPE_STICKY_SEC must reuse the first hop's escape direction
    so a few pixels of jitter can't reverse her mid-escape -- ty must
    move strictly toward min_y on every single push, never back up."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    clock = {"t": 0.0}
    monkeypatch.setattr("squid_pet.wanderer.time.time", lambda: clock["t"])
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 1000.0, 800.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    origin = [max_x, max_y]

    def _set_origin(x, y):
        recorder.append((x, y))
        origin[0], origin[1] = x, y

    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: tuple(origin),
        set_window_origin=_set_origin,
        get_visible_frame=lambda: (vx, vy, vw, vh),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc.set_stroll_mode("edges")
    # Same jitter magnitudes a real +/-3px mouse wobble produced in manual
    # testing -- small enough to flip dy's sign push to push, well inside
    # STUCK_ESCAPE_STICKY_SEC of each other.
    jitters = [-2.2, 2.1, 1.6, -1.5, -0.0, -0.3, 0.9, 1.7, -2.4, -2.8]
    prev_ty = max_y
    for j in jitters:
        clock["t"] += 0.2
        ox, oy = origin
        cursor_x, cursor_y = ox - 50.0, oy + CHAR_TOP_IN_WIN / 2 + j
        wc._do_request_nudge(cursor_x=cursor_x, cursor_y=cursor_y)
        tx, ty = recorder[-1]
        assert tx == max_x, f"must stay pinned to the right edge, not drift left (tx={tx})"
        assert ty <= prev_ty, (
            f"must never move back up mid-escape (jitter reversed her): "
            f"prev_ty={prev_ty}, ty={ty}"
        )
        prev_ty = ty
    assert prev_ty < max_y - 300, f"expected substantial cumulative progress, ended at ty={prev_ty}"


def test_stuck_escape_direction_recomputes_after_long_gap(monkeypatch):
    """A lull longer than STUCK_ESCAPE_STICKY_SEC must drop the cached
    escape direction -- an unrelated later stuck hop shouldn't be forced
    to reuse a stale direction from a completely different bout."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    clock = {"t": 0.0}
    monkeypatch.setattr("squid_pet.wanderer.time.time", lambda: clock["t"])
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 1000.0, 800.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    origin = (max_x, max_y)
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
    ox, oy = origin
    cursor_x, cursor_y = ox - 50.0, oy + CHAR_TOP_IN_WIN / 2
    wc._do_request_nudge(cursor_x=cursor_x, cursor_y=cursor_y)
    assert wc._corner_escape_cache is not None

    clock["t"] += STUCK_ESCAPE_STICKY_SEC + 1.0
    wc._do_request_nudge(cursor_x=cursor_x, cursor_y=cursor_y)
    # Recomputed fresh (not an assertion on the exact value -- just that
    # the sticky cache was refreshed, not silently reused past its window).
    assert wc._corner_escape_cache_time == pytest.approx(STUCK_ESCAPE_STICKY_SEC + 1.0)


# ── Corner flee: WanderController.request_flee_to_corner() (2026-08-21) ──
# Independent trigger from the plain hop above -- fired directly by
# passthrough.CornerFleeApproachTracker once the cursor has re-entered her
# clickable bbox CORNER_FLEE_THRESHOLD times in a row (see
# test_nudge_trigger.py for the trigger-side logic). No streak bookkeeping
# lives in WanderController itself anymore -- a single
# _do_request_flee_to_corner call always flees straight to the corner.

def _range_bounds(vx, vy, vw, vh):
    """Same min_x/max_x/min_y/max_y formula production code uses."""
    min_x = vx + EDGE_MARGIN_PX
    max_x = vx + vw - WIN_W - EDGE_MARGIN_PX
    min_y = vy + BOTTOM_MARGIN_PX
    max_y = vy + vh - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
    return min_x, max_x, min_y, max_y


def test_flee_to_corner_lands_on_corner_away_from_cursor(wc, recorder, monkeypatch):
    """A single request_flee_to_corner call must land exactly on the
    corner of her wander range that's away from the cursor, not a short
    hop. Cursor is far to the right (+x) and level (dy~0 from
    win-center) -> expect the left corner on the max_y side
    (dy=win_center_y-cursor_y is positive since cursor_y=0 < CHAR_TOP_IN_WIN/2)."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    vx, vy, vw, vh = -10_000.0, -10_000.0, 20_000.0, 20_000.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    wc._do_request_flee_to_corner(cursor_x=5000.0, cursor_y=0.0)
    tx, ty = recorder[-1]
    assert (tx, ty) == pytest.approx((min_x, max_y), rel=1e-6)


def test_corner_flee_picks_far_side_even_when_nearer_to_cursor_side(wc, recorder, monkeypatch):
    """Regression (2026-08-19, reported live): she was still sitting near
    the RIGHT edge (only a few 70px hops away from where repeated nudges
    from the right had been landing) when the flee trigger fired. The
    corner geometrically NEAREST her current position was therefore still
    a right-side corner -- fleeing there put her right back in the way of
    whoever was nudging her from the right. The flee must pick the corner
    AWAY FROM THE CURSOR instead, even though it's farther from her
    current spot than the wrong corner would've been."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    vx, vy, vw, vh = 0.0, 0.0, 2000.0, 800.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    # She's sitting close to the RIGHT side (only a short hop in from
    # max_x) -- the geometrically nearest corner from here is top/bottom
    # RIGHT, same side as the cursor doing the nudging.
    origin = (max_x - 50.0, 300.0)
    recorder2 = []
    wc2 = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: recorder2.append((x, y)),
        get_visible_frame=lambda: (vx, vy, vw, vh),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc2.set_stroll_mode("anywhere")  # isolate corner-picking from edge pinning
    # Cursor approaches from further right, i.e. from the SAME side as
    # the nearest-corner-to-self would be.
    cursor_x, cursor_y = max_x + 500.0, 300.0
    wc2._do_request_flee_to_corner(cursor_x=cursor_x, cursor_y=cursor_y)
    tx, ty = recorder2[-1]
    assert tx == min_x, (
        f"fled to tx={tx}, but cursor was on the RIGHT (x={cursor_x}) -- "
        f"she must flee to the LEFT side (min_x={min_x}), not back toward it"
    )


def test_corner_flee_lands_on_edge_in_edges_stroll_mode(monkeypatch):
    """In default 'edges' stroll mode, the corner target must land ON the
    edge she's already pinned to -- _project_nudge_to_stroll_mode snaps
    the perpendicular coordinate back to that edge's boundary (see
    test_corner_flee_stays_on_current_edge_not_diagonal_cut below for the
    case where that projection actually changes the raw pick)."""
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
    wc._do_request_flee_to_corner(cursor_x=500.0, cursor_y=300.0)
    tx, ty = recorder[-1]
    assert tx == min_x
    assert ty in (min_y, max_y)


def test_corner_flee_stays_on_current_edge_not_diagonal_cut(monkeypatch):
    """Regression (2026-08-21, Pink report): fleeing to a corner from the
    right sent her diagonally across the screen instead of sliding
    straight along the bottom edge she was already pinned to.

    She's on the BOTTOM edge (oy == min_y) near the right side. The
    cursor is roughly level with her (dy ~= 0), which -- before routing
    the raw away-from-cursor corner pick through
    _project_nudge_to_stroll_mode -- resolved the tie to max_y (the TOP),
    producing a diagonal cut clean across the wander range instead of a
    straight slide to the bottom-left corner."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 2000.0, 800.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    origin = (max_x - 300.0, min_y)  # on the bottom edge, near the right side
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
    ox, oy = origin
    # Exactly level with her window-center -> dy == 0, the tie that used
    # to resolve to "away_y = max_y" regardless of which edge she's on.
    cursor_x, cursor_y = max_x + 500.0, oy + CHAR_TOP_IN_WIN / 2
    wc._do_request_flee_to_corner(cursor_x=cursor_x, cursor_y=cursor_y)
    tx, ty = recorder[-1]
    assert ty == min_y, f"must stay on the bottom edge, not jump to the top (ty={ty})"
    assert tx == min_x, f"must slide left to the bottom-left corner (tx={tx})"


def test_corner_flee_switches_to_other_edge_when_already_cornered(monkeypatch):
    """Regression (2026-08-21, Pink report #2): she's already sitting IN
    the bottom-left corner. Fled to a corner from the right (no
    horizontal room left -- she's already as far left as she can go), she
    must turn and slide UP the left edge to the top-left corner, not
    collapse to a no-op.

    Before this fix, _project_nudge_to_stroll_mode's single edge
    classification at a corner (bottom>top>left>right priority always
    picks "bottom" here) forced ty back to her current min_y even though
    away_y had already correctly picked max_y -- silently cancelling the
    only axis that actually had room."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 2000.0, 800.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    origin = (min_x, min_y)  # already at the bottom-left corner
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
    ox, oy = origin
    # Fled to a corner from the right, level with her -- "away" wants
    # x=min_x (already there, no room) and y=max_y (the actually-available
    # move).
    cursor_x, cursor_y = ox + 500.0, oy + CHAR_TOP_IN_WIN / 2
    wc._do_request_flee_to_corner(cursor_x=cursor_x, cursor_y=cursor_y)
    tx, ty = recorder[-1]
    assert tx == min_x, f"must stay on the left edge (tx={tx})"
    assert ty == max_y, f"must turn and slide up to the top-left corner, not stay put (ty={ty})"


def test_corner_flee_dead_end_turns_90_degrees(monkeypatch):
    """New case (2026-08-21, Pink request): she's already sitting exactly
    AT the corner away from the cursor -- pinned on BOTH axes at once, so
    the straight-line 'away from cursor' direction has genuinely no room
    left. Previously this silently no-op'd (tx, ty = ox, oy). Now she
    must turn 90 degrees and slide to whichever ADJACENT corner (sharing
    one edge with the one she's in) ends up farther from the cursor,
    instead of freezing in place.

    She's in the bottom-left corner; cursor sits up near the top-right,
    which keeps away_x=min_x/away_y=min_y pinned (still a dead end) while
    making the wide (2000px) horizontal distance to top-left clearly
    bigger than the short (800px) vertical distance to bottom-right, so
    the farther-corner pick is unambiguous."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    recorder = []
    vx, vy, vw, vh = 0.0, 0.0, 2000.0, 800.0
    min_x, max_x, min_y, max_y = _range_bounds(vx, vy, vw, vh)
    origin = (min_x, min_y)  # already at the bottom-left corner
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
    cursor_x, cursor_y = max_x - 50.0, max_y - 50.0  # near the top-right corner
    wc._do_request_flee_to_corner(cursor_x=cursor_x, cursor_y=cursor_y)
    tx, ty = recorder[-1]
    assert (tx, ty) != origin, "must not freeze in place at a dead end"
    assert (tx, ty) == (min_x, max_y), (
        f"expected a 90-degree turn to the top-left corner (farther from "
        f"the cursor than bottom-right), got ({tx},{ty})"
    )
