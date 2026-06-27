"""post-e2e-polish 2026-06-27 Fix 1: celebrate_hold_sec config knob tests.

Covers:
  (a) Default 20s baseline both detectors
  (b) Config override is read at use site (hot-reloadable: each celebrate
      arm picks up the latest config value, no restart needed)
  (c) GitDetector fires celebrate on touched HEAD
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from squid_pet.detectors import TPADetector, GitDetector


# ── (a) Defaults ────────────────────────────────────────────────────────
def test_codepuppy_default_celebrate_hold_is_20s():
    """TPADetector class const = 20 (was 4 pre-Fix-1)."""
    d = TPADetector()
    assert d.CELEBRATE_DURATION_SEC == 20


def test_git_default_celebrate_hold_is_20s():
    """GitDetector class const = 20.0 (was 4.0 pre-Fix-1)."""
    d = GitDetector(project_dirs=[])
    assert d.CELEBRATE_HOLD_SEC == 20.0


# ── (b) Config-override hot-reload ──────────────────────────────────────
def test_codepuppy_celebrate_hold_reads_config_on_arm():
    """TPA detector reads celebrate_hold_sec at the moment celebrate arms.

    Confirms hot-reload: change config value, next celebrate-edge picks
    up the new value without restart.
    """
    # Build a detector with all-injected scan fns (no watcher imports).
    d = TPADetector(
        find_processes_fn=lambda: ["fake_proc"],
        aggregate_cpu_fn=lambda procs: 0.0,  # idle now
        most_recent_tool_activity_age_fn=lambda: float("inf"),
        newest_subagent_age_fn=lambda: float("inf"),
        has_active_shell_children_fn=lambda p: False,
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


def test_codepuppy_falls_back_to_class_const_if_config_broken():
    """If config import fails (e.g. test sandbox), use class const."""
    d = TPADetector(
        find_processes_fn=lambda: ["fake_proc"],
        aggregate_cpu_fn=lambda procs: 0.0,
        most_recent_tool_activity_age_fn=lambda: float("inf"),
        newest_subagent_age_fn=lambda: float("inf"),
        has_active_shell_children_fn=lambda p: False,
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
