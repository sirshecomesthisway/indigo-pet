"""Regression: constants must agree across window.py / wanderer.py / passthrough.py.

The 'top-edge strobe' bug of 2026-06-16 was caused by window.py bumping
WINDOW_HEIGHT from 220 -> 300 (for hearts headroom) while wanderer.py
was left at WIN_H = 220. The 80px mismatch caused the wanderer's edge
classifier to target positions above the visible frame, which then got
clamped — strobing the frontend edge-rotation at rhythm-walk frequency.

This test exists so a future edit to window dimensions cannot silently
re-introduce the same class of bug.
"""
from squid_pet import window, wanderer, passthrough


def test_window_height_constants_agree():
    """All modules that reason about window height MUST use the same value."""
    assert window.WINDOW_HEIGHT == wanderer.WIN_H, (
        f"window.WINDOW_HEIGHT={window.WINDOW_HEIGHT} but "
        f"wanderer.WIN_H={wanderer.WIN_H}. These MUST agree or the wanderer's "
        f"edge classifier will target positions outside the visible frame, "
        f"causing edge-flap / strobe (see kennel drawer 2026-06-16). "
        f"Fix: update wanderer.WIN_H to match, or consolidate into a shared "
        f"squid_pet.geometry module."
    )
    assert window.WINDOW_HEIGHT == passthrough.WINDOW_HEIGHT, (
        f"window.WINDOW_HEIGHT={window.WINDOW_HEIGHT} but "
        f"passthrough.WINDOW_HEIGHT={passthrough.WINDOW_HEIGHT}. "
        f"Passthrough alpha-mask geometry depends on this matching."
    )


def test_window_width_constants_agree():
    """Width parallel to height — same anti-pattern protection."""
    assert window.WINDOW_WIDTH == wanderer.WIN_W, (
        f"window.WINDOW_WIDTH={window.WINDOW_WIDTH} but "
        f"wanderer.WIN_W={wanderer.WIN_W}. Will cause edge-flap on left/right."
    )


def test_top_margin_constants_agree():
    """TOP_MARGIN_PX must match between window.py's hard clamp and
    wanderer.py's wander-target picker, same class of bug as WIN_H/WIN_W."""
    assert window.TOP_MARGIN_PX == wanderer.TOP_MARGIN_PX, (
        f"window.TOP_MARGIN_PX={window.TOP_MARGIN_PX} but "
        f"wanderer.TOP_MARGIN_PX={wanderer.TOP_MARGIN_PX}. These MUST agree "
        f"or the hard clamp and the wander-target picker disagree on how "
        f"close to the top edge she's allowed to go."
    )


def test_top_margin_stays_above_confirmed_broken_floor():
    """2026-08-18: TOP_MARGIN_PX<=35 (and briefly 10) were confirmed via
    screenshot to leave her nearly/fully invisible at the top edge -- the
    CSS rotate(180deg) + translateY top-edge transform clips in a way that
    CHAR_TOP_IN_WIN-based hand derivation didn't correctly predict, twice.
    100 was subsequently confirmed (by Pink, visually) to be fully visible;
    2026-08-30, 50 was ALSO confirmed fully visible (live screenshot),
    narrowing the safe boundary further. Goal now is hugging the edge as
    tightly as possible without clipping, found by bisecting the (35, 50]
    range downward, one visually-confirmed step at a time -- see
    wanderer.TOP_MARGIN_PX for the current step and what to try next in
    either direction.

    This test only guards against silently sliding back down to the
    specific value already proven broken -- it does NOT assert the current
    value is optimal. Narrow this range only after an actual visual check,
    not from a fresh derivation alone (that's what broke things twice).
    """
    CONFIRMED_BROKEN = 35
    CONFIRMED_SAFE = 50
    assert CONFIRMED_BROKEN < wanderer.TOP_MARGIN_PX <= CONFIRMED_SAFE, (
        f"wanderer.TOP_MARGIN_PX={wanderer.TOP_MARGIN_PX} is outside the "
        f"known range ({CONFIRMED_BROKEN}, {CONFIRMED_SAFE}]. Values at or "
        f"below {CONFIRMED_BROKEN} are confirmed to clip her off the top of "
        f"the screen (screenshot evidence, 2026-08-18); {CONFIRMED_SAFE} is "
        f"confirmed fully visible (65, 100, and 155 also confirmed/proven "
        f"safe, but {CONFIRMED_SAFE} is the tightest one visually confirmed "
        f"so far)."
    )


def test_edge_margin_keeps_left_edge_classification_working():
    """EDGE_MARGIN_PX now drives ONLY the left side (Pink-2026-08-29 --
    see WanderController._x_bounds and its EDGE_MARGIN_PX comment). This
    computes the same d_left the classifier would see when she's sitting
    at her actual (hard-clamp-limited) leftmost position, and asserts it
    stays within EDGE_BAND_PX -- i.e. she's still recognized as "on the
    left edge" there, so rotation still triggers.
    """
    d_left_at_hard_clamp = abs(wanderer.EDGE_MARGIN_PX - (-window.CHAR_LEFT_IN_WIN))
    assert d_left_at_hard_clamp <= wanderer.EDGE_BAND_PX, (
        f"d_left at the hard-clamped left position = {d_left_at_hard_clamp}, "
        f"outside [0, {wanderer.EDGE_BAND_PX}]. If EDGE_MARGIN_PX "
        f"({wanderer.EDGE_MARGIN_PX}) drifted from -CHAR_LEFT_IN_WIN "
        f"({-window.CHAR_LEFT_IN_WIN}), she may no longer be classified as "
        f"'on the left edge' while actually sitting there, silently "
        f"breaking left-edge rotation."
    )


def test_x_bounds_matches_windows_hard_clamp_exactly():
    """Regression (Pink-2026-08-29, live report: "不會停在真正的右上角" --
    she never actually stops at the true corner). WanderController's own
    max_x used to be a SEPARATE approximation (EDGE_MARGIN_PX's overshoot-
    past-window's-clamp trick) tuned against window.CHAR_RIGHT_IN_WIN=190.
    When CHAR_RIGHT_IN_WIN was tightened to 158 (2026-08-18) nothing
    re-derived the wanderer-side approximation, so it silently drifted
    9px away from the real hard clamp -- window.py's clamp still caught
    and positioned her at the (correct, tightened) real edge every time,
    but wanderer's OWN bookkeeping never found out, so it kept believing
    9 more pixels of progress were always still available and kept
    re-triggering a walk toward them that could never actually complete
    (see WanderController._x_bounds' docstring for the full mechanism).

    _x_bounds() now derives max_x directly from window.CHAR_RIGHT_IN_WIN,
    so this asserts EXACT equality with window._char_bounds()'s own
    max_ox -- not just "within EDGE_BAND_PX tolerance" like the
    classification-only guard above, since a several-px silent drift is
    exactly what caused this bug while still passing that looser check.
    """
    fake_frame = (0.0, 0.0, 1440.0, 875.0)
    vx, vy, vw, vh = fake_frame
    wc = wanderer.WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: None,
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: fake_frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    min_x, max_x = wc._x_bounds(vx, vw)
    _min_ox, max_ox, _min_oy, _max_oy = window._char_bounds(vx, vy, vw, vh)
    assert max_x == max_ox, (
        f"wanderer._x_bounds max_x={max_x} != window._char_bounds max_ox="
        f"{max_ox} -- these must be IDENTICAL, not just close, or the "
        f"9px-gap bug (2026-08-29) recurs."
    )


# ── corner_origin() must reach the same tight position wandering does ──
# Pink-2026-08-27o: corner_origin() (menu snap / next_corner / startup)
# used to inset by a cosmetic EDGE_MARGIN(20)px -- landing 60-70px short
# of where wanderer.py's own edge-hugging margins actually reach.
# Confirmed live: "not hugging the edge" persisted even after the
# rotation-sync fix (force_edge/_edge_for_corner), because that bug was
# never actually about rotation -- it was the POSITION itself falling
# short. corner_origin() now derives from the exact same _char_bounds()
# clamp_origin_to_screen already enforces everywhere else.

def test_corner_origin_matches_char_bounds_exactly(monkeypatch):
    fake_frame = (100.0, 50.0, 1440.0, 875.0)
    monkeypatch.setattr(window, "_visible_frame", lambda: fake_frame)
    vx, vy, vw, vh = fake_frame
    min_ox, max_ox, min_oy, max_oy = window._char_bounds(vx, vy, vw, vh)

    assert window.corner_origin("bottom-right") == (max_ox, min_oy)
    assert window.corner_origin("top-right") == (max_ox, max_oy)
    assert window.corner_origin("bottom-left") == (min_ox, min_oy)
    assert window.corner_origin("top-left") == (min_ox, max_oy)


def test_corner_origin_recognized_by_wanderers_distance_classifier(monkeypatch):
    """The corner-snapped position must be recognized as 'on that edge'
    by wanderer._compute_edge_at's own distance-based classifier (used
    by refresh_edge() for drag-end etc), not just by the separate
    force_edge()/_edge_for_corner() workaround -- confirms the two
    classification mechanisms now agree, since corner_origin() reaches
    (near enough) the exact same bounds wanderer.py's margins target."""
    fake_frame = (0.0, 0.0, 1440.0, 875.0)
    monkeypatch.setattr(window, "_visible_frame", lambda: fake_frame)

    origin_box = {}
    wc = wanderer.WanderController(
        get_state=lambda: "idle",
        is_drag_active=lambda: False,
        get_window_origin=lambda: origin_box.get("v"),
        set_window_origin=lambda x, y: None,
        get_visible_frame=lambda: fake_frame,
        set_sub_state=lambda s: None,
        set_edge=lambda e: None,
    )
    for corner, expected_edge in [
        ("bottom-right", "bottom"), ("top-right", "top"),
        ("bottom-left", "bottom"), ("top-left", "top"),
    ]:
        origin_box["v"] = window.corner_origin(corner)
        edge = wc.refresh_edge()
        assert edge == expected_edge, (
            f"{corner} snapped to {origin_box['v']} but wanderer's distance "
            f"classifier says edge={edge!r}, not {expected_edge!r} -- "
            f"corner_origin() and wanderer.py's margins have drifted "
            f"out of sync again."
        )


# ── backend sleeping vs the frontend's drowsy stage (2026-09-04) ──────
# Sleeping moved off macOS HID idle onto the agents' own quiet clock
# (84953db), which put the backend threshold on the same axis the
# frontend already used to stage drowsy -> sleeping. Cross that staging
# and the slump animation silently dies: the frontend only advances its
# mood while the backend state is idle/sleeping, and once a mood is set
# it BLOCKS state-driven sprite swaps ("Mood layer owns the sprite when
# active" in index.html), so a mismatch can also strand her on
# drowsy.png while state.json says sleeping.
import re
from pathlib import Path

from squid_pet import watcher


def _frontend_const(name: str) -> int:
    """Read a numeric const out of index.html -- the frontend has no test
    harness of its own, and these two values must track Python's."""
    html = (Path(window.__file__).parent / "frontend" / "index.html").read_text()
    m = re.search(rf"const\s+{name}\s*=\s*(\d+)", html)
    assert m, f"{name} not found in index.html"
    return int(m.group(1))


def test_backend_sleeping_leaves_room_for_the_drowsy_stage():
    drowsy = _frontend_const("DROWSY_IDLE_SEC")
    assert watcher.IDLE_THRESHOLD_SEC > drowsy, (
        f"watcher.IDLE_THRESHOLD_SEC={watcher.IDLE_THRESHOLD_SEC} must be "
        f"GREATER than the frontend's DROWSY_IDLE_SEC={drowsy}. The drowsy "
        f"slump only plays while the backend still reports idle; at or "
        f"below the drowsy mark she jumps straight to the sleeping sprite "
        f"and the stage is dead code."
    )


def test_both_layers_call_the_same_moment_asleep():
    sleeping = _frontend_const("SLEEPING_IDLE_SEC")
    assert sleeping == watcher.IDLE_THRESHOLD_SEC, (
        f"index.html SLEEPING_IDLE_SEC={sleeping} but "
        f"watcher.IDLE_THRESHOLD_SEC={watcher.IDLE_THRESHOLD_SEC}. If the "
        f"frontend's deep-sleep mark lands later than the backend's, the "
        f"mood layer stays 'drowsy' and blocks the sprite swap -- she sits "
        f"on drowsy.png while state.json says sleeping."
    )
