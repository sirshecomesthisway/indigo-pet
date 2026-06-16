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
