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
from unittest.mock import MagicMock, patch
import pytest

from squid_pet import window
from squid_pet.wanderer import (
    WanderController,
    CHAR_TOP_IN_WIN,
    EDGE_MARGIN_PX,
    BOTTOM_MARGIN_PX,
    TOP_MARGIN_PX,
    WIN_W,
)


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
        tx, ty, edge_hint = wc._pick_target_for_band("short", 500, 400, 0, 1000, 0, 800)
        assert 0 <= tx <= 1000, f"x={tx} out of frame"
        assert 0 <= ty <= 800, f"y={ty} out of frame"
        assert edge_hint is None, "polar pick should carry no edge hint"


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
        tx, ty, _edge = wc._pick_edge_destination(995, 5, 0, 1000, 0, 800)
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
        tx, ty, _edge = wc._pick_edge_destination(1000, 732, 0, 1000, 0, 800)
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
        tx, ty, edge = wc._pick_edge_destination(1000, 400, 0, 1000, 0, 800)
        assert tx == 1000, f"mid-right edge: x should stay at max_x=1000, got {tx}"
        assert ty in (0, 800), f"mid-right edge: y should be a corner, got {ty}"
        assert edge == "right", f"mid-right edge: edge hint should be 'right', got {edge!r}"


# ── refresh_edge after manual move (fix 2026-06-16) ───────────────────
# Bug: drag and corner-snap bypass the wanderer's wrapped origin setter
# (they call NSWindow.setFrameOrigin_ directly), so _update_edge never
# fires and the sprite stays rotated for the old edge until next walk.
# Fix: public refresh_edge() polls live origin and triggers _update_edge.

def test_refresh_edge_picks_up_new_position(monkeypatch):
    """After a 'drag' (origin changes externally), refresh_edge should
    re-read origin and update the edge tracker without needing a walk."""
    edge_calls = []
    # Frame (0,0,1000,800). Derive the LEFT-edge x from the live EDGE_MARGIN_PX
    # (not hardcoded -- a hardcoded 12.0 here silently broke when
    # EDGE_MARGIN_PX went negative on 2026-08-18 to reach window.py's
    # CHAR_LEFT_IN_WIN hard clamp) so this test survives future tuning.
    # -> valid origin range: x in [EDGE_MARGIN_PX, 788], y in [-40, max_y]
    # where max_y = 800 - CHAR_TOP_IN_WIN - EDGE_MARGIN_PX  (symbolic; survives
    # tuning of CHAR_TOP_IN_WIN — Pink 2026-07-07 head-hug fix bumped it 165->145).
    _LEFT_X = float(EDGE_MARGIN_PX)
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

    # Simulate user dragging Squid to mid LEFT edge.
    current_origin[0] = _LEFT_X
    current_origin[1] = 250.0
    e2 = wc.refresh_edge()
    assert e2 == "left", f"after drag to ({_LEFT_X},250), expected left, got {e2!r}"
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


# ── sticky edge classification near corners (fix 2026-08-27h) ──────────
# Bug: a user watching Squid walk near a corner reported "flips and turns
# multiple times, like she's not able to determine which direction it
# should be going". Root cause: _update_edge fires on EVERY origin
# update during a walk/hop/flee (~30Hz), and the raw nearest-edge
# comparison naturally see-saws between two edges whose bands legitimately
# overlap near a corner -- e.g. d_bottom=23 vs d_right=6 one tick, then
# d_bottom=10 vs d_right=21 the next, as she moves smoothly through the
# corner region. Each flip fired a new sprite rotation. Fix: stay on the
# CURRENTLY tracked edge as long as it's still within EDGE_BAND_PX, even
# if a different edge is momentarily nearer -- only reclassify once she's
# genuinely left the tracked edge's band.
def test_edge_stays_sticky_while_oscillating_near_a_corner():
    """Frame (0,0,1000,800): bottom-right corner sits near (842,-45)
    (max_x = vw - window.CHAR_RIGHT_IN_WIN = 1000-158, fix 2026-08-29 --
    see WanderController._x_bounds). Walk through 3 positions where raw
    nearest-edge would flip bottom->right->bottom, but she should stay
    classified 'bottom' throughout since bottom's own distance never
    exceeds EDGE_BAND_PX."""
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    # First tick: unambiguously bottom (d_bottom=25 < d_right=31).
    wc._update_edge(811, -20)
    assert wc._last_edge == "bottom"

    # Second tick: raw nearest would flip to right (d_right=6 < d_bottom=23),
    # but bottom is still in band (23 <= EDGE_BAND_PX=60) -> sticks.
    wc._update_edge(836, -22)
    assert wc._last_edge == "bottom", (
        "should stay on 'bottom' -- it's still within EDGE_BAND_PX even "
        "though 'right' is momentarily nearer"
    )

    # Third tick: back toward bottom-nearer again -- still just 'bottom',
    # never having flipped away.
    wc._update_edge(816, -35)
    assert wc._last_edge == "bottom"

    # Only ONE edge notification should have fired across all 3 ticks
    # (the initial bottom classification) -- no flip-flop notifications.
    assert edge_calls == ["bottom"]


def test_edge_reclassifies_once_truly_left_the_band():
    """Contrast case: once she's genuinely left the tracked edge's band,
    reclassification still happens normally -- stickiness isn't a
    permanent lock, just anti-flicker."""
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    wc._update_edge(811, -20)
    assert wc._last_edge == "bottom"

    # Walk well clear of the bottom band (d_bottom=245 >> EDGE_BAND_PX)
    # while landing on the right edge (d_right=0).
    wc._update_edge(842, 200)
    assert wc._last_edge == "right"
    assert edge_calls == ["bottom", "right"]


# ── walk-start flip: preamble vs. first-tick position (fix 2026-08-27j) ─
# Bug: after the corner-oscillation fix above, a user still saw her flip
# right when an idle walk STARTED. Root cause: _rotate_first_preamble
# pre-rotates to the walk's destination edge BEFORE she's moved -- but
# _walk_to's very first step still lands at essentially the OLD position
# (t≈0 in the easing curve). Even the sticky check can legitimately fail
# there: the old position isn't necessarily within EDGE_BAND_PX of the
# NEW target edge (e.g. walking from an off-edge position to a distant
# edge point), so the very next _update_edge call reclassified her
# straight back to the old edge one tick after the preamble rotated her
# forward -- a visible flip right at the start, then a second flip
# forward again once she actually got close. Fix: _edge_locked_for_walk
# suppresses _update_edge's independent reclassification for the
# duration of a walk that just pre-rotated, trusting the preamble's
# decision instead of re-deriving it from raw position tick by tick.

def test_walk_preamble_locks_edge_against_first_tick_reversion():
    edge_calls = []
    frame = (0.0, 0.0, 1000.0, 800.0)
    # Start dead center -- off any edge entirely (_last_edge starts "").
    origin = (500.0, 400.0)
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    # Destination on the LEFT edge, far from the starting position.
    min_x = float(EDGE_MARGIN_PX)
    wc._rotate_first_preamble(min_x, 400.0)
    assert edge_calls == ["left"], "preamble should pre-rotate to the target edge"
    assert wc._edge_locked_for_walk is True

    # Simulate the walk's first tick -- still essentially at the OLD
    # (off-edge, far-from-"left") position, since _walk_to's easing
    # starts at t=0.
    wc._update_edge(*origin)
    assert edge_calls == ["left"], (
        "must NOT flip back just because the first walk tick is still "
        "near the old, off-edge position -- that's the exact flip seen live"
    )
    assert wc._last_edge == "left"


def test_edge_lock_releases_and_resyncs_when_walk_ends():
    """Once the walk finishes, tracking must resume from the REAL final
    position -- covers an aborted walk that never actually reached the
    pre-rotated target edge's band."""
    edge_calls = []
    frame = (0.0, 0.0, 1000.0, 800.0)
    origin = [500.0, 400.0]
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (origin[0], origin[1]),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    min_x = float(EDGE_MARGIN_PX)
    wc._rotate_first_preamble(min_x, 400.0)
    assert wc._edge_locked_for_walk is True

    # Walk "aborts" without ever reaching the left edge -- she's still
    # dead center. Directly exercise _walk_to's end-of-walk cleanup by
    # calling the same steps it takes after its loop.
    wc._set_sub_state("")
    wc._edge_locked_for_walk = False
    final = wc._get_origin()
    wc._update_edge(final[0], final[1])

    assert wc._edge_locked_for_walk is False
    assert edge_calls[-1] == "", (
        "post-walk resync must reflect where she actually ended up "
        "(still off-edge), not the pre-rotated target that was never reached"
    )


# ── rotate-first corner-tie: walking a wall to its own corner (fix
# 2026-08-27i) ───────────────────────────────────────────────────────
# Bug (Pink report): strolling vertically along the LEFT edge showed her
# feet pointing UP while walking up and DOWN while walking down, instead
# of staying rotated into the wall the whole way. Root cause:
# _pick_edge_destination's "walk along the current edge to its own
# corner" targets the corner point EXACTLY -- e.g. walking down the left
# edge targets (min_x, min_y), where left's distance (0) ties exactly
# with bottom's (0). _rotate_first_preamble classified that destination
# with the plain bottom>top>left>right priority, which resolves the tie
# to "bottom" (deg=0, feet down) or, for the top-left corner, "top"
# (deg=180, feet up) -- and the walk-long edge lock then plays that wrong
# rotation for the ENTIRE vertical traverse, not just at the corner.

def test_walk_down_left_edge_to_its_own_corner_stays_left():
    frame = (0.0, 0.0, 1000.0, 800.0)
    min_x = 0.0 + EDGE_MARGIN_PX
    min_y = 0.0 + BOTTOM_MARGIN_PX
    # Already on the left edge, partway up it.
    origin = (min_x, 400.0)
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    wc._last_edge = "left"  # already tracked as walking the left wall

    # Destination is the BOTTOM-LEFT corner -- ties left(0) with bottom(0).
    wc._rotate_first_preamble(min_x, min_y)

    assert edge_calls == [] or edge_calls == ["left"], (
        f"corner tie must resolve to the edge she's already walking "
        f"(left), not flip to bottom just because the destination is "
        f"also that corner: got {edge_calls}"
    )
    assert wc._last_edge == "left"


def test_walk_up_left_edge_to_its_own_corner_stays_left():
    frame = (0.0, 0.0, 1000.0, 800.0)
    min_x = 0.0 + EDGE_MARGIN_PX
    max_y = 0.0 + 800.0 - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
    origin = (min_x, 300.0)
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    wc._last_edge = "left"

    # Destination is the TOP-LEFT corner -- ties left(0) with top(0).
    wc._rotate_first_preamble(min_x, max_y)

    assert edge_calls == [] or edge_calls == ["left"], (
        f"corner tie must resolve to the edge she's already walking "
        f"(left), not flip to top just because the destination is "
        f"also that corner: got {edge_calls}"
    )
    assert wc._last_edge == "left"


# ── settle-into-top/bottom on arrival at a corner (fix 2026-08-30) ─────
# Pink report + screenshot: heart/speech-bubble only have a dedicated
# CSS layout for the "top" pose (index.html), not "left"/"right" -- so
# resting at a top corner still classified "left"/"right" (correct
# WHILE WALKING there, hugging the wall) left those decorations visibly
# misaligned with her actual rotated body once she stopped. Fixed by
# having _walk_to's post-walk resync use the plain fixed-priority
# classifier (bottom>top>left>right) instead of the sticky one, so a
# completed walk that ends exactly on a corner (always true --
# _pick_edge_destination's targets are always corner points) settles
# into "top"/"bottom" rather than staying pinned to whichever wall she
# was hugging in transit.

def test_walk_settles_into_top_after_reaching_top_left_corner(monkeypatch):
    """Full _walk_to() path: walking UP the left edge to the top-left
    corner must stay 'left' (hugging the wall) for the walk's rotate-
    first decision, then settle into 'top' once the walk completes --
    not stay 'left' at rest, which is what left heart/bubble misaligned
    with her rotated body."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    frame = (0.0, 0.0, 1000.0, 800.0)
    min_x = 0.0 + EDGE_MARGIN_PX
    max_y = 0.0 + 800.0 - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
    origin_box = {"v": (min_x, 300.0)}
    edge_calls = []

    def _set_origin(x, y):
        origin_box["v"] = (x, y)

    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin_box["v"],
        set_window_origin=_set_origin,
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    wc._last_edge = "left"  # already sliding up the left wall

    wc._walk_to(min_x, 300.0, min_x, max_y, frame[0], frame[1], frame[2], frame[3],
                edge_hint="left")

    # No rotate-first notification fires for the walk itself -- she was
    # already tracked as "left" going in (edge_hint agrees, nothing
    # changes), which is exactly "hugging the wall the whole way" with
    # zero redundant chatter. The ONLY transition is the post-walk settle.
    assert edge_calls == ["top"], (
        f"expected a single transition straight to 'top' on arrival (no "
        f"redundant re-notify of 'left', which never changed during the "
        f"walk itself): got {edge_calls}"
    )
    assert wc._last_edge == "top", (
        f"once the walk completes at the top-left corner, she must "
        f"settle into 'top' (the pose with dedicated heart/bubble "
        f"layout), not stay pinned to 'left': edge_calls={edge_calls}"
    )
    assert origin_box["v"] == (min_x, max_y), "walk must actually reach the corner"


def test_genuine_edge_switch_at_corner_still_reclassifies():
    """The corner-tie preference must NOT swallow a real transition: if
    she's currently on the BOTTOM edge and the destination is clearly on
    the RIGHT edge (not tied), rotate-first must still pick "right"."""
    frame = (0.0, 0.0, 1000.0, 800.0)
    min_y = 0.0 + BOTTOM_MARGIN_PX
    max_x = 0.0 + 1000.0 - window.CHAR_RIGHT_IN_WIN
    origin = (500.0, min_y)
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    wc._last_edge = "bottom"

    # Destination well up the right edge -- not a corner, not tied.
    wc._rotate_first_preamble(max_x, 400.0)

    assert edge_calls == ["right"]
    assert wc._last_edge == "right"


# ── edge_hint authoritative source (fix 2026-08-29) ────────────────────
# Bug (recurring Pink report): strolling UP the left/right edge still
# showed feet-up (the "top" pose) instead of hugging the wall. Root
# cause: 2026-08-27i only fixed the tie-break when the INCOMING tracked
# edge already equals one side of the destination corner's tie. It does
# nothing when she arrives at a corner tracked via one wall (e.g.
# "bottom") and then picks a walk that slides along a DIFFERENT wall to
# ITS far corner (e.g. up the left edge to top-left): the destination
# ties left(0)/top(0), the incoming tracked edge "bottom" matches
# neither side, so the tie-break falls through to fixed
# bottom>top>left>right priority and picks "top" for the whole vertical
# walk. _pick_edge_destination already knows -- unambiguously, from
# which branch picked (tx, ty) -- that this walk is a left-edge walk, so
# it now hands that down as edge_hint instead of leaving
# _rotate_first_preamble to re-guess it from the tied destination point.

def test_pick_edge_destination_hints_left_for_walk_up_from_bottom_left_corner():
    """Direct unit check on the picker: walking UP the left edge from the
    bottom-left corner must be hinted "left", not derived after the fact
    from the (tied) destination point."""
    min_x, max_x = 0.0, 1000.0
    min_y, max_y = 0.0, 800.0
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (min_x, min_y),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    with patch("squid_pet.wanderer.random.choice", side_effect=[
        ("left", 0.0, 2),  # choose the left edge out of the corner's "nearby" set
        1,                  # direction_to_corner=+1 -> the FAR (top) corner
    ]):
        tx, ty, edge = wc._pick_edge_destination(min_x, min_y, min_x, max_x, min_y, max_y)

    assert (tx, ty) == (min_x, max_y), "should target the top-left corner"
    assert edge == "left", (
        f"walking up the left edge must be hinted 'left', got {edge!r} -- "
        f"the destination ties left/top, so a post-hoc guess from (tx,ty) "
        f"alone (the old behavior) is exactly the bug this hint prevents"
    )


def test_rotate_first_preamble_uses_edge_hint_over_priority_guess_walking_up():
    """Integration of the fix at _rotate_first_preamble: even with an
    incoming tracked edge ("bottom") that matches neither side of the
    left/top tie at the destination, an explicit edge_hint="left" must
    win outright -- no fallback to the bottom>top>left>right guess that
    previously produced the feet-up bug."""
    frame = (0.0, 0.0, 1000.0, 800.0)
    min_x = 0.0 + EDGE_MARGIN_PX
    max_y = 0.0 + 800.0 - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
    origin = (min_x, 0.0 + BOTTOM_MARGIN_PX)  # sitting at the bottom-left corner
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    wc._last_edge = "bottom"  # she arrived here via the bottom edge

    # Now walking UP the left edge to the top-left corner (ties left/top).
    wc._rotate_first_preamble(min_x, max_y, edge_hint="left")

    assert edge_calls == ["left"], (
        f"expected the hint 'left' to win over the bottom>top>left>right "
        f"priority guess (which would pick 'top', i.e. feet-up): got {edge_calls}"
    )
    assert wc._last_edge == "left"


def test_rotate_first_preamble_uses_edge_hint_over_priority_guess_walking_up_right():
    """Same bug, mirrored on the right edge (also reported: 'left/right
    edges' both affected)."""
    frame = (0.0, 0.0, 1000.0, 800.0)
    max_x = 0.0 + 1000.0 - window.CHAR_RIGHT_IN_WIN
    max_y = 0.0 + 800.0 - CHAR_TOP_IN_WIN - TOP_MARGIN_PX
    origin = (max_x, 0.0 + BOTTOM_MARGIN_PX)  # sitting at the bottom-right corner
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin,
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    wc._last_edge = "bottom"

    # Walking UP the right edge to the top-right corner (ties right/top).
    wc._rotate_first_preamble(max_x, max_y, edge_hint="right")

    assert edge_calls == ["right"], (
        f"expected the hint 'right' to win over the priority guess "
        f"(which would pick 'top', i.e. feet-up): got {edge_calls}"
    )
    assert wc._last_edge == "right"


# ── force_edge for corner-snap paths (fix 2026-08-27d) ─────────────────
# Bug: menu snap / next_corner / startup all called refresh_edge() after
# a corner-snap, same fix pattern as the drag case above -- but it never
# actually worked for corner-snapped positions specifically. window.py's
# move_to_corner uses its own EDGE_MARGIN(20), never coordinated with
# this module's tighter wander-target margins, so a corner-snapped
# window sits just outside EDGE_BAND_PX and refresh_edge() silently
# returns "" regardless of which corner she's actually at. force_edge()
# sidesteps the distance heuristic entirely for callers who already know
# the edge authoritatively (window._edge_for_corner).

def test_force_edge_sets_and_notifies_on_change():
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    result = wc.force_edge("top")
    assert result == "top"
    assert edge_calls == ["top"]


def test_force_edge_is_a_noop_when_edge_unchanged():
    edge_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: edge_calls.append(e),
    )
    wc.force_edge("bottom")
    wc.force_edge("bottom")  # same edge again -- must not re-fire
    assert edge_calls == ["bottom"]


def test_force_edge_handles_none_and_empty_string():
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    assert wc.force_edge("") == ""
    assert wc.force_edge(None) == ""


# ── nudge/walk mutual exclusion (fix 2026-08-30) ────────────────────────
# Pink report: "flakey... losing some frames when she runs" while
# nudging her. Root cause: wander-walk threads (_walk_to) and
# nudge/flee-hop threads (_animate_hop) had NO mutual exclusion --
# each independently ticks its own interpolation and calls
# self._set_origin() on its own schedule, so if the RoutineController's
# idle-walk scheduler fires while a nudge/flee is mid-hop (routine
# timing is independent of user cursor activity, so this coincidence
# is not rare), the two threads' writes interleave and the window
# visibly jumps between two unrelated trajectories tick by tick --
# stutter, not smooth motion along either path. Fixed via
# self._hop_active (set for the duration of _animate_hop): a wander
# walk now checks it before starting AND on every tick already in
# flight, and yields immediately. Deliberately one-directional -- a
# nudge/flee must never be interrupted by an ambient wander walk
# starting, only the reverse.

def test_walk_does_not_start_while_hop_is_active():
    """_do_request_walk must no-op entirely if a nudge/flee is currently
    animating -- never fire a walk that would race its writes."""
    frame = (0.0, 0.0, 1000.0, 800.0)
    origin_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 300.0),
        set_window_origin=lambda x, y: origin_calls.append((x, y)),
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc._hop_active = True

    wc._do_request_walk("edge")

    assert origin_calls == [], (
        "a walk must not move the window at all while a hop is active"
    )


def test_walk_aborts_immediately_once_a_hop_becomes_active(monkeypatch):
    """_walk_to's tick loop must bail out the instant _hop_active is
    (already, or becomes) True, not fight the hop for the window origin."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    frame = (0.0, 0.0, 1000.0, 800.0)
    origin_calls = []
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 300.0),
        set_window_origin=lambda x, y: origin_calls.append((x, y)),
        get_visible_frame=lambda: frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc._hop_active = True  # simulate a hop already in flight

    wc._walk_to(0.0, 300.0, 500.0, 300.0, frame[0], frame[1], frame[2], frame[3])

    assert origin_calls == [], (
        f"walk must abort on its very first tick when a hop is already "
        f"active, writing nothing: got {origin_calls}"
    )


def test_animate_hop_clears_hop_active_flag_after_completion(monkeypatch):
    """_hop_active must not stay stuck True after a hop finishes --
    otherwise every future wander walk would be permanently blocked."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    assert wc._hop_active is False
    wc._animate_hop(0.0, 0.0, 70.0, 0.0, my_gen=0)
    assert wc._hop_active is False, "flag must be cleared once the hop completes"


def test_animate_hop_clears_hop_active_flag_when_superseded(monkeypatch):
    """Same guarantee via the OTHER exit path: a hop that gets preempted
    mid-flight by a newer one (early `return`, not the loop's natural
    end) must still clear _hop_active -- a bare `return` inside the
    try/finally still runs `finally`, but this locks that in."""
    monkeypatch.setattr("squid_pet.wanderer.time.sleep", lambda _s: None)
    wc = WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: (0.0, 0.0),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: (0.0, 0.0, 1000.0, 800.0),
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    wc._nudge_generation = 5
    wc._animate_hop(0.0, 0.0, 70.0, 0.0, my_gen=1)  # stale generation from the start
    assert wc._hop_active is False, (
        "flag must be cleared even when superseded before the loop "
        "ever runs a real tick"
    )
