"""Regression test for find_claude_code_processes()'s cmdline-basename
matching.

Guards against the bug found while wiring up ClaudeCodeDetector
(2026-08-14): psutil.Process.name() is NOT reliable for the `claude`
binary on macOS -- it returns the versioned install path's basename
(e.g. "2.1.227"), not "claude". Matching must go through cmdline()[0]
instead, same as find_tpa_processes.
"""
from __future__ import annotations

import psutil

from squid_pet import watcher


class _FakeProc:
    def __init__(self, pid, cmdline, name_value="not-claude"):
        self.pid = pid
        self._cmdline = cmdline
        self._name = name_value

    def cmdline(self):
        return self._cmdline

    def name(self):
        return self._name


def test_matches_bare_claude_cmdline(monkeypatch):
    procs = [
        _FakeProc(1, ["claude"], name_value="2.1.227"),
        _FakeProc(2, ["zsh"]),
        _FakeProc(3, []),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_claude_code_processes()
    assert [p.pid for p in matches] == [1]


def test_matches_full_path_claude_cmdline(monkeypatch):
    """Invoked via full path (e.g. /usr/local/bin/claude) still matches
    on the basename."""
    procs = [_FakeProc(7, ["/usr/local/bin/claude", "--flag"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_claude_code_processes()
    assert [p.pid for p in matches] == [7]


def test_does_not_match_name_only(monkeypatch):
    """Even if Process.name() happened to report 'claude', matching
    must go through cmdline -- this is the exact bug that was found."""
    procs = [_FakeProc(9, ["2.1.227"], name_value="claude")]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_claude_code_processes()
    assert matches == []


def test_process_iter_errors_are_skipped(monkeypatch):
    class _Dead(_FakeProc):
        def cmdline(self):
            raise psutil.NoSuchProcess(pid=1)

    procs = [_Dead(1, []), _FakeProc(2, ["claude"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_claude_code_processes()
    assert [p.pid for p in matches] == [2]
