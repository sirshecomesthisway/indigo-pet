"""
Squid Pet entry point.

Usage:
    python -m squid_pet                # runs full pet (window + watcher)
    python -m squid_pet --watcher-only # just the watcher daemon, no window
    python -m squid_pet --check        # one-shot state print, then exit
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="squid-pet", description="Desktop pet for TPA")
    parser.add_argument(
        "--watcher-only", action="store_true",
        help="Run only the watcher daemon (no window). Useful for LaunchAgents."
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Compute one state snapshot and print it as JSON, then exit."
    )
    args = parser.parse_args()

    if args.check:
        from . import watcher
        sm = watcher.StateMachine()
        # Prime CPU sampling
        sm.compute()
        import time as _t; _t.sleep(0.3)
        state = sm.compute()
        from dataclasses import asdict
        import json as _j
        print(_j.dumps(asdict(state), indent=2))
        return

    if args.watcher_only:
        from . import watcher
        watcher.run_watcher_loop()
        return

    # Default: full pet (window + watcher thread)
    # SINGLETON GUARD via fcntl.flock — ATOMIC against concurrent launches.
    # Old version (check pid then write) had a race: two launches could both
    # see "no live pid" and both write their own pid before either claimed.
    # flock() in non-blocking mode is the canonical fix.
    import os as _os
    import fcntl as _fcntl
    lock_path = Path.home() / ".squid-pet" / "lock"
    pid_path  = Path.home() / ".squid-pet" / "pid"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep file handle alive for process lifetime (lock released on close/exit)
    _lock_fd = _os.open(str(lock_path), _os.O_RDWR | _os.O_CREAT, 0o644)
    try:
        _fcntl.flock(_lock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except BlockingIOError:
        existing_pid = "unknown"
        try:
            existing_pid = pid_path.read_text().strip()
        except Exception:
            pass
        print(f"[squid-pet] REFUSING TO START: another Squid is alive "
              f"(pid {existing_pid}, holds lock at {lock_path}). "
              f"Run 'squid stop' or 'squid restart' to replace her.",
              file=sys.stderr)
        sys.exit(3)
    # We hold the lock — write our pid for diagnostics
    pid_path.write_text(str(_os.getpid()))
    # Keep _lock_fd alive in module globals so lock survives main()
    globals()["_squid_singleton_lock"] = _lock_fd
    # Cleanup on exit
    import atexit as _atexit
    def _cleanup_singleton():
        try: pid_path.unlink(missing_ok=True)
        except Exception: pass
        try: _os.close(_lock_fd)
        except Exception: pass
    _atexit.register(_cleanup_singleton)

    try:
        from . import window
    except ImportError as e:
        print(f"[squid-pet] pywebview not available ({e})", file=sys.stderr)
        print(f"[squid-pet] falling back to watcher-only mode", file=sys.stderr)
        from . import watcher
        watcher.run_watcher_loop()
        return

    window.main()


if __name__ == "__main__":
    main()
