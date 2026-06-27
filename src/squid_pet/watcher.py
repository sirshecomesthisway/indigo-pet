"""
Squid Pet Watcher — observes TPA + macOS activity and emits state.

State model:
  - idle         : nothing happening
  - thinking     : tpa CPU > 5%, no recent tool activity
  - working      : tpa has shell child process, OR CPU > 5% with recent autosave/subagent/command_history write (tool calls)
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
CPU_BUSY_THRESHOLD = 15.0           # %
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
        """Re-point the TPA-detector cache after a detector list swap."""
        self._tpa_detector = next(
            (d for d in self.detectors if d.name == "tpa"), None
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
        self._last_state = st.state
        return st

    def _other_detectors(self):
        """Iterator over detectors excluding TPA (and excluding disabled)."""
        return (d for d in self.detectors
                if d.name != "tpa" and d.enabled)

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
            tpa_celebrating = tpa.is_celebrating(now)
            tpa_grooving = tpa.is_grooving(now)
        else:
            cpu = 0.0
            running = False
            shell_active = False
            tool_activity_age = float("inf")
            subagent_age = float("inf")
            sustained_busy = False
            tpa_celebrating = False
            tpa_grooving = False

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
            st.message = "🎉 done!" if tpa_edge_fired else "🎉 nice!"
            return st

        # ── 3. GROOVING ── TPA subagent active, or any detector says so
        if tpa_grooving or other_grooving():
            st.state = "grooving"
            st.message = "🤸 subagent" if tpa_grooving else "🤸 creative burst"
            return st

        # ── 4. TPA RUNNING -- richer TPA-specific cascade ──
        if running:
            # 4a. CONCERNED -- recent error in TPA log
            if error_age != float("inf") and cpu < CPU_BUSY_THRESHOLD:
                reason, severity = parse_last_error(ERRORS_LOG)
                window = (CONCERN_TRANSIENT_LOOKBACK_SEC
                          if severity == "transient" else CONCERN_LOOKBACK_SEC)
                if error_age < window:
                    st.state = "concerned"
                    st.concern_reason = reason or " recent error"
                    st.concern_severity = severity or "hard"
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
            if shell_active:
                self.working_hold_until = now + _work_hold
                st.state = "working"
                st.message = "🛠️ running shell"
                return st
            if sustained_busy and tool_activity_age < _tool_win:
                self.working_hold_until = now + _work_hold
                st.state = "working"
                st.message = f"⌨️ writing ({int(cpu)}% cpu)"
                return st
            # 4b-prime: STICKY WORKING -- LLM-gen gap, recent work + still busy
            if now < self.working_hold_until and (sustained_busy or cpu > 5):
                st.state = "working"
                st.message = "✨ working"
                return st
            # 4c. THINKING -- CPU busy but no recent work signals
            if sustained_busy:
                st.state = "thinking"
                st.message = "🤔 thinking"
                return st
            # 4d. Post-busy celebrate -- TPADetector handles
            #     this via its own celebrate_until; surfaced via
            #     tpa_celebrating above. Nothing to do here.

        # ── 5. NON-TPA DETECTORS -- generic busy fallback ──
        if other_busy():
            st.state = "thinking"
            st.message = "🤔 working"
            return st

        # ── 6. Default -- idle/watching ──
        st.state = "idle"
        st.message = "👂 listening" if running else "👀 watching"
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
