"""
Tests for squid_pet.threading_guards.

Strategy: cover the four behaviors of the decorators:
  1. main-thread fast path (run inline, return value propagates)
  2. off-thread dispatch path (fire-and-forget returns None)
  3. blocking variant: off-thread + waits for result
  4. blocking variant: timeout + exception propagation

We monkeypatch ``_is_main_thread`` and ``_dispatch_main`` at module
level to drive deterministic branches without needing an actual Cocoa
runloop in the test process.
"""
from __future__ import annotations

import threading
import time
import pytest

from squid_pet import threading_guards as tg
from squid_pet.threading_guards import (
    cocoa_main_thread,
    cocoa_main_thread_blocking,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
class _FakeDispatcher:
    """Captures dispatched calls and executes them synchronously on
    a fresh thread, simulating Cocoa's main-thread runloop."""

    def __init__(self):
        self.calls: list[tuple] = []

    def dispatch(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        # Run in a separate thread so the simulated "main thread"
        # isn't the caller's thread.
        t = threading.Thread(target=fn, args=args, kwargs=kwargs)
        t.start()
        t.join()


# ----------------------------------------------------------------------
# cocoa_main_thread (fire-and-forget)
# ----------------------------------------------------------------------
def test_main_thread_fast_path_runs_inline_and_returns_value(monkeypatch):
    monkeypatch.setattr(tg, "_is_main_thread", lambda: True)

    @cocoa_main_thread
    def f(x, y):
        return x + y

    assert f(2, 3) == 5


def test_off_thread_dispatches_and_returns_none(monkeypatch):
    fake = _FakeDispatcher()
    monkeypatch.setattr(tg, "_is_main_thread", lambda: False)
    monkeypatch.setattr(tg, "_dispatch_main", fake.dispatch)

    seen = []

    @cocoa_main_thread
    def f(x):
        seen.append(x)
        return "should-not-propagate"

    result = f(42)
    assert result is None, "off-thread call must return None"
    assert seen == [42], "dispatched call must have executed"
    assert len(fake.calls) == 1


def test_decorator_marks_function_as_guarded():
    @cocoa_main_thread
    def f():
        pass

    assert getattr(f, "__cocoa_guarded__", False) is True


# ----------------------------------------------------------------------
# cocoa_main_thread_blocking
# ----------------------------------------------------------------------
def test_blocking_main_thread_fast_path(monkeypatch):
    monkeypatch.setattr(tg, "_is_main_thread", lambda: True)

    @cocoa_main_thread_blocking
    def f(a):
        return a * 2

    assert f(7) == 14


def test_blocking_off_thread_returns_result(monkeypatch):
    fake = _FakeDispatcher()
    monkeypatch.setattr(tg, "_is_main_thread", lambda: False)
    monkeypatch.setattr(tg, "_dispatch_main", fake.dispatch)

    @cocoa_main_thread_blocking
    def f():
        return "hello-from-main"

    assert f() == "hello-from-main"


def test_blocking_off_thread_propagates_exception(monkeypatch):
    fake = _FakeDispatcher()
    monkeypatch.setattr(tg, "_is_main_thread", lambda: False)
    monkeypatch.setattr(tg, "_dispatch_main", fake.dispatch)

    @cocoa_main_thread_blocking
    def f():
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        f()


def test_blocking_timeout_raises_timeout_error(monkeypatch):
    """If the dispatcher never runs the queued callable, the wait
    times out and we raise TimeoutError with a useful message."""

    def never_dispatch(fn, *args, **kwargs):
        # Capture but never execute -- simulates a wedged main thread.
        pass

    monkeypatch.setattr(tg, "_is_main_thread", lambda: False)
    monkeypatch.setattr(tg, "_dispatch_main", never_dispatch)

    def f():
        return "never-reached"

    # Apply the decorator manually with a tiny timeout.
    wrapped = cocoa_main_thread_blocking(f, timeout=0.1)

    with pytest.raises(TimeoutError, match="did not complete within 0.1s"):
        wrapped()
