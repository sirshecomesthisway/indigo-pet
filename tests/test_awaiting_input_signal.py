"""Pink-2026-06-29: replace CPU-heuristic approval detection with a
DIRECT signal from TPA itself.

Background:
* `~/.tpa/llm_active.flag` already exists -- written by TPA's
  sitecustomize.py monkey-patch when an LLM stream is in progress.
* This adds the SIBLING signal for the OTHER transition: when TPA is
  sitting at the interactive prompt awaiting Pink's input.

Format: `~/.tpa/awaiting_input/<pid>` file per TPA process.
Presence == that TPA is at the prompt right now. Absence == it's not.

Squid scans the directory; any file whose PID is still alive
triggers `approval_needed` immediately, no CPU guessing needed.
Dead-PID files are evicted (TPA crashed mid-prompt).
"""
from __future__ import annotations
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from squid_pet import watcher


@pytest.fixture
def tmp_awaiting_dir(tmp_path, monkeypatch):
    """Redirect the awaiting-input dir to a tmp path for the test."""
    d = tmp_path / "awaiting_input"
    d.mkdir()
    monkeypatch.setattr(watcher, "_AWAITING_INPUT_DIR", str(d))
    return d


def test_no_flag_dir_returns_empty(tmp_path, monkeypatch):
    """If the awaiting_input dir doesn't even exist, function returns []."""
    monkeypatch.setattr(watcher, "_AWAITING_INPUT_DIR",
                        str(tmp_path / "nope"))
    assert watcher.tpa_pids_awaiting_input() == []


def test_empty_dir_returns_empty(tmp_awaiting_dir):
    """Empty dir == no TPA awaiting input."""
    assert watcher.tpa_pids_awaiting_input() == []


def test_alive_pid_flag_is_reported(tmp_awaiting_dir):
    """A flag file whose PID is alive should be reported."""
    my_pid = os.getpid()
    (tmp_awaiting_dir / str(my_pid)).write_text(str(time.time()))
    assert watcher.tpa_pids_awaiting_input() == [my_pid]


def test_dead_pid_flag_is_evicted(tmp_awaiting_dir):
    """A flag file whose PID is dead should be removed AND not reported.

    TPA crashed mid-prompt -> stale flag -> Squid shouldn't wave forever.
    """
    dead_pid = 999999            # essentially guaranteed not to exist
    flag = tmp_awaiting_dir / str(dead_pid)
    flag.write_text(str(time.time()))
    pids = watcher.tpa_pids_awaiting_input()
    assert dead_pid not in pids
    assert not flag.exists(), "dead-PID flag should have been evicted"


def test_multiple_alive_pids_all_reported(tmp_awaiting_dir):
    """Multi-TPA: every flag with an alive PID is reported."""
    my_pid = os.getpid()
    parent_pid = os.getppid()
    (tmp_awaiting_dir / str(my_pid)).write_text("a")
    (tmp_awaiting_dir / str(parent_pid)).write_text("b")
    pids = sorted(watcher.tpa_pids_awaiting_input())
    assert pids == sorted([my_pid, parent_pid])


def test_non_integer_filenames_ignored(tmp_awaiting_dir):
    """Random files in the dir (e.g. .DS_Store) shouldn't crash us."""
    (tmp_awaiting_dir / ".DS_Store").write_text("junk")
    (tmp_awaiting_dir / "README.md").write_text("hi")
    (tmp_awaiting_dir / str(os.getpid())).write_text("real")
    pids = watcher.tpa_pids_awaiting_input()
    assert pids == [os.getpid()]


# ── Integration: compute() should fire approval_needed instantly when
#                a flag is present, ignoring CPU heuristics entirely. ──

def test_compute_fires_approval_needed_when_flag_present(
    tmp_awaiting_dir, monkeypatch,
):
    """The whole point: drop the CPU-idle guessing -- flag presence is
    the SOLE trigger when the awaiting_input signal is available."""
    my_pid = os.getpid()
    (tmp_awaiting_dir / str(my_pid)).write_text(str(time.time()))

    from unittest.mock import MagicMock
    sm = watcher.StateMachine()
    sm._tpa_detector = MagicMock(tpa_running=True)
    # Force the inner cascade to pick a TPA-active state -- the override
    # should kick in REGARDLESS of what _compute_inner returned.
    sm._compute_inner = lambda: watcher.PetState(
        state="working", message="x", tpa_running=True,
    )
    # No procs / per-proc idle at all -- direct signal alone fires.
    with patch.object(watcher, "find_tpa_processes",
                      return_value=[]):
        with patch("squid_pet.config.get") as mg:
            mg.side_effect = lambda k, default=None: {
                "approval_alert_enabled": True,
                "approval_alert_threshold_sec": 10.0,
                "approval_alert_sound": "Glass",
                "approval_alert_text": "your turn",
            }.get(k, default)
            st = sm.compute()
    assert st.state == "approval_needed", (
        f"flag present should fire approval_needed; got {st.state!r}, "
        f"reason={st.state_reason!r}"
    )
    assert "awaiting_input" in st.state_reason.lower() or \
           "flag" in st.state_reason.lower()
