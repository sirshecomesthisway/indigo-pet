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
    parser.add_argument(
        "--doctor", action="store_true",
        help="Run 6-check end-to-end self-test (process, state, launchd, "
             "window visible, not-wedged, startup log). Exit 0 = healthy, "
             "exit N = check N failed."
    )
    parser.add_argument(
        "--doctor-json", action="store_true",
        help="Like --doctor but output machine-readable JSON."
    )
    parser.add_argument(
        "--why", action="store_true",
        help="Explain the current state: which detectors fired and why."
    )
    parser.add_argument(
        "--why-json", action="store_true",
        help="Like --why but output machine-readable JSON."
    )
    args = parser.parse_args()

    if args.doctor or args.doctor_json:
        from . import doctor as _doctor
        sys.exit(_doctor.run_doctor(json_output=args.doctor_json))

    if args.why or args.why_json:
        _run_why(json_output=args.why_json)
        return


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



def _run_why(json_output: bool = False) -> None:
    """Implementation of --why and --why-json."""
    from . import watcher
    from dataclasses import asdict
    import json as _j
    import time as _t

    sm = watcher.StateMachine()
    sm.compute()        # prime CPU sampling
    _t.sleep(0.3)
    st = sm.compute()

    per_detector = []
    now = _t.time()
    for d in sm.detectors:
        # Fire all three queries FIRST so internal caches reflect this tick,
        # then capture diagnostic so the count fields match what fired.
        fired_busy = bool(d.is_busy(now))
        fired_celeb = bool(d.is_celebrating(now))
        fired_groov = bool(d.is_grooving(now))
        entry = d.diagnostic()
        entry["fired_busy"] = fired_busy
        entry["fired_celebrating"] = fired_celeb
        entry["fired_grooving"] = fired_groov
        per_detector.append(entry)

    # Pink-2026-06-29: surface the approval-alert kill switch in --why.
    # If approval_alert_enabled is False, no per_proc_idle ever triggers a
    # flag-wave -- and the user has no way to discover this without grepping
    # config.json. So plumb it through here.
    from . import config as _cfg
    try:
        procs = watcher.find_tpa_processes()
    except Exception:
        procs = []
    # Prime per-PID CPU sampling so the second call gives a real reading.
    _ = watcher.per_process_max_idle_seconds(procs) if procs else 0.0
    _t.sleep(0.3)
    per_proc_idle = (watcher.per_process_max_idle_seconds(procs)
                     if procs else 0.0)
    approval_alert = {
        "enabled": bool(_cfg.get("approval_alert_enabled", True)),
        "threshold_sec": float(_cfg.get("approval_alert_threshold_sec", 10.0)),
        "per_proc_max_idle_sec": per_proc_idle,
    }

    report = {
        "state": asdict(st),
        "detectors": per_detector,
        "approval_alert": approval_alert,
        "verdict": _explain_verdict(st, per_detector, approval_alert),
    }
    if json_output:
        print(_j.dumps(report, indent=2, default=str))
    else:
        _print_why_human(report)


def _explain_verdict(state, per_detector, approval_alert=None) -> str:
    """One-line plain-English explanation of which detector(s) caused the
    current state. Used by the --why CLI."""
    fired = [d for d in per_detector
             if d["fired_busy"] or d["fired_celebrating"] or d["fired_grooving"]]
    if state.state == "sleeping":
        return (f"sleeping because macOS HID idle = "
                f"{state.idle_seconds:.0f}s (>= 300s threshold)")
    if state.state == "idle" and not fired:
        return "idle because no detector fired and macOS is active"
    triggers = []
    for d in fired:
        kinds = []
        if d["fired_celebrating"]:
            kinds.append("celebrating")
        if d["fired_grooving"]:
            kinds.append("grooving")
        if d["fired_busy"]:
            kinds.append("busy")
        triggers.append(d["name"] + "=" + "+".join(kinds))
    if triggers:
        base = "state=" + state.state + " because " + ", ".join(triggers)
    else:
        base = "state=" + state.state
    # If the user has disabled the approval-alert toggle AND a TPA is idle
    # past threshold, call it out so they know WHY no flag is waving.
    if approval_alert and not approval_alert.get("enabled", True):
        ppi = approval_alert.get("per_proc_max_idle_sec", 0.0)
        thr = approval_alert.get("threshold_sec", 10.0)
        if ppi >= thr:
            base += (" -- NOTE: approval_alert is OFF; flag-wave would"
                     " otherwise be firing (per_proc_idle="
                     + str(ppi) + "s >= " + str(thr) + "s)")
    return base


def _print_why_human(report: dict) -> None:
    """Pretty-printed --why output for terminal use. ANSI bold + yellow."""
    BOLD = "\033[1m"
    YEL = "\033[33m"
    RST = "\033[0m"
    st = report["state"]
    print(f"squid-pet state: {BOLD}{st['state']}{RST}")
    print(f"  message:        {st['message']}")
    print(f"  cpu_percent:    {st['cpu_percent']}")
    print(f"  idle (HID):     {st['idle_seconds']}s")
    print(f"  agent_idle:        {st['agent_idle_seconds']}s")
    print(f"  TPA running:     {st['tpa_running']}")
    # Fix C (2026-06-28): show state_reason -- the one-line answer to
    # "why is she in this state right now?" without decoding CPU and
    # detector booleans by hand.
    if st.get("state_reason"):
        print(f"  WHY:            {st['state_reason']}")
    if st["concern_reason"]:
        print(f"  concern:        {st['concern_reason']} ({st['concern_severity']})")
    print()
    print("DETECTORS:")
    for d in report["detectors"]:
        on = "ON " if d["enabled"] else "off"
        fired_kinds = []
        if d["fired_busy"]:
            fired_kinds.append("busy")
        if d["fired_celebrating"]:
            fired_kinds.append("celebrating")
        if d["fired_grooving"]:
            fired_kinds.append("grooving")
        flame = f"{YEL}{','.join(fired_kinds)}{RST}" if fired_kinds else "quiet"
        print(f"  [{on}] {d['name']:<12} -> {flame}")
        for k, v in d.items():
            if k in ("name", "enabled", "fired_busy",
                     "fired_celebrating", "fired_grooving"):
                continue
            print(f"      {k}: {v}")
    print()
    # Pink-2026-06-29: surface the approval-alert kill switch so the user
    # can spot "why isn't Squid waving her flag?" without grepping config.
    aa = report.get("approval_alert") or {}
    if aa:
        toggle = "ON" if aa["enabled"] else f"{YEL}OFF{RST}"
        print(f"APPROVAL ALERT: {toggle}  "
              f"(threshold={aa['threshold_sec']}s, "
              f"per_proc_max_idle={aa['per_proc_max_idle_sec']}s)")
        if not aa["enabled"]:
            print(f"  {YEL}!! alerts are OFF -- flag-wave override disabled.{RST}")
            print("     Re-enable via tray: Bubbles -> 'Your turn' alerts.")
        print()
    print("VERDICT:", report["verdict"])


if __name__ == "__main__":
    main()

