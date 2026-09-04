"""Tests for macos_idle_seconds() -- CPU fix 6 (2026-09-03).

Reading "how long since the user touched the keyboard" used to fork
`ioreg -c IOHIDSystem` every second and parse 96KB of text out of it.
Measured inside the real daemon that cost 134ms of CPU per tick -- half
of Squid's entire tick -- because forking a process carrying Cocoa,
WebKit and 11 threads is far more expensive than the 36.5ms a small
harness suggested.

CoreGraphics answers the same question directly:
CGEventSourceSecondsSinceLastEventType, measured at 0.0014ms and
agreeing with ioreg to within the time ioreg itself takes to run.
ioreg stays as the fallback.
"""
from __future__ import annotations

import subprocess

import pytest

from squid_pet import watcher


@pytest.fixture(autouse=True)
def _reset_quartz():
    watcher._QUARTZ_IDLE_FN = None
    yield
    watcher._QUARTZ_IDLE_FN = None


class _ForkSpy:
    """Stands in for subprocess.run so a fork can be detected."""

    def __init__(self, stdout="", raises=None):
        self.calls = 0
        self._stdout = stdout
        self._raises = raises

    def __call__(self, *a, **k):
        self.calls += 1
        if self._raises:
            raise self._raises
        return subprocess.CompletedProcess(a[0] if a else [], 0, self._stdout, "")


IOREG_OUTPUT = '    | |   "HIDIdleTime" = 7500000000\n'


def test_reads_idle_time_without_forking(monkeypatch):
    """The whole point: no subprocess on the happy path."""
    spy = _ForkSpy()
    monkeypatch.setattr(subprocess, "run", spy)
    watcher._QUARTZ_IDLE_FN = lambda: 42.5

    assert watcher.macos_idle_seconds() == 42.5
    assert spy.calls == 0


def test_falls_back_to_ioreg_when_coregraphics_is_unavailable(monkeypatch):
    """A machine without the Quartz bindings still gets a real answer."""
    spy = _ForkSpy(stdout=IOREG_OUTPUT)
    monkeypatch.setattr(subprocess, "run", spy)
    monkeypatch.setattr(watcher, "_quartz_idle_seconds", lambda: None)

    assert watcher.macos_idle_seconds() == pytest.approx(7.5)
    assert spy.calls == 1


def test_falls_back_when_the_coregraphics_call_itself_fails(monkeypatch):
    """Bindings present but the call blows up (no window server, say) --
    fall back rather than propagate."""
    spy = _ForkSpy(stdout=IOREG_OUTPUT)
    monkeypatch.setattr(subprocess, "run", spy)

    def _boom():
        raise RuntimeError("no window server")

    watcher._QUARTZ_IDLE_FN = _boom

    assert watcher.macos_idle_seconds() == pytest.approx(7.5)
    assert spy.calls == 1


def test_returns_zero_when_both_paths_fail(monkeypatch):
    """Unchanged contract: idle is best-effort, and 0.0 means 'assume the
    user is right here' -- never let a failed read put her to sleep."""
    monkeypatch.setattr(subprocess, "run", _ForkSpy(raises=OSError("nope")))
    monkeypatch.setattr(watcher, "_quartz_idle_seconds", lambda: None)

    assert watcher.macos_idle_seconds() == 0.0


def test_missing_bindings_are_not_retried_every_tick(monkeypatch):
    """Resolving the import is remembered, so a machine without Quartz
    does not pay an ImportError once a second forever."""
    attempts = {"n": 0}
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def _counting_import(name, *a, **k):
        if name == "Quartz":
            attempts["n"] += 1
            raise ImportError("no Quartz here")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _counting_import)
    monkeypatch.setattr(subprocess, "run", _ForkSpy(stdout=IOREG_OUTPUT))

    watcher.macos_idle_seconds()
    watcher.macos_idle_seconds()
    watcher.macos_idle_seconds()

    assert attempts["n"] == 1
