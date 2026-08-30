"""
Squid Pet Watcher — observes Claude Code / Codex activity and emits state.

State model:
  - idle         : nothing happening
  - thinking     : Claude Code / Codex transcript written recently (streaming)
  - working      : Claude Code / Codex has a live shell child, or a project
                   file was just written
  - celebrating  : Claude Code's/Codex's busy signal just dropped to idle
                   (sticky window), or GitDetector saw a fresh commit
  - sleeping     : macOS idle > 5 min
  - approval_needed : Claude Code's Notification hook (scripts/
                   claude_pet_hook.py) reports a session is waiting on you

  Pink-2026-08-27: TPA (a separate CLI coding agent this project
  originally watched) has been fully removed, including the approval-
  needed/flag-wave mechanism that used to be TPA-driven
  (~/.tpa/awaiting_input/<pid> + a CPU-idle fallback) -- Code
  Puppy was never actually installed/run on this machine, and the
  Claude-Code-native replacement (an official Notification hook) has
  been live and tested since 2026-08-26. "grooving" and "concerned" had
  no Claude Code/Codex equivalent to begin with and remain unreachable
  via natural detection (still settable via the
  ~/.squid-pet/force_state debug override for testing/demos).

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
STATE_DIR = Path.home() / ".squid-pet"
STATE_FILE = STATE_DIR / "state.json"

POLL_INTERVAL_SEC = 1.0
IDLE_THRESHOLD_SEC = 300           # 5 min macOS idle → sleeping
# Auto-wake: after this long in sleeping, force one wake cycle even if
# macOS is still idle. Gives Squid a pet-like rest/wake rhythm instead of
# being a static sticker on the screen all afternoon.
AUTO_WAKE_AFTER_SLEEPING_SEC = 600   # 10 min asleep → wake for one rhythm cycle
AUTO_WAKE_DURATION_SEC = 180         # 3 min awake window (roughly one full IDLE_ROUTINE pass)
# Names of transient CLI tools that indicate ACTIVE tool use. Shared by
# has_active_shell_children() for Claude Code / Codex's shell_active signal.
# Excludes shells (bash/sh/zsh) because shells are always the wrapper —
# we want to detect the actual TOOL inside the shell (grep, git, etc).
# Excludes runtime hosts (python/node/npm/pip) because agentic CLIs
# themselves are often python/node and playwright keeps a long-lived
# node process.
# post-e2e-polish 2026-06-27 Fix 8: widened from a narrow CLI whitelist
# (which missed bash/python/node etc.) to ALSO include the bash/sh wrapper
# and common language interpreters used in agentic tool calls. Without
# this, `python -m my_tool` or `npm test` running under the agent looks
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
    # language interpreters (most agentic tool calls run these)
    "python", "python3", "node", "npm", "npx", "ruby", "deno", "bun",
    # the shell wrapper itself -- if bash is alive under the agent, a tool is running
    "bash", "sh", "zsh", "fish",
    # misc
    "sleep", "tee", "xargs", "env",
)

# Pink-2026-08-27k: subset of SHELL_CHILD_NAMES that are wrapper shells,
# not the meaningful command -- Claude Code's actual Bash-tool invocation
# is `zsh -c 'source <shell-snapshot> ... && eval "<real command>" ...'`,
# so the FIRST shell-child match found is almost always this wrapper
# itself, whose cmdline is a huge, useless snapshot-sourcing preamble
# (confirmed live). has_active_shell_children() still counts these
# (a wrapper being alive IS evidence a tool is running underneath), but
# latest_shell_child_cmdline() below skips them and keeps walking for an
# actual reportable command instead of returning "running zsh".
SHELL_WRAPPER_NAMES = frozenset({"bash", "sh", "zsh", "fish"})


# ────────────────────────────────────────────────────────────────────────
# State dataclass
# ────────────────────────────────────────────────────────────────────────
@dataclass
class PetState:
    state: str = "idle"
    sub_state: str = ""          # optional flavor text
    idle_seconds: float = 0.0          # macOS HID idle (kbd/mouse system-wide)
    # Pink-2026-08-27: field name predates Claude Code/Codex support --
    # despite the "cp" prefix this is generic now (seconds since the
    # state machine last left an "active" state), not TPA-specific.
    # Kept as-is rather than renamed: it's a load-bearing state.json field
    # the frontend's drowsy-entry logic reads every tick.
    agent_idle_seconds: float = 0.0
    claude_code_running: bool = False
    codex_running: bool = False
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


def _find_processes_by_argv0_basename(names: frozenset[str]) -> list[psutil.Process]:
    """Return processes whose cmdline()[0] basename is in ``names``.

    Shared by find_claude_code_processes and find_codex_processes.
    NOTE: psutil's Process.name() is NOT reliable for these binaries on
    macOS -- empirically verified (2026-08-14) that it returns the
    versioned install path's basename for the `claude` CLI (e.g.
    "2.1.227", from ~/.local/share/claude/versions/2.1.227), not
    "claude" itself. `ps aux`'s `comm` column resolves this differently
    and shows the real name, which is what led to the wrong assumption
    during initial development that a name-match would work.
    `cmdline()[0]` is reliable, so matching goes through it instead --
    cmdline is fetched per-process (not via process_iter's bulk prefetch)
    because psutil can raise an uncaught SystemError from KERN_PROCARGS2
    during that bulk prefetch on macOS; the per-process try/except here
    isolates the failure to one process instead of the whole scan.
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
        if exe in names:
            matches.append(p)
    return matches


def find_claude_code_processes() -> list[psutil.Process]:
    """Return all running Claude Code CLI processes (the `claude` binary)."""
    return _find_processes_by_argv0_basename(frozenset({"claude"}))


CODEX_HEADLESS_SUBCOMMANDS = frozenset({
    "app-server", "exec", "exec-server", "mcp", "mcp-server",
})


def find_codex_processes() -> list[psutil.Process]:
    """Return all running *interactive* Codex CLI processes.

    Codex's npm distribution ships a JS shim (bin/codex.js under node)
    that resolves and spawns the platform-native Rust binary as a child
    process; the shim itself doesn't implement any business logic. We
    match the native binary's own argv[0] (codex-rs produces both a
    `codex` CLI binary and a `codex-tui` TUI binary), not the node
    wrapper -- consistent with how find_claude_code_processes matches
    the real binary rather than a name-unreliable proxy.

    Excludes headless/server-mode invocations: `codex app-server`
    (JSON-RPC/stdio server for other programs to drive Codex
    programmatically -- used by IDE extensions and remote/automation
    tooling) and `codex exec`/`exec-server`/`mcp`/`mcp-server` (one-shot
    or embedded automation, no human watching a terminal). Empirically
    motivated (2026-08-15): a third-party tool on the
    dev machine runs a vendored `codex app-server --listen stdio://` as
    a background component, which would otherwise make Squid look
    "aware" of Codex activity that has nothing to do with the user
    typing into Codex CLI themselves.
    """
    matches = _find_processes_by_argv0_basename(frozenset({"codex", "codex-tui"}))
    interactive = []
    for p in matches:
        try:
            cmdline = p.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, SystemError):
            continue
        if len(cmdline) > 1 and cmdline[1] in CODEX_HEADLESS_SUBCOMMANDS:
            continue
        interactive.append(p)
    return interactive


# Pink-2026-08-27: bundle IDs for the terminal-notifier -activate flag,
# so a clicked approval-needed notification brings the actual app hosting
# Claude Code to the front instead of a generic/unhelpful target (see
# find_terminal_app_bundle_for_claude_code's docstring for why this
# exists: plain `osascript -e 'display notification'` has no click-
# action support at all -- clicking "Show" just foregrounds whatever
# process ran the script, which macOS attributes to Script Editor,
# opening an empty window).
_TERMINAL_APP_BUNDLE_IDS = {
    "Terminal": "com.apple.Terminal",
    "iTerm2": "com.googlecode.iterm2",
    "iTerm": "com.googlecode.iterm2",
    "WezTerm": "com.github.wez.wezterm",
    "Alacritty": "org.alacritty",
    "kitty": "net.kovidgoyal.kitty",
    "Warp": "dev.warp.Warp-Stable",
    "Code": "com.microsoft.VSCode",
    "Code Helper": "com.microsoft.VSCode",
}


def find_terminal_app_bundle_for_claude_code() -> str | None:
    """Walk the parent-process chain of any running `claude` process to
    find which terminal emulator (or IDE-integrated terminal) is hosting
    it, so a notification click can activate THAT specific app.

    Best-effort and coarse: if multiple Claude Code sessions are running
    in different terminal apps, this just returns whichever is found
    first -- there's no way to know from the hook payload (no PID) which
    session actually fired the notification. Returns None if no claude
    process is found or its ancestry doesn't hit a recognized terminal
    app within a few hops (caller falls back to a plain notification
    with no working click action).
    """
    for proc in find_claude_code_processes():
        try:
            cur = proc
            depth = 0
            while cur is not None and depth < 10:
                name = cur.name()
                if name in _TERMINAL_APP_BUNDLE_IDS:
                    return _TERMINAL_APP_BUNDLE_IDS[name]
                cur = cur.parent()
                depth += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


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
# Claude Code direct-signal approval detection (Notification hook)
# ────────────────────────────────────────────────────────────────────────
# Pink-2026-08-27: this used to run alongside a parallel TPA-driven
# mechanism (~/.tpa/awaiting_input/<pid>, PID-keyed, with a CPU-
# heuristic fallback) -- fully removed. TPA was never actually
# installed/run on this machine, and this Claude-Code-native signal (fed
# by an official hook, not a monkeypatch) has been live and tested since
# 2026-08-26.
# fed by an official Claude Code hook (scripts/claude_pet_hook.py, wired
# into ~/.claude/settings.json under hooks.Notification/UserPromptSubmit/
# SessionEnd) instead of a private sitecustomize.py patch. Key difference:
# hook payloads carry a session_id, never a PID, so liveness can't be
# checked via psutil.pid_exists() -- cleanup instead relies on the hook
# itself deleting the flag on UserPromptSubmit/SessionEnd, with a
# time-based staleness prune here as a belt-and-braces fallback for a
# session that dies without firing either (e.g. a force-killed terminal).
#
# No engagement gate is needed here: a Notification event only ever
# fires mid- or post-turn, never for a freshly-opened session that has
# never done anything, so there's no "fresh session, never engaged"
# false-positive class to filter out.
CLAUDE_AWAITING_INPUT_DIR = os.path.join(
    os.path.expanduser("~"), ".squid-pet", "claude_awaiting_input"
)
CLAUDE_AWAITING_INPUT_STALE_SEC = 7200.0  # 2h -- crashed-session disk cleanup only
_CLAUDE_SESSION_SNOOZE_SEC = 120.0  # once seen & deferred this long, quiet down until it re-arms
_CLAUDE_SESSION_FLAG_FIRST_SEEN: dict[str, float] = {}


def _scan_session_flag_dir(
    dir_path: str, stale_sec: float, fresh_sec: float | None = None,
) -> list[str]:
    """Shared scan/prune logic for the session-id-keyed flag directories
    (claude_awaiting_input/, claude_finished/): list entries, skip
    dotfiles, evict (delete) anything older than stale_sec as crash-safety
    disk cleanup, and -- when fresh_sec is given -- additionally exclude
    (without deleting) entries older than fresh_sec from the returned
    list, for signals whose "still counts" window is narrower than their
    crash-safety cleanup window (e.g. claude_finished's celebrate_hold_sec
    vs its much larger stale threshold).

    Returns sorted list (deterministic for tests). Missing dir or any OS
    error -> [] (signal is best-effort; never crash the tick) -- listdir's
    own FileNotFoundError is an OSError, so a missing dir needs no
    separate pre-check."""
    now = time.time()
    live: list[str] = []
    try:
        names = os.listdir(dir_path)
    except OSError:
        return []
    for name in names:
        if name.startswith("."):
            continue
        path = os.path.join(dir_path, name)
        try:
            age = now - os.stat(path).st_mtime
        except OSError:
            continue
        if age > stale_sec:
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        if fresh_sec is not None and age > fresh_sec:
            continue
        live.append(name)
    return sorted(live)


def claude_sessions_awaiting_input() -> list[str]:
    """Return session_ids of Claude Code sessions currently awaiting input.

    scripts/claude_pet_hook.py writes `<dir>/<session_id>` on a
    Notification event (permission_prompt or idle_prompt) and removes it
    on UserPromptSubmit/SessionEnd. Entries older than
    CLAUDE_AWAITING_INPUT_STALE_SEC are evicted here as a safety net for a
    session that died without either firing (crash, force-kill).
    """
    return _scan_session_flag_dir(CLAUDE_AWAITING_INPUT_DIR, CLAUDE_AWAITING_INPUT_STALE_SEC)


# ── "just finished" flag (Pink-2026-08-27f: real Stop-hook signal) ─────
# Replaces the old busy->idle heuristic edge (ClaudeCodeDetector watching
# shell/file/transcript-mtime activity drop) as the celebrate trigger for
# Claude Code. That heuristic fired on any >20s gap with no tool call --
# a normal reasoning stretch mid-task, not a real completion -- confirmed
# live as a false "finished with claude!" bubble while still working.
# scripts/claude_pet_hook.py writes <dir>/<session_id> on the official
# Stop hook (fires exactly when Claude finishes responding and hands
# control back). Unlike claude_awaiting_input, nothing ever explicitly
# REMOVES this flag -- there's no natural "un-finished" event to hang a
# removal on -- so freshness is entirely age-based: an entry only counts
# as "just finished, worth celebrating now" within CLAUDE_FINISHED_FRESH_SEC
# of being written. Older entries are simply excluded from the live list
# (not deleted) until they cross CLAUDE_AWAITING_INPUT-style
# CLAUDE_FINISHED_STALE_SEC, at which point they're pruned as disk
# cleanup, same crash-safety pattern as the awaiting-input dir.
CLAUDE_FINISHED_DIR = os.path.join(
    os.path.expanduser("~"), ".squid-pet", "claude_finished"
)
CLAUDE_FINISHED_STALE_SEC = 7200.0  # 2h -- crashed-session disk cleanup only
CLAUDE_FINISHED_FRESH_SEC_DEFAULT = 20.0  # shared default with celebrate_hold_sec config


def claude_sessions_just_finished() -> list[str]:
    """Return session_ids of Claude Code sessions whose Stop hook fired
    within the last celebrate_hold_sec seconds (hot-reloadable config,
    same knob that controls how long the celebrating sprite-state holds
    visually -- see StateMachine._compute_inner's CELEBRATING branch).
    """
    try:
        from . import config as _cfg
        fresh_sec = float(_cfg.get("celebrate_hold_sec", CLAUDE_FINISHED_FRESH_SEC_DEFAULT))
    except Exception:
        fresh_sec = CLAUDE_FINISHED_FRESH_SEC_DEFAULT
    return _scan_session_flag_dir(CLAUDE_FINISHED_DIR, CLAUDE_FINISHED_STALE_SEC, fresh_sec)


def claude_finished_freshest_age(now: float | None = None) -> float | None:
    """Seconds since the most recently written Stop-hook flag among
    currently-fresh sessions (see claude_sessions_just_finished), or None
    if none are fresh right now.

    Pink-2026-08-30: lets StateMachine._compute_inner tell "just stopped"
    (age near 0) apart from "stopped a while ago and nothing since" (age
    approaching celebrate_hold_sec) -- the age is what distinguishes the
    GROOVING beat from the CELEBRATING beat, since Stop itself fires
    identically for both a mid-task turn and the final one.
    """
    if now is None:
        now = time.time()
    session_ids = claude_sessions_just_finished()
    if not session_ids:
        return None
    ages = []
    for sid in session_ids:
        path = os.path.join(CLAUDE_FINISHED_DIR, sid)
        try:
            ages.append(now - os.stat(path).st_mtime)
        except OSError:
            continue
    return min(ages) if ages else None


# ── "recapping" flag (Pink-2026-08-30) ──────────────────────────────────
# scripts/claude_pet_hook.py writes <dir>/<session_id> on the official
# PreCompact hook (fires right before Claude Code summarizes/compacts its
# context, whether from /compact or an automatic low-context trigger) and
# removes it on PostCompact. Pink noticed Squid flashing to a generic,
# unexplained "thinking" during a compaction and asked for it called out
# explicitly -- unlike the busy signals above, a compaction is pure
# summarization (no tool calls, no file writes), so it needs its own
# signal rather than piggybacking on shell/file/transcript activity.
CLAUDE_RECAPPING_DIR = os.path.join(
    os.path.expanduser("~"), ".squid-pet", "claude_recapping"
)
CLAUDE_RECAPPING_STALE_SEC = 7200.0  # 2h -- crash-safety disk cleanup only
# PostCompact should clear this within seconds under normal operation;
# this is only a ceiling in case it never fires (older Claude Code build,
# hook failure) so a stuck flag can't wave "recapping" forever.
CLAUDE_RECAPPING_FRESH_SEC = 120.0


def claude_sessions_recapping() -> list[str]:
    """Return session_ids of Claude Code sessions currently compacting
    (recapping) their context. See the module comment above this
    function for the PreCompact/PostCompact protocol.
    """
    return _scan_session_flag_dir(
        CLAUDE_RECAPPING_DIR, CLAUDE_RECAPPING_STALE_SEC, CLAUDE_RECAPPING_FRESH_SEC
    )


def filter_eligible_claude_sessions(session_ids: list[str]) -> list[str]:
    """Filter direct-signal Claude Code session_ids down to those that
    deserve a flag-wave right now.

    Single gate: DIRECT-SIGNAL SNOOZE, same principle as
    filter_eligible_awaiting_pids -- once a session has been waving for
    _CLAUDE_SESSION_SNOOZE_SEC without the flag disappearing, the user
    has clearly seen it and consciously deferred. Quiet down until the
    flag disappears (they replied) and reappears (a fresh prompt/
    permission wait) with a new birth-time clock.

    Also maintains _CLAUDE_SESSION_FLAG_FIRST_SEEN: records birth time for
    any new flag, evicts entries whose flag has gone away.
    """
    now = time.time()
    live = set(session_ids)

    for sid in [s for s in _CLAUDE_SESSION_FLAG_FIRST_SEEN.keys()
                if s not in live]:
        del _CLAUDE_SESSION_FLAG_FIRST_SEEN[sid]

    eligible: list[str] = []
    for sid in session_ids:
        first_seen = _CLAUDE_SESSION_FLAG_FIRST_SEEN.setdefault(sid, now)
        if now - first_seen > _CLAUDE_SESSION_SNOOZE_SEC:
            continue
        eligible.append(sid)

    return eligible


def snooze_all_awaiting_now() -> int:
    """Pink-2026-06-30 v3 / 2026-08-27: MANUAL "calm Squid" action for the
    right-click menu. Backdates every currently-tracked session's birth
    time past the snooze window so filter_eligible_claude_sessions will
    drop it on the next tick.

    The natural re-arm still works: when you reply (flag disappears) the
    entry is evicted, and when the session hits its next wait (flag
    reappears) the birth time is fresh -- so waves come back for
    genuinely new work.

    Also snoozes sessions whose flag we haven't yet recorded (rare edge
    case: menu clicked in the same tick as a new flag appearing).

    Returns the number of sessions snoozed, so the menu can show a hint.
    """
    now = time.time()
    claude_stale = now - _CLAUDE_SESSION_SNOOZE_SEC - 1.0

    # Also cover any live flag we might have missed observing yet (the
    # scan of the awaiting dir is cheap enough to do inline).
    live_sessions = set(claude_sessions_awaiting_input())
    for sid in live_sessions:
        _CLAUDE_SESSION_FLAG_FIRST_SEEN[sid] = claude_stale

    # Backdate everything we're already tracking (belt-and-braces).
    count = 0
    for sid in list(_CLAUDE_SESSION_FLAG_FIRST_SEEN.keys()):
        _CLAUDE_SESSION_FLAG_FIRST_SEEN[sid] = claude_stale
        count += 1
    return count


def count_currently_waving_sessions() -> int:
    """Menu helper: how many Claude Code sessions are actively waving
    right now (i.e. have a flag AND would pass the eligibility filter)?
    Used to enable/disable the 'Calm Squid' menu item."""
    try:
        raw_sessions = claude_sessions_awaiting_input()
    except Exception:
        raw_sessions = []
    return len(filter_eligible_claude_sessions(raw_sessions))


# ────────────────────────────────────────────────────────────────────────
# State machine
# ────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────
# Live tool-activity detection
# ────────────────────────────────────────────────────────────────────────

def has_active_shell_children(procs) -> bool:
    """True if any of the given processes has an actively-running CLI
    tool underneath it (at ANY depth — so we catch agent → bash → grep,
    not just direct children). Shared by ClaudeCodeDetector and
    CodexDetector for their shell_active signal.

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
    """Pink-2026-08-27k: cmdline of the first actively-running CLI tool
    found under any of the given processes -- same SHELL_CHILD_NAMES /
    recursive-children walk as has_active_shell_children, but returns
    the actual command instead of just a bool, so a "still working"
    reannounce can say "running pytest" instead of staying silent.
    Replaces the removed TPA-only latest_shell_child_cmdline that
    fed this same feature before TPA was pulled out.

    Skips SHELL_WRAPPER_NAMES matches (bash/sh/zsh/fish) -- see that
    constant's comment: Claude Code's actual Bash-tool invocation wraps
    everything in a shell-snapshot-sourcing preamble, so the wrapper
    shell is almost always the FIRST match in the walk, and its own
    cmdline is a long, useless housekeeping string, not the real command.
    Confirmed live. Keeps walking past it for an actual reportable tool.

    Any error (including a fake/test process object with no .children())
    -> None, matching has_active_shell_children's best-effort contract.
    """
    if not procs:
        return None
    try:
        import psutil
        for p in procs:
            try:
                for ch in p.children(recursive=True):
                    try:
                        name = (ch.name() or "").lower()
                        if name in SHELL_WRAPPER_NAMES:
                            continue
                        if name in SHELL_CHILD_NAMES:
                            cmdline = ch.cmdline()
                            if cmdline:
                                return cmdline
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        return None
    return None


class StateMachine:
    """
    Computes the pet's emotional state each tick by querying a list of
    pluggable detectors (ClaudeCode, Codex, Git, Terminal, IDE -- see
    detectors.py).

    Priority cascade: sleeping > celebrating > grooving > working >
    thinking > idle. Claude Code / Codex are promoted into the rich
    working/thinking cascade; other detectors fire celebrating/grooving/
    thinking via the generic OR fallback at the bottom of the cascade.
    approval_needed (Claude Code's Notification hook) can override any
    of the above -- see that block below.
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
        """Re-point the Claude-Code/Codex/Git detector caches after a
        detector list swap. Claude/Codex feed the same rich
        working/thinking cascade in _compute_inner (see
        claude-code-detector and codex-detector design docs); Git is
        cached here too so PetApi.update()'s LLM-bubble context
        enrichment (window.py) can read live git-activity signal
        without walking self.detectors itself on every state change."""
        self._claude_detector = next(
            (d for d in self.detectors if d.name == "claude_code"), None
        )
        self._codex_detector = next(
            (d for d in self.detectors if d.name == "codex"), None
        )
        self._git_detector = next(
            (d for d in self.detectors if d.name == "git"), None
        )
        # Sticky celebrate window (post-CPU-drop)
        self.celebrate_until = 0.0
        # post-e2e-polish 2026-06-27 Fix 7: sticky working window.
        # Hold "working" for working_hold_sec between tool calls
        # so Squid does not flicker to "thinking" in LLM-gen gaps.
        self.working_hold_until = 0.0
        # agent_idle tracking: clock starts whenever state enters "idle".
        # Independent of macOS HID activity -- you can keep typing in Slack
        # and this clock still ticks up. (Field/method names keep the "cp"
        # prefix for state.json schema stability; the tracking itself is
        # generic, not TPA-specific -- see PetState.agent_idle_seconds.)
        self._agent_idle_since: float = 0.0
        self._last_state: str = ""
        # Auto-wake bookkeeping
        self._sleeping_since: float = 0.0
        self._force_awake_until: float = 0.0
        # v0.2.1 -- "your turn" alert latch. Fires once per approval_needed
        # episode (see the approval-needed block in compute()).
        self._approval_alert_fired: bool = False
        self._approval_alert_at: float = 0.0


    _AGENT_ACTIVE_STATES = frozenset({
        "thinking", "working", "grooving", "celebrating", "concerned"
    })

    def compute(self, *, notify: bool = True) -> PetState:
        """Run the cascade, then layer in agent_idle_seconds tracking.

        Hot-reloads detectors from settings.json if the file changed
        since the last tick (only when this StateMachine owns its
        detector list -- explicit lists passed in stay immutable).

        notify=False skips firing the real OS approval notification --
        for read-only diagnostic callers (squid why) that need to call
        compute() to prime detector caches without causing side effects.
        """
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

        awaiting_sessions_raw = claude_sessions_awaiting_input()

        # ── STALE-FLAG SELF-HEAL ─────────────────────────────────────
        # Pink-2026-08-27, real bug caught via live use: Claude Code's
        # Notification hook fires permission_prompt/idle_prompt and we
        # latch a flag file, but if Claude resumes work WITHOUT the user
        # submitting a fresh top-level prompt (approval granted some
        # other way, auto-mode proceeding on its own, a multi-step
        # agentic task continuing unattended), UserPromptSubmit/
        # SessionEnd never fires to clear it -- the flag (and the wave +
        # OS notification) stays stuck showing "your turn" even while
        # you're actively watching it work.
        #
        # Self-heal: our OWN independently-verified activity signal
        # (st.state == working/thinking, from real shell/file/streaming
        # evidence -- nothing to do with the hook) is proof any pending
        # wait has been resolved, regardless of which mechanism resolved
        # it. Coarse -- aggregate across all Claude Code processes, not
        # per-session, since the hook payload carries no PID to
        # disambiguate which session is the one now active -- but far
        # better than trusting a hook event that may simply never fire.
        #
        # awaiting_sessions_raw is only re-scanned from disk (a second
        # syscall) when self-heal actually had something to clean up --
        # the common case (nothing awaiting, or not working/thinking)
        # needs just the one scan above. The re-scan itself is NOT
        # optional when self-heal does run: individual os.unlink calls
        # below can silently fail (caught per-file), so re-deriving from
        # disk -- rather than assuming the whole loop succeeded and
        # setting the list to [] -- is what keeps the APPROVAL-NEEDED
        # block below correct if some entries didn't actually clear.
        if st.state in ("working", "thinking") and awaiting_sessions_raw:
            try:
                for sid in awaiting_sessions_raw:
                    try:
                        os.unlink(os.path.join(CLAUDE_AWAITING_INPUT_DIR, sid))
                    except OSError:
                        pass
                    _CLAUDE_SESSION_FLAG_FIRST_SEEN.pop(sid, None)
            except Exception:
                pass
            awaiting_sessions_raw = claude_sessions_awaiting_input()

        # ── APPROVAL-NEEDED ALERT ──────────────────────────────────
        # DIRECT signal: Claude Code's own Notification hook (scripts/
        # claude_pet_hook.py) writes ~/.squid-pet/claude_awaiting_input/
        # <session_id> when a session is asking for input RIGHT NOW.
        # No CPU guessing, no fallback -- this is the only path.
        try:
            from . import config as _cfg
            _enabled = bool(_cfg.get("approval_alert_enabled", True))
            _sound = str(_cfg.get("approval_alert_sound", "Glass") or "")
            _text = str(_cfg.get("approval_alert_text", "your turn"))
        except Exception:
            _enabled, _sound, _text = True, "Glass", "your turn"

        # Pink-2026-08-26: no engagement gate needed (see
        # filter_eligible_claude_sessions's docstring for why).
        awaiting_sessions = filter_eligible_claude_sessions(
            awaiting_sessions_raw if _enabled else []
        )
        fired_reason: str | None = None
        if awaiting_sessions:
            fired_reason = ("awaiting_input flag from Claude Code session(s) "
                            + ",".join(awaiting_sessions))

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
                if notify:
                    _fire_approval_notification(_text, _sound)
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
        """Iterator over detectors excluding ClaudeCode and Codex (both
        are promoted into the rich branch-4 cascade below instead of the
        flat OR-fallback -- excluding them here avoids double counting),
        and excluding disabled detectors."""
        return (d for d in self.detectors
                if d.name not in ("claude_code", "codex")
                and d.enabled)

    def _compute_inner(self) -> PetState:
        now = time.time()

        claude = self._claude_detector
        # Trigger one scan if we have a Claude Code detector (populates
        # claude_code_running for the state.json schema); see
        # claude-code-detector design.md for why this detector is
        # promoted into the rich cascade instead of the flat generic
        # OR-fallback.
        if claude is not None and claude.enabled:
            _ = claude.is_busy(now)
            claude_running = claude.claude_code_running
            claude_shell_active = claude.shell_active
            claude_file_active = claude.file_active
            claude_streaming = claude.streaming
            # Pink-2026-08-27f: was claude.is_celebrating(now) -- the
            # detector's own busy->idle heuristic edge (shell/file/
            # transcript-mtime activity dropping). Replaced with the
            # real Stop-hook signal (claude_sessions_just_finished()):
            # the heuristic fired on any >20s gap with no tool call, a
            # normal mid-task reasoning stretch, not a real completion --
            # confirmed live as a false "finished with claude!" bubble
            # while Claude was still actively working. Stop fires
            # exactly when Claude finishes responding and hands control
            # back, same fix pattern as the approval_needed migration off
            # CPU heuristics onto the Notification hook.
            #
            # Pink-2026-08-30: Stop fires identically on every turn, mid-
            # task or truly final (confirmed live via claude_hook.log --
            # this very session's Stop flag rewrote a dozen+ times across
            # one long conversation), so raw freshness alone can't tell
            # "made progress" from "actually done". claude_finished_age
            # is that Stop flag's age; below it feeds a settle window
            # (claude_groove_settle_sec) that decides GROOVING vs
            # CELEBRATING -- see branches 2/3.
            claude_finished_age = claude_finished_freshest_age(now)
        else:
            claude_running = False
            claude_shell_active = False
            claude_file_active = False
            claude_streaming = False
            claude_finished_age = None

        codex = self._codex_detector
        # Same pattern as the Claude Code block -- see codex-detector
        # design.md.
        if codex is not None and codex.enabled:
            _ = codex.is_busy(now)
            codex_running = codex.codex_running
            codex_shell_active = codex.shell_active
            codex_file_active = codex.file_active
            codex_streaming = codex.streaming
            codex_celebrating = codex.is_celebrating(now)
        else:
            codex_running = False
            codex_shell_active = False
            codex_file_active = False
            codex_streaming = False
            codex_celebrating = False

        # Merged signals feeding branch 4 below.
        #
        # working_evidence_merged covers two kinds of "hard" evidence a
        # tool is actually running: a live subprocess (shell_active,
        # Bash-tool-style calls) and a recent project-file write
        # (file_active, catches in-process Edit/Write/apply_patch calls
        # that never spawn a subprocess -- see ClaudeCodeDetector's
        # docstring for the bug report that motivated this signal).
        any_agent_running = claude_running or codex_running
        working_evidence_merged = (
            claude_shell_active or codex_shell_active
            or claude_file_active or codex_file_active
        )
        streaming_merged = claude_streaming or codex_streaming

        def _working_reason() -> str:
            """Which agent's hard evidence (shell/file) earns credit in
            state_reason, priority order. Only called once
            working_evidence_merged is already known True."""
            if claude_shell_active:
                return "shell child active (claude_code)"
            if codex_shell_active:
                return "shell child active (codex)"
            if claude_file_active:
                return "file write detected (claude_code)"
            if codex_file_active:
                return "file write detected (codex)"
            return "shell child active"  # unreachable; keeps mypy/readers happy

        def _streaming_reason() -> str:
            """Which agent's streaming signal earns credit. Only called
            once streaming_merged is already known True."""
            if claude_streaming:
                return "claude streaming"
            return "codex streaming"

        idle = macos_idle_seconds()

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
            # Cache holds (fired: bool, name: str|None) once computed --
            # one cell for the whole result instead of two parallel cells
            # that had to be kept in sync.
            if other_celebrating_cache[0] is None:
                fired, name = False, None
                for d in self._other_detectors():
                    if d.is_celebrating(now):
                        fired, name = True, d.name
                        break
                other_celebrating_cache[0] = (fired, name)
            return other_celebrating_cache[0][0]

        def other_celebrating_name() -> str | None:
            """Only meaningful after other_celebrating() has actually run
            -- if the CELEBRATING branch's `or` short-circuited before
            reaching it (e.g. a manually-armed celebrate_until), the
            cache is still empty and there's no "other" name to report."""
            return other_celebrating_cache[0][1] if other_celebrating_cache[0] else None

        def other_grooving() -> bool:
            if other_grooving_cache[0] is None:
                other_grooving_cache[0] = any(
                    d.is_grooving(now) for d in self._other_detectors()
                )
            return other_grooving_cache[0]

        st = PetState(
            idle_seconds=round(idle, 1),
            claude_code_running=claude_running,
            codex_running=codex_running,
            timestamp=now,
        )

        # ── 1. SLEEPING ── user is away AND the agent isn't actively busy.
        # Pink-2026-08-27g: sleeping used to override EVERYTHING
        # unconditionally ("regardless of any other signal", including
        # active agent work). A user caught this looking wrong live:
        # squid showed sleeping while Claude Code was actively working
        # in the background, because the user had stepped away from the
        # keyboard for 5+ minutes (easy to happen mid-session). Sleeping
        # is about USER presence; working/thinking are about AGENT
        # activity -- when the agent is genuinely busy, that should win,
        # so squid doesn't misleadingly "doze" through real work. Reuses
        # the exact same merged signals branch 4 below uses to decide
        # working/thinking, so this can never disagree with what the
        # rest of the cascade would call "busy".
        agent_actively_busy = working_evidence_merged or streaming_merged
        if idle >= IDLE_THRESHOLD_SEC and not agent_actively_busy:
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
                return st
            # else: inside wake window -- fall through to evaluate other states
        else:
            self._sleeping_since = 0.0
            self._force_awake_until = 0.0

        # claude_finished_age (set above) can't by itself tell "finished
        # this turn, more coming" from "finished the whole task" -- Stop
        # fires identically either way (confirmed live via
        # claude_hook.log). Two more signals turn it into a decision:
        #
        #   - claude_resumed_work: shell/file evidence that Claude has
        #     already started doing something new since that Stop. If
        #     so, the turn that just ended was clearly mid-task -- don't
        #     show either beat, let branch 4 report WORKING/THINKING.
        #   - claude_groove_settle_sec (config, default 8s): how long a
        #     Stop gets the benefit of the doubt as GROOVING before, with
        #     still no resumed work, it's treated as the last turn and
        #     promoted to CELEBRATING. Must stay under celebrate_hold_sec
        #     (the outer bound after which the Stop flag stops counting
        #     as fresh at all, checked inside claude_finished_age).
        try:
            from . import config as _cfg
            _groove_settle_sec = float(_cfg.get('claude_groove_settle_sec', 8))
        except Exception:
            _groove_settle_sec = 8.0
        claude_resumed_work = claude_shell_active or claude_file_active
        claude_just_stopped = claude_finished_age is not None and not claude_resumed_work
        claude_settled_celebrating = claude_just_stopped and claude_finished_age >= _groove_settle_sec
        claude_grooving_now = claude_just_stopped and claude_finished_age < _groove_settle_sec

        # ── 2. CELEBRATING ── real milestones: claude_settled_celebrating
        # (Stop fired and nothing resumed for claude_groove_settle_sec --
        # see above), codex's own busy->idle edge, any other detector's
        # (e.g. Git's fresh-commit) celebrate signal, or a manually-armed
        # self.celebrate_until (force_state / test hook).
        if (
            now < self.celebrate_until
            or claude_settled_celebrating
            or codex_celebrating
            or other_celebrating()
        ):
            st.state = "celebrating"
            if claude_settled_celebrating:
                st.state_reason = "claude celebrating"
            elif codex_celebrating:
                st.state_reason = "codex celebrating"
            elif other_celebrating_name():
                st.state_reason = f"{other_celebrating_name()} celebrating"
            else:
                st.state_reason = "celebrating"
            st.message = "🎉 nice!"
            return st

        # ── 3. GROOVING ── a lighter, per-turn "still making progress"
        # beat. claude_grooving_now (Stop just fired, within
        # claude_groove_settle_sec, nothing resumed yet -- see above)
        # fires this; any other detector's is_grooving() also lands here.
        if claude_grooving_now or other_grooving():
            st.state = "grooving"
            st.state_reason = "claude grooving" if claude_grooving_now else "creative burst"
            st.message = "🤸 creative burst"
            return st

        # ── 3b. RECAPPING ── Claude Code's PreCompact hook fired and
        # PostCompact hasn't cleared it yet -- Claude is summarizing its
        # own context, not doing agent work (no tool calls happen during
        # a compaction). Reported as a flavor of "thinking" -- see
        # claude_sessions_recapping()'s module comment -- so it wins over
        # branch 4 below even if stale file-write evidence from just
        # before the compact started is still technically "fresh".
        if claude is not None and claude.enabled and claude_sessions_recapping():
            st.state = "thinking"
            st.state_reason = "claude recapping"
            st.message = "📝 recapping..."
            return st

        # ── 4. CLAUDE CODE OR CODEX RUNNING -- richer working/thinking
        # cascade. See claude-code-detector and codex-detector design
        # docs for the merge table.
        if any_agent_running:
            # post-e2e-polish 2026-06-27 Fix 7: config-driven hold window
            try:
                from . import config as _cfg
                _work_hold = float(_cfg.get('working_hold_sec', 25))
            except Exception:
                _work_hold = 25.0
            # 4a. WORKING -- actively running tool / shell command, or a
            # project file was just written (catches in-process
            # Edit/Write/apply_patch calls that never spawn a
            # subprocess). Merged across Claude Code and Codex.
            if working_evidence_merged:
                self.working_hold_until = now + _work_hold
                st.state = "working"
                st.state_reason = _working_reason()
                st.message = "🛠️ running shell"
                return st
            # 4a-prime: STICKY WORKING -- LLM-gen gap, recent work + still busy.
            if now < self.working_hold_until and (
                streaming_merged or working_evidence_merged
            ):
                st.state = "working"
                st.state_reason = f"working hold ({int(self.working_hold_until - now)}s left)"
                st.message = "✨ working"
                return st
            # 4b. THINKING -- Claude Code's / Codex's transcript-write-
            # recency signal (their heartbeat equivalent -- see design docs).
            if streaming_merged:
                st.state = "thinking"
                st.state_reason = _streaming_reason()
                st.message = "🤔 thinking"
                return st

        # ── 5. OTHER DETECTORS -- generic busy fallback ──
        if other_busy():
            st.state = "thinking"
            st.state_reason = "non-agent detector busy"
            st.message = "🤔 working"
            return st

        # ── 6. Default -- idle/watching ──
        st.state = "idle"
        st.state_reason = "no signals"
        st.message = "👀 watching"
        return st


# ────────────────────────────────────────────────────────────────────────
# Writer loop
# ────────────────────────────────────────────────────────────────────────


def _applescript_escape(s: str) -> str:
    """Escape a string for safe interpolation inside an AppleScript
    double-quoted literal. text/sound below come from the user's own
    ~/.squid-pet/config.json (approval_alert_text/approval_alert_sound)
    -- local and not attacker-controlled today, but interpolating them
    unescaped means a stray `"` (or `\\`) in a config value would break
    out of the string and inject arbitrary AppleScript. Escape rather
    than trust the source stays benign forever."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _fire_approval_notification(text: str, sound: str, source_label: str = "Claude Code") -> None:
    """Fire a macOS notification banner in a background thread.

    Runs in ~50ms so we do not block the watcher loop. Silent on failure
    (notification is supplementary; the bubble is the primary signal).

    source_label names which agent is actually waiting. Only Claude Code
    calls this today (see StateMachine.compute()'s approval block); kept
    as a parameter rather than a hardcoded string so a future agent-
    specific direct signal (e.g. Codex) can reuse this function without
    lying about the source -- Pink-2026-08-26 found a real bug where this
    was hardcoded to "TPA" unconditionally, so a Claude Code
    session firing this alert showed a banner reading "TPA: your
    turn" even though TPA was never running.

    Pink-2026-08-27: prefers `terminal-notifier` (if installed) over
    plain `osascript -e 'display notification'`, because the latter has
    NO click-action support -- clicking "Show" just foregrounds whatever
    process ran the AppleScript, which macOS attributes generically to
    Script Editor, opening an empty window (a real, confusing bug caught
    via live use). terminal-notifier's `-activate <bundle-id>` makes
    "Show" bring the actual terminal app hosting Claude Code to the
    front instead. Falls back to the old osascript-only behavior if
    terminal-notifier isn't installed (`brew install terminal-notifier`).
    """
    import shutil
    import subprocess
    import threading

    def _go():
        title = "Squid"
        body = source_label + ": " + text
        notifier = shutil.which("terminal-notifier")
        if notifier:
            try:
                bundle_id = find_terminal_app_bundle_for_claude_code()
            except Exception:
                bundle_id = None
            cmd = [notifier, "-title", title, "-message", body]
            if sound:
                cmd += ["-sound", sound]
            if bundle_id:
                cmd += ["-activate", bundle_id]
            try:
                subprocess.run(cmd, timeout=3, capture_output=True)
                return
            except Exception as e:
                print("[squid-pet] terminal-notifier failed, falling back: "
                      + str(e), flush=True)
        try:
            body_escaped = _applescript_escape(body)
            sound_clause = (
                ' sound name "' + _applescript_escape(sound) + '"' if sound else ""
            )
            script = ('display notification "' + body_escaped + '" with title "'
                      + _applescript_escape(title) + '"' + sound_clause)
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
