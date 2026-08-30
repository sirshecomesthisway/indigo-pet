"""Pink-2026-08-27f: tests for claude_sessions_just_finished(), the
signal that replaced ClaudeCodeDetector's busy->idle celebrate-edge
heuristic.

Background: the old heuristic (shell_active/file_active/streaming
merged signal flipping true->false) fired on any >20s gap with no tool
call -- an ordinary mid-task reasoning stretch, not a verified
completion. Confirmed live as a false "finished with claude!" bubble
while Claude was still actively working. The real signal is Claude
Code's official Stop hook (fires exactly when Claude finishes
responding and hands control back): scripts/claude_pet_hook.py writes
~/.squid-pet/claude_finished/<session_id> on Stop, mirroring the
existing claude_awaiting_input/<session_id> pattern used for
approval_needed -- see test_claude_awaiting_input_signal.py for the
sibling signal this one is structurally modeled on.

Key structural difference from claude_awaiting_input: nothing ever
explicitly REMOVES a finished flag (there's no natural "un-finished"
event to hang a removal on, unlike UserPromptSubmit/SessionEnd for
awaiting-input). Freshness is entirely age-based against
celebrate_hold_sec (hot-reloadable config, default 20s -- the same knob
that controls how long the celebrating sprite-state visually holds).
Only the much larger CLAUDE_FINISHED_STALE_SEC (2h) triggers actual
disk cleanup, as a crash-safety net.

The hook script itself (scripts/claude_pet_hook.py) is tested
separately in tests/test_claude_pet_hook_script.py by invoking it as a
real subprocess.
"""
from __future__ import annotations
import os
import time
from unittest.mock import patch

import pytest

from squid_pet import watcher


@pytest.fixture
def tmp_finished_dir(tmp_path, monkeypatch):
    """Redirect the Claude just-finished dir to a tmp path for the test."""
    d = tmp_path / "claude_finished"
    d.mkdir()
    monkeypatch.setattr(watcher, "CLAUDE_FINISHED_DIR", str(d))
    return d


def _write(path, mtime_age_sec: float) -> None:
    path.write_text("stop")
    mtime = time.time() - mtime_age_sec
    os.utime(path, (mtime, mtime))


# ── basic dir handling ──────────────────────────────────────────────────

def test_no_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "CLAUDE_FINISHED_DIR", str(tmp_path / "nope"))
    assert watcher.claude_sessions_just_finished() == []


def test_empty_dir_returns_empty(tmp_finished_dir):
    assert watcher.claude_sessions_just_finished() == []


def test_fresh_flag_is_reported(tmp_finished_dir):
    _write(tmp_finished_dir / "sess-abc", mtime_age_sec=1.0)
    assert watcher.claude_sessions_just_finished() == ["sess-abc"]


def test_multiple_sessions_all_reported_sorted(tmp_finished_dir):
    _write(tmp_finished_dir / "sess-b", mtime_age_sec=1.0)
    _write(tmp_finished_dir / "sess-a", mtime_age_sec=1.0)
    assert watcher.claude_sessions_just_finished() == ["sess-a", "sess-b"]


def test_dotfiles_are_ignored(tmp_finished_dir):
    _write(tmp_finished_dir / "sess-real", mtime_age_sec=1.0)
    (tmp_finished_dir / ".DS_Store").write_text("junk")
    assert watcher.claude_sessions_just_finished() == ["sess-real"]


# ── freshness window (celebrate_hold_sec) ───────────────────────────────

def test_flag_older_than_celebrate_hold_is_excluded_but_not_deleted(tmp_finished_dir):
    """Past the celebrate window, a finished-flag stops counting as
    'worth celebrating now' -- but unlike the stale/crash-cleanup case,
    it is NOT deleted (nothing else owns removing it; deleting it here
    would just be arbitrary)."""
    f = tmp_finished_dir / "sess-old"
    _write(f, mtime_age_sec=25.0)  # default celebrate_hold_sec is 20
    with patch("squid_pet.config.get",
               side_effect=lambda k, default=None: default):
        assert watcher.claude_sessions_just_finished() == []
    assert f.exists(), "past-freshness-window flags are excluded, not deleted"


def test_flag_just_under_celebrate_hold_survives(tmp_finished_dir):
    f = tmp_finished_dir / "sess-fresh"
    _write(f, mtime_age_sec=15.0)
    with patch("squid_pet.config.get",
               side_effect=lambda k, default=None: default):
        assert watcher.claude_sessions_just_finished() == ["sess-fresh"]


def test_celebrate_hold_sec_config_override_extends_freshness_window(tmp_finished_dir):
    """Hot-reloadable: a larger celebrate_hold_sec picks up flags that
    the default 20s window would have already excluded."""
    f = tmp_finished_dir / "sess-old"
    _write(f, mtime_age_sec=25.0)
    with patch("squid_pet.config.get",
               side_effect=lambda k, default=None: 30 if k == "celebrate_hold_sec" else default):
        assert watcher.claude_sessions_just_finished() == ["sess-old"]


def test_config_error_falls_back_to_default_fresh_window(tmp_finished_dir):
    f = tmp_finished_dir / "sess-fresh"
    _write(f, mtime_age_sec=15.0)  # under the 20s default
    with patch("squid_pet.config.get", side_effect=RuntimeError("boom")):
        assert watcher.claude_sessions_just_finished() == ["sess-fresh"]


# ── stale/crash cleanup (2h, independent of celebrate_hold_sec) ────────

def test_stale_flag_is_evicted(tmp_finished_dir):
    """A flag older than CLAUDE_FINISHED_STALE_SEC is disk-cleanup-pruned
    regardless of celebrate_hold_sec -- crash-safety net, same pattern as
    claude_awaiting_input's stale eviction."""
    f = tmp_finished_dir / "sess-dead"
    _write(f, mtime_age_sec=watcher.CLAUDE_FINISHED_STALE_SEC + 60)
    assert watcher.claude_sessions_just_finished() == []
    assert not f.exists(), "stale flag should be deleted from disk"


def test_fresh_flag_just_under_stale_threshold_is_excluded_not_deleted(tmp_finished_dir):
    """Well past celebrate_hold_sec but still under the 2h stale
    threshold: excluded from the live list, but left on disk (not yet a
    crash-cleanup candidate)."""
    f = tmp_finished_dir / "sess-lingering"
    _write(f, mtime_age_sec=watcher.CLAUDE_FINISHED_STALE_SEC - 60)
    with patch("squid_pet.config.get",
               side_effect=lambda k, default=None: default):
        assert watcher.claude_sessions_just_finished() == []
    assert f.exists()
