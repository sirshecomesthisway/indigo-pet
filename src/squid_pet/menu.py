"""
Native macOS right-click context menu for Squid AND a menu bar status
item that mirrors the same menu. Both rebuild on every open so dynamic
labels (Hidden/Visible, Muted, Paused-with-countdown) stay accurate.

Tier 1 menu rebuild 2026-06-28 (Pink/Indigo):
  - NSStatusItem in menu bar with state-aware emoji icon
  - Hide Squid toggle (true hide via NSWindow.alpha=0, not just pause)
  - Confirm-on-Quit modal (NSAlert) to prevent misclick disaster
  - IA cleanup: Position submenu, Bubbles submenu, mood gated by
    SQUID_DEV env var, "Pause Squid" -> "Pause wandering"
"""
from __future__ import annotations
import os
import objc
from AppKit import (
    NSMenu, NSMenuItem, NSApp, NSEvent, NSStatusBar,
    NSVariableStatusItemLength, NSAlert, NSAlertFirstButtonReturn,
    NSAlertSecondButtonReturn,
)
try:
    from AppKit import NSOnState, NSOffState
except ImportError:
    NSOnState, NSOffState = 1, 0
from Foundation import NSObject
from PyObjCTools import AppHelper


# Emoji constants -- declared at module top so the menu builder reads
# clean and the SquidMenu can pick the right one for the status icon.
EMO_SQUID = "🦑"
EMO_ZZZ = "💤"
EMO_MUTE = "🔇"
EMO_PIN_LOC = "📍"
EMO_ANCHOR = "⚓"
EMO_CROSSHAIR = "🎯"
EMO_MASK = "🎭"
EMO_PAUSE = "⏸"
EMO_PLAY = "▶"
EMO_SPRINT = "🏃‍♀️"
EMO_DOCTOR = "🩺"
EMO_CHART = "📊"
EMO_SCROLL = "📜"
EMO_RELOAD = "↻"
EMO_XMARK = "❌"
EMO_GHOST = "👻"
EMO_SPARKLE = "✨"

DEV_MODE = bool(os.environ.get("SQUID_DEV"))


class _MenuTarget(NSObject):
    """ObjC target for menu selectors AND NSMenuDelegate for live rebuild."""

    def initWithApi_(self, api):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self.api = api
        self.parent = None  # SquidMenu sets this so menuNeedsUpdate can rebuild
        return self

    # NSMenuDelegate: called by AppKit just before a menu opens. We rebuild
    # the items in place so the status-bar menu shows live state.
    def menuNeedsUpdate_(self, menu):
        try:
            menu.removeAllItems()
            _populate_menu(menu, self, self.api)
        except Exception as e:
            print(f"[squid-pet] menuNeedsUpdate failed: {e}", flush=True)

    # Position
    def snapTL_(self, s): self.api._menu_snap("top-left")
    def snapTR_(self, s): self.api._menu_snap("top-right")
    def snapBL_(self, s): self.api._menu_snap("bottom-left")
    def snapBR_(self, s): self.api._menu_snap("bottom-right")
    def togglePin_(self, s): self.api._menu_toggle_pin()
    def recenter_(self, s): self.api._menu_recenter()
    def strollAnywhere_(self, s): self.api._menu_set_stroll_mode("anywhere")
    def strollEdges_(self, s):    self.api._menu_set_stroll_mode("edges")

    # Hide / Pause / Bubbles
    def toggleHide_(self, s): self.api._menu_toggle_hide()
    def pauseWander5_(self, s):  self.api._menu_pause_wander(5)
    def pauseWander15_(self, s): self.api._menu_pause_wander(15)
    def pauseWander30_(self, s): self.api._menu_pause_wander(30)
    def pauseWander60_(self, s): self.api._menu_pause_wander(60)
    def resumeWander_(self, s):  self.api._menu_resume_wander()
    def toggleMute_(self, s): self.api._menu_toggle_mute()
    def toggleLLMBubbles_(self, s): self.api._menu_toggle_llm_bubbles()

    # Easter eggs
    def sprintPerimeter_(self, s): self.api._menu_sprint_perimeter()

    # Mood (DEV only)
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
    def quitAppConfirmed_(self, s):
        """task 6: confirm-on-Quit. NSAlert with Cancel as default."""
        try:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Quit Squid?")
            alert.setInformativeText_(
                "Squid will stop watching until next login. "
                "Run \"squid start\" in Terminal to bring her back."
            )
            alert.addButtonWithTitle_("Cancel")  # default (return key)
            alert.addButtonWithTitle_("Quit")
            resp = alert.runModal()
            # NSAlertFirstButtonReturn = Cancel; NSAlertSecondButtonReturn = Quit
            if resp == NSAlertSecondButtonReturn:
                self.api._menu_quit()
            else:
                print("[squid-pet] quit cancelled by user", flush=True)
        except Exception as e:
            print(f"[squid-pet] quit confirm failed, falling back to direct quit: {e}",
                  flush=True)
            self.api._menu_quit()


def _add(menu, title, target, selector, enabled=True, checked=False, key=""):
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title, selector, key
    )
    item.setTarget_(target)
    item.setEnabled_(bool(enabled))
    item.setState_(NSOnState if checked else NSOffState)
    menu.addItem_(item)
    return item


def _populate_menu(menu, target, api) -> None:
    """Build menu items into the given (already-empty) NSMenu. Used by
    both right-click open (fresh menu) and status-bar reopen (delegate-driven)."""
    menu.setAutoenablesItems_(False)

    # ============ TOP: Hide/Show (the killer feature) ============
    hidden = bool(getattr(api, "_hidden", False))
    hide_label = (
        f"{EMO_GHOST}  Show Squid" if hidden
        else f"{EMO_GHOST}  Hide Squid"
    )
    _add(menu, hide_label, target, "toggleHide:")
    menu.addItem_(NSMenuItem.separatorItem())

    # ============ Position submenu (was 6 flat items) ============
    pos = NSMenu.alloc().init()
    pos.setAutoenablesItems_(False)
    _add(pos, f"{EMO_PIN_LOC}  Top-Left", target, "snapTL:")
    _add(pos, f"{EMO_PIN_LOC}  Top-Right", target, "snapTR:")
    _add(pos, f"{EMO_PIN_LOC}  Bottom-Left", target, "snapBL:")
    _add(pos, f"{EMO_PIN_LOC}  Bottom-Right", target, "snapBR:")
    pos.addItem_(NSMenuItem.separatorItem())
    pinned = bool(getattr(api, "_pinned", False))
    pin_label = (
        f"{EMO_ANCHOR}  Pinned in place" if pinned
        else f"{EMO_ANCHOR}  Pin in place"
    )
    _add(pos, pin_label, target, "togglePin:", checked=pinned)
    _add(pos, f"{EMO_CROSSHAIR}  Recenter", target, "recenter:")
    pos.addItem_(NSMenuItem.separatorItem())
    stroll_mode = getattr(api, "_stroll_mode", "edges")
    _add(pos, "Stroll: anywhere", target, "strollAnywhere:",
         checked=(stroll_mode == "anywhere"))
    _add(pos, "Stroll: edges only", target, "strollEdges:",
         checked=(stroll_mode == "edges"))
    pos_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"{EMO_PIN_LOC}  Position", None, ""
    )
    pos_root.setSubmenu_(pos)
    menu.addItem_(pos_root)

    # ============ Bubbles submenu (was scattered in Easter eggs) ============
    bub = NSMenu.alloc().init()
    bub.setAutoenablesItems_(False)
    try:
        muted_now = api.is_muted()
    except Exception:
        muted_now = False
    # task 3: clearer mute label
    mute_label = (
        f"{EMO_MUTE}  Muted (click to unmute)" if muted_now
        else f"{EMO_MUTE}  Mute bubbles"
    )
    _add(bub, mute_label, target, "toggleMute:", checked=muted_now)
    try:
        llm_on = api.is_llm_bubbles_enabled()
    except Exception:
        llm_on = False
    llm_label = (
        f"{EMO_SPARKLE}  LLM bubbles (on)" if llm_on
        else f"{EMO_SPARKLE}  LLM bubbles (off)"
    )
    _add(bub, llm_label, target, "toggleLLMBubbles:", checked=llm_on)
    bub_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"{EMO_SPARKLE}  Bubbles", None, ""
    )
    bub_root.setSubmenu_(bub)
    menu.addItem_(bub_root)

    # ============ Pause wandering submenu (renamed from "Pause Squid") ============
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
    _add(pause_menu, f"{EMO_PLAY}  Resume now", target, "resumeWander:",
         enabled=paused_now)
    pause_root_title = (
        f"{EMO_PAUSE}  Wandering paused ({remaining_min}m left)" if paused_now
        else f"{EMO_PAUSE}  Pause wandering"
    )
    pause_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        pause_root_title, None, ""
    )
    pause_root.setSubmenu_(pause_menu)
    menu.addItem_(pause_root)

    menu.addItem_(NSMenuItem.separatorItem())

    # ============ Mood submenu (DEV only) ============
    if DEV_MODE:
        mood = NSMenu.alloc().init()
        mood.setAutoenablesItems_(False)
        forced = getattr(api, "_forced_state", None) or ""
        for label, sel, name in [
            ("Idle", "moodIdle:", "idle"),
            ("Thinking", "moodThinking:", "thinking"),
            ("Working", "moodWorking:", "working"),
            ("Grooving", "moodGrooving:", "grooving"),
            ("Celebrate", "moodCelebrate:", "celebrating"),
            ("Sleep", "moodSleep:", "sleeping"),
            ("Concerned", "moodConcerned:", "concerned"),
        ]:
            _add(mood, label, target, sel, checked=(forced == name))
        mood.addItem_(NSMenuItem.separatorItem())
        _add(mood, f"{EMO_RELOAD}  Clear override", target, "moodClear:",
             enabled=bool(forced))
        mood_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"{EMO_MASK}  Mood (dev)", None, ""
        )
        mood_root.setSubmenu_(mood)
        menu.addItem_(mood_root)

    # ============ Easter eggs (just sprint now) ============
    _add(menu, f"{EMO_SPRINT}  Sprint the perimeter!", target, "sprintPerimeter:")
    menu.addItem_(NSMenuItem.separatorItem())

    # ============ Diagnostics ============
    has_err = bool(api._menu_has_recent_error())
    _add(menu, f"{EMO_DOCTOR}  What's wrong?", target, "whatsWrong:", enabled=has_err)
    _add(menu, f"{EMO_CHART}  Show stats", target, "showStats:")
    _add(menu, f"{EMO_SCROLL}  Open Squid log", target, "openLog:")
    menu.addItem_(NSMenuItem.separatorItem())

    # ============ Lifecycle ============
    _add(menu, f"{EMO_RELOAD}  Restart Squid", target, "restartApp:")
    # task 6: quit goes through the confirming selector now
    _add(menu, f"{EMO_XMARK}  Quit Squid...", target, "quitAppConfirmed:")


def _build_menu(target, api) -> NSMenu:
    """Build a fresh menu (used by right-click). The status-bar menu
    uses the delegate-driven rebuild instead via _populate_menu."""
    menu = NSMenu.alloc().init()
    _populate_menu(menu, target, api)
    return menu


class SquidMenu:
    """Owns the menu target, the global right-click monitor, AND the
    NSStatusItem in the menu bar. All three share one _MenuTarget."""

    def __init__(self, api):
        self.api = api
        self.target = _MenuTarget.alloc().initWithApi_(api)
        self.target.parent = self
        self._status_item = None
        self._status_menu = None
        self._install_global_monitor()
        # Defer status item creation to the next main-thread tick so
        # NSApp is fully alive (this constructor runs from on_loaded).
        AppHelper.callAfter(self._install_status_item)

    def _install_global_monitor(self):
        try:
            from AppKit import (
                NSEvent, NSEventMaskRightMouseDown,
                NSEventMaskOtherMouseDown,
            )
            mask = NSEventMaskRightMouseDown | NSEventMaskOtherMouseDown
            self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, self._on_global_rightclick
            )
            self._local_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask, self._on_local_rightclick
            )
            print("[squid-pet] right-click global monitor installed", flush=True)
        except Exception as e:
            print(f"[squid-pet] could not install right-click monitor: {e}",
                  flush=True)

    def _install_status_item(self):
        """Add the menu bar status item. Icon reflects current state.
        Clicking it (either button) opens the same menu as right-click."""
        try:
            self._status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
                NSVariableStatusItemLength
            )
            self._status_menu = NSMenu.alloc().init()
            self._status_menu.setDelegate_(self.target)  # rebuild on open
            self._status_item.setMenu_(self._status_menu)
            self.refresh_status_icon()
            print("[squid-pet] menu bar status item installed", flush=True)
        except Exception as e:
            print(f"[squid-pet] status item install failed: {e}", flush=True)

    def refresh_status_icon(self):
        """Set the menu bar icon to reflect current state. Called when
        hide / mute toggles. Priority: hidden > muted > visible."""
        if self._status_item is None:
            return
        try:
            hidden = bool(getattr(self.api, "_hidden", False))
            try:
                muted = bool(self.api.is_muted())
            except Exception:
                muted = False
            if hidden:
                glyph = EMO_ZZZ
            elif muted:
                glyph = EMO_MUTE
            else:
                glyph = EMO_SQUID
            btn = self._status_item.button()
            if btn is not None:
                btn.setTitle_(glyph)
        except Exception as e:
            print(f"[squid-pet] refresh_status_icon failed: {e}", flush=True)

    def _on_global_rightclick(self, event):
        try:
            from AppKit import NSApp, NSEvent
            loc = NSEvent.mouseLocation()
            for w in NSApp.windows():
                try:
                    if str(w.title()) != "Squid":
                        continue
                    wf = w.frame()
                    if (wf.origin.x <= loc.x <= wf.origin.x + wf.size.width
                            and wf.origin.y <= loc.y <= wf.origin.y + wf.size.height):
                        print(f"[squid-pet] global right-click hit Squid at "
                              f"({loc.x:.0f},{loc.y:.0f})", flush=True)
                        self.show_at_cursor()
                        return
                except Exception:
                    continue
        except Exception as e:
            print(f"[squid-pet] right-click handler err: {e}", flush=True)

    def _on_local_rightclick(self, event):
        self._on_global_rightclick(event)
        return event

    def show_at_cursor(self) -> None:
        def _on_main():
            try:
                menu = _build_menu(self.target, self.api)
                win = None
                for w in NSApp.windows():
                    try:
                        if str(w.title()) == "Squid":
                            win = w
                            break
                    except Exception:
                        continue
                if win is None:
                    print("[squid-pet] menu: window not found", flush=True)
                    return
                NSApp.activateIgnoringOtherApps_(True)
                win.makeKeyAndOrderFront_(None)
                loc = NSEvent.mouseLocation()
                print(f"[squid-pet] menu: popUp at screen ({loc.x:.0f},{loc.y:.0f})",
                      flush=True)
                menu.popUpMenuPositioningItem_atLocation_inView_(
                    None, loc, None
                )
                print("[squid-pet] menu: popUp returned", flush=True)
            except Exception as e:
                print(f"[squid-pet] menu show failed: {e}", flush=True)

        AppHelper.callAfter(_on_main)
