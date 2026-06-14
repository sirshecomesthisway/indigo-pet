"""
Native macOS right-click context menu for Indigo, built with NSMenu via
PyObjC. Dispatches all actions to PetApi (via a _MenuTarget NSObject so
Objective-C can call selectors on it).

The menu is rebuilt on every right-click so checkmarks (pinned, current
forced mood) and enabled states (What's wrong? greys out if no recent
error) reflect live state.
"""
from __future__ import annotations
import objc
from AppKit import (
    NSMenu, NSMenuItem, NSApp, NSEvent,
)
try:
    # NSOn/OffState constants (varies by PyObjC version)
    from AppKit import NSOnState, NSOffState
except ImportError:
    NSOnState, NSOffState = 1, 0
from Foundation import NSObject, NSPoint
from PyObjCTools import AppHelper


# ──────────────────────────────────────────────────────────────────
# Target: the NSObject that receives every menu selector
# ──────────────────────────────────────────────────────────────────
class _MenuTarget(NSObject):
    """Single Objective-C target for every menu item. Each method below
    corresponds to a selector string passed to NSMenuItem."""

    def initWithApi_(self, api):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self.api = api
        return self

    # Position
    def snapTL_(self, s): self.api._menu_snap("top-left")
    def snapTR_(self, s): self.api._menu_snap("top-right")
    def snapBL_(self, s): self.api._menu_snap("bottom-left")
    def snapBR_(self, s): self.api._menu_snap("bottom-right")
    def togglePin_(self, s): self.api._menu_toggle_pin()
    def recenter_(self, s): self.api._menu_recenter()

    # Pause Squid (timed)
    def pauseWander5_(self, s):  self.api._menu_pause_wander(5)
    def pauseWander15_(self, s): self.api._menu_pause_wander(15)
    def pauseWander30_(self, s): self.api._menu_pause_wander(30)
    def pauseWander60_(self, s): self.api._menu_pause_wander(60)
    def resumeWander_(self, s):  self.api._menu_resume_wander()

    # Funny easter eggs
    def sprintPerimeter_(self, s): self.api._menu_sprint_perimeter()
    # Stroll path (restored 2026-06-13)
    def strollAnywhere_(self, s): self.api._menu_set_stroll_mode("anywhere")
    def strollEdges_(self, s):    self.api._menu_set_stroll_mode("edges")
    def toggleMute_(self, s): self.api._menu_toggle_mute()

    # Stroll path

    # Mood
    def moodIdle_(self, s): self.api._menu_force("idle")
    def moodThinking_(self, s): self.api._menu_force("thinking")
    def moodGrooving_(self, s): self.api._menu_force("grooving")
    def moodCelebrate_(self, s): self.api._menu_force("celebrating")
    def moodSleep_(self, s): self.api._menu_force("sleeping")
    def moodConcerned_(self, s): self.api._menu_force("concerned")
    def moodWorking_(self, s): self.api._menu_force("working")
    def moodClear_(self, s): self.api._menu_clear_force()

    # Diagnostics
    def whatsWrong_(self, s): self.api._menu_whats_wrong()
    def showStats_(self, s): self.api._menu_show_stats()
    def openLog_(self, s): self.api._menu_open_log()

    # Lifecycle
    def restartApp_(self, s): self.api._menu_restart()
    def quitApp_(self, s): self.api._menu_quit()


# ──────────────────────────────────────────────────────────────────
# Menu builder
# ──────────────────────────────────────────────────────────────────
def _add(menu, title, target, selector, enabled=True, checked=False):
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title, selector, ""
    )
    item.setTarget_(target)
    item.setEnabled_(bool(enabled))
    item.setState_(NSOnState if checked else NSOffState)
    menu.addItem_(item)
    return item


def _build_menu(target, api) -> NSMenu:
    menu = NSMenu.alloc().init()
    menu.setAutoenablesItems_(False)

    # ── Position ──
    _add(menu, "📍  Top-Left", target, "snapTL:")
    _add(menu, "📍  Top-Right", target, "snapTR:")
    _add(menu, "📍  Bottom-Left", target, "snapBL:")
    _add(menu, "📍  Bottom-Right", target, "snapBR:")
    menu.addItem_(NSMenuItem.separatorItem())

    pinned = bool(getattr(api, "_pinned", False))
    pin_label = "⚓  Pinned in place" if pinned else "⚓  Pin in place"
    _add(menu, pin_label, target, "togglePin:", checked=pinned)
    _add(menu, "🎯  Recenter", target, "recenter:")
    menu.addItem_(NSMenuItem.separatorItem())

    # ── Mood submenu ──
    mood = NSMenu.alloc().init()
    mood.setAutoenablesItems_(False)
    forced = getattr(api, "_forced_state", None) or ""
    mood_entries = [
        ("😌  Idle",       "moodIdle:",      "idle"),
        ("💭  Thinking",   "moodThinking:",  "thinking"),
        ("🛠  Working",    "moodWorking:",   "working"),
        ("💃  Grooving",   "moodGrooving:",  "grooving"),
        ("🎉  Celebrate",  "moodCelebrate:", "celebrating"),
        ("😴  Sleep",      "moodSleep:",     "sleeping"),
        ("😟  Concerned",  "moodConcerned:", "concerned"),
    ]
    for label, sel, name in mood_entries:
        _add(mood, label, target, sel, checked=(forced == name))
    mood.addItem_(NSMenuItem.separatorItem())
    _add(mood, "↻  Clear override (live)", target, "moodClear:",
         enabled=bool(forced))

    mood_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "🎭  Mood", None, ""
    )
    mood_root.setSubmenu_(mood)
    menu.addItem_(mood_root)

    # ── Stroll path submenu (restored 2026-06-13) ──
    stroll_mode = getattr(api, "_stroll_mode", "edges")
    stroll = NSMenu.alloc().init()
    stroll.setAutoenablesItems_(False)
    _add(stroll, "Anywhere",   target, "strollAnywhere:",
         checked=(stroll_mode == "anywhere"))
    _add(stroll, "Edges only", target, "strollEdges:",
         checked=(stroll_mode == "edges"))
    stroll_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Stroll path", None, ""
    )
    stroll_root.setSubmenu_(stroll)
    menu.addItem_(stroll_root)


    # ── Pause Squid submenu ──
    import time as _t
    paused_until = float(getattr(api, "_wander_paused_until", 0.0) or 0.0)
    paused_now = _t.time() < paused_until
    remaining_min = max(0, int(round((paused_until - _t.time()) / 60))) if paused_now else 0
    pause_menu = NSMenu.alloc().init()
    pause_menu.setAutoenablesItems_(False)
    _add(pause_menu, "5 minutes",  target, "pauseWander5:")
    _add(pause_menu, "15 minutes", target, "pauseWander15:")
    _add(pause_menu, "30 minutes", target, "pauseWander30:")
    _add(pause_menu, "60 minutes", target, "pauseWander60:")
    pause_menu.addItem_(NSMenuItem.separatorItem())
    _add(pause_menu, "▶  Resume now", target, "resumeWander:",
         enabled=paused_now)
    pause_root_title = (
        f"⏸  Squid paused ({remaining_min}m left)" if paused_now
        else "⏸  Pause Squid"
    )
    pause_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        pause_root_title, None, ""
    )
    pause_root.setSubmenu_(pause_menu)
    menu.addItem_(pause_root)

    menu.addItem_(NSMenuItem.separatorItem())

    # ── Easter eggs ──
    _add(menu, "🏃‍♀️  Sprint the perimeter!", target, "sprintPerimeter:")
    # ----- Observer mute toggle (observer-mode 2026-06-13) -----
    try:
        muted_now = api.is_muted()
    except Exception:
        muted_now = False
    mute_label = "Unmute Squid" if muted_now else "Mute Squid"
    _add(menu, mute_label, target, "toggleMute:", checked=muted_now)
    menu.addItem_(NSMenuItem.separatorItem())

    # ── Diagnostics ──
    has_err = bool(api._menu_has_recent_error())
    _add(menu, "🩺  What's wrong?", target, "whatsWrong:", enabled=has_err)
    _add(menu, "📊  Show stats", target, "showStats:")
    _add(menu, "📜  Open Indigo log", target, "openLog:")
    menu.addItem_(NSMenuItem.separatorItem())

    # ── Lifecycle ──
    _add(menu, "↻  Restart Indigo", target, "restartApp:")
    _add(menu, "❌  Quit Indigo", target, "quitApp:")

    return menu


# ──────────────────────────────────────────────────────────────────
# Public controller
# ──────────────────────────────────────────────────────────────────
class IndigoMenu:
    """Owns the menu target. Call .show_at_cursor() from any thread —
    dispatches the actual popUp to the AppKit main thread."""

    def __init__(self, api):
        self.api = api
        # Retain the target so PyObjC doesn't GC it between menu opens.
        self.target = _MenuTarget.alloc().initWithApi_(api)
        # Install a GLOBAL right-click monitor. This catches right-clicks
        # anywhere on screen, lets us check if the cursor is over Indigo's
        # window bounds (regardless of pixel alpha / passthrough), and
        # triggers the menu directly. Fixes the case where right-clicking
        # a transparent area near her sprite fails because passthrough
        # routes the click to the desktop instead of WKWebView.
        self._install_global_monitor()

    def _install_global_monitor(self):
        try:
            from AppKit import (
                NSEvent, NSEventMaskRightMouseDown,
                NSEventMaskOtherMouseDown,
            )
            # Combined mask: right-click AND middle/ctrl-click variants.
            mask = NSEventMaskRightMouseDown | NSEventMaskOtherMouseDown
            self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, self._on_global_rightclick
            )
            # Also add a LOCAL monitor (for events delivered to our own app),
            # in case the click actually does hit a non-transparent pixel.
            self._local_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask, self._on_local_rightclick
            )
            print("[indigo-pet] right-click global monitor installed", flush=True)
        except Exception as e:
            print(f"[indigo-pet] could not install right-click monitor: {e}",
                  flush=True)

    def _on_global_rightclick(self, event):
        """Called for right-clicks anywhere on screen. Show menu only if
        the cursor is over Indigo's window bounds."""
        try:
            from AppKit import NSApp, NSEvent
            loc = NSEvent.mouseLocation()  # screen coords, y from bottom
            for w in NSApp.windows():
                try:
                    if str(w.title()) != "Indigo":
                        continue
                    wf = w.frame()
                    if (wf.origin.x <= loc.x <= wf.origin.x + wf.size.width
                            and wf.origin.y <= loc.y <= wf.origin.y + wf.size.height):
                        print(f"[indigo-pet] global right-click hit Indigo at "
                              f"({loc.x:.0f},{loc.y:.0f})", flush=True)
                        self.show_at_cursor()
                        return
                except Exception:
                    continue
        except Exception as e:
            print(f"[indigo-pet] right-click handler err: {e}", flush=True)

    def _on_local_rightclick(self, event):
        """Local monitor — return the event to let normal processing
        continue, but also trigger our menu."""
        self._on_global_rightclick(event)
        return event

    def show_at_cursor(self) -> None:
        def _on_main():
            try:
                menu = _build_menu(self.target, self.api)
                # Find our window
                win = None
                for w in NSApp.windows():
                    try:
                        if str(w.title()) == "Indigo":
                            win = w
                            break
                    except Exception:
                        continue
                if win is None:
                    print("[indigo-pet] menu: window not found", flush=True)
                    return
                # Bring Indigo forward so menu can claim focus.
                NSApp.activateIgnoringOtherApps_(True)
                win.makeKeyAndOrderFront_(None)

                # Use SCREEN-SPACE coords with view=None — avoids any
                # contentView-flip issues that can push the menu off-screen.
                # NSEvent.mouseLocation() returns screen coords with y
                # measured from BOTTOM (Cocoa convention) which is exactly
                # what popUpMenuPositioningItem expects when view is None.
                loc = NSEvent.mouseLocation()
                print(f"[indigo-pet] menu: popUp at screen ({loc.x:.0f},{loc.y:.0f})",
                      flush=True)
                menu.popUpMenuPositioningItem_atLocation_inView_(
                    None, loc, None
                )
                print("[indigo-pet] menu: popUp returned", flush=True)
            except Exception as e:
                print(f"[indigo-pet] menu show failed: {e}", flush=True)

        AppHelper.callAfter(_on_main)
