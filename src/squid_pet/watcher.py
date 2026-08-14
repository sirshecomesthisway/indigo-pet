"""
Squid Pet Watcher — observes TPA + macOS activity and emits state.

State model:
  - idle         : nothing happening
  - thinking     : (PRIMARY) CPs sitecustomize patch wrote ~/.tpa/llm_active.flag
                  while mid-LLM-stream. Authoritative signal that the model is generating.
                  (FALLBACK) tpa CPU >= cpu_busy_threshold (default 20%) for 4+ consecutive ticks,
                  no recent tool activity. Only fires when TPA install lacks the heartbeat patch.
  - working      : tpa has shell child process, OR sustained CPU (>= cpu_busy_threshold for 4+ ticks) with recent autosave/subagent/command_history write
  - grooving     : subagent file in subagent_sessions/ modified < 30s ago
  - celebrating  : task just completed (heuristic: CPU was high then dropped to 0)
  - concerned    : recent error in errors.log
  - sleeping     : macOS idle > 5 min OR no tpa process
  - reviewing    : (V2 — needs permission prompt detection)

State is written to ~/.squid-pet/state.json every 1s, frontend polls it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import psutil

# ────────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────────
CODE_PUPPY_HOME = Path.home() / ".tpa"
PROMPT_LOG = CODE_PUPPY_HOME / "logs" / "prompt_timestamps.log"
ERRORS_LOG = CODE_PUPPY_HOME / "logs" / "errors.log"
AUTOSAVES_DIR = CODE_PUPPY_HOME / "autosaves"       # live session .pkl (THE signal)
COMMAND_HISTORY = CODE_PUPPY_HOME / "command_history.txt"  # user-typed commands
SUBAGENT_DIR = CODE_PUPPY_HOME / "subagent_sessions"

STATE_DIR = Path.home() / ".squid-pet"
STATE_FILE = STATE_DIR / "state.json"

POLL_INTERVAL_SEC = 1.0
IDLE_THRESHOLD_SEC = 300           # 5 min macOS idle → sleeping
# Auto-wake: after this long in sleeping, force one wake cycle even if
# macOS is still idle. Gives Squid a pet-like rest/wake rhythm instead of
# being a static sticker on the screen all afternoon.
AUTO_WAKE_AFTER_SLEEPING_SEC = 600   # 10 min asleep → wake for one rhythm cycle
AUTO_WAKE_DURATION_SEC = 180         # 3 min awake window (roughly one full IDLE_ROUTINE pass)
CPU_BUSY_THRESHOLD = 20.0           # % (Fix 10 2026-06-27: 15.0 -> 20.0)
TOOL_ACTIVE_WINDOW_SEC = 20        # post-e2e-polish 2026-06-27 Fix 6: was 8s; bumped to 20s. ANY tool-activity file touched within N sec -> working. Override via config tool_active_window_sec (hot-reloadable).
SUBAGENT_ACTIVE_WINDOW_SEC = 30    # subagent file touched within last N sec → grooving
# Names of transient CLI tools that indicate ACTIVE tool use.
# Excludes shells (bash/sh/zsh) because shells are always the wrapper —
# we want to detect the actual TOOL inside the shell (grep, git, etc).
# Excludes runtime hosts (python/node/npm/pip) because tpa itself
# is python and playwright keeps a long-lived node process.
# post-e2e-polish 2026-06-27 Fix 8: widened from a narrow CLI whitelist
# (which missed bash/python/node etc.) to ALSO include the bash/sh wrapper
# tpa spawns AND common language interpreters used in agentic tool
# calls. Without this, `python -m my_tool` or `npm test` ran under TPA look
# like nothing is happening and Squid drops to "thinking" mid-tool.
SHELL_CHILD_NAMES = (
    # search/file CLIs
    "rg", "grep", "find", "sed", "awk", "diff", "jq", "fd", "ag",
    "ripgrep", "ls", "cat", "tail", "head", "sort", "uniq", "wc",
    # net/git/cloud tooling
    "git", "gh", "curl", "wget", "ssh", "scp", "rsync", "kubectl",
    "gcloud", "aws", "az", "docker", "helm", "terraform",
    # build tooling
    "make", "cmake", "pytest", "uv", "pip", "cargo", "go", "mvn", "gradle",
    # language interpreters (most TPA tool calls run these)
    "python", "python3", "node", "npm", "npx", "ruby", "deno", "bun",
    # the shell wrapper itself -- if bash is alive under TPA, a tool is running
    "bash", "sh", "zsh", "fish",
    # misc
    "sleep", "tee", "xargs", "env",
)
CELEBRATE_DURATION_SEC = 20        # post-e2e-polish 2026-06-27 Fix 1: was 4
CONCERN_LOOKBACK_SEC = 60          # hard errors stay concerned this long
CONCERN_TRANSIENT_LOOKBACK_SEC = 20  # network/timeout errors auto-clear faster


# ────────────────────────────────────────────────────────────────────────
# State dataclass
# ────────────────────────────────────────────────────────────────────────
@dataclass
class PetState:
    state: str = "idle"
    sub_state: str = ""          # optional flavor text
    cpu_percent: float = 0.0
    idle_seconds: float = 0.0          # macOS HID idle (kbd/mouse system-wide)
    agent_idle_seconds: float = 0.0       # seconds since TPA last left "idle" state
    tpa_running: bool = False
    claude_code_running: bool = False
    timestamp: float = 0.0
    message: str = ""             # short caption shown under the pet
    concern_reason: str = ""      # short headline of why concerned (for tooltip)
    concern_severity: str = ""    # "transient" (network) or "hard" (code crash)
    # Fix C (2026-06-28): short human-readable explanation of WHY this
    # state fired this tick. Surfaced in `squid why` + optionally used
    # as the bubble.
    state_reason: str = ""


# ────────────────────────────────────────────────────────────────────────
# macOS idle time (no pyobjc required)
# ────────────────────────────────────────────────────────────────────────
def macos_idle_seconds() -> float:
    """Return system idle time in seconds via ioreg."""
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=2
        )
        for line in result.stdout.splitlines():
            if "HIDIdleTime" in line:
                # value is in nanoseconds
                ns = int(line.split("=")[-1].strip())
                return ns / 1_000_000_000.0
    except Exception:
        pass
    return 0.0


# ────────────────────────────────────────────────────────────────────────
# TPA process detection
# ────────────────────────────────────────────────────────────────────────
def find_tpa_processes() -> list[psutil.Process]:
    """Return all running tpa processes.

    NOTE: We deliberately DO NOT prefetch cmdline via process_iter([...])
    because psutil on macOS can raise an uncaught SystemError from
    KERN_PROCARGS2 during the bulk prefetch (per-process try/except cannot
    catch errors that fire inside process_iter's prefetch path). Fetching
    cmdline lazily inside the per-process try block isolates the failure.
    """
    matches = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            cmdline = " ".join(p.cmdline() or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied, SystemError):
            continue
        try:
            if "tpa" in cmdline or "tpa" in cmdline:
                # Filter to actual python processes, not bash wrappers
                if "python" in cmdline or "tpa" in cmdline.split("/")[-1]:
                    # post-e2e-polish 2026-06-27 Fix 9: skip headless
                    # one-shot TPA runs (daily-summary cron, doghouse pings,
                    # scripted automations). They have --prompt in argv;
                    # they are NOT interactive Pink sessions, so Squid
                    # should stay idle while they run. Pink reported
                    # "no TPA is running" while the daily summary cron
                    # was active and Squid showed "thinking" -- that
                    # confused her. Filter them out here so the entire
                    # downstream cascade (CPU, shell_active, busy)
                    # ignores them.
                    if " --prompt " in (" " + cmdline + " "):
                        continue
                    matches.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def find_claude_code_processes() -> list[psutil.Process]:
    """Return all running Claude Code CLI processes.

    NOTE: psutil's Process.name() is NOT reliable for this binary on
    macOS -- empirically verified (2026-08-14) it returns the versioned
    install path's basename (e.g. "2.1.227", from
    ~/.local/share/claude/versions/2.1.227), not "claude". `ps aux`'s
    `comm` column resolves this differently and shows "claude", which
    is what led to the wrong assumption during initial development that
    a name-match would work. `cmdline()[0]` is reliable (`["claude"]`),
    so this follows the same cmdline-based, lazily-fetched pattern as
    find_tpa_processes (see its docstring for why cmdline must
    be fetched per-process rather than via process_iter's bulk prefetch).
    """
    matches = []
    for p in psutil.process_iter(["pid"]):
        try:
            cmdline = p.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, SystemError):
            continue
        if not cmdline:
            continue
        exe = cmdline[0].rsplit("/", 1)[-1]
        if exe == "claude":
            matches.append(p)
    return matches


def aggregate_cpu(procs: list[psutil.Process]) -> float:
    """Sum CPU% across given processes (single sample, non-blocking)."""
    total = 0.0
    for p in procs:
        try:
            # cpu_percent(None) returns since-last-call; first call returns 0.
            total += p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


# ────────────────────────────────────────────────────────────────────────
# Per-process idle tracking for multi-TPA approval detection
# ────────────────────────────────────────────────────────────────────────
# When Pink has multiple TPA consoles open, the aggregate state machine
# masks per-process idleness: if TPA-A is working but TPA-B is waiting for
# approval, aggregate CPU stays high, agent_idle_seconds=0, and approval
# never fires. Track each PID's last-busy timestamp so approval can fire
# whenever ANY single TPA has been quiet past threshold.
_PER_PID_LAST_BUSY: dict[int, float] = {}
_PER_PID_EVER_BUSY: set[int] = set()
_PER_PID_BUSY_CPU_THRESHOLD = 5.0  # %, per-process (lower than aggregate)
# Pink-2026-06-30: A single tick over the CPU threshold isn't proof of
# real activity -- Python GC, prompt_toolkit redraws, and OS bookkeeping
# routinely produce one-tick blips. Require N consecutive busy ticks
# before promoting a PID into _PER_PID_EVER_BUSY. A real LLM call sustains
# CPU for many seconds; a blip does not. Streak resets on any idle tick.
_PER_PID_BUSY_STREAK: dict[int, int] = {}
_PER_PID_SUSTAINED_BUSY_TICKS = 3
# Pink-2026-06-30: Once a PID has been observed writing its awaiting_input
# flag (= we know it has the sitecustomize.py patch), the DIRECT signal is
# authoritative for that PID forever. Skip the CPU fallback entirely --
# no GC blip can falsely fire approval_needed for a patched TPA.
_PER_PID_EVER_WROTE_FLAG: set[int] = set()
# Pink-2026-06-29 follow-up: once a TPA has been waving for SNOOZE_WINDOW_SEC
# without becoming busy again (Pink "saw it and chose to defer"), drop it
# from the eligible set. It only re-fires after the TPA cycles busy -> idle
# again (= Pink replied and got a new response).
_PENDING_APPROVAL_SNOOZE_SEC = 120.0  # 2 minutes

# Pink-2026-06-30 v3: DIRECT-SIGNAL snooze. The awaiting_input flag is
# authoritative but relentless -- once written, TPA keeps it there for the
# entire duration of its idle prompt. Without a snooze, Squid would wave
# forever. Same principle as the fallback snooze: if Pink has seen the
# flag for N seconds and hasn't replied, she's chosen to defer -- quiet
# down until the TPA cycles busy again (which happens the moment she
# actually types something and TPA starts responding).
_PENDING_APPROVAL_DIRECT_SNOOZE_SEC = 120.0  # 2 minutes (Pink 2026-06-30: 5 min felt too long)

# Pink-2026-06-30 v3: birth time of each awaiting_input flag. Populated
# when we first see the flag for a PID, cleared when the flag disappears
# (Pink replied) or the PID dies. Enables the direct-signal snooze above.
_PER_PID_FLAG_FIRST_SEEN: dict[int, float] = {}

# Pink-2026-06-29 v2: DIRECT signal from TPA itself. TPA's sitecustomize.py
# touches `~/.tpa/awaiting_input/<pid>` whenever its interactive
# prompt is awaiting user input. Presence of an alive-PID file = TPA is
# asking for input RIGHT NOW. Stops the CPU-heuristic guessing entirely.
_AWAITING_INPUT_DIR = os.path.join(
    os.path.expanduser("~"), ".tpa", "awaiting_input"
)


def tpa_pids_awaiting_input() -> list[int]:
    """Return PIDs of TPA processes currently sitting at the prompt.

    Each TPA, via sitecustomize.py, writes a file `<dir>/<pid>` on entry
    to its prompt loop and deletes it on exit. We scan the dir and
    keep only files whose PIDs are still alive. Dead-PID files are
    EVICTED so a crashed TPA doesn't leave a stuck-on signal.

    Returns sorted list (deterministic for tests). Missing dir or any
    OS error -> [] (signal is best-effort; never crash the tick).
    """
    if not os.path.isdir(_AWAITING_INPUT_DIR):
        return []
    alive: list[int] = []
    try:
        names = os.listdir(_AWAITING_INPUT_DIR)
    except OSError:
        return []
    for name in names:
        # Filenames must be all-digit PIDs. Skip anything else (e.g.
        # .DS_Store, README, accidental editor swap files).
        if not name.isdigit():
            continue
        pid = int(name)
        path = os.path.join(_AWAITING_INPUT_DIR, name)
        if psutil.pid_exists(pid):
            alive.append(pid)
            # Pink-2026-06-30: This PID has proven it speaks the new
            # protocol. Trust the direct signal exclusively from now on;
            # skip the CPU fallback for this PID forever.
            _PER_PID_EVER_WROTE_FLAG.add(pid)
        else:
            # Crashed TPA -- evict the stale flag so we don't lie forever.
            try:
                os.unlink(path)
            except OSError:
                pass
            # Also drop from the trust set so a future PID reusing this
            # number isn't accidentally trusted as patched.
            _PER_PID_EVER_WROTE_FLAG.discard(pid)
    return sorted(alive)


def per_process_pending_approval_idle(
    procs: list[psutil.Process],
) -> float:
    """Idle duration for the most-stale TPA that is genuinely awaiting input.

    Stricter than `per_process_max_idle_seconds` -- a PID is only ELIGIBLE
    for approval-wave consideration when:

    1. It has been observed BUSY at least once (cpu >= threshold).
       Filters out CPs that were opened and never used.
    2. It is currently idle.
    3. Idle duration <= SNOOZE_WINDOW. Past that, Pink has clearly seen
       the wave and is choosing to defer -- the wave should quiet down
       until the TPA cycles busy -> idle again (= she replied).

    Returns the MAX idle across eligible PIDs (so a single waiting TPA
    fires regardless of what others are doing), or 0.0 if nothing is
    eligible. Threshold filtering (10s default) lives in the caller --
    we return raw idle so the caller stays in charge of policy.
    """
    now = time.time()
    live_pids: set[int] = set()
    max_idle = 0.0
    for p in procs:
        try:
            pid = p.pid
            cpu = p.cpu_percent(interval=None)
            live_pids.add(pid)
            # Pink-2026-06-30 v3: BUSY TRACKING for ALL CPs, patched or not.
            # We need _PER_PID_EVER_BUSY populated for patched CPs too --
            # the direct-signal path uses it as an "has this TPA ever been
            # engaged?" gate to suppress startup false-fires. Previously
            # patched CPs skipped this block entirely (short-circuit went
            # HERE) and _PER_PID_EVER_BUSY stayed empty for them.
            if cpu >= _PER_PID_BUSY_CPU_THRESHOLD:
                _PER_PID_LAST_BUSY[pid] = now
                _PER_PID_BUSY_STREAK[pid] = _PER_PID_BUSY_STREAK.get(pid, 0) + 1
                if _PER_PID_BUSY_STREAK[pid] >= _PER_PID_SUSTAINED_BUSY_TICKS:
                    _PER_PID_EVER_BUSY.add(pid)
            else:
                _PER_PID_BUSY_STREAK[pid] = 0
            # Pink-2026-06-30: PATCHED-TPA SHORT-CIRCUIT for fallback firing.
            # If this PID has ever written its awaiting_input flag, we
            # KNOW it has the sitecustomize.py patch. The direct signal
            # is the only path of truth for it -- skip the CPU FALLBACK
            # so GC blips can't false-fire approval_needed. Busy tracking
            # above still runs so _PER_PID_EVER_BUSY stays accurate.
            if pid in _PER_PID_EVER_WROTE_FLAG:
                continue
            if cpu >= _PER_PID_BUSY_CPU_THRESHOLD:
                # Already tracked above -- skip fallback-idle computation.
                pass
            else:
                # Streak was already reset above.
                # Two cases for the rest:
                #   a) Never observed sustained-busy -> skip (not eligible).
                #   b) Observed sustained-busy at some point -> compute
                #      idle and apply snooze window.
                if pid not in _PER_PID_EVER_BUSY:
                    continue
                last = _PER_PID_LAST_BUSY.get(pid)
                if last is None:
                    continue
                idle = now - last
                if idle > _PENDING_APPROVAL_SNOOZE_SEC:
                    # Snoozed -- wait for the next busy cycle to re-arm.
                    continue
                if idle > max_idle:
                    max_idle = idle
        except (psutil.NoSuchProcess, psutil.AccessDenied,
                AttributeError, TypeError):
            continue
    # Evict dead PIDs from all caches
    dead = set(_PER_PID_LAST_BUSY.keys()) - live_pids
    for pid in dead:
        del _PER_PID_LAST_BUSY[pid]
        _PER_PID_EVER_BUSY.discard(pid)
        _PER_PID_BUSY_STREAK.pop(pid, None)
    return round(max_idle, 1)


def snooze_all_awaiting_now() -> int:
    """Pink-2026-06-30 v3: MANUAL "calm Squid" action for the right-click menu.

    Reuses the direct-signal snooze mechanic: for every PID currently in
    _PER_PID_FLAG_FIRST_SEEN, backdate its birth time past the snooze
    window so filter_eligible_awaiting_pids will drop it on the next tick.

    The natural re-arm still works: when Pink replies (flag disappears)
    the entry is evicted, and when TPA hits its next prompt (flag
    reappears) the birth time is fresh -- so waves come back for
    genuinely new work.

    Also snoozes PIDs whose flag we haven't yet recorded (rare edge
    case: menu clicked in the same tick as a new flag appearing).

    Returns the number of PIDs snoozed, so the menu can show a hint.
    """
    now = time.time()
    stale = now - _PENDING_APPROVAL_DIRECT_SNOOZE_SEC - 1.0

    # Also cover any live flag we might have missed observing yet (the
    # scan of the awaiting dir is cheap enough to do inline).
    live = set(tpa_pids_awaiting_input())
    for pid in live:
        _PER_PID_FLAG_FIRST_SEEN[pid] = stale

    # Backdate any PIDs we're already tracking (belt-and-braces).
    count = 0
    for pid in list(_PER_PID_FLAG_FIRST_SEEN.keys()):
        _PER_PID_FLAG_FIRST_SEEN[pid] = stale
        count += 1
    return count


def count_currently_waving_pids() -> int:
    """Menu helper: how many TPA PIDs are actively waving right now
    (i.e. have a flag AND would pass the eligibility filter)?
    Used to enable/disable the 'Calm Squid' menu item."""
    try:
        raw = tpa_pids_awaiting_input()
    except Exception:
        return 0
    return len(filter_eligible_awaiting_pids(raw))


def filter_eligible_awaiting_pids(awaiting_pids: list[int]) -> list[int]:
    """Filter direct-signal awaiting_input PIDs down to those that deserve
    a flag-wave right now.

    Two gates:

    1. **ENGAGEMENT GATE.** The PID must have been observed sustained-busy
       at least once (i.e. present in _PER_PID_EVER_BUSY). Otherwise it's
       a freshly-launched TPA that wrote its flag at startup but Pink has
       never actually engaged with -- waving for it is a false fire.

    2. **DIRECT-SIGNAL SNOOZE.** Once we've been aware of the flag for
       _PENDING_APPROVAL_DIRECT_SNOOZE_SEC without the flag disappearing,
       Pink has clearly seen the wave and consciously deferred. Quiet
       down until the flag disappears (= she typed) and reappears (= TPA
       finished her request and is now waiting for the next).

    Also maintains _PER_PID_FLAG_FIRST_SEEN: records birth time for any
    new flag, evicts entries whose flag has gone away.
    """
    now = time.time()
    live_awaiting = set(awaiting_pids)

    # Evict first-seen entries whose flag has disappeared (Pink replied
    # or TPA crashed -- either way the snooze clock resets).
    for pid in [p for p in _PER_PID_FLAG_FIRST_SEEN.keys()
                if p not in live_awaiting]:
        del _PER_PID_FLAG_FIRST_SEEN[pid]

    eligible: list[int] = []
    for pid in awaiting_pids:
        # Record birth time on first sighting.
        first_seen = _PER_PID_FLAG_FIRST_SEEN.setdefault(pid, now)

        # Gate 1: engagement. Skip fresh-startup CPs.
        if pid not in _PER_PID_EVER_BUSY:
            continue

        # Gate 2: snooze. Skip stale-defer.
        if now - first_seen > _PENDING_APPROVAL_DIRECT_SNOOZE_SEC:
            continue

        eligible.append(pid)

    return eligible


def per_process_max_idle_seconds(procs: list[psutil.Process]) -> float:
    """Maximum idle duration across the given TPA processes.

    Each PID is considered "busy this tick" if its individual CPU%
    crosses _PER_PID_BUSY_CPU_THRESHOLD; otherwise its idle timer
    advances. Returns the LONGEST idle duration across all processes
    (so if ANY TPA has been quiet for 12s, this returns >=12). Dead
    PIDs are evicted from the cache.
    """
    now = time.time()
    live_pids: set[int] = set()
    max_idle = 0.0
    for p in procs:
        try:
            pid = p.pid
            cpu = p.cpu_percent(interval=None)
            live_pids.add(pid)
            if cpu >= _PER_PID_BUSY_CPU_THRESHOLD:
                _PER_PID_LAST_BUSY[pid] = now
            else:
                # First time seeing this PID idle? Treat "birth" as last-busy
                # so brand-new processes don't immediately count as idle for
                # eternity.
                _PER_PID_LAST_BUSY.setdefault(pid, now)
                idle = now - _PER_PID_LAST_BUSY[pid]
                if idle > max_idle:
                    max_idle = idle
        except (psutil.NoSuchProcess, psutil.AccessDenied,
                AttributeError, TypeError):
            # AttributeError/TypeError: test mocks sometimes inject non-Process
            # sentinels (strings, ints). Skip them rather than crashing the
            # entire watcher tick.
            continue
    # Evict dead PIDs so the dict doesn't grow forever
    dead = set(_PER_PID_LAST_BUSY.keys()) - live_pids
    for pid in dead:
        del _PER_PID_LAST_BUSY[pid]
    return round(max_idle, 1)

def file_age_sec(p: Path) -> float:
    """Seconds since file was modified. Returns large number if missing."""
    try:
        return time.time() - p.stat().st_mtime
    except (FileNotFoundError, OSError):
        return float("inf")


def newest_file_age_in_dir(d: Path, pattern: str = "*") -> float:
    """Find youngest file in dir matching pattern, return its age in seconds."""
    try:
        files = list(d.glob(pattern))
        if not files:
            return float("inf")
        return time.time() - max(f.stat().st_mtime for f in files)
    except (FileNotFoundError, OSError):
        return float("inf")


def newest_session_log_age() -> float:
    """Age of the most recently modified tpa session log."""
    return newest_file_age_in_dir(CODE_PUPPY_HOME / "logs", "log_*.txt")


# ────────────────────────────────────────────────────────────────────────
# State machine
# ────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────
# Error log parsing — extracts the last meaningful exception and classifies
# it as transient (network/timeout — usually self-heals) vs hard (real crash).
# ────────────────────────────────────────────────────────────────────────

# Patterns that suggest a transient/network problem (auto-recovers, less alarming).
_TRANSIENT_HINTS = (
    "TimeoutError", "ConnectionError", "ConnectError", "APIConnectionError",
    "ReadTimeout", "ConnectTimeout", "RemoteProtocolError",
    "Connection error", "Cancelled via cancel scope", "deadline exceeded",
    "ModelAPIError",
)
# Patterns that suggest a real code/logic problem (need your attention).
_HARD_HINTS = (
    "TypeError", "ValueError", "AttributeError", "KeyError", "IndexError",
    "NameError", "SyntaxError", "AssertionError", "RuntimeError",
    "ImportError", "ModuleNotFoundError", "ZeroDivisionError",
)

# Friendlier display labels for noisy class names.
_DISPLAY_LABELS = {
    "TimeoutError": "⏱ timeout",
    "ConnectionError": "🌐 network",
    "ConnectError": "🌐 network",
    "APIConnectionError": "🌐 API connection",
    "ReadTimeout": "⏱ read timeout",
    "ConnectTimeout": "⏱ connect timeout",
    "ModelAPIError": "🤖 LLM API error",
    "RemoteProtocolError": "🌐 protocol error",
    "TypeError": "💥 TypeError",
    "ValueError": "💥 ValueError",
    "AttributeError": "💥 AttributeError",
    "KeyError": "💥 KeyError",
    "ImportError": "💥 ImportError",
    "ModuleNotFoundError": "💥 missing module",
    "SyntaxError": "💥 SyntaxError",
    "RuntimeError": "💥 RuntimeError",
}


def parse_last_error(errors_log: Path, lookback_bytes: int = 32_000) -> tuple[str, str]:
    """Read the tail of errors.log and return (reason, severity).

    severity is "transient" if any transient hint matches, else "hard" if a
    hard hint matches, else "transient" by default (don't over-alarm).
    reason is a friendly short string for the tooltip.
    """
    try:
        size = errors_log.stat().st_size
        if size == 0:
            return ("", "")
        with open(errors_log, "rb") as f:
            f.seek(max(0, size - lookback_bytes))
            tail = f.read().decode("utf-8", errors="ignore")
    except Exception:
        return ("", "")

    # Walk through lines bottom-up looking for the most recent recognizable error.
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    transient_match = None
    hard_match = None
    for ln in reversed(lines):
        for hint in _HARD_HINTS:
            if hint in ln and not hard_match:
                hard_match = hint
                break
        for hint in _TRANSIENT_HINTS:
            if hint in ln and not transient_match:
                transient_match = hint
                break
        if hard_match and transient_match:
            break

    if hard_match:
        return (_DISPLAY_LABELS.get(hard_match, f"💥 {hard_match}"), "hard")
    if transient_match:
        return (_DISPLAY_LABELS.get(transient_match, f"⚠ {transient_match}"), "transient")
    return ("⚠ unknown error", "hard")




# ────────────────────────────────────────────────────────────────────────
# Live tool-activity detection
# Captures EITHER:
#   • Recent write to autosaves/*.pkl  → main agent ran a tool (write_file,
#     edit, search, etc.) — tpa autosaves on every turn.
#   • Recent write to subagent_sessions/*.pkl → subagent fired.
#   • Recent write to command_history.txt → user typed/submitted a prompt.
#   • Live child shell process under tpa → shell command in flight.
# Combined, these reliably distinguish "writing code / running shell"
# (working) from "thinking" (LLM call, CPU only).
# ────────────────────────────────────────────────────────────────────────

def most_recent_tool_activity_age() -> float:
    """Return seconds since the most recent tool-activity file was touched.
    Returns float('inf') if no signal found.
    """
    now = time.time()
    best = float("inf")
    candidates = []
    try:
        if AUTOSAVES_DIR.exists():
            candidates += list(AUTOSAVES_DIR.glob("*.pkl"))
        if SUBAGENT_DIR.exists():
            candidates += list(SUBAGENT_DIR.glob("*.pkl"))
        if COMMAND_HISTORY.exists():
            candidates.append(COMMAND_HISTORY)
    except Exception:
        return best
    for f in candidates:
        try:
            age = now - f.stat().st_mtime
            if age < best:
                best = age
        except Exception:
            continue
    return best


def has_active_shell_children(procs) -> bool:
    """True if any tpa process has an actively-running CLI tool
    underneath it (at ANY depth — so we catch tpa → bash → grep,
    not just direct children).

    Strict exact-name match against SHELL_CHILD_NAMES (which excludes
    shells and language runtimes — they're the wrapper, not the tool).
    """
    if not procs:
        return False
    try:
        import psutil
        for p in procs:
            try:
                # recursive=True walks grandchildren too — needed because
                # bash is the immediate child and the tool is a grandchild.
                for ch in p.children(recursive=True):
                    try:
                        name = (ch.name() or "").lower()
                        if name in SHELL_CHILD_NAMES:
                            return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        return False
    return False


def latest_shell_child_cmdline(procs) -> list[str] | None:
    """Return the cmdline of the most-recently-started shell child of any
    tpa process. Used by observer to enrich the 'working' bubble
    with the actual tool name (e.g. ['pytest', 'tests/', '-v']).

    Returns None if no shell child is active, or on any psutil error.
    The 'most recent' selection means an in-progress long-running pytest
    won't get overshadowed by a 100ms `git status` that fires mid-test.
    Actually wait, opposite: if pytest is running and a `git status` also
    runs, we'd surface git status. That's fine -- ephemeral commands
    finish fast so the bubble naturally rolls back to pytest's text on
    the next tick.
    """
    if not procs:
        return None
    try:
        candidates = []
        for p in procs:
            try:
                for ch in p.children(recursive=True):
                    try:
                        name = (ch.name() or "").lower()
                        if name in SHELL_CHILD_NAMES:
                            candidates.append((ch.create_time(), ch))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not candidates:
            return None
        # Pick the most recently started -- gives "running git push"
        # priority over a long-running pytest in the rare overlap case.
        candidates.sort(key=lambda t: t[0], reverse=True)
        _, ch = candidates[0]
        try:
            return ch.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
    except Exception:
        return None


class StateMachine:
    """
    Computes the pet's emotional state each tick by querying a list of
    pluggable detectors (TPA, Git, Terminal, IDE -- see detectors.py).

    The 9-state priority cascade is preserved: sleeping > celebrating >
    no-TPA-idle > grooving > concerned > working > thinking > post-busy
    celebrate > idle. TPA-specific richer states (working/thinking/concerned)
    are still gated on the TPA detector's signals; non-TPA detectors can
    fire celebrating/grooving/thinking via the generic OR fallback at
    the bottom of the cascade.

    State.json schema is preserved -- cpu_percent and tpa_running
    are populated from the TPADetector's public attrs.
    """

    # Settings file path (centralised so tests can monkey-patch).
    _SETTINGS_FILE = STATE_DIR / "settings.json"

    def __init__(self, detectors: list | None = None) -> None:
        # Track whether caller explicitly supplied detectors. If they did,
        # we never hot-reload (caller controls the list). If they didn't,
        # we own the list and pick up settings.json changes at runtime.
        self._owns_detectors = detectors is None
        self._settings_mtime: float = 0.0
        if detectors is None:
            settings = self._load_settings()
            from .detectors import build_detectors as _bd
            detectors = _bd(settings)
        self.detectors = list(detectors)
        self._refresh_tpa_detector_ref()

    # --- Settings load + hot-reload ----------------------------------
    def _load_settings(self) -> dict:
        """Read settings.json, update tracked mtime. Empty dict on error."""
        try:
            st = self._SETTINGS_FILE.stat()
            self._settings_mtime = st.st_mtime
            return json.loads(self._SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            self._settings_mtime = 0.0
            return {}

    def _maybe_reload_settings(self) -> None:
        """Hot-reload detectors if settings.json mtime changed.

        Called at the top of compute() every tick (~800ms). Cheap: one
        stat() syscall. Only rebuilds if we own the detector list
        (i.e. caller didn't pass one explicitly -- test contexts and
        custom embeddings keep their immutable list)."""
        if not self._owns_detectors:
            return
        try:
            mtime = self._SETTINGS_FILE.stat().st_mtime
        except OSError:
            return
        if mtime == self._settings_mtime:
            return
        # Settings changed -- rebuild detectors.
        settings = self._load_settings()
        from .detectors import build_detectors as _bd
        new_detectors = _bd(settings)
        self.detectors = list(new_detectors)
        self._refresh_tpa_detector_ref()
        enabled_names = [d.name for d in self.detectors if d.enabled]
        print(f"[squid-pet] settings.json changed -- detectors reloaded: "
              f"{enabled_names}", flush=True)

    def _refresh_tpa_detector_ref(self) -> None:
        """Re-point the TPA/Claude-Code detector caches after a detector
        list swap. Both feed the same rich working/thinking cascade in
        _compute_inner (see claude-code-detector design.md)."""
        self._tpa_detector = next(
            (d for d in self.detectors if d.name == "tpa"), None
        )
        self._claude_detector = next(
            (d for d in self.detectors if d.name == "claude_code"), None
        )
        # Sticky celebrate window (post-CPU-drop)
        self.celebrate_until = 0.0
        # post-e2e-polish 2026-06-27 Fix 7: sticky working window.
        # Hold "working" for working_hold_sec between tool calls
        # so Squid does not flicker to "thinking" in LLM-gen gaps.
        self.working_hold_until = 0.0
        # TPA-state-idle tracking: clock starts whenever state enters "idle".
        # Independent of macOS HID activity -- Pink can keep typing in Slack
        # and TPA-idle clock still ticks up.
        self._agent_idle_since: float = 0.0
        self._last_state: str = ""
        # Auto-wake bookkeeping
        self._sleeping_since: float = 0.0
        self._force_awake_until: float = 0.0
        # v0.2.1 -- "your turn" alert latch. Fires once per busy-to-idle
        # cycle when TPA is still running but idle past the threshold
        # (= probably waiting for user input).
        self._approval_alert_fired: bool = False
        self._approval_alert_at: float = 0.0


    # --- Back-compat proxies to the TPADetector state. -----------
    # External callers (tests, the upcoming `squid why` CLI) can query
    # "is the TPA detector currently busy" without poking into the
    # detector list themselves.
    @property
    def was_busy(self) -> bool:
        return self._tpa_detector._was_busy if self._tpa_detector else False

    @was_busy.setter
    def was_busy(self, value: bool) -> None:
        if self._tpa_detector is not None:
            self._tpa_detector._was_busy = bool(value)

    @property
    def busy_streak(self) -> int:
        return self._tpa_detector._busy_streak if self._tpa_detector else 0

    @busy_streak.setter
    def busy_streak(self, value: int) -> None:
        if self._tpa_detector is not None:
            self._tpa_detector._busy_streak = int(value)

    _AGENT_ACTIVE_STATES = frozenset({
        "thinking", "working", "grooving", "celebrating", "concerned"
    })

    def compute(self) -> PetState:
        """Run the cascade, then layer in agent_idle_seconds tracking.

        Hot-reloads detectors from settings.json if the file changed
        since the last tick (only when this StateMachine owns its
        detector list -- explicit lists passed in stay immutable)."""
        self._maybe_reload_settings()
        st = self._compute_inner()
        now = time.time()
        agent_active_now = st.state in self._AGENT_ACTIVE_STATES
        agent_active_prev = self._last_state in self._AGENT_ACTIVE_STATES
        if not agent_active_now:
            if agent_active_prev or self._agent_idle_since == 0.0:
                self._agent_idle_since = now
            st.agent_idle_seconds = round(now - self._agent_idle_since, 1)
        else:
            st.agent_idle_seconds = 0.0
            self._agent_idle_since = 0.0
            # Reset alert latch when TPA goes active again
            self._approval_alert_fired = False
        self._last_state = st.state

        # ── APPROVAL-NEEDED ALERT ──────────────────────────────────
        # Priority order (highest first):
        #   1. DIRECT signal: TPA's sitecustomize.py touches
        #      ~/.tpa/awaiting_input/<pid> when its prompt is
        #      awaiting input. Presence of an alive-PID flag = TPA is
        #      ASKING FOR INPUT RIGHT NOW. No CPU guessing.
        #   2. FALLBACK: per_process_pending_approval_idle for TPA
        #      versions that don't have the signal yet (or have it
        #      disabled). CPU heuristic with snooze cap.
        tpa_running_now = (self._tpa_detector.tpa_running
                          if self._tpa_detector else False)
        try:
            procs = find_tpa_processes()
        except Exception:
            procs = []
        per_proc_idle = (per_process_pending_approval_idle(procs)
                         if procs else 0.0)
        try:
            from . import config as _cfg
            _enabled = bool(_cfg.get("approval_alert_enabled", True))
            _threshold = float(_cfg.get("approval_alert_threshold_sec", 10.0))
            _sound = str(_cfg.get("approval_alert_sound", "Glass") or "")
            _text = str(_cfg.get("approval_alert_text", "your turn"))
            # Pink-2026-07-14: the CPU-heuristic fallback is OFF by default.
            # It fired approval_needed whenever a TPA went idle at its ordinary
            # ">>> " prompt after doing work -- i.e. the plain "waiting for
            # input" state, which Pink does NOT want Squid to wave for. Squid
            # now waves ONLY on the direct ask_user_question / approval signal.
            _fallback = bool(_cfg.get("approval_alert_fallback_enabled", False))
        except Exception:
            _enabled, _threshold, _sound, _text = True, 10.0, "Glass", "your turn"
            _fallback = False

        # Direct signal beats everything. No threshold, no snooze --
        # TPA explicitly said "I'm waiting on you".
        awaiting_pids_raw = tpa_pids_awaiting_input() if _enabled else []
        # Pink-2026-06-30 v3: apply engagement gate + direct-signal snooze.
        # The raw flag list is the "TPA claims to be waiting" set; the
        # eligible list is the "Pink should be nudged about it right now"
        # set. Difference matters at TPA startup (fresh flag, never engaged)
        # and after Pink has already seen the wave and deferred.
        awaiting_pids = filter_eligible_awaiting_pids(awaiting_pids_raw)
        fired_reason: str | None = None
        if awaiting_pids:
            fired_reason = ("awaiting_input flag from TPA pid(s) "
                            + ",".join(str(p) for p in awaiting_pids))
        elif (_fallback and tpa_running_now and per_proc_idle > 0
              and _enabled and per_proc_idle >= _threshold):
            fired_reason = ("approval needed ("
                            + str(int(per_proc_idle))
                            + "s per-proc idle, fallback)")

        if fired_reason is not None:
            # OVERRIDE whatever the cascade picked. approval_needed is
            # the only state that REQUIRES Pink to act, so it wins.
            st.state = "approval_needed"
            st.message = _text
            st.state_reason = fired_reason
            # Fire OS notification ONCE per idle cycle
            if not self._approval_alert_fired:
                self._approval_alert_fired = True
                self._approval_alert_at = now
                _sound_label = _sound if _sound else "off"
                print(
                    "[squid-pet] approval alert fired ("
                    + fired_reason + ", sound=" + _sound_label + ")",
                    flush=True,
                )
        else:
            # No alert is fired this tick. Reset the OS-notification latch
            # so the next genuine alert (after Pink replies + new response)
            # gets a fresh ping.
            self._approval_alert_fired = False
        # ── FORCE-STATE OVERRIDE (test/demo) ─────────────────────────
        # If ~/.squid-pet/force_state exists with a non-empty state name,
        # use it directly. Lets Pink test any state visually or take demo
        # screenshots without waiting for natural triggers. Remove the
        # file (or write empty) to resume normal computation. Highest
        # priority -- overrides every other branch including approval.
        try:
            from pathlib import Path as _P
            _force_file = _P.home() / ".squid-pet" / "force_state"
            if _force_file.exists():
                _forced = _force_file.read_text().strip()
                if _forced:
                    st.state = _forced
                    st.state_reason = "force_state override (" + _forced + ")"
        except Exception:
            pass
        return st

    def _other_detectors(self):
        """Iterator over detectors excluding TPA and ClaudeCode
        (both are promoted into the rich branch-4 cascade below instead
        of the flat OR-fallback -- excluding them here avoids double
        counting), and excluding disabled detectors."""
        return (d for d in self.detectors
                if d.name not in ("tpa", "claude_code") and d.enabled)

    def _compute_inner(self) -> PetState:
        now = time.time()
        cp = self._tpa_detector

        # Trigger one scan if we have a TPA detector (populates cpu_percent +
        # tpa_running for the state.json schema).
        if cp is not None and tpa.enabled:
            _ = tpa.is_busy(now)
            # If TPA isn't running, any leftover was_busy edge from a prior
            # session is stale -- clear it so we don't celebrate spuriously
            # next time TPA starts.
            if not tpa.tpa_running:
                tpa._was_busy = False
                tpa._celebrate_until = 0.0
            cpu = tpa.cpu_percent
            running = tpa.tpa_running
            shell_active = tpa.shell_active
            tool_activity_age = tpa.tool_activity_age
            subagent_age = tpa.subagent_age
            sustained_busy = tpa.sustained_busy
            llm_streaming = tpa.llm_streaming
            llm_flag_age = tpa.llm_flag_age
            tpa_celebrating = tpa.is_celebrating(now)
            tpa_grooving = tpa.is_grooving(now)
        else:
            cpu = 0.0
            running = False
            shell_active = False
            tool_activity_age = float("inf")
            subagent_age = float("inf")
            sustained_busy = False
            llm_streaming = False
            llm_flag_age = float("inf")
            tpa_celebrating = False
            tpa_grooving = False

        claude = self._claude_detector
        # Trigger one scan if we have a Claude Code detector (populates
        # claude_code_running for the state.json schema). Mirrors the TPA
        # block above; see claude-code-detector design.md for why this
        # detector is promoted into the rich cascade instead of the flat
        # non-TPA OR-fallback.
        if claude is not None and claude.enabled:
            _ = claude.is_busy(now)
            claude_running = claude.claude_code_running
            claude_shell_active = claude.shell_active
            claude_streaming = claude.streaming
        else:
            claude_running = False
            claude_shell_active = False
            claude_streaming = False

        # Merged signals feeding branch 4 below. tpa_running /
        # tpa.shell_active / tpa.llm_streaming keep their own TPA-only
        # meaning for state.json and the TPA-specific sub-branches (4a
        # concerned, the cpu-heuristic 4c) -- only the branches that
        # generalize cleanly are broadened.
        tpa_or_claude_running = running or claude_running
        shell_active_merged = shell_active or claude_shell_active
        streaming_merged = llm_streaming or claude_streaming

        idle = macos_idle_seconds()
        error_age = file_age_sec(ERRORS_LOG)

        # Other-detector signals (computed lazily to avoid wasted scans
        # when we exit the cascade early).
        other_busy_cache = [None]
        other_celebrating_cache = [None]
        other_grooving_cache = [None]

        def other_busy() -> bool:
            if other_busy_cache[0] is None:
                other_busy_cache[0] = any(
                    d.is_busy(now) for d in self._other_detectors()
                )
            return other_busy_cache[0]

        def other_celebrating() -> bool:
            if other_celebrating_cache[0] is None:
                other_celebrating_cache[0] = any(
                    d.is_celebrating(now) for d in self._other_detectors()
                )
            return other_celebrating_cache[0]

        def other_grooving() -> bool:
            if other_grooving_cache[0] is None:
                other_grooving_cache[0] = any(
                    d.is_grooving(now) for d in self._other_detectors()
                )
            return other_grooving_cache[0]

        st = PetState(
            cpu_percent=round(cpu, 1),
            idle_seconds=round(idle, 1),
            tpa_running=running,
            claude_code_running=claude_running,
            timestamp=now,
        )

        # ── 1. SLEEPING ── user is away.
        if idle >= IDLE_THRESHOLD_SEC:
            if self._sleeping_since == 0.0:
                self._sleeping_since = now
            sleeping_for = now - self._sleeping_since
            if sleeping_for >= AUTO_WAKE_AFTER_SLEEPING_SEC and now >= self._force_awake_until:
                self._force_awake_until = now + AUTO_WAKE_DURATION_SEC
                self._sleeping_since = 0.0
                print("[squid-pet] auto-wake: opening 3-min wake window after 10 min asleep",
                      flush=True)
            if now >= self._force_awake_until:
                st.state = "sleeping"
                st.state_reason = f"idle {int(idle // 60)}m"
                st.message = f"💤 idle {int(idle // 60)}m"
                # Stale: user is away, clear any leftover busy edge so we
                # don't fire a celebrate as soon as they come back.
                if cp is not None:
                    tpa._was_busy = False
                return st
            # else: inside wake window -- fall through to evaluate other states
        else:
            self._sleeping_since = 0.0
            self._force_awake_until = 0.0

        # Edge: TPA detector just newly armed its own celebrate window this
        # tick -- propagate to the StateMachine's celebrate_until (so the
        # sticky window survives even if the detector's value gets reset)
        # and use the distinctive "done!" message for this single tick.
        tpa_edge_fired = False
        if cp is not None and tpa._celebrate_until > self.celebrate_until:
            self.celebrate_until = tpa._celebrate_until
            tpa_edge_fired = True

        # ── 2. CELEBRATING ── sticky post-CPU-drop, or any detector says so
        if now < self.celebrate_until or tpa_celebrating or other_celebrating():
            st.state = "celebrating"
            st.state_reason = "post-busy: done" if tpa_edge_fired else "celebrating"
            st.message = "🎉 done!" if tpa_edge_fired else "🎉 nice!"
            return st

        # ── 3. GROOVING ── TPA subagent active, or any detector says so
        if tpa_grooving or other_grooving():
            st.state = "grooving"
            st.state_reason = "subagent active" if tpa_grooving else "creative burst"
            st.message = "🤸 subagent" if tpa_grooving else "🤸 creative burst"
            return st

        # ── 4. TPA OR CLAUDE CODE RUNNING -- richer working/thinking cascade ──
        # Claude Code is promoted in here (not left in the flat non-TPA
        # fallback) so it gets the same working/thinking distinction TPA
        # gets. See claude-code-detector design.md for the merge table.
        if tpa_or_claude_running:
            # 4a. CONCERNED -- recent error in TPA log. Stays TPA-only: no
            # errors.log equivalent exists for Claude Code (explicit
            # non-goal), so this must not fire from claude_running alone.
            if running and error_age != float("inf") and cpu < CPU_BUSY_THRESHOLD:
                reason, severity = parse_last_error(ERRORS_LOG)
                window = (CONCERN_TRANSIENT_LOOKBACK_SEC
                          if severity == "transient" else CONCERN_LOOKBACK_SEC)
                if error_age < window:
                    st.state = "concerned"
                    st.concern_reason = reason or " recent error"
                    st.concern_severity = severity or "hard"
                    st.state_reason = f"error: {(reason or 'recent error')[:40]}"
                    st.message = st.concern_reason
                    return st
            # post-e2e-polish 2026-06-27 Fix 6+7: config-driven windows
            try:
                from . import config as _cfg
                _tool_win = float(_cfg.get('tool_active_window_sec', TOOL_ACTIVE_WINDOW_SEC))
                _work_hold = float(_cfg.get('working_hold_sec', 25))
            except Exception:
                _tool_win = TOOL_ACTIVE_WINDOW_SEC
                _work_hold = 25.0
            # 4b. WORKING -- actively running tool / shell command.
            # Merged: fires from either TPA's or Claude Code's live
            # tool-subprocess signal.
            if shell_active_merged:
                self.working_hold_until = now + _work_hold
                st.state = "working"
                st.state_reason = ("shell child active" if shell_active
                                    else "shell child active (claude_code)")
                st.message = "🛠️ running shell"
                return st
            if sustained_busy and tool_activity_age < _tool_win:
                self.working_hold_until = now + _work_hold
                st.state = "working"
                st.state_reason = f"writing (cpu {int(cpu)}%, tool {int(tool_activity_age)}s ago)"
                st.message = f"⌨️ writing ({int(cpu)}% cpu)"
                return st
            # 4b-prime: STICKY WORKING -- LLM-gen gap, recent work + still busy.
            # Merged: without streaming_merged/shell_active_merged here, this
            # grace window never applied to Claude-only sessions (all TPA
            # fields default False/0 when TPA isn't running).
            if now < self.working_hold_until and (
                sustained_busy or cpu > 5 or streaming_merged or shell_active_merged
            ):
                st.state = "working"
                st.state_reason = f"working hold ({int(self.working_hold_until - now)}s left)"
                st.message = "✨ working"
                return st
            # 4c-prime. REAL THINKING (Fix 10b 2026-06-27) -- TPA's sitecustomize
            # has written ~/.tpa/llm_active.flag while mid-stream. This is
            # the authoritative "LLM is thinking" signal, replacing the CPU
            # heuristic. Runs AFTER 'working' checks because if a tool is actively
            # executing, that's a more useful state than 'thinking'. Merged with
            # Claude Code's transcript-write-recency signal (its own heartbeat
            # equivalent -- see design.md).
            if streaming_merged:
                st.state = "thinking"
                st.state_reason = "llm streaming" if llm_streaming else "claude streaming"
                st.message = "🤔 thinking"
                return st
            # 4c. THINKING (FALLBACK) -- CPU busy heuristic for TPA installs
            # without the sitecustomize patch. Less reliable; prone to TUI-render
            # false positives even with the 20% threshold from Fix 10.
            if sustained_busy:
                st.state = "thinking"
                st.state_reason = f"cpu busy proxy ({int(cpu)}%)"
                st.message = "🤔 thinking"
                return st
            # 4d. Post-busy celebrate -- TPADetector handles
            #     this via its own celebrate_until; surfaced via
            #     tpa_celebrating above. Nothing to do here.

        # ── 5. NON-TPA DETECTORS -- generic busy fallback ──
        if other_busy():
            st.state = "thinking"
            st.state_reason = "non-cp detector busy"
            st.message = "🤔 working"
            return st

        # ── 6. Default -- idle/watching ──
        st.state = "idle"
        st.state_reason = "cp idle, listening" if running else "no signals"
        st.message = "👂 listening" if running else "👀 watching"
        return st


# ────────────────────────────────────────────────────────────────────────
# Writer loop
# ────────────────────────────────────────────────────────────────────────


def _fire_approval_notification(text: str, sound: str) -> None:
    """Fire a macOS notification banner in a background thread.

    osascript is ~50ms so we do not block the watcher loop. Silent on
    failure (notification is supplementary; the bubble is the primary
    signal).
    """
    import subprocess, threading

    def _go():
        try:
            title = "Squid"
            body = "TPA: " + text
            sound_clause = ' sound name "' + sound + '"' if sound else ""
            script = 'display notification "' + body + '" with title "' + title + '"' + sound_clause
            subprocess.run(
                ["osascript", "-e", script],
                timeout=3,
                capture_output=True,
            )
        except Exception as e:
            print("[squid-pet] notification fire failed: " + str(e), flush=True)

    threading.Thread(target=_go, daemon=True).start()


def write_state(state: PetState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2))
    tmp.replace(STATE_FILE)


def run_watcher_loop() -> None:
    """Main watcher loop — runs forever, writes state.json every POLL_INTERVAL_SEC."""
    sm = StateMachine()
    print(f"[squid-pet] watcher started; state file: {STATE_FILE}")
    while True:
        try:
            state = sm.compute()
            write_state(state)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[squid-pet] watcher error: {e}")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    run_watcher_loop()
