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
    100 was subsequently confirmed (by Pink, visually) to be fully visible.
    Goal now is hugging the edge as tightly as possible without clipping,
    found by bisecting the (35, 100] range downward, one visually-confirmed
    step at a time -- see wanderer.TOP_MARGIN_PX for the current step and
    what to try next in either direction.

    This test only guards against silently sliding back down to the
    specific value already proven broken -- it does NOT assert the current
    value is optimal. Narrow this range only after an actual visual check,
    not from a fresh derivation alone (that's what broke things twice).
    """
    CONFIRMED_BROKEN = 35
    CONFIRMED_SAFE = 65
    assert CONFIRMED_BROKEN < wanderer.TOP_MARGIN_PX <= CONFIRMED_SAFE, (
        f"wanderer.TOP_MARGIN_PX={wanderer.TOP_MARGIN_PX} is outside the "
        f"known range ({CONFIRMED_BROKEN}, {CONFIRMED_SAFE}]. Values at or "
        f"below {CONFIRMED_BROKEN} are confirmed to clip her off the top of "
        f"the screen (screenshot evidence, 2026-08-18); {CONFIRMED_SAFE} is "
        f"confirmed fully visible (100 and 155 also confirmed/proven safe, "
        f"but {CONFIRMED_SAFE} is the tightest one visually confirmed so far)."
    )


def test_edge_margin_keeps_edge_classification_working():
    """EDGE_MARGIN_PX drives both (a) where the wander-target picker aims
    for left/right and (b) the edge classifier's reference boundary for
    "is she close enough to this edge to rotate-and-face it". Pushing it
    very negative (2026-08-18, so wander targets reach window.py's
    ALREADY-TRUSTED CHAR_LEFT_IN_WIN/CHAR_RIGHT_IN_WIN hard clamp instead
    of stopping short of it) can silently break classification for
    whichever side has the LESS generous hard clamp, if pushed further
    than that side tolerates -- CHAR_RIGHT_IN_WIN(190) is tighter than
    CHAR_LEFT_IN_WIN(51), so the right side is the binding constraint.

    This computes the same d_right the classifier would see when she's
    sitting at her actual (hard-clamp-limited) rightmost position, and
    asserts it stays within EDGE_BAND_PX -- i.e. she's still recognized
    as "on the right edge" there, so rotation still triggers.
    """
    d_right_at_hard_clamp = (
        window.CHAR_RIGHT_IN_WIN - wanderer.WIN_W - wanderer.EDGE_MARGIN_PX
    )
    assert 0 <= d_right_at_hard_clamp <= wanderer.EDGE_BAND_PX, (
        f"d_right at the hard-clamped right position = {d_right_at_hard_clamp}, "
        f"outside [0, {wanderer.EDGE_BAND_PX}]. If EDGE_MARGIN_PX "
        f"({wanderer.EDGE_MARGIN_PX}) was made more negative, she may no "
        f"longer be classified as 'on the right edge' while actually sitting "
        f"there, silently breaking right-edge rotation."
    )
