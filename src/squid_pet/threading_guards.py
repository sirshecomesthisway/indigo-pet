"""
threading_guards
================

Cocoa-main-thread enforcement decorators for Squid.

Why this exists
---------------
On 2026-06-16 Squid silently wedged because ``move_to_corner()`` was
invoked from a WebKit content thread via pywebview's ``on_loaded``
fallback. On macOS 14+, ``NSWindow.setFrameOrigin_`` from a non-main
thread blocks indefinitely with no exception, no log, no crash. The
window stayed at pywebview's default ``(100, 100)`` while every health
check happily reported "process alive".

Root cause: nothing structurally prevented Cocoa calls from sneaking
onto worker threads. This module is layer 1 of the four-layer defense
(see ``openspec/changes/safe-startup-verification``).

Usage
-----
Decorate any function that touches NSWindow / NSApp / NSScreen / NSView::

    from squid_pet.threading_guards import cocoa_main_thread

    @cocoa_main_thread
    def move_to_corner(corner: str) -> None:
        nw.setFrameOrigin_(NSPoint(x, y))

If called from the main thread, runs inline (no overhead).
If called from any other thread, dispatches via
``PyObjCTools.AppHelper.callAfter`` (fire-and-forget) and returns
``None`` to the caller.

For the rare case where you need a return value AND must call from
a worker thread, use the blocking variant::

    @cocoa_main_thread_blocking
    def get_window_frame() -> NSRect:
        return nw.frame()

This waits up to 5 seconds on a threading.Event for the main-thread
dispatch to complete and returns the result (or raises TimeoutError).

Non-Mac systems
---------------
On systems without PyObjC (Linux CI), the decorators degrade to plain
pass-through: the wrapped function runs inline on the calling thread.
This keeps unit tests that don't actually touch Cocoa green in CI.
"""
from __future__ import annotations

import functools
import threading
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# ----------------------------------------------------------------------
# Platform detection -- import-time, cached
# ----------------------------------------------------------------------
try:
    from PyObjCTools import AppHelper  # type: ignore
    from AppKit import NSThread  # type: ignore

    _COCOA_AVAILABLE = True

    def _is_main_thread() -> bool:
        """True if current thread is the Cocoa main thread."""
        return bool(NSThread.isMainThread())

    def _dispatch_main(fn: Callable[..., Any], *args, **kwargs) -> None:
        """Fire-and-forget dispatch onto the Cocoa main thread."""
        if kwargs:
            # AppHelper.callAfter does not pass kwargs; wrap in a closure.
            AppHelper.callAfter(lambda: fn(*args, **kwargs))
        else:
            AppHelper.callAfter(fn, *args)

except ImportError:
    _COCOA_AVAILABLE = False

    def _is_main_thread() -> bool:
        # Fallback for non-Mac (Linux CI): use Python threading check.
        return threading.current_thread() is threading.main_thread()

    def _dispatch_main(fn: Callable[..., Any], *args, **kwargs) -> None:
        # No Cocoa -> just call inline. This branch only fires on non-Mac
        # systems where any "NSWindow" call would have failed at import
        # time anyway.
        fn(*args, **kwargs)


# ----------------------------------------------------------------------
# Public decorators
# ----------------------------------------------------------------------
def cocoa_main_thread(fn: F) -> F:
    """Run ``fn`` on the Cocoa main thread.

    If the caller is already on the main thread, ``fn`` runs inline and
    its return value is propagated. If called from any other thread,
    the call is dispatched fire-and-forget via ``AppHelper.callAfter``
    and the wrapper returns ``None`` immediately.

    This is the SAFE default for NSWindow setters that have no useful
    return value (``setFrameOrigin_``, ``setAlphaValue_``, etc.).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if _is_main_thread():
            return fn(*args, **kwargs)
        _dispatch_main(fn, *args, **kwargs)
        return None
    # mark so the audit / pre-commit hook can recognise the guard
    wrapper.__cocoa_guarded__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


def cocoa_main_thread_blocking(fn: F, timeout: float = 5.0) -> F:
    """Run ``fn`` on the Cocoa main thread and synchronously wait for
    its result (with a timeout).

    Use this only when the return value or exception MUST propagate to
    the caller. Carries thread-sync overhead and a small deadlock risk
    if the main thread is itself waiting on the calling thread.

    Raises:
        TimeoutError: if the main-thread dispatch does not complete
            within ``timeout`` seconds.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if _is_main_thread():
            return fn(*args, **kwargs)

        done = threading.Event()
        result_box: dict = {"value": None, "exc": None}

        def _runner():
            try:
                result_box["value"] = fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 -- propagate to caller
                result_box["exc"] = e
            finally:
                done.set()

        _dispatch_main(_runner)
        if not done.wait(timeout=timeout):
            raise TimeoutError(
                f"cocoa_main_thread_blocking: {fn.__qualname__} did not "
                f"complete within {timeout}s. Main thread may be blocked."
            )
        if result_box["exc"] is not None:
            raise result_box["exc"]
        return result_box["value"]

    wrapper.__cocoa_guarded__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


__all__ = [
    "cocoa_main_thread",
    "cocoa_main_thread_blocking",
    "_COCOA_AVAILABLE",
    "_is_main_thread",
]
