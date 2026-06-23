"""
squid doctor -- six-check end-to-end self-test.

Why this exists
---------------
The 2026-06-16 wedge proved that "process exists" is not sufficient
proof that Squid is healthy. The window can be invisibly stuck at
pywebview default (100, 100) while every process check passes. This
module verifies the actual user-visible contract: a process is
running, its state is fresh, its launchd job is registered, and most
importantly its window is actually rendered on screen at the
expected corner.

Invocation:
    squid-pet --doctor              # human-readable output, exit 0/N
    squid-pet --doctor --json       # machine-readable JSON

Exit codes:
    0 = all checks PASS
    N = check N failed (1..6; matches CHECK_ORDER ordering)

Design: each check function returns a ``CheckResult`` with passed=bool,
diagnostic text, suggested-fix hint, and a reference back to the design
doc / kennel drawer that explains the failure mode.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# ----------------------------------------------------------------------
# Filesystem paths (single source of truth -- mirrors window.py / watcher.py)
# ----------------------------------------------------------------------
STATE_DIR = Path.home() / ".squid-pet"
STATE_FILE = STATE_DIR / "state.json"
PID_FILE = STATE_DIR / "pid"
POSITION_FILE = STATE_DIR / "position.json"
STDOUT_LOG = Path("/tmp/squid-pet.out.log")

LAUNCHD_LABEL = "com.pink.squid-pet"

STATE_FRESHNESS_MAX_SEC = 5.0
WINDOW_CORNER_TOLERANCE_PX = 50.0


# ----------------------------------------------------------------------
# Result dataclass
# ----------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str
    passed: bool
    diagnostic: str
    suggested_fix: str = ""
    drawer_ref: str = ""

    def as_line(self, index: int) -> str:
        status = "PASS" if self.passed else "FAIL"
        line = f"[{index}/6] {self.name:<26} ... {status}  {self.diagnostic}"
        if not self.passed and self.suggested_fix:
            line += f"\n         fix: {self.suggested_fix}"
        return line


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------
def check_process_running(pid_path: Path = PID_FILE) -> CheckResult:
    """Check 1: a process with the saved pid is alive."""
    name = "process running"
    if not pid_path.exists():
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"no pid file at {pid_path}",
            suggested_fix="start Squid: 'python -m squid_pet' or relaunch via menu",
            drawer_ref="design.md D5",
        )
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError) as e:
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"unreadable pid file: {e}",
            suggested_fix=f"rm {pid_path}; then relaunch",
            drawer_ref="design.md D5",
        )
    try:
        os.kill(pid, 0)  # signal 0 = existence probe, no actual signal
    except (ProcessLookupError, PermissionError):
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"pid {pid} from pid file is not alive",
            suggested_fix=f"rm {pid_path}; then relaunch Squid",
            drawer_ref="design.md D5",
        )
    return CheckResult(
        name=name, passed=True,
        diagnostic=f"pid {pid} alive",
    )


def check_state_json_fresh(state_path: Path = STATE_FILE,
                           max_age_sec: float = STATE_FRESHNESS_MAX_SEC,
                           now_fn=None) -> CheckResult:
    """Check 2: state.json mtime is recent (watcher loop is alive)."""
    name = "state.json fresh"
    now = (now_fn or time.time)()
    if not state_path.exists():
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"no state file at {state_path}",
            suggested_fix="watcher thread did not start -- relaunch Squid",
            drawer_ref="design.md D5",
        )
    age = now - state_path.stat().st_mtime
    if age > max_age_sec:
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"state.json last updated {age:.1f}s ago (max {max_age_sec:.0f}s)",
            suggested_fix="watcher thread may be stalled -- relaunch Squid",
            drawer_ref="design.md D5",
        )
    return CheckResult(
        name=name, passed=True,
        diagnostic=f"updated {age:.1f}s ago",
    )


def check_launchd_loaded(label: str = LAUNCHD_LABEL) -> CheckResult:
    """Check 3: launchd has our job registered."""
    name = "launchd job loaded"
    try:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"launchctl call failed: {e}",
            suggested_fix="install LaunchAgent: see docs/STARTUP_SAFETY.md",
            drawer_ref="design.md D6",
        )
    if result.returncode != 0:
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"launchctl list {label} -> rc={result.returncode}",
            suggested_fix=f"launchctl bootstrap gui/$UID ~/Library/LaunchAgents/{label}.plist",
            drawer_ref="design.md D6",
        )
    return CheckResult(
        name=name, passed=True,
        diagnostic=f"job {label} loaded",
    )


def _get_visible_window_for_pid(pid: int) -> Optional[dict]:
    """Return CGWindowList entry for the given pid where alpha>0 and on-screen,
    or None. Returns None gracefully on non-Mac / Quartz import failure."""
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionAll,
            kCGNullWindowID,
            kCGWindowOwnerPID,
            kCGWindowAlpha,
            kCGWindowIsOnscreen,
            kCGWindowBounds,
        )
    except ImportError:
        return None
    info = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
    for w in info:
        if w.get(kCGWindowOwnerPID) != pid:
            continue
        if not w.get(kCGWindowIsOnscreen, False):
            continue
        if float(w.get(kCGWindowAlpha, 0)) <= 0.0:
            continue
        bounds = w.get(kCGWindowBounds)
        if bounds is None:
            continue
        return {
            "x": float(bounds.get("X", 0)),
            "y": float(bounds.get("Y", 0)),
            "w": float(bounds.get("Width", 0)),
            "h": float(bounds.get("Height", 0)),
            "alpha": float(w.get(kCGWindowAlpha, 0)),
        }
    return None


def check_window_visible(pid_path: Path = PID_FILE,
                          window_lookup=None) -> CheckResult:
    """Check 4: CGWindowList shows a visible window for our pid.

    This is the check that would have caught the 2026-06-16 wedge:
    the pywebview default (100,100) window was technically rendered
    but at the wrong position with alpha=1.0; the issue was that
    every other layer assumed visible = correct.
    """
    name = "window visible"
    lookup = window_lookup or _get_visible_window_for_pid
    if not pid_path.exists():
        return CheckResult(
            name=name, passed=False,
            diagnostic="no pid file -- cannot determine which window to check",
            suggested_fix="relaunch Squid",
            drawer_ref="design.md D1",
        )
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError) as e:
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"unreadable pid file: {e}",
            suggested_fix=f"rm {pid_path}; then relaunch",
            drawer_ref="design.md D1",
        )
    win = lookup(pid)
    if win is None:
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"no visible window found for pid {pid} via CGWindowList",
            suggested_fix=("the window may be wedged off-screen. "
                          "Relaunch Squid. If repeated: thread-safety regression -- "
                          "audit recent commits for NSWindow setters without "
                          "@cocoa_main_thread"),
            drawer_ref="kennel drawer 239 / commit 0d21f15",
        )
    return CheckResult(
        name=name, passed=True,
        diagnostic=f"pid {pid} window at ({win['x']:.0f},{win['y']:.0f}) "
                   f"size {win['w']:.0f}x{win['h']:.0f} alpha={win['alpha']:.2f}",
    )


def check_window_in_expected_corner(
    pid_path: Path = PID_FILE,
    position_path: Path = POSITION_FILE,
    tolerance_px: float = WINDOW_CORNER_TOLERANCE_PX,
    window_lookup=None,
    corner_origin_fn=None,
) -> CheckResult:
    """Check 5: window is NOT at the pywebview-default wedge position.

    The 2026-06-16 wedge left the window stuck at pywebview's default
    (100, 100) origin because move_to_corner silently failed when called
    from a WebKit thread. After Squid is healthy, the window is either
    at a chosen corner OR being moved by the wanderer -- but never at
    (100, 100) for more than a fraction of a second.

    We deliberately do NOT compare against position.json's saved corner
    because the wanderer legitimately moves the window all over the
    screen during routine. Comparing to that would false-positive
    constantly. Instead we check for the specific bug signature.
    """
    name = "window not wedged"
    lookup = window_lookup or _get_visible_window_for_pid
    if not pid_path.exists():
        return CheckResult(
            name=name, passed=False, diagnostic="no pid file",
            suggested_fix="relaunch Squid", drawer_ref="design.md D1",
        )
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return CheckResult(
            name=name, passed=False, diagnostic="unreadable pid file",
            suggested_fix=f"rm {pid_path}; relaunch", drawer_ref="design.md D1",
        )
    win = lookup(pid)
    if win is None:
        return CheckResult(
            name=name, passed=False,
            diagnostic="no visible window (check 4 would have caught this)",
            drawer_ref="kennel drawer 239",
        )
    PYWEBVIEW_DEFAULT_X = 100.0
    PYWEBVIEW_DEFAULT_Y = 100.0
    WEDGE_TOLERANCE_PX = 10.0
    dx = abs(win["x"] - PYWEBVIEW_DEFAULT_X)
    dy = abs(win["y"] - PYWEBVIEW_DEFAULT_Y)
    if dx <= WEDGE_TOLERANCE_PX and dy <= WEDGE_TOLERANCE_PX:
        return CheckResult(
            name=name, passed=False,
            diagnostic=(f"window at ({win['x']:.0f},{win['y']:.0f}) -- "
                       f"matches pywebview default wedge position. "
                       f"move_to_corner did not run (likely thread-safety bug)."),
            suggested_fix=("audit recent NSWindow setters for missing "
                          "@cocoa_main_thread. See kennel drawer 239 and "
                          "commit 0d21f15 for the canonical instance."),
            drawer_ref="kennel drawer 239 / commit 0d21f15",
        )
    return CheckResult(
        name=name, passed=True,
        diagnostic=(f"at ({win['x']:.0f},{win['y']:.0f}) -- not at "
                   f"pywebview default (100,100)"),
    )

REQUIRED_STARTUP_MARKERS = (
    "watcher thread started",
    "passthrough loop started",
    "routine thread started",
    "context menu ready",
    "startup complete",
)


def check_startup_log_complete(log_path: Path = STDOUT_LOG,
                                head_bytes: int = 16_000) -> CheckResult:
    """Check 6: log HEAD (first ~16KB) contains all expected startup markers.

    Startup markers fire once when Squid boots. After hours of runtime
    the log is full of per-tick lines; the startup markers are at the
    top. We read only the head of the log (small, fast) to verify the
    boot sequence completed cleanly even on long-running instances.
    """
    name = "startup log markers"
    if not log_path.exists():
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"no log file at {log_path}",
            suggested_fix="logs missing -- launchd plist may not be wiring stdout",
            drawer_ref="design.md D6",
        )
    try:
        with log_path.open("rb") as f:
            head = f.read(head_bytes)
        head_text = head.decode("utf-8", errors="replace")
    except OSError as e:
        return CheckResult(
            name=name, passed=False,
            diagnostic=f"could not read log: {e}",
            drawer_ref="design.md D6",
        )
    missing = [m for m in REQUIRED_STARTUP_MARKERS if m not in head_text]
    if missing:
        return CheckResult(
            name=name, passed=False,
            diagnostic=(f"missing startup markers in first {head_bytes} "
                       f"bytes of log: {', '.join(missing)}"),
            suggested_fix=("Squid may have started but not completed init. "
                          "Check log around the missing markers for an exception."),
            drawer_ref="design.md D5",
        )
    return CheckResult(
        name=name, passed=True,
        diagnostic=f"all {len(REQUIRED_STARTUP_MARKERS)} startup markers present",
    )

# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------
CHECK_ORDER = (
    ("process running", check_process_running),
    ("state.json fresh", check_state_json_fresh),
    ("launchd job loaded", check_launchd_loaded),
    ("window visible", check_window_visible),
    ("window not wedged", check_window_in_expected_corner),
    ("startup log markers", check_startup_log_complete),
)


def run_all_checks() -> list[CheckResult]:
    return [fn() for _name, fn in CHECK_ORDER]


def run_doctor(json_output: bool = False) -> int:
    """Run all 6 checks. Print results. Return exit code (0 = healthy)."""
    results = run_all_checks()
    if json_output:
        payload = {
            "healthy": all(r.passed for r in results),
            "checks": [asdict(r) for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        print("squid doctor -- end-to-end self-test")
        print("-" * 70)
        for i, r in enumerate(results, start=1):
            print(r.as_line(i))
        print("-" * 70)
        passed = sum(1 for r in results if r.passed)
        print(f"{passed}/{len(results)} checks passed")
    # Exit code: 0 if all pass, otherwise the 1-based index of first failure.
    for i, r in enumerate(results, start=1):
        if not r.passed:
            return i
    return 0
