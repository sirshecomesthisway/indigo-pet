"""Tests for the per-pid cmdline cache -- CPU fix 3 (2026-09-03).

_find_processes_by_argv0_basename() had to call cmdline() on every one
of ~520 processes to find the handful of agent binaries, because
Process.name() lies about the `claude` binary (see
test_find_claude_code_processes). That is ~35ms, and it ran once per
agent per tick.

A process's argv is fixed once it has exec'd, so the answer can be
reused -- as long as we can prove it is still the SAME process. Identity
is (pid, create_time); create_time is free because psutil already
fetched it to build the Process object.
"""
from __future__ import annotations

import time

import psutil
import pytest

from squid_pet import watcher


SETTLED = time.time() - 3600.0   # old enough that its argv cannot still change


@pytest.fixture(autouse=True)
def _clear_cache():
    watcher._CMDLINE_CACHE.clear()
    yield
    watcher._CMDLINE_CACHE.clear()


class _FakeProc:
    def __init__(self, pid, cmdline, create_time=SETTLED, raises=None):
        self.pid = pid
        self._cmdline = cmdline
        self._create_time = create_time
        self._raises = raises
        self.cmdline_calls = 0

    def create_time(self):
        return self._create_time

    def cmdline(self):
        self.cmdline_calls += 1
        if self._raises:
            raise self._raises
        return self._cmdline


def _iter_returning(procs, monkeypatch):
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))


def test_settled_process_cmdline_is_fetched_once_across_lookups(monkeypatch):
    """The whole point: the second lookup of the tick (Codex, after
    Claude Code) must not re-read 520 process argv blocks."""
    procs = [_FakeProc(1, ["claude"]), _FakeProc(2, ["zsh"]), _FakeProc(3, ["/usr/bin/ssh"])]
    _iter_returning(procs, monkeypatch)

    first = watcher.find_claude_code_processes()
    second = watcher.find_claude_code_processes()

    assert [p.pid for p in first] == [1]
    assert [p.pid for p in second] == [1]
    assert [p.cmdline_calls for p in procs] == [1, 1, 1]


def test_recycled_pid_is_not_answered_from_the_old_process(monkeypatch):
    """Same pid, different process. create_time is the identity guard --
    without it, a recycled pid would keep reporting the dead process's
    argv and Squid would miss a whole session."""
    old = _FakeProc(42, ["zsh"], create_time=SETTLED)
    _iter_returning([old], monkeypatch)
    assert watcher.find_claude_code_processes() == []

    recycled = _FakeProc(42, ["claude"], create_time=SETTLED + 1.0)
    _iter_returning([recycled], monkeypatch)

    assert [p.pid for p in watcher.find_claude_code_processes()] == [42]


def test_young_process_is_refetched_until_its_argv_settles(monkeypatch):
    """Between fork and exec a child still carries its PARENT's argv, and
    exec does not change create_time -- so a snapshot taken in that
    window would be wrong forever. Freshly-created processes are re-read
    rather than cached."""
    forking = _FakeProc(77, ["zsh"], create_time=time.time())
    _iter_returning([forking], monkeypatch)
    assert watcher.find_claude_code_processes() == []

    exec_ed = _FakeProc(77, ["claude"], create_time=forking._create_time)
    _iter_returning([exec_ed], monkeypatch)

    assert [p.pid for p in watcher.find_claude_code_processes()] == [77]


def test_unreadable_cmdline_is_remembered_too(monkeypatch):
    """202 of ~520 processes on this machine deny cmdline access, at
    ~6ms a pass. A denial is as stable as the argv itself for a given
    process instance, so asking again every tick is pure waste."""
    denied = _FakeProc(5, [], raises=psutil.AccessDenied(pid=5))
    _iter_returning([denied], monkeypatch)

    watcher.find_claude_code_processes()
    watcher.find_claude_code_processes()

    assert denied.cmdline_calls == 1


def test_dead_processes_are_evicted(monkeypatch):
    """The cache must not grow for the life of the daemon."""
    _iter_returning([_FakeProc(1, ["claude"]), _FakeProc(2, ["zsh"])], monkeypatch)
    watcher.find_claude_code_processes()
    assert set(watcher._CMDLINE_CACHE) == {1, 2}

    _iter_returning([_FakeProc(1, ["claude"])], monkeypatch)
    watcher.find_claude_code_processes()

    assert set(watcher._CMDLINE_CACHE) == {1}


def test_process_without_create_time_still_matches_and_is_never_cached(monkeypatch):
    """Identity is unprovable without create_time, so such a process is
    read fresh every time rather than trusted. (Real psutil always has
    it; test doubles elsewhere in the suite do not.)"""
    class _NoIdentity:
        pid = 8
        cmdline_calls = 0

        def cmdline(self):
            _NoIdentity.cmdline_calls += 1
            return ["claude"]

    proc = _NoIdentity()
    _iter_returning([proc], monkeypatch)
    assert [p.pid for p in watcher.find_claude_code_processes()] == [8]
    _iter_returning([proc], monkeypatch)
    assert [p.pid for p in watcher.find_claude_code_processes()] == [8]

    assert _NoIdentity.cmdline_calls == 2
    assert watcher._CMDLINE_CACHE == {}


def test_codex_subcommand_filter_reuses_the_cached_cmdline(monkeypatch):
    """find_codex_processes re-reads cmdline to screen out headless
    subcommands; that read must come from the cache, not the kernel."""
    interactive = _FakeProc(11, ["codex"])
    headless = _FakeProc(12, ["codex", "app-server"])
    _iter_returning([interactive, headless], monkeypatch)

    assert [p.pid for p in watcher.find_codex_processes()] == [11]
    assert interactive.cmdline_calls == 1
    assert headless.cmdline_calls == 1
