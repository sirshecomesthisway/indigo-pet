"""One project-tree walk per tick, shared by every detector -- CPU fix 5.

ClaudeCodeDetector (10s window) and IDEDetector (30s window) each walked
~/Projects separately, every second. Measured on this machine the walk
alone costs 67-186ms, which is essentially all of what those two
detectors cost in the daemon (74ms and 65ms of a ~100ms tick).

The windows are nested, so one walk over the widest window answers both:
each detector filters the shared ages down to its own window. IDEDetector
already did exactly this internally for its own 5s/30s pair.

Caveat, deliberate and documented: _scan_recent_file_ages stops early
once max_files (200) matches are collected, so a walk over the WIDER
window can stop sooner than a narrow one would have. That only bites
when 200+ files change within 30s; on this machine a 30s window matches
0 files, and IDEDetector has always accepted the same trade-off for its
own two windows.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from squid_pet import detectors as D
from squid_pet.detectors import ClaudeCodeDetector, IDEDetector


@pytest.fixture(autouse=True)
def _clear_tree_cache():
    """The shared walk is a module-level cache, so it outlives a test."""
    D._tree_walk_cache = None
    yield
    D._tree_walk_cache = None


class _Proc:
    pid = 1


def _claude(**kw):
    return ClaudeCodeDetector(
        find_processes_fn=lambda: [_Proc()],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        shell_cmdline_fn=lambda p: None,
        projects_dir=Path("/fake/.claude/projects"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        project_dirs=["/fake/Projects"],
        **kw,
    )


def _ide(**kw):
    return IDEDetector(process_iter_fn=lambda: iter([]),
                       project_dirs=["/fake/Projects"], **kw)


def _counting_walk(monkeypatch, ages):
    calls = []

    def _walk(dirs, window_sec, *, now=None, **kw):
        calls.append(window_sec)
        return [a for a in ages if a <= window_sec]

    monkeypatch.setattr(D, "_scan_recent_file_ages", _walk)
    return calls


def test_two_detectors_share_one_walk_within_a_tick(monkeypatch):
    calls = _counting_walk(monkeypatch, [2.0, 20.0])
    claude, ide = _claude(), _ide()

    claude._scan(now=1000.0)
    ide._scan(now=1000.0)

    assert len(calls) == 1, f"expected one walk, got {calls}"


def test_the_shared_walk_covers_the_widest_window(monkeypatch):
    """Whoever walks first must collect enough for the other."""
    calls = _counting_walk(monkeypatch, [2.0, 20.0])

    _claude()._scan(now=1000.0)      # Claude only needs 10s

    assert calls == [IDEDetector.GROOVING_WINDOW_SEC]


def test_each_detector_still_sees_only_its_own_window(monkeypatch):
    """Sharing must not widen anyone's window: a file touched 20s ago is
    IDE grooving activity, but it is NOT Claude file activity."""
    _counting_walk(monkeypatch, [2.0, 20.0])
    claude, ide = _claude(), _ide()

    claude._scan(now=1000.0)
    ide._scan(now=1000.0)

    assert claude.file_active is True          # the 2.0s file is in its 10s window
    assert ide.recent_file_count_grooving == 2  # both are within 30s
    assert ide.recent_file_count_busy == 1      # only the 2.0s file is within 5s


def test_claude_ignores_files_outside_its_own_window(monkeypatch):
    _counting_walk(monkeypatch, [20.0])
    claude = _claude()

    claude._scan(now=1000.0)

    assert claude.file_active is False


def test_the_next_tick_walks_again(monkeypatch):
    calls = _counting_walk(monkeypatch, [2.0])
    claude = _claude()

    claude._scan(now=1000.0)
    claude._scan(now=1001.0)

    assert len(calls) == 2


def test_different_project_dirs_do_not_share_a_walk(monkeypatch):
    calls = _counting_walk(monkeypatch, [2.0])

    _claude()._scan(now=1000.0)
    ClaudeCodeDetector(
        find_processes_fn=lambda: [_Proc()],
        aggregate_cpu_fn=lambda p: 0.0,
        has_active_shell_children_fn=lambda p: False,
        shell_cmdline_fn=lambda p: None,
        projects_dir=Path("/fake/.claude/projects"),
        glob_fn=lambda root: iter([]),
        stat_fn=lambda p: (_ for _ in ()).throw(OSError()),
        project_dirs=["/somewhere/else"],
    )._scan(now=1000.0)

    assert len(calls) == 2
