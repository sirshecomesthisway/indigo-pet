#!/usr/bin/env python3
"""
claude_pet_hook.py -- squid-pet's Claude Code hook receiver.

Wired into ~/.claude/settings.json under hooks.Notification,
hooks.UserPromptSubmit, hooks.SessionEnd, and hooks.Stop. Maintains two
per-session flag-file signals that watcher.py reads:
  - "awaiting input" (claude_sessions_awaiting_input()) -- mirrors what
    TPA's own sitecustomize.py patch does via
    ~/.tpa/awaiting_input/<pid>, except keyed by session_id,
    since Claude Code hook payloads carry no PID.
  - "just finished" (claude_sessions_just_finished()) -- Pink-2026-08-27f:
    replaces the old busy->idle heuristic edge (ClaudeCodeDetector
    watching shell/file/transcript-mtime activity drop) as the
    "celebrate" trigger. That heuristic could -- and did, confirmed
    live -- flip mid-task during an ordinary >20s gap with no tool call,
    producing a false "finished with claude!" bubble while Claude was
    still actively working. The official Stop hook fires exactly when
    Claude finishes responding and hands control back, which is what
    "worth celebrating" actually means -- same fix pattern as the
    Notification-hook migration above, applied to a different signal.

Protocol:
  - Notification with notification_type in {permission_prompt, idle_prompt}
    -> write <awaiting_input_dir>/<session_id>  (Claude is waiting on you)
  - UserPromptSubmit -> remove <awaiting_input_dir>/<session_id>  (you replied)
  - SessionEnd -> remove <awaiting_input_dir>/<session_id>  (session is gone)
  - Stop -> write <finished_dir>/<session_id>  (Claude just finished a turn)

No message/transcript content is ever read or written for either signal
-- session_id and mtime only, matching this project's stated privacy
stance (see ClaudeCodeDetector's docstring on why transcript content is
never read). Stop's payload includes a last_assistant_message field;
it is deliberately ignored.

Confirmed empirically (2026-08-25/26, live Claude Code 2.1.239): the
Notification payload for both permission_prompt and idle_prompt includes
session_id, transcript_path, cwd, prompt_id, hook_event_name, message,
notification_type. UserPromptSubmit/SessionEnd/Stop are assumed to carry
session_id too (documented as a field common to every hook event) but
were NOT independently re-verified against a live payload the same way
-- that's what the always-on log below is for: check
~/.squid-pet/claude_hook.log after a real prompt-submit/session-end/stop
to confirm this script actually saw and handled them, without needing
another dedicated diagnostic pass. Also unverified: whether a Task-tool
subagent's completion fires this same top-level Stop event in addition
to its own SubagentStop (which this script does not handle) -- if the
log ever shows Stop firing implausibly often during subagent-heavy
sessions, that's the first thing to check.

Never raises past main(), never blocks Claude Code, always exits 0 -- a
bug here must not be able to interfere with normal Claude Code use. This
script deliberately has ZERO dependencies beyond the stdlib and does NOT
import the squid_pet package (the hook's execution environment has no
guarantee squid_pet is on PYTHONPATH).

Testability: the base directory (normally ~/.squid-pet) is read from the
SQUID_PET_HOME env var if set, so tests can point it at a tmp_path
without touching the real ~/.squid-pet.
"""
from __future__ import annotations

import json
import os
import sys
import time

_SQUID_PET_HOME = os.environ.get(
    "SQUID_PET_HOME", os.path.join(os.path.expanduser("~"), ".squid-pet")
)
FLAG_DIR = os.path.join(_SQUID_PET_HOME, "claude_awaiting_input")
FINISHED_DIR = os.path.join(_SQUID_PET_HOME, "claude_finished")
LOG_PATH = os.path.join(_SQUID_PET_HOME, "claude_hook.log")
_LOG_MAX_BYTES = 200_000
_LOG_KEEP_LINES = 1000

_HANDLED_NOTIFICATION_TYPES = frozenset({"permission_prompt", "idle_prompt"})
_REMOVE_ON_EVENTS = frozenset({"UserPromptSubmit", "SessionEnd"})


def _truncate_log_if_large() -> None:
    """Keep the log bounded -- this runs for the lifetime of the user's
    Claude Code usage, so an unbounded append-only file is a real risk
    over months of use."""
    try:
        if os.path.getsize(LOG_PATH) <= _LOG_MAX_BYTES:
            return
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()
        with open(LOG_PATH, "w") as f:
            f.writelines(lines[-_LOG_KEEP_LINES:])
    except OSError:
        pass


def _log(line: str) -> None:
    try:
        os.makedirs(_SQUID_PET_HOME, exist_ok=True)
        _truncate_log_if_large()
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.time():.0f} {line}\n")
    except Exception:
        pass


def _ensure_dir(path: str, event: str) -> bool:
    """mkdir -p, logging + returning False on failure so the caller can
    bail out before attempting a file op inside a dir that isn't there."""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:
        _log(f"{event} MKDIR_FAILED {e!r}")
        return False


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        _log(f"PARSE_ERROR {e!r}")
        return 0

    event = payload.get("hook_event_name", "")
    session_id = payload.get("session_id", "")
    if not session_id:
        _log(f"{event} NO_SESSION_ID")
        return 0

    if event == "Notification":
        ntype = payload.get("notification_type", "")
        if not _ensure_dir(FLAG_DIR, event):
            return 0
        flag_path = os.path.join(FLAG_DIR, session_id)
        if ntype in _HANDLED_NOTIFICATION_TYPES:
            try:
                with open(flag_path, "w") as f:
                    f.write(ntype)
                _log(f"Notification {session_id} WRITE {ntype}")
            except Exception as e:
                _log(f"Notification {session_id} WRITE_FAILED {e!r}")
        else:
            _log(f"Notification {session_id} IGNORED {ntype!r}")
    elif event in _REMOVE_ON_EVENTS:
        if not _ensure_dir(FLAG_DIR, event):
            return 0
        flag_path = os.path.join(FLAG_DIR, session_id)
        try:
            os.unlink(flag_path)
            _log(f"{event} {session_id} REMOVED")
        except FileNotFoundError:
            _log(f"{event} {session_id} NOOP (no flag)")
        except Exception as e:
            _log(f"{event} {session_id} REMOVE_FAILED {e!r}")
    elif event == "Stop":
        if not _ensure_dir(FINISHED_DIR, event):
            return 0
        finished_path = os.path.join(FINISHED_DIR, session_id)
        try:
            with open(finished_path, "w") as f:
                f.write("stop")
            _log(f"Stop {session_id} WRITE")
        except Exception as e:
            _log(f"Stop {session_id} WRITE_FAILED {e!r}")
    else:
        _log(f"UNKNOWN_EVENT {event!r} {session_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
