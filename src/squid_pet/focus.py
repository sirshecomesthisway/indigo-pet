"""focus.py -- "take me to the window that's waiting".

Pink-2026-09-01: double-clicking a waving Squid should land you in the
session that actually needs you, not merely acknowledge it.

Matching is by TTY, which is exact. A Claude Code process has a
controlling terminal (`/dev/ttysNNN`); Terminal.app exposes the same
value as `tty of tab`. Comparing those two identifies the precise tab,
with nothing inferred.

The first attempt matched the terminal TITLE against the session's
working directory, which meant recording cwd on the awaiting-input flag
and widening what docs/PRIVACY.md promises. It was then measured against
a live window and did not even work: Claude Code sets the title to a
summary of the current task ("pinksmac -- Squid demo script with status
mentions"), not the directory. TTY needs no new stored data at all, so
the flag's contents are unchanged.

Two levels, because only one is universally available:

  1. THE APP. watcher.find_terminal_app_bundle_for_claude_code() walks a
     claude process's parent chain to whichever terminal is hosting it --
     the same lookup terminal-notifier's -activate uses for the banner's
     Show button. Always available.
  2. THE TAB. Terminal.app only; its AppleScript dictionary exposes tabs
     and their ttys. iTerm2 models sessions differently and VS Code
     exposes no addressable terminal tab, so both fall back to (1).

Best-effort by nature: the hook payload carries no PID, so with several
sessions waiting there is no way to know which one fired -- the same
limitation find_terminal_app_bundle_for_claude_code documents.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Optional

TERMINAL_APP_BUNDLE_ID = "com.apple.Terminal"


def _signal_dirs() -> dict:
    """Which flag directory names the session responsible for each state.

    Pink-2026-09-01: originally only the wave could take you anywhere.
    Pink asked for the same on every active sprite -- "except idle/drowsy/
    sleeping, because they mean she's doing nothing" -- and every active
    state already has a directory naming who caused it, so the same
    session -> project -> process -> tty -> tab chain covers them all.

    Resting states are absent by design, not by omission: with nothing
    running there is no window a double-click could honestly raise, and
    yanking one to the front would be worse than doing nothing.
    """
    from . import watcher as w
    return {
        "working":         w.CLAUDE_TURN_ACTIVE_DIR,
        "thinking":        w.CLAUDE_TURN_ACTIVE_DIR,
        "approval_needed": w.CLAUDE_AWAITING_INPUT_DIR,
        "celebrating":     w.CLAUDE_TASK_COMPLETE_DIR,
        "grooving":        w.CLAUDE_FINISHED_DIR,
    }


STATE_SIGNAL_DIRS = _signal_dirs()


def _freshest_in(dir_path: str) -> Optional[str]:
    """Newest flag filename (a session id) in a signal directory.

    Freshest rather than first: whichever session most recently caused the
    state is the one you are reacting to.
    """
    import os
    try:
        names = os.listdir(dir_path)
    except OSError:
        return None
    best: tuple[float, str] | None = None
    for name in names:
        if name.startswith("."):
            continue
        try:
            mtime = os.stat(os.path.join(dir_path, name)).st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, name)
    return best[1] if best else None


def freshest_waiting_session() -> Optional[str]:
    """Session id of the most recently raised wave."""
    from .watcher import CLAUDE_AWAITING_INPUT_DIR
    return _freshest_in(CLAUDE_AWAITING_INPUT_DIR)


def waiting_session_tty() -> Optional[str]:
    """Controlling terminal of the session that is actually waiting.

    Pink-2026-09-01: resolved through THAT session rather than "whichever
    claude process turns up first". A session's project directory
    identifies its process (watcher.claude_session_tty), which is what
    makes this correct when several sessions are running -- the hook
    payload's missing PID otherwise leaves it guessing, the same
    limitation find_terminal_app_bundle_for_claude_code documents.

    Falls back to any live process's tty, which is exactly right in the
    single-session case and no worse than the old behaviour otherwise.
    """
    try:
        from .watcher import claude_session_tty, find_claude_code_processes
    except Exception:
        return None
    sid = freshest_waiting_session()
    if sid:
        try:
            tty = claude_session_tty(sid)
            if tty:
                return tty
        except Exception:
            pass
    try:
        for proc in find_claude_code_processes():
            try:
                tty = proc.terminal()
            except Exception:
                continue
            if tty:
                return tty
    except Exception:
        pass
    return None


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def build_terminal_focus_script(tty: str) -> str:
    """AppleScript raising the Terminal tab whose tty matches, and saying
    which it managed. Pure string building so the interesting part is
    testable without driving a real window."""
    t = _escape_applescript(tty)
    return (
        'tell application "Terminal"\n'
        "    activate\n"
        "    repeat with w in windows\n"
        "        repeat with tb in tabs of w\n"
        f'            if (tty of tb as text) is "{t}" then\n'
        "                set index of w to 1\n"
        "                set selected tab of w to tb\n"
        '                return "matched"\n'
        "            end if\n"
        "        end repeat\n"
        "    end repeat\n"
        '    return "app-only"\n'
        "end tell\n"
    )


def build_app_activate_script(bundle_id: str) -> str:
    """Fallback: raise the hosting app without picking a window."""
    return f'tell application id "{_escape_applescript(bundle_id)}" to activate\n'


def focus_for_state(state: str,
                    run: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """Bring the window responsible for `state` to the front.

    Returns "matched" (that tab is now front), "app-only" (right app,
    unknown tab), "none" (no terminal identified) or "resting" (nothing to
    open -- idle, drowsy, sleeping, or any state we have no signal for).
    """
    signal_dir = STATE_SIGNAL_DIRS.get(state)
    if signal_dir is None:
        return "resting"
    sid = _freshest_in(signal_dir)
    tty = None
    if sid:
        try:
            from .watcher import claude_session_tty
            tty = claude_session_tty(sid)
        except Exception:
            tty = None
    if tty is None:
        # The flag may have aged out of its freshness window while the
        # sprite still shows the state. Raising the right app still beats
        # doing nothing.
        tty = _any_claude_tty()
    return _raise(tty, run)


def _any_claude_tty() -> Optional[str]:
    try:
        from .watcher import find_claude_code_processes
        for proc in find_claude_code_processes():
            try:
                tty = proc.terminal()
            except Exception:
                continue
            if tty:
                return tty
    except Exception:
        pass
    return None


def _raise(tty: Optional[str],
           run: Optional[Callable[[str], Optional[str]]] = None) -> str:
    runner = run if run is not None else _run_osascript
    try:
        from .watcher import find_terminal_app_bundle_for_claude_code
        bundle = find_terminal_app_bundle_for_claude_code()
    except Exception:
        bundle = None
    if bundle == TERMINAL_APP_BUNDLE_ID and tty:
        out = runner(build_terminal_focus_script(tty))
        if out is not None:
            return out.strip() or "app-only"
    if bundle:
        runner(build_app_activate_script(bundle))
        return "app-only"
    return "none"


def focus_waiting_session(run: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """Bring the waiting session to the front.

    Returns what it achieved: "matched" (that tab is now front), "app-only"
    (right app, unknown tab), or "none". `run` is injectable so tests never
    raise a real window.
    """
    runner = run if run is not None else _run_osascript
    try:
        from .watcher import find_terminal_app_bundle_for_claude_code
        bundle = find_terminal_app_bundle_for_claude_code()
    except Exception:
        bundle = None

    tty = waiting_session_tty()
    if bundle == TERMINAL_APP_BUNDLE_ID and tty:
        out = runner(build_terminal_focus_script(tty))
        if out is not None:
            return out.strip() or "app-only"
    if bundle:
        runner(build_app_activate_script(bundle))
        return "app-only"
    return "none"


def _run_osascript(script: str) -> Optional[str]:
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=5)
        return r.stdout
    except Exception as e:
        print(f"[squid-pet] focus failed: {e}", flush=True)
        return None
