"""The sprite window must be identified, not guessed from window order.

NSApp.windows() also contains the menu-bar NSStatusItem's backing
windows. Picking "the first visible window" returned the status item
whenever AppKit happened to order it first, so positioning, alpha,
all-Spaces and the passthrough hit-test all operated on a 29x24 menu-bar
item instead of the 200x300 sprite -- which looks to the user like Squid
has frozen.
"""
from __future__ import annotations

from squid_pet.window import _pick_pet_window


class _Win:
    def __init__(self, cls: str, visible: bool = True):
        self._cls = cls
        self._visible = visible

    def isVisible(self):
        return self._visible

    def className(self):
        return self._cls


def test_status_bar_window_is_never_mistaken_for_the_pet():
    status = _Win("NSStatusBarWindow")
    sprite = _Win("NSWindow")
    assert _pick_pet_window([status, sprite]) is sprite


def test_pet_window_still_found_when_it_comes_first():
    sprite = _Win("NSWindow")
    status = _Win("NSStatusBarWindow")
    assert _pick_pet_window([sprite, status]) is sprite


def test_invisible_windows_are_skipped():
    hidden = _Win("NSWindow", visible=False)
    sprite = _Win("NSWindow")
    assert _pick_pet_window([hidden, sprite]) is sprite


def test_returns_none_when_only_status_items_exist():
    assert _pick_pet_window([_Win("NSStatusBarWindow")]) is None


def test_a_window_that_raises_does_not_abort_the_search():
    class _Angry:
        def isVisible(self):
            raise RuntimeError("window went away")

        def className(self):
            return "NSWindow"

    sprite = _Win("NSWindow")
    assert _pick_pet_window([_Angry(), sprite]) is sprite


def test_open_context_menu_is_not_mistaken_for_the_pet():
    """Right-clicking Squid opens an NSMenu, which is also a window.

    Same failure shape as the status item: while the menu is up, the
    pet's own geometry must still be the one we act on.
    """
    menu = _Win("NSCarbonMenuWindow")
    sprite = _Win("NSWindow")
    assert _pick_pet_window([menu, sprite]) is sprite


def test_menu_window_manager_window_is_also_excluded():
    menu = _Win("NSMenuWindowManagerWindow")
    sprite = _Win("NSWindow")
    assert _pick_pet_window([menu, sprite]) is sprite
