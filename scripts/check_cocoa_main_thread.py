"""
Pre-commit hook: refuse commits that introduce direct NSWindow/NSApp
setters without an @cocoa_main_thread decorator on an enclosing
function or an AppHelper.callAfter dispatch covering them.

This is layer 3 of the safe-startup-verification four-layer defense.
See docs/STARTUP_SAFETY.md and kennel drawer 239.

Detection strategy: AST-based. Identifies actual function-call attribute
access (e.g. ``nw.setFrameOrigin_(...)``) -- ignores docstrings, comments,
and string literals that merely mention the API name.

Acceptance rules (any one is sufficient):
  1. Any enclosing function (innermost or outer) is decorated with
     ``@cocoa_main_thread`` or ``@cocoa_main_thread_blocking``.
  2. The name of any enclosing function appears as the first arg to
     ``AppHelper.callAfter(<name>)`` anywhere in the file.
     (This handles the common pattern of defining ``_inner`` inside
     ``outer`` and then doing ``AppHelper.callAfter(_inner)``.)
  3. The line itself has a trailing ``# noqa: cocoa-main-thread`` marker.

Run manually:
    python scripts/check_cocoa_main_thread.py src/squid_pet/window.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


# Cocoa method names that MUST run on the main thread.
SUSPICIOUS_ATTRS = frozenset({
    "setFrameOrigin_", "setFrame_", "setAlphaValue_",
    "setIgnoresMouseEvents_", "setLevel_", "setCollectionBehavior_",
    "setOpaque_", "setBackgroundColor_", "orderFront_", "orderOut_",
    "makeKeyAndOrderFront_", "makeKeyWindow", "setSubviews_",
    "activateIgnoringOtherApps_", "setActivationPolicy_",
})

BYPASS_MARKER = "noqa: cocoa-main-thread"
GUARD_NAMES = frozenset({"cocoa_main_thread", "cocoa_main_thread_blocking"})


def _decorator_marks_safe(decorator: ast.AST) -> bool:
    """True if the decorator is one of the GUARD_NAMES (incl. qualified)."""
    if isinstance(decorator, ast.Call):
        return _decorator_marks_safe(decorator.func)
    if isinstance(decorator, ast.Name):
        return decorator.id in GUARD_NAMES
    if isinstance(decorator, ast.Attribute):
        return decorator.attr in GUARD_NAMES
    return False


def _build_parent_map(tree: ast.AST) -> dict:
    """Map each child node to its parent."""
    parent: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _enclosing_functions(node: ast.AST, parent_map: dict):
    """Yield enclosing FunctionDefs from innermost outward."""
    cur = parent_map.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield cur
        cur = parent_map.get(cur)


def _collect_callafter_dispatched_names(tree: ast.AST) -> set:
    """Return set of function names passed as the first positional arg
    to ``AppHelper.callAfter(name, ...)`` anywhere in the tree."""
    names: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "callAfter"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            names.add(first.id)
        elif isinstance(first, ast.Attribute):
            names.add(first.attr)
    return names


def find_violations(path: Path) -> list:
    """Return list of (lineno, stripped-text) for unguarded NSWindow setters."""
    try:
        src = path.read_text()
    except OSError:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []  # not our job to enforce syntax

    parent_map = _build_parent_map(tree)
    callafter_safe = _collect_callafter_dispatched_names(tree)
    lines = src.splitlines()

    violations = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in SUSPICIOUS_ATTRS:
            continue
        lineno = node.lineno
        line = lines[lineno - 1] if 1 <= lineno <= len(lines) else ""
        if BYPASS_MARKER in line:
            continue

        # Walk outward through enclosing functions: any guarded
        # decorator OR any matching callAfter-dispatched name accepts.
        safe = False
        for fn in _enclosing_functions(node, parent_map):
            if any(_decorator_marks_safe(d) for d in fn.decorator_list):
                safe = True
                break
            if fn.name in callafter_safe:
                safe = True
                break
        if safe:
            continue
        violations.append((lineno, line.strip()))
    return violations


def main(argv: list) -> int:
    paths = [Path(p) for p in argv[1:] if p.endswith(".py")]
    found = []
    for p in paths:
        for ln, text in find_violations(p):
            found.append((p, ln, text))
    if not found:
        return 0

    print("", file=sys.stderr)
    print("Cocoa-main-thread audit FAILED -- unguarded NSWindow setter(s):",
          file=sys.stderr)
    for p, ln, text in found:
        print(f"  {p}:{ln}: {text}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix one of three ways:", file=sys.stderr)
    print("  1. Add @cocoa_main_thread to the enclosing function", file=sys.stderr)
    print("     (import from squid_pet.threading_guards)", file=sys.stderr)
    print("  2. Wrap via AppHelper.callAfter(_closure_name)", file=sys.stderr)
    print("  3. Append `# noqa: cocoa-main-thread` with a justification", file=sys.stderr)
    print("", file=sys.stderr)
    print("Why this matters: docs/STARTUP_SAFETY.md and kennel drawer 239.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
