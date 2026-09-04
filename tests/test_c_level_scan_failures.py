"""A psutil C-layer failure must cost one scan, not the whole tick.

Seen live at startup:

    [squid-pet] watcher error: <built-in function proc_cmdline>
    returned a result with an exception set

psutil raises that SystemError out of macOS's KERN_PROCARGS2 path, and
it comes out of the process_iter GENERATOR -- so it lands outside every
per-process try/except in the scan loops, escapes compute(), and the
watcher thread's blanket handler drops the entire tick: no state, no
state.json write, Squid frozen for that second.

The prefetch (process_iter(["name"])) is what triggers it, because on
macOS psutil's name() re-reads the whole cmdline at the 15-char
truncation limit. Fix 4 already removed the two big prefetches; these
tests make the iteration itself unable to take a tick down, whatever
psutil throws next.
"""
from __future__ import annotations

import psutil
import pytest

from squid_pet import detectors as D
from squid_pet import watcher
from squid_pet.detectors import IDEDetector, TerminalDetector

BOOM = "<built-in function proc_cmdline> returned a result with an exception set"


@pytest.fixture(autouse=True)
def _clear_caches():
    watcher._CMDLINE_CACHE.clear()
    D._tree_walk_cache = None
    yield
    watcher._CMDLINE_CACHE.clear()
    D._tree_walk_cache = None


class _Proc:
    def __init__(self, pid, argv=None, name="zsh", cpu=0.0, children=()):
        self.pid = pid
        self._argv = argv or []
        self._name = name
        self._cpu = cpu
        self._children = list(children)

    def create_time(self):
        return 1000.0

    def cmdline(self):
        return self._argv

    def name(self):
        return self._name

    def cpu_percent(self):
        return self._cpu

    def children(self, recursive=False):
        return self._children


def _iter_then_explode(*procs):
    """psutil's own generator raising mid-iteration, the way the C layer
    does -- the failure is NOT attached to any one process object."""
    def _gen(*a, **k):
        for p in procs:
            yield p
        raise SystemError(BOOM)
    return _gen


def test_agent_lookup_survives_a_c_level_failure_mid_scan(monkeypatch):
    monkeypatch.setattr(
        psutil, "process_iter",
        _iter_then_explode(_Proc(1, ["claude"]), _Proc(2, ["zsh"])))

    assert [p.pid for p in watcher.find_claude_code_processes()] == [1]


def test_ide_scan_survives_a_c_level_failure_mid_scan(monkeypatch):
    monkeypatch.setattr(
        psutil, "process_iter",
        _iter_then_explode(_Proc(1, ["/Applications/Cursor.app/Contents/MacOS/Cursor"], cpu=9.0)))
    d = IDEDetector(recent_files_fn=lambda w: [])

    d._scan(now=1000.0)   # must not raise

    assert d.cpu_percent == 9.0


def test_terminal_scan_survives_a_c_level_failure_mid_scan(monkeypatch):
    """TerminalDetector was still asking for the ["name", ...] prefetch,
    the exact call that produces this SystemError."""
    child = _Proc(9, name="pytest")
    shell = _Proc(1, name="zsh", children=[child])
    monkeypatch.setattr(psutil, "process_iter", _iter_then_explode(shell))
    d = TerminalDetector()

    assert d.is_busy(now=1000.0 + 999) is True


def test_terminal_scan_does_not_ask_psutil_to_prefetch_attributes(monkeypatch):
    seen = {}

    def _spy(*a, **k):
        seen["args"] = (a, k)
        return iter([])

    monkeypatch.setattr(psutil, "process_iter", _spy)
    TerminalDetector().is_busy(now=1000.0)

    assert seen["args"] == ((), {}), (
        f"process_iter must be called with no attrs, got {seen['args']}"
    )


def test_child_walk_survives_a_c_level_failure_on_one_child():
    """Same failure one level down: reading a child's cmdline blows up.
    That child is unreadable, not the whole walk."""
    class _BadChild:
        def name(self):
            return "git"

        def cmdline(self):
            raise SystemError(BOOM)

    good = _Proc(2, argv=["pytest", "-v"], name="pytest")
    parent = _Proc(1, children=[_BadChild(), good])

    assert watcher.shell_child_activity([parent]) == (True, ["pytest", "-v"])
