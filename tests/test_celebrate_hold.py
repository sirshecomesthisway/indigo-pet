"""post-e2e-polish 2026-06-27 Fix 1: celebrate_hold_sec config knob tests.

Covers:
  (a) Default 20s baseline on both detectors
  (b) Config override is read at use site (hot-reloadable: each celebrate
      arm picks up the latest config value, no restart needed)
  (c) GitDetector fires celebrate on touched HEAD

Pink-2026-08-22: TPADetector tests converted to ClaudeCodeDetector
(TPADetector was removed -- TPA was never actually
installed/run on this machine). ClaudeCodeDetector gained the exact same
busy->idle celebrate-edge mechanism this same day.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from squid_pet.detectors import ClaudeCodeDetector, GitDetector


# ── (a) Defaults ────────────────────────────────────────────────────────
def test_claude_code_default_celebrate_hold_is_20s():
    """ClaudeCodeDetector class const = 20."""
    d = ClaudeCodeDetector()
    assert d.CELEBRATE_DURATION_SEC == 20


def test_git_default_celebrate_hold_is_20s():
    """GitDetector class const = 20.0 (was 4.0 pre-Fix-1)."""
    d = GitDetector(project_dirs=[])
    assert d.CELEBRATE_HOLD_SEC == 20.0


# ── (b) Config-override hot-reload ──────────────────────────────────────
def test_claude_code_celebrate_hold_reads_config_on_arm():
    """ClaudeCodeDetector reads celebrate_hold_sec at the moment
    celebrate arms.

    Confirms hot-reload: change config value, next celebrate-edge picks
    up the new value without restart.
    """
    d = ClaudeCodeDetector(
        find_processes_fn=lambda: ["fake_proc"],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        projects_dir=Path("/fake"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        recent_file_ages_fn=lambda: [],
    )
    # Simulate "was busy" so next idle tick arms celebrate.
    d._was_busy = True

    # Override config to return 7.0
    with patch("squid_pet.config.get", side_effect=lambda k, default=None:
               7.0 if k == "celebrate_hold_sec" else default):
        d._scan(now=100.0)
        # Should be exactly 100.0 + 7.0 = 107.0
        assert abs(d._celebrate_until - 107.0) < 0.001, \
            f"expected 107.0, got {d._celebrate_until}"


def test_git_celebrate_hold_reads_config_on_arm(tmp_path):
    """GitDetector reads celebrate_hold_sec at the moment celebrate arms."""
    # Make a fake repo: <tmp>/myrepo/.git/HEAD with a fresh mtime
    repo = tmp_path / "myrepo"
    git = repo / ".git"
    git.mkdir(parents=True)
    head = git / "HEAD"
    head.write_text("ref: refs/heads/main\n")
    # Touch HEAD to now-1 so it's <5s ago
    fake_now = 1000.0
    os.utime(head, (fake_now - 1, fake_now - 1))
    (git / "refs" / "heads").mkdir(parents=True)

    d = GitDetector(project_dirs=[str(tmp_path)])
    with patch("squid_pet.config.get", side_effect=lambda k, default=None:
               12.5 if k == "celebrate_hold_sec" else default):
        # Force discovery by setting last-discovery to past
        d._discovered_at = 0.0
        # is_celebrating() -> _refresh() -> arms _celebrate_until
        assert d.is_celebrating(fake_now), "should fire celebrate on fresh HEAD"
        # Expected: fake_now + 12.5 = 1012.5
        assert abs(d._celebrate_until - 1012.5) < 0.001, \
            f"expected 1012.5, got {d._celebrate_until}"


def test_claude_code_falls_back_to_class_const_if_config_broken():
    """If config import fails (e.g. test sandbox), use class const."""
    d = ClaudeCodeDetector(
        find_processes_fn=lambda: ["fake_proc"],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        projects_dir=Path("/fake"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        recent_file_ages_fn=lambda: [],
    )
    d._was_busy = True
    # Make config.get raise
    with patch("squid_pet.config.get", side_effect=RuntimeError("boom")):
        d._scan(now=200.0)
        # Should fall back to class const (20.0)
        assert abs(d._celebrate_until - 220.0) < 0.001


# ── (c) GitDetector fires celebrate on touched HEAD ─────────────────────
def test_git_celebrate_fires_on_fresh_head(tmp_path):
    """End-to-end: touch .git/HEAD -> is_celebrating(now) True."""
    repo = tmp_path / "myrepo"
    git = repo / ".git"
    git.mkdir(parents=True)
    head = git / "HEAD"
    head.write_text("ref: refs/heads/main\n")
    fake_now = 1000.0
    os.utime(head, (fake_now - 1, fake_now - 1))
    (git / "refs" / "heads").mkdir(parents=True)

    d = GitDetector(project_dirs=[str(tmp_path)])
    d._discovered_at = 0.0  # force first discovery
    assert d.is_celebrating(fake_now), \
        "GitDetector should report celebrating on fresh HEAD mtime"
