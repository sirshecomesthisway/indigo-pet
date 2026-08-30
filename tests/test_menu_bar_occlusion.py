"""Tests for the menu-bar occlusion fix (2026-08-29 Pink report): "she's
translucent in the top-right corner and I never hovered her".

Root cause: passthrough.py's hit-test only does window-frame + sprite-bbox
geometry -- it has no idea the system menu bar renders on top of every
window. When Squid is docked at a top corner, that geometry (plus the
edge=="top" hit-box shift toward the visual sprite position) lands
squarely under the menu bar's icon strip (Wi-Fi, clock, Control Center on
the right side of the screen). A cursor sitting there to use the menu bar
-- nothing to do with Squid -- reads as a continuous hover and triggers
the hover-fade.

_occluded_by_menu_bar is the pure geometry check: is this cursor Y at or
above the visible-frame top (i.e. in the menu bar strip)? Pure logic, no
AppKit/threading -- same rationale as HoverDwellTracker (test_hover_fade.py).
"""
from __future__ import annotations

from squid_pet.passthrough import _occluded_by_menu_bar


def test_cursor_below_menu_bar_not_occluded():
    assert _occluded_by_menu_bar(cy=800.0, visible_frame_top=875.0) is False


def test_cursor_inside_menu_bar_strip_is_occluded():
    assert _occluded_by_menu_bar(cy=887.5, visible_frame_top=875.0) is True


def test_cursor_exactly_at_visible_frame_top_is_occluded():
    """The menu bar owns its own boundary line -- >= not >."""
    assert _occluded_by_menu_bar(cy=875.0, visible_frame_top=875.0) is True
