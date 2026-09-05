"""post-e2e-polish 2026-06-27 Fix 1: celebrate_hold_sec config knob tests.

Covers:
  (a) Default 20s baseline on GitDetector
  (b) Config override is read at use site (hot-reloadable: each celebrate
      arm picks up the latest config value, no restart needed)
  (c) GitDetector fires celebrate on touched HEAD

Pink-2026-08-22: the legacy agent's detector tests were converted to
ClaudeCodeDetector (that detector was removed -- the agent was never actually
installed/run on this machine). ClaudeCodeDetector gained the exact same
busy->idle celebrate-edge mechanism this same day.

Pink-2026-08-27f: ClaudeCodeDetector's busy->idle celebrate-edge
machinery (_was_busy/_celebrate_until, tested below) was removed
entirely -- it fired on any >20s gap with no tool call (an ordinary
mid-task reasoning stretch), not a verified completion, confirmed live
as a false "finished with claude!" bubble. The real signal is now
Claude Code's official Stop hook: see
tests/test_watcher_claude_code_cascade.py (state-machine-level
integration) and tests/test_claude_pet_hook_script.py (the hook script
itself) for the replacement coverage. GitDetector's celebrate_hold_sec
tests are untouched -- its signal (an actual .git/HEAD mtime change)
was never part of this problem.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from squid_pet.detectors import GitDetector


# ── (a) Defaults ────────────────────────────────────────────────────────
def test_git_default_celebrate_hold_is_20s():
    """GitDetector class const = 20.0 (was 4.0 pre-Fix-1)."""
    d = GitDetector(project_dirs=[])
    assert d.CELEBRATE_HOLD_SEC == 20.0


# ── (b) Config-override hot-reload ──────────────────────────────────────
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
