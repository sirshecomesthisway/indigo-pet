"""Tests for watcher.latest_shell_child_cmdline() -- Pink-2026-08-27k:
replaces the removed legacy-agent-only latest_shell_child_cmdline, feeding
the "still working" reannounce bubble a concrete "running X" instead of
staying silent. Mirrors has_active_shell_children's recursive-children
walk / SHELL_CHILD_NAMES matching, but returns the cmdline instead of
just a bool.
"""
from __future__ import annotations

from squid_pet.watcher import latest_shell_child_cmdline


class _FakeChild:
    def __init__(self, name, cmdline):
        self._name = name
        self._cmdline = cmdline

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmdline


class _FakeProc:
    def __init__(self, children=None):
        self._children = children or []

    def children(self, recursive=False):
        return self._children


def test_no_procs_returns_none():
    assert latest_shell_child_cmdline([]) is None
    assert latest_shell_child_cmdline(None) is None


def test_returns_cmdline_of_matching_shell_child():
    proc = _FakeProc(children=[_FakeChild("pytest", ["pytest", "-v", "tests/"])])
    assert latest_shell_child_cmdline([proc]) == ["pytest", "-v", "tests/"]


def test_no_matching_child_returns_none():
    proc = _FakeProc(children=[_FakeChild("some_random_daemon", ["whatever"])])
    assert latest_shell_child_cmdline([proc]) is None


def test_ignores_child_with_empty_cmdline():
    """A matching-name child whose cmdline() somehow comes back empty
    should be skipped, not returned as an empty list."""
    proc = _FakeProc(children=[_FakeChild("git", [])])
    assert latest_shell_child_cmdline([proc]) is None


def test_matches_first_shell_child_across_multiple_processes():
    proc1 = _FakeProc(children=[_FakeChild("unrelated", ["x"])])
    proc2 = _FakeProc(children=[_FakeChild("git", ["git", "push", "origin", "main"])])
    assert latest_shell_child_cmdline([proc1, proc2]) == ["git", "push", "origin", "main"]


def test_skips_wrapper_shell_and_finds_real_command_beneath_it():
    """Pink-2026-08-27k regression, confirmed live: Claude Code's real
    Bash-tool invocation is `zsh -c 'source <snapshot> && ... && eval
    "<real command>" ...'` -- zsh itself matches SHELL_CHILD_NAMES first
    and its cmdline is a long, useless housekeeping string. Must skip it
    and keep walking to the real tool (spawned as zsh's own child once
    the eval'd command actually runs)."""
    wrapper = _FakeChild("zsh", ["/bin/zsh", "-c", "source snapshot.sh && ... && eval 'git push'"])
    real_cmd = _FakeChild("git", ["git", "push", "origin", "main"])

    class _NestedProc(_FakeProc):
        """wrapper.children() would include real_cmd in a real recursive
        walk (psutil's children(recursive=True) flattens all depths) --
        simulate that flattened order directly."""

    proc = _NestedProc(children=[wrapper, real_cmd])
    assert latest_shell_child_cmdline([proc]) == ["git", "push", "origin", "main"]


def test_wrapper_shell_alone_with_no_real_command_beneath_returns_none():
    """If the only match is the wrapper itself (real command still
    embedded in an eval string, not yet spawned as a separate process),
    correctly report nothing rather than the ugly wrapper cmdline."""
    wrapper = _FakeChild("bash", ["/bin/bash", "-c", "source snapshot.sh && ..."])
    proc = _FakeProc(children=[wrapper])
    assert latest_shell_child_cmdline([proc]) is None


def test_fake_process_without_children_method_does_not_crash():
    """Real detector tests construct bare objects (pid-only) with no
    .children() at all -- must degrade to None, not raise."""
    class _Bare:
        pid = 1234

    assert latest_shell_child_cmdline([_Bare()]) is None
