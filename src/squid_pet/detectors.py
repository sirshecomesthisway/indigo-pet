"""
Pluggable activity detectors for squid-pet.

Each detector observes a different signal source and returns three booleans
per tick: ``is_busy(now)``, ``is_celebrating(now)``, ``is_grooving(now)``.
The StateMachine ORs across all enabled detectors. This lets squid-pet
react to Claude Code / Codex activity, git commits, terminal commands,
and IDE bursts.

Pink-2026-08-22: TPADetector (the original TPA-aware busy/thinking/
celebrating/grooving/concerned detector) was removed -- TPA was
never actually installed/run on this machine, so it never fired anything
in practice. The TPA-specific approval_needed/flag-wave machinery in
watcher.py (find_tpa_processes, tpa_pids_awaiting_input,
per_process_pending_approval_idle, etc.) is untouched and still fully
TPA-driven -- it's kept pending a Claude-Code-native replacement signal
(see the Notification-hook idea in that change's discussion).

Design goals:
* Each detector is fully unit-testable in isolation. All filesystem,
  psutil, and time dependencies are injected via constructor args so
  tests can mock them without touching the real disk or process table.
* Detectors are stateless across ticks except for caches (e.g. the
  60-second git-repo discovery cache) and the few sticky timers that
  the design contract requires (e.g. 4-second celebrate hold).
* ``diagnostic()`` always returns a plain dict for the ``squid why``
  command -- never raises.

See ``openspec/changes/trigger-broadening/design.md`` for the full
contract per detector (D1 git, D2 terminal, D3 IDE, D4 settings, D5
privacy), and ``openspec/changes/claude-code-detector/design.md`` for
``ClaudeCodeDetector``, which -- unlike git/terminal/IDE -- is promoted
into the rich working/thinking/celebrating cascade rather than the flat
generic fallback.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol, runtime_checkable


# ----------------------------------------------------------------------
# Detector protocol
# ----------------------------------------------------------------------
@runtime_checkable
class Detector(Protocol):
    """All detectors implement this interface."""
    name: str
    enabled: bool

    def is_busy(self, now: float) -> bool: ...
    def is_celebrating(self, now: float) -> bool: ...
    def is_grooving(self, now: float) -> bool: ...
    def diagnostic(self) -> dict: ...


# ----------------------------------------------------------------------
# ClaudeCodeDetector -- detects `claude` CLI activity
# ----------------------------------------------------------------------
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


class ClaudeCodeDetector:
    """Detect Claude Code CLI activity, giving engineers whose daily
    driver is Claude Code a working/thinking/celebrating distinction
    instead of falling through the flat generic OR-fallback.

    Three independent signals:
      shell_active -- a live non-shell descendant process under `claude`
        (reuses watcher.has_active_shell_children). Catches Bash-tool
        calls only -- NOT in-process tools like Edit/Write, which never
        spawn a subprocess.
      file_active -- a file under project_dirs was modified within
        FILE_ACTIVE_WINDOW_SEC (reuses the same file-mtime scan
        IDEDetector uses). Catches exactly the Edit/Write/apply_patch
        gap shell_active misses -- verified against a real bug report
        (2026-08-14): Squid stayed "thinking" while Claude was actively
        editing files because only shell_active/streaming existed then.
      streaming -- the youngest ~/.claude/projects/*/*.jsonl transcript
        was written within STREAMING_STALE_SEC. Backstops both signals
        above (transcript is touched on any turn, tool or plain text),
        at the cost of coarser granularity -- this is what maps to
        "thinking" when neither shell_active nor file_active fire.

    No content is ever read from any transcript -- mtime only. See
    design.md for why (undocumented on-disk format; observed to include
    non-conversational bookkeeping lines, not just user/assistant turns).
    """
    name = "claude_code"

    STREAMING_STALE_SEC = 20.0
    DISCOVERY_CACHE_SEC = 60.0
    CANDIDATE_MAX_AGE_SEC = 900.0  # drop transcripts idle >15min from the cache
    FILE_ACTIVE_WINDOW_SEC = 10.0  # a bit more generous than IDEDetector's 5s

    def __init__(
        self,
        enabled: bool = True,
        *,
        find_processes_fn: Callable | None = None,
        aggregate_cpu_fn: Callable | None = None,
        has_active_shell_children_fn: Callable | None = None,
        shell_cmdline_fn: Callable | None = None,
        projects_dir: Path | None = None,
        glob_fn: Callable | None = None,
        stat_fn: Callable | None = None,
        project_dirs: Iterable[str] | None = None,
        recent_file_ages_fn: Callable | None = None,
    ) -> None:
        self.enabled = enabled
        self._find_processes = find_processes_fn
        self._aggregate_cpu = aggregate_cpu_fn
        self._has_active_shell_children = has_active_shell_children_fn
        # Separate injectable fn (not folded into has_active_shell_children_fn)
        # so existing callers/tests that only inject a bool-returning
        # has_active_shell_children_fn keep working unchanged -- this one
        # defaults independently, lazily, at first use in _scan().
        self._shell_cmdline_fn = shell_cmdline_fn
        self._projects_dir = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS_DIR
        self._glob = glob_fn or (lambda root: root.glob("*/*.jsonl"))
        self._stat = stat_fn or os.stat
        raw_dirs = list(project_dirs) if project_dirs is not None else [str(Path.home() / "Projects")]
        self.project_dirs = [Path(d).expanduser() for d in raw_dirs]
        self._recent_file_ages = recent_file_ages_fn or (
            lambda: _scan_recent_file_ages(self.project_dirs, self.FILE_ACTIVE_WINDOW_SEC)
        )
        self._candidates: list = []
        self._candidates_at: float = 0.0
        self._last_scan_ts: float = 0.0
        self.cpu_percent: float = 0.0
        self.claude_code_running: bool = False
        self.shell_active: bool = False
        self.shell_cmdline: list[str] | None = None
        self.file_active: bool = False
        self.transcript_age: float = float("inf")
        self.streaming: bool = False

    def _lazy_defaults(self) -> None:
        if self._find_processes is None:
            from . import watcher as _w
            self._find_processes = _w.find_claude_code_processes
            self._aggregate_cpu = _w.aggregate_cpu
            self._has_active_shell_children = _w.has_active_shell_children

    def _discover(self, now: float) -> list:
        if self._candidates and (now - self._candidates_at) < self.DISCOVERY_CACHE_SEC:
            return self._candidates
        found = []
        try:
            for f in self._glob(self._projects_dir):
                try:
                    mtime = self._stat(str(f)).st_mtime
                except OSError:
                    continue
                if (now - mtime) <= self.CANDIDATE_MAX_AGE_SEC:
                    found.append(f)
        except OSError:
            pass
        self._candidates = found
        self._candidates_at = now
        return self._candidates

    def _newest_transcript_age(self, now: float) -> float:
        newest_mtime = 0.0
        for f in self._discover(now):
            try:
                mtime = self._stat(str(f)).st_mtime
            except OSError:
                continue
            newest_mtime = max(newest_mtime, mtime)
        if newest_mtime == 0.0:
            return float("inf")
        return max(0.0, now - newest_mtime)

    def _scan(self, now: float) -> None:
        if now == self._last_scan_ts:
            return
        self._lazy_defaults()
        procs = self._find_processes()
        self.claude_code_running = bool(procs)
        self.cpu_percent = round(
            self._aggregate_cpu(procs) if procs else 0.0, 1
        )
        self.shell_active = (
            self._has_active_shell_children(procs) if procs else False
        )
        if self.shell_active:
            cmdline_fn = self._shell_cmdline_fn
            if cmdline_fn is None:
                from . import watcher as _w
                cmdline_fn = _w.latest_shell_child_cmdline
            self.shell_cmdline = cmdline_fn(procs)
        else:
            self.shell_cmdline = None
        self.file_active = bool(self._recent_file_ages()) if procs else False
        self.transcript_age = self._newest_transcript_age(now)
        self.streaming = self.transcript_age < self.STREAMING_STALE_SEC
        self._last_scan_ts = now

    def is_busy(self, now: float) -> bool:
        if not self.enabled:
            return False
        self._scan(now)
        return self.shell_active or self.file_active or self.streaming

    def is_celebrating(self, now: float) -> bool:
        # Pink-2026-08-27f: was a busy->idle heuristic edge (shell/file/
        # transcript-mtime activity dropping) -- fired on any >20s gap
        # with no tool call, a normal mid-task reasoning stretch, not a
        # real completion (confirmed live: a false "finished with
        # claude!" bubble while Claude was still working). The real
        # signal is now Claude Code's official Stop hook -- see
        # watcher.claude_sessions_just_finished(), read directly by
        # StateMachine._compute_inner() rather than through this
        # detector, since it's a session-keyed hook signal (like
        # approval_needed) rather than a process/file/transcript scan.
        # This always returns False; kept only so callers using the
        # Detector protocol uniformly don't need an isinstance check.
        return False

    def is_grooving(self, now: float) -> bool:
        return False  # no reliable signal yet -- documented non-goal

    def diagnostic(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "claude_code_running": self.claude_code_running,
            "cpu_percent": self.cpu_percent,
            "shell_active": self.shell_active,
            "shell_cmdline": self.shell_cmdline,
            "file_active": self.file_active,
            "transcript_age": self.transcript_age,
            "streaming": self.streaming,
        }


# ----------------------------------------------------------------------
# CodexDetector -- detects the OpenAI Codex CLI
# ----------------------------------------------------------------------
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


class CodexDetector:
    """Detect Codex CLI activity. Same role and shape as
    ClaudeCodeDetector -- process presence, live tool subprocess, recent
    project-file writes, and session-transcript write recency -- for
    engineers whose daily driver is Codex instead of (or alongside)
    Claude Code. See ClaudeCodeDetector's docstring for the rationale
    behind each of the three busy signals; identical here.

    Session transcripts are nested by date --
    ~/.codex/sessions/YYYY/MM/DD/*.jsonl -- so discovery uses a
    recursive glob rather than ClaudeCodeDetector's fixed two-level
    pattern.

    Kept as an independent class rather than sharing a base with
    ClaudeCodeDetector, matching this module's existing convention
    (GitDetector/TerminalDetector/IDEDetector each implement the
    Detector protocol independently despite structural overlap) --
    the two can evolve independently (e.g. Codex's
    ~/.codex/log/codex-tui.log could become its own future signal).
    """
    name = "codex"

    STREAMING_STALE_SEC = 20.0
    DISCOVERY_CACHE_SEC = 60.0
    CANDIDATE_MAX_AGE_SEC = 900.0
    FILE_ACTIVE_WINDOW_SEC = 10.0

    def __init__(
        self,
        enabled: bool = True,
        *,
        find_processes_fn: Callable | None = None,
        aggregate_cpu_fn: Callable | None = None,
        has_active_shell_children_fn: Callable | None = None,
        shell_cmdline_fn: Callable | None = None,
        sessions_dir: Path | None = None,
        glob_fn: Callable | None = None,
        stat_fn: Callable | None = None,
        project_dirs: Iterable[str] | None = None,
        recent_file_ages_fn: Callable | None = None,
    ) -> None:
        self.enabled = enabled
        self._find_processes = find_processes_fn
        self._aggregate_cpu = aggregate_cpu_fn
        self._has_active_shell_children = has_active_shell_children_fn
        self._shell_cmdline_fn = shell_cmdline_fn
        self._sessions_dir = Path(sessions_dir) if sessions_dir else CODEX_SESSIONS_DIR
        self._glob = glob_fn or (lambda root: root.glob("**/*.jsonl"))
        self._stat = stat_fn or os.stat
        raw_dirs = list(project_dirs) if project_dirs is not None else [str(Path.home() / "Projects")]
        self.project_dirs = [Path(d).expanduser() for d in raw_dirs]
        self._recent_file_ages = recent_file_ages_fn or (
            lambda: _scan_recent_file_ages(self.project_dirs, self.FILE_ACTIVE_WINDOW_SEC)
        )
        self._candidates: list = []
        self._candidates_at: float = 0.0
        self._last_scan_ts: float = 0.0
        self.cpu_percent: float = 0.0
        self.codex_running: bool = False
        self.shell_active: bool = False
        self.shell_cmdline: list[str] | None = None
        self.file_active: bool = False
        self.transcript_age: float = float("inf")
        self.streaming: bool = False

    def _lazy_defaults(self) -> None:
        if self._find_processes is None:
            from . import watcher as _w
            self._find_processes = _w.find_codex_processes
            self._aggregate_cpu = _w.aggregate_cpu
            self._has_active_shell_children = _w.has_active_shell_children

    def _discover(self, now: float) -> list:
        if self._candidates and (now - self._candidates_at) < self.DISCOVERY_CACHE_SEC:
            return self._candidates
        found = []
        try:
            for f in self._glob(self._sessions_dir):
                try:
                    mtime = self._stat(str(f)).st_mtime
                except OSError:
                    continue
                if (now - mtime) <= self.CANDIDATE_MAX_AGE_SEC:
                    found.append(f)
        except OSError:
            pass
        self._candidates = found
        self._candidates_at = now
        return self._candidates

    def _newest_transcript_age(self, now: float) -> float:
        newest_mtime = 0.0
        for f in self._discover(now):
            try:
                mtime = self._stat(str(f)).st_mtime
            except OSError:
                continue
            newest_mtime = max(newest_mtime, mtime)
        if newest_mtime == 0.0:
            return float("inf")
        return max(0.0, now - newest_mtime)

    def _scan(self, now: float) -> None:
        if now == self._last_scan_ts:
            return
        self._lazy_defaults()
        procs = self._find_processes()
        self.codex_running = bool(procs)
        self.cpu_percent = round(
            self._aggregate_cpu(procs) if procs else 0.0, 1
        )
        self.shell_active = (
            self._has_active_shell_children(procs) if procs else False
        )
        if self.shell_active:
            cmdline_fn = self._shell_cmdline_fn
            if cmdline_fn is None:
                from . import watcher as _w
                cmdline_fn = _w.latest_shell_child_cmdline
            self.shell_cmdline = cmdline_fn(procs)
        else:
            self.shell_cmdline = None
        self.file_active = bool(self._recent_file_ages()) if procs else False
        self.transcript_age = self._newest_transcript_age(now)
        self.streaming = self.transcript_age < self.STREAMING_STALE_SEC
        self._last_scan_ts = now

    def is_busy(self, now: float) -> bool:
        if not self.enabled:
            return False
        self._scan(now)
        return self.shell_active or self.file_active or self.streaming

    def is_celebrating(self, now: float) -> bool:
        return False  # no reliable signal yet -- documented non-goal

    def is_grooving(self, now: float) -> bool:
        return False  # no reliable signal yet -- documented non-goal

    def diagnostic(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "codex_running": self.codex_running,
            "cpu_percent": self.cpu_percent,
            "shell_active": self.shell_active,
            "shell_cmdline": self.shell_cmdline,
            "file_active": self.file_active,
            "transcript_age": self.transcript_age,
            "streaming": self.streaming,
        }


# ----------------------------------------------------------------------
# GitDetector -- watches .git/HEAD, .git/index, .git/refs/heads/ mtimes
# ----------------------------------------------------------------------
class GitDetector:
    """Detect git activity by polling .git/HEAD, .git/index, and .git/refs/
    mtimes -- no shell-out, no file content read.

    Per design D1:
      HEAD modified <5s ago         -> is_celebrating (4s sticky)
      index modified <5s ago        -> is_busy (staged files)
      refs/heads/* modified <5s ago -> is_celebrating (4s sticky, just pushed)

    Caches the discovered .git directory list for 60s to avoid hammering
    the filesystem. Caps at 50 repos to keep the scan cheap.
    """
    name = "git"

    BUSY_WINDOW_SEC = 5.0
    CELEBRATE_HOLD_SEC = 20.0  # post-e2e-polish 2026-06-27 Fix 1: was 4.0
    DISCOVERY_CACHE_SEC = 60.0
    MAX_REPOS = 50
    MAX_DEPTH = 4

    def __init__(
        self,
        project_dirs: Iterable[str] | None = None,
        enabled: bool = True,
        *,
        walk_fn: Callable | None = None,
        stat_fn: Callable | None = None,
    ) -> None:
        self.enabled = enabled
        # Expand ``~`` once, normalize. Skip non-existent silently here;
        # the warning is the settings loader's job.
        raw = list(project_dirs or [str(Path.home() / "Projects")])
        self.project_dirs = [Path(d).expanduser() for d in raw]
        self._walk = walk_fn or os.walk
        self._stat = stat_fn or os.stat
        self._discovered: list[Path] = []
        self._discovered_at: float = 0.0
        self._celebrate_until: float = 0.0
        # Diagnostic
        self._last_busy_reason: str = ""
        self._last_celebrate_reason: str = ""

    def _discover(self, now: float) -> list[Path]:
        if (now - self._discovered_at) < self.DISCOVERY_CACHE_SEC and self._discovered:
            return self._discovered
        repos: list[Path] = []
        for root in self.project_dirs:
            if not root.exists():
                continue
            for dirpath, dirnames, _ in self._walk(str(root)):
                # Depth cap relative to root
                rel_depth = Path(dirpath).resolve().relative_to(
                    root.resolve()
                ).parts if Path(dirpath).resolve() != root.resolve() else ()
                depth = len(rel_depth)
                # Prune obvious junk subdirs to keep the walk cheap
                dirnames[:] = [
                    d for d in dirnames
                    if d not in (
                        "node_modules", ".venv", "venv",
                        "__pycache__", ".pytest_cache", "dist", "build",
                    )
                ]
                if ".git" in dirnames:
                    repos.append(Path(dirpath) / ".git")
                    # Don't descend into the repo's subdirs for more .gits
                    dirnames[:] = [d for d in dirnames if d != ".git"]
                if depth >= self.MAX_DEPTH:
                    dirnames[:] = []
                if len(repos) >= self.MAX_REPOS:
                    break
            if len(repos) >= self.MAX_REPOS:
                break
        self._discovered = repos[: self.MAX_REPOS]
        self._discovered_at = now
        return self._discovered

    def _mtime(self, p: Path) -> float:
        try:
            return self._stat(str(p)).st_mtime
        except OSError:
            return 0.0

    def _scan_repos(self, now: float) -> tuple[bool, bool, str, str]:
        """Returns (any_busy, any_celebrating, busy_reason, celebrate_reason)."""
        any_busy = False
        any_celeb = False
        busy_reason = ""
        celeb_reason = ""
        for git_dir in self._discover(now):
            head = git_dir / "HEAD"
            index = git_dir / "index"
            refs_heads = git_dir / "refs" / "heads"
            head_age = now - self._mtime(head) if self._mtime(head) else float("inf")
            index_age = now - self._mtime(index) if self._mtime(index) else float("inf")
            refs_age = now - self._mtime(refs_heads) if self._mtime(refs_heads) else float("inf")
            if head_age < self.BUSY_WINDOW_SEC:
                any_celeb = True
                celeb_reason = f"HEAD touched in {git_dir.parent.name} ({head_age:.1f}s ago)"
            if refs_age < self.BUSY_WINDOW_SEC and head_age >= self.BUSY_WINDOW_SEC:
                any_celeb = True
                celeb_reason = f"refs/heads/ touched in {git_dir.parent.name} ({refs_age:.1f}s ago)"
            if index_age < self.BUSY_WINDOW_SEC and head_age >= self.BUSY_WINDOW_SEC:
                any_busy = True
                busy_reason = f"index staged in {git_dir.parent.name} ({index_age:.1f}s ago)"
        return any_busy, any_celeb, busy_reason, celeb_reason

    def _refresh(self, now: float) -> tuple[bool, bool]:
        any_busy, any_celeb, br, cr = self._scan_repos(now)
        if any_celeb:
            # post-e2e-polish 2026-06-27 Fix 1: config-driven hold
            try:
                from . import config as _cfg
                hold = float(_cfg.get('celebrate_hold_sec', self.CELEBRATE_HOLD_SEC))
            except Exception:
                hold = self.CELEBRATE_HOLD_SEC
            self._celebrate_until = now + hold
            self._last_celebrate_reason = cr
        if any_busy:
            self._last_busy_reason = br
        return any_busy, now < self._celebrate_until

    def is_busy(self, now: float) -> bool:
        if not self.enabled:
            return False
        busy, _ = self._refresh(now)
        return busy

    def is_celebrating(self, now: float) -> bool:
        if not self.enabled:
            return False
        _, celeb = self._refresh(now)
        return celeb

    def is_grooving(self, now: float) -> bool:
        return False  # git has no grooving signal

    def diagnostic(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "repos_watched": len(self._discovered),
            "last_busy_reason": self._last_busy_reason,
            "last_celebrate_reason": self._last_celebrate_reason,
            "celebrate_until": self._celebrate_until,
        }


# ----------------------------------------------------------------------
# TerminalDetector -- psutil scan for shells with active long-running children
# ----------------------------------------------------------------------
SHELL_NAMES = frozenset({"zsh", "bash", "fish", "sh"})


class TerminalDetector:
    """Detect terminal activity by counting shells with non-shell children
    that have been running >MIN_CHILD_AGE_SEC. No celebrating/grooving."""
    name = "terminal"

    MIN_CHILD_AGE_SEC = 3.0

    def __init__(
        self,
        enabled: bool = True,
        *,
        process_iter_fn: Callable | None = None,
    ) -> None:
        self.enabled = enabled
        self._process_iter = process_iter_fn  # if None, resolve lazily
        self._last_count: int = 0

    def _iter_procs(self):
        if self._process_iter is not None:
            return self._process_iter()
        import psutil
        return psutil.process_iter(["name", "pid", "create_time"])

    def _count_active(self, now: float) -> int:
        count = 0
        for p in self._iter_procs():
            try:
                info = getattr(p, "info", None) or {
                    "name": p.name(), "pid": p.pid,
                    "create_time": p.create_time(),
                }
                if info.get("name") not in SHELL_NAMES:
                    continue
                children = p.children() if hasattr(p, "children") else []
                for c in children:
                    c_info = getattr(c, "info", None) or {
                        "name": c.name(),
                        "create_time": c.create_time(),
                    }
                    if c_info.get("name") in SHELL_NAMES:
                        continue
                    age = now - (c_info.get("create_time") or now)
                    if age >= self.MIN_CHILD_AGE_SEC:
                        count += 1
                        break
            except Exception:
                continue
        return count

    def is_busy(self, now: float) -> bool:
        if not self.enabled:
            return False
        self._last_count = self._count_active(now)
        return self._last_count >= 1

    def is_celebrating(self, now: float) -> bool:
        return False

    def is_grooving(self, now: float) -> bool:
        return False

    def diagnostic(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "active_shell_count": self._last_count,
        }


# ----------------------------------------------------------------------
# Shared file-mtime scan -- used by IDEDetector and by ClaudeCodeDetector/
# CodexDetector's file-write signal (in-process Edit/Write-style tool
# calls don't spawn a subprocess, so shell-child detection alone misses
# them; a recent write under project_dirs is the fallback evidence).
# ----------------------------------------------------------------------
_FILE_SCAN_SKIP_DIRS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__",
    ".git", ".pytest_cache", "dist", "build",
})


def _scan_recent_file_ages(
    project_dirs: Iterable[Path],
    window_sec: float,
    *,
    now: float | None = None,
    walk_fn: Callable | None = None,
    stat_fn: Callable | None = None,
    max_depth: int = 5,
    max_files: int = 200,
) -> list[float]:
    """Ages (seconds) of files modified within window_sec across
    project_dirs. Capped depth/file-count to stay cheap on large trees;
    skips common junk directories. Read-only: stats mtimes only, never
    opens a file's contents."""
    walk_fn = walk_fn or os.walk
    stat_fn = stat_fn or os.stat
    now = now if now is not None else time.time()
    cutoff = now - window_sec
    ages: list[float] = []
    for root in project_dirs:
        root = Path(root)
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in walk_fn(str(root)):
            dirnames[:] = [d for d in dirnames if d not in _FILE_SCAN_SKIP_DIRS]
            rel = Path(dirpath).resolve().relative_to(root.resolve()).parts \
                if Path(dirpath).resolve() != root.resolve() else ()
            if len(rel) > max_depth:
                dirnames[:] = []
                continue
            for fn in filenames:
                try:
                    m = stat_fn(os.path.join(dirpath, fn)).st_mtime
                except OSError:
                    continue
                if m >= cutoff:
                    ages.append(now - m)
                    if len(ages) >= max_files:
                        return ages
    return ages


# ----------------------------------------------------------------------
# IDEDetector -- psutil for IDE processes + project file mtime cross-check
# ----------------------------------------------------------------------
DEFAULT_IDE_PROCESSES = (
    "Code", "Cursor", "idea", "pycharm", "webstorm", "rubymine",
    "goland", "clion",
)


class IDEDetector:
    """Detect IDE activity by aggregating CPU% of matching processes and
    cross-referencing recent file modifications in project_dirs.

    Per design D3:
      CPU >=3% AND project file <5s ago  -> is_busy
      CPU >=3% AND no recent file        -> nothing (likely background indexing)
      CPU <3%  AND project file <5s ago  -> is_busy (autosave during reflection)
      >5 distinct project files modified in last 30s -> is_grooving
    """
    name = "ide"

    BUSY_CPU_THRESHOLD = 3.0
    RECENT_FILE_WINDOW_SEC = 5.0
    GROOVING_WINDOW_SEC = 30.0
    GROOVING_FILE_COUNT = 5

    def __init__(
        self,
        project_dirs: Iterable[str] | None = None,
        ide_processes: Iterable[str] | None = None,
        enabled: bool = True,
        *,
        process_iter_fn: Callable | None = None,
        recent_files_fn: Callable | None = None,
    ) -> None:
        self.enabled = enabled
        raw_dirs = list(project_dirs or [str(Path.home() / "Projects")])
        self.project_dirs = [Path(d).expanduser() for d in raw_dirs]
        self.ide_processes = frozenset(ide_processes or DEFAULT_IDE_PROCESSES)
        self._process_iter = process_iter_fn
        # recent_files_fn(window_sec) -> list[float]  (ages of recently-modified files)
        self._recent_files = recent_files_fn or self._default_recent_files
        self.cpu_percent: float = 0.0
        self.recent_file_count_busy: int = 0
        self.recent_file_count_grooving: int = 0
        self._last_scan_ts: float = 0.0

    def _iter_procs(self):
        if self._process_iter is not None:
            return self._process_iter()
        import psutil
        return psutil.process_iter(["name"])

    def _aggregate_cpu(self) -> float:
        total = 0.0
        for p in self._iter_procs():
            try:
                info = getattr(p, "info", None) or {"name": p.name()}
                if info.get("name") not in self.ide_processes:
                    continue
                if hasattr(p, "cpu_percent"):
                    total += float(p.cpu_percent())
            except Exception:
                continue
        return total

    def _default_recent_files(self, window_sec: float) -> list[float]:
        """Return list of ages (sec) of files modified within ``window_sec``
        across project_dirs. Capped at 200 files and depth 5 to stay cheap.
        Skips junk dirs."""
        return _scan_recent_file_ages(self.project_dirs, window_sec)

    def _scan(self, now: float) -> None:
        # Same once-per-tick guard as ClaudeCodeDetector/CodexDetector.
        # The watcher calls is_busy() AND is_grooving() every tick, and
        # both used to re-scan -- so each tick paid for two full process
        # enumerations and two project-tree walks instead of one.
        if now == self._last_scan_ts:
            return
        self.cpu_percent = self._aggregate_cpu()
        # Two windows: 5s busy / 30s grooving. Compute the larger then partition.
        recent = self._recent_files(self.GROOVING_WINDOW_SEC)
        self.recent_file_count_grooving = len(recent)
        self.recent_file_count_busy = sum(
            1 for a in recent if a < self.RECENT_FILE_WINDOW_SEC
        )
        self._last_scan_ts = now

    def is_busy(self, now: float) -> bool:
        if not self.enabled:
            return False
        self._scan(now)
        has_recent_file = self.recent_file_count_busy >= 1
        cpu_busy = self.cpu_percent >= self.BUSY_CPU_THRESHOLD
        # busy if (cpu_busy AND recent_file) or (no cpu but recent_file -- autosave)
        # We do NOT fire on cpu_busy alone (background indexing false-positive).
        return has_recent_file and (cpu_busy or self.cpu_percent < self.BUSY_CPU_THRESHOLD)

    def is_celebrating(self, now: float) -> bool:
        return False

    def is_grooving(self, now: float) -> bool:
        if not self.enabled:
            return False
        self._scan(now)
        return self.recent_file_count_grooving >= self.GROOVING_FILE_COUNT

    def diagnostic(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "cpu_percent": round(self.cpu_percent, 1),
            "recent_file_count_5s": self.recent_file_count_busy,
            "recent_file_count_30s": self.recent_file_count_grooving,
            "ide_processes": sorted(self.ide_processes),
            "project_dirs": [str(p) for p in self.project_dirs],
        }


# ----------------------------------------------------------------------
# Factory: build detectors from a settings dict
# ----------------------------------------------------------------------
DEFAULT_TRIGGERS = {
    "claude_code": True,
    "codex": True,
    "git": True,
    "terminal": False,  # off by default: misfires on any dev machine
    "ide": True,
    "project_dirs": [str(Path.home() / "Projects")],
    "ide_processes": list(DEFAULT_IDE_PROCESSES),
}


def build_detectors(settings: dict | None = None) -> list:
    """Build a list of Detector instances from the ``triggers`` subsection
    of settings.json. Missing keys take DEFAULT_TRIGGERS values.

    Pink-2026-08-22: TPADetector removed (TPA was never
    actually run on this machine). A settings.json with a leftover
    "tpa" key is harmless -- it's simply ignored.
    """
    s = (settings or {}).get("triggers", {}) if settings else {}
    project_dirs = s.get("project_dirs", DEFAULT_TRIGGERS["project_dirs"])
    ide_processes = s.get("ide_processes", DEFAULT_TRIGGERS["ide_processes"])
    detectors = [
        ClaudeCodeDetector(project_dirs=project_dirs, enabled=s.get("claude_code", True)),
        CodexDetector(project_dirs=project_dirs, enabled=s.get("codex", True)),
        GitDetector(project_dirs=project_dirs, enabled=s.get("git", True)),
        TerminalDetector(enabled=s.get("terminal", False)),
        IDEDetector(
            project_dirs=project_dirs,
            ide_processes=ide_processes,
            enabled=s.get("ide", True),
        ),
    ]
    return detectors
