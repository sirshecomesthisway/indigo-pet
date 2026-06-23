"""
Tests for the cocoa-main-thread pre-commit hook.

Strategy: write temporary .py files into tmp_path with various
shapes and assert the hook flags / passes them correctly.
"""
from __future__ import annotations
import sys
from pathlib import Path
import importlib.util


def _load_hook():
    """Load scripts/check_cocoa_main_thread.py as a module (it has no
    package structure)."""
    here = Path(__file__).resolve().parent.parent
    script = here / "scripts" / "check_cocoa_main_thread.py"
    spec = importlib.util.spec_from_file_location("cocoa_hook", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOOK = _load_hook()


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sample.py"
    p.write_text(body)
    return p


# ---------------------------------------------------------------- violations
def test_unguarded_setframeorigin_is_flagged(tmp_path):
    p = _write(tmp_path, """
def m():
    nw.setFrameOrigin_(NSPoint(0, 0))
""")
    v = HOOK.find_violations(p)
    assert len(v) == 1
    assert "setFrameOrigin_" in v[0][1]


def test_module_level_setter_is_flagged(tmp_path):
    """A setter at module level (no enclosing function) is always suspicious."""
    p = _write(tmp_path, "nw.setAlphaValue_(0.5)\n")
    v = HOOK.find_violations(p)
    assert len(v) == 1


# ---------------------------------------------------------------- safe paths
def test_decorated_function_is_safe(tmp_path):
    p = _write(tmp_path, """
@cocoa_main_thread
def m():
    nw.setFrameOrigin_(NSPoint(0, 0))
""")
    assert HOOK.find_violations(p) == []


def test_decorated_blocking_variant_is_safe(tmp_path):
    p = _write(tmp_path, """
@cocoa_main_thread_blocking
def m():
    return nw.frame()
""")
    # frame() isnt in SUSPICIOUS but lets also check a setter
    p2 = _write(tmp_path, """
@cocoa_main_thread_blocking
def m():
    nw.orderFront_(None)
""")
    assert HOOK.find_violations(p2) == []


def test_qualified_decorator_is_safe(tmp_path):
    """e.g. @guards.cocoa_main_thread should also pass."""
    p = _write(tmp_path, """
@guards.cocoa_main_thread
def m():
    nw.setFrameOrigin_(NSPoint(0, 0))
""")
    assert HOOK.find_violations(p) == []


def test_callafter_in_same_function_is_safe(tmp_path):
    """Setter inside a function that uses AppHelper.callAfter is acceptable."""
    p = _write(tmp_path, """
def m():
    def _inner():
        nw.setFrameOrigin_(NSPoint(0, 0))
    AppHelper.callAfter(_inner)
""")
    assert HOOK.find_violations(p) == []


def test_noqa_bypass(tmp_path):
    p = _write(tmp_path, """
def m():
    nw.setFrameOrigin_(NSPoint(0, 0))  # noqa: cocoa-main-thread -- handled
""")
    assert HOOK.find_violations(p) == []


# ---------------------------------------------------------------- main()
def test_main_returns_0_when_clean(tmp_path, capsys):
    p = _write(tmp_path, "x = 1\n")
    rc = HOOK.main(["check_cocoa_main_thread.py", str(p)])
    assert rc == 0


def test_main_returns_1_and_prints_when_violations(tmp_path, capsys):
    p = _write(tmp_path, """
def m():
    nw.setFrameOrigin_(NSPoint(0, 0))
""")
    rc = HOOK.main(["check_cocoa_main_thread.py", str(p)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "Cocoa-main-thread audit FAILED" in captured.err
    assert "setFrameOrigin_" in captured.err
    assert "noqa: cocoa-main-thread" in captured.err  # fix instructions


# ---------------------------------------------------------------- live repo
def test_live_codebase_has_zero_violations():
    """Sanity check: after Groups 1+2 of safe-startup-verification, the
    actual repo should have no violations. If this fails, the hook
    found a regression."""
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src" / "squid_pet"
    total = []
    for py in src_dir.glob("*.py"):
        # Skip .bak files just in case glob picks them up
        if ".bak" in py.name:
            continue
        for ln, text in HOOK.find_violations(py):
            total.append((py.name, ln, text))
    assert total == [], (
        f"live codebase has unguarded NSWindow setters: {total}. "
        "Either decorate them or audit threading_guards.py."
    )
