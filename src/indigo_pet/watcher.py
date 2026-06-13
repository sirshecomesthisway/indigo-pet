"""
Indigo Pet Watcher — observes TPA + macOS activity and emits state.

State model:
  - idle         : nothing happening
  - thinking     : tpa CPU > 5%, no recent tool activity
  - working      : tpa has shell child process, OR CPU > 5% with recent autosave/subagent/command_history write (tool calls)
  - grooving     : subagent file in subagent_sessions/ modified < 30s ago
  - celebrating  : task just completed (heuristic: CPU was high then dropped to 0)
  - concerned    : recent error in errors.log
  - sleeping     : macOS idle > 5 min OR no tpa process
  - reviewing    : (V2 — needs permission prompt detection)

State is written to ~/.indigo-pet/state.json every 1s, frontend polls it.
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

STATE_DIR = Path.home() / ".indigo-pet"
STATE_FILE = STATE_DIR / "state.json"

POLL_INTERVAL_SEC = 1.0
IDLE_THRESHOLD_SEC = 300           # 5 min macOS idle → sleeping
CPU_BUSY_THRESHOLD = 5.0           # %
TOOL_ACTIVE_WINDOW_SEC = 8         # ANY tool-activity file touched within N sec → working
SUBAGENT_ACTIVE_WINDOW_SEC = 30    # subagent file touched within last N sec → grooving
# Names of transient CLI tools that indicate ACTIVE tool use.
# Excludes shells (bash/sh/zsh) because shells are always the wrapper —
# we want to detect the actual TOOL inside the shell (grep, git, etc).
# Excludes runtime hosts (python/node/npm/pip) because tpa itself
# is python and playwright keeps a long-lived node process.
SHELL_CHILD_NAMES = ("rg", "grep", "find", "git", "sed", "awk",
                     "curl", "wget", "tail", "head", "diff",
                     "make", "pytest", "gh", "jq", "fd", "ag",
                     "ripgrep", "ls")
CELEBRATE_DURATION_SEC = 4         # how long to hold celebrating state
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
    timestamp: float = 0.0
    message: str = ""             # short caption shown under the pet
    concern_reason: str = ""      # short headline of why concerned (for tooltip)
    concern_severity: str = ""    # "transient" (network) or "hard" (code crash)


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
    """Return all running tpa processes."""
    matches = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(p.info["cmdline"] or [])
            if "tpa" in cmdline or "tpa" in cmdline:
                # Filter to actual python processes, not bash wrappers
                if "python" in cmdline or "tpa" in cmdline.split("/")[-1]:
                    matches.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
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
# Log timestamp helpers
# ────────────────────────────────────────────────────────────────────────
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
    Computes the pet's emotional state each tick.

    Maintains a small amount of memory:
      - was_busy: whether CPU was recently busy (enables celebration detection)
      - celebrate_until: timestamp until which we should keep celebrating
    """

    def __init__(self) -> None:
        self.was_busy = False
        self.celebrate_until = 0.0
        # TPA-state-idle tracking: clock starts whenever state enters "idle"
        # (i.e. TPA has nothing to do). Resets whenever state leaves idle.
        # Independent of macOS HID activity — Pink can keep typing in Slack
        # and TPA-idle clock still ticks up.
        self._agent_idle_since: float = 0.0
        self._last_state: str = ""
        self.busy_streak = 0   # consecutive ticks with CPU >= threshold (burst suppression)
        # Prime cpu_percent so first real call returns meaningful number
        self._cpu_primed = False

    # States where TPA is actively chewing on work. Any of these should
    # reset the agent_idle counter. Everything else (idle, sleeping, etc.)
    # is "TPA is not doing anything" → agent_idle keeps ticking.
    _AGENT_ACTIVE_STATES = frozenset({
        "thinking", "working", "grooving", "celebrating", "concerned"
    })

    def compute(self) -> PetState:
        """Compute state, then layer in agent_idle_seconds tracking.
        agent_idle_seconds counts how long TPA has been continuously NOT doing
        any work — independent of whether Pink is typing in other apps OR
        whether mac HID went to sleep. Only TPA-active states reset it."""
        st = self._compute_inner()
        now = time.time()
        agent_active_now = st.state in self._AGENT_ACTIVE_STATES
        agent_active_prev = self._last_state in self._AGENT_ACTIVE_STATES
        if not agent_active_now:
            # TPA is doing nothing → agent_idle should tick.
            if agent_active_prev or self._agent_idle_since == 0.0:
                # Either just transitioned from active → idle, or first time.
                self._agent_idle_since = now
            st.agent_idle_seconds = round(now - self._agent_idle_since, 1)
        else:
            # TPA is busy → reset.
            st.agent_idle_seconds = 0.0
            self._agent_idle_since = 0.0
        self._last_state = st.state
        return st

    def _compute_inner(self) -> PetState:
        now = time.time()
        procs = find_tpa_processes()
        running = len(procs) > 0

        # Prime CPU on first call
        if not self._cpu_primed:
            aggregate_cpu(procs)
            self._cpu_primed = True
            time.sleep(0.1)

        cpu = aggregate_cpu(procs) if running else 0.0
        idle = macos_idle_seconds()

        # NEW: real tool-activity signals.
        # These replace the legacy session_log_age which referenced a stale
        # log dir that hasn't been written since the old tpa version.
        tool_activity_age = most_recent_tool_activity_age() if running else float("inf")
        shell_active = has_active_shell_children(procs) if running else False

        # Burst-suppress: typing in TUI causes brief CPU spikes. Only treat
        # CPU as "busy" if it stays >= threshold for 2+ ticks in a row.
        if cpu >= CPU_BUSY_THRESHOLD:
            self.busy_streak += 1
        else:
            self.busy_streak = 0
        sustained_busy = self.busy_streak >= 2
        session_log_age = newest_session_log_age()
        subagent_age = newest_file_age_in_dir(SUBAGENT_DIR, "*.pkl")
        error_age = file_age_sec(ERRORS_LOG)

        st = PetState(
            cpu_percent=round(cpu, 1),
            idle_seconds=round(idle, 1),
            tpa_running=running,
            timestamp=now,
        )

        # ── State priority order (highest priority wins) ──

        # 1. SLEEPING — user is away
        if idle >= IDLE_THRESHOLD_SEC:
            st.state = "sleeping"
            st.message = f"💤 idle {int(idle // 60)}m"
            self.was_busy = False
            return st

        # 2. CELEBRATING — sustained, then released
        if now < self.celebrate_until:
            st.state = "celebrating"
            st.message = "🎉 nice!"
            return st

        # 3. NO CODE PUPPY → idle (just chillin')
        if not running:
            st.state = "idle"
            st.message = "👀 watching"
            self.was_busy = False
            return st

        # 4. GROOVING — subagent active
        if subagent_age < SUBAGENT_ACTIVE_WINDOW_SEC:
            st.state = "grooving"
            st.message = "🤹 subagent"
            self.was_busy = True
            return st

        # 5. CONCERNED — recent error
        if error_age != float("inf") and cpu < CPU_BUSY_THRESHOLD:
            # Parse + classify the most recent error.
            reason, severity = parse_last_error(ERRORS_LOG)
            window = (CONCERN_TRANSIENT_LOOKBACK_SEC
                      if severity == "transient" else CONCERN_LOOKBACK_SEC)
            if error_age < window:
                st.state = "concerned"
                st.concern_reason = reason or "⚠ recent error"
                st.concern_severity = severity or "hard"
                st.message = st.concern_reason
                return st

        # 6. WORKING — actively running a tool: shell command in flight, or
        #    CPU busy with very recent autosave/subagent/command_history write.
        if shell_active:
            st.state = "working"
            st.message = "🛠 running shell"
            self.was_busy = True
            return st
        if sustained_busy and tool_activity_age < TOOL_ACTIVE_WINDOW_SEC:
            st.state = "working"
            st.message = f"⌨️ writing ({int(cpu)}% cpu)"
            self.was_busy = True
            return st

        # 7. THINKING — busy but no recent log writes (LLM call)
        if sustained_busy:
            st.state = "thinking"
            st.message = "💭 thinking"
            self.was_busy = True
            return st

        # 8. CPU dropped from busy → celebrate (task likely complete)
        if self.was_busy and cpu < 1.0:
            st.state = "celebrating"
            st.message = "🎉 done!"
            self.celebrate_until = now + CELEBRATE_DURATION_SEC
            self.was_busy = False
            return st

        # 9. Default — idle/watching
        st.state = "idle"
        st.message = "👀 listening"
        return st


# ────────────────────────────────────────────────────────────────────────
# Writer loop
# ────────────────────────────────────────────────────────────────────────
def write_state(state: PetState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2))
    tmp.replace(STATE_FILE)


def run_watcher_loop() -> None:
    """Main watcher loop — runs forever, writes state.json every POLL_INTERVAL_SEC."""
    sm = StateMachine()
    print(f"[indigo-pet] watcher started; state file: {STATE_FILE}")
    while True:
        try:
            state = sm.compute()
            write_state(state)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[indigo-pet] watcher error: {e}")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    run_watcher_loop()
