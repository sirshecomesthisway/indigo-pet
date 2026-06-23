"""Tests for TerminalDetector via synthetic psutil-shaped objects."""
from __future__ import annotations
import time
from squid_pet.detectors import TerminalDetector


class _Child:
    def __init__(self, name, create_time):
        self._name = name
        self._create = create_time
        self.info = {"name": name, "create_time": create_time}
    def name(self): return self._name
    def create_time(self): return self._create


class _Proc:
    def __init__(self, name, create_time=0.0, children=None):
        self._name = name
        self._create = create_time
        self._children = children or []
        self.pid = id(self)
        self.info = {"name": name, "pid": self.pid, "create_time": create_time}
    def name(self): return self._name
    def create_time(self): return self._create
    def children(self): return self._children


def _detector_with(procs):
    return TerminalDetector(process_iter_fn=lambda: iter(procs))


def test_no_shells_is_quiet():
    d = _detector_with([_Proc("Code"), _Proc("Cursor")])
    assert d.is_busy(now=100.0) is False


def test_shell_with_no_children_is_quiet():
    d = _detector_with([_Proc("zsh", children=[])])
    assert d.is_busy(now=100.0) is False


def test_shell_with_recent_child_under_3s_is_quiet():
    """Just-started commands shouldn't trigger -- prevents shell-prompt flicker."""
    now = 100.0
    proc = _Proc("zsh", children=[_Child("python", create_time=now - 1.0)])
    d = _detector_with([proc])
    assert d.is_busy(now=now) is False


def test_shell_with_old_long_running_child_fires_busy():
    now = 100.0
    proc = _Proc("zsh", children=[_Child("pytest", create_time=now - 30.0)])
    d = _detector_with([proc])
    assert d.is_busy(now=now) is True


def test_shell_with_only_shell_children_is_quiet():
    """Nested shells shouldn't count -- it's still 'just sitting at prompt'."""
    now = 100.0
    proc = _Proc("zsh", children=[_Child("bash", create_time=now - 30.0)])
    d = _detector_with([proc])
    assert d.is_busy(now=now) is False


def test_multiple_shells_one_busy_fires_busy():
    now = 100.0
    procs = [
        _Proc("zsh", children=[]),
        _Proc("bash", children=[_Child("rg", create_time=now - 5.0)]),
        _Proc("fish", children=[]),
    ]
    d = _detector_with(procs)
    assert d.is_busy(now=now) is True


def test_celebrating_and_grooving_always_false():
    d = _detector_with([_Proc("zsh", children=[_Child("x", 0)])])
    assert d.is_celebrating(now=100.0) is False
    assert d.is_grooving(now=100.0) is False


def test_disabled_is_false():
    d = TerminalDetector(enabled=False, process_iter_fn=lambda: iter([
        _Proc("zsh", children=[_Child("python", create_time=0.0)])
    ]))
    assert d.is_busy(now=100.0) is False


def test_diagnostic_includes_count():
    now = 100.0
    procs = [_Proc("zsh", children=[_Child("rg", create_time=now - 10.0)])]
    d = _detector_with(procs)
    d.is_busy(now=now)
    diag = d.diagnostic()
    assert diag["name"] == "terminal"
    assert diag["active_shell_count"] == 1
