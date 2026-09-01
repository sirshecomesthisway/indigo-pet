#!/usr/bin/env python3
"""
squid_task_complete.py -- explicit "this task is genuinely done" signal.

Unlike claude_pet_hook.py's Stop-driven "just finished a turn" flag
(fires identically on every turn, mid-task or final -- Stop alone can't
tell those apart, and neither can any timer on top of it: ordinary reply
latency covers both cases equally), this script is invoked DELIBERATELY
by Claude itself via a shell command, only when it judges the WHOLE
task/request -- not just the turn that just ended -- complete.

watcher.py's StateMachine treats a fresh marker here as a genuine
CELEBRATING trigger, the same tier as a real git commit/push or Codex's
own busy->idle celebrate edge (see claude_task_marked_complete_recently).

Usage: python3 squid_task_complete.py
Reads the session id from $CLAUDE_CODE_SESSION_ID (falls back to
"unknown" if unset, e.g. run outside Claude Code -- still works, just
loses per-session attribution).

Writes <CLAUDE_TASK_COMPLETE_DIR>/<session_id>, mtime = now. No message/
transcript content is read or written, consistent with this project's
stated privacy stance (see claude_pet_hook.py's docstring).

Zero dependencies beyond the stdlib; never raises past main(), always
exits 0 -- a failure here must never look like a shell error to Claude.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

TASK_COMPLETE_DIR = Path.home() / ".squid-pet" / "claude_task_complete"


def main() -> int:
    try:
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "unknown")
        TASK_COMPLETE_DIR.mkdir(parents=True, exist_ok=True)
        (TASK_COMPLETE_DIR / session_id).touch()
        print(f"[squid-pet] task marked complete (session {session_id})")
    except Exception as e:
        print(f"[squid-pet] squid_task_complete failed: {e!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
