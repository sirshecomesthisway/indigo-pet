"""Tests for watcher.shell_child_activity() -- CPU fix 2 (2026-09-03).

has_active_shell_children() and latest_shell_child_cmdline() each walked
the entire descendant tree of every agent process, and the second ran
only to re-find the child the first had already seen and discarded. At
1 Hz across two agents that is four full tree walks a second. This
merges them into ONE walk returning both signals.

The two signals deliberately disagree about wrapper shells -- see
SHELL_WRAPPER_NAMES -- so the merged walk carries two accumulators:
`active` latches on any SHELL_CHILD_NAMES match (wrappers included,
because a live wrapper IS evidence a tool is running), while `cmdline`
latches only on a non-wrapper match with a real cmdline.
"""
from __future__ import annotations

from squid_pet.watcher import shell_child_activity


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
        self.walks = 0

    def children(self, recursive=False):
        self.walks += 1
        return self._children


def test_no_procs_is_inactive_and_silent():
    assert shell_child_activity([]) == (False, None)
    assert shell_child_activity(None) == (False, None)


def test_matching_child_reports_active_and_its_cmdline():
    proc = _FakeProc(children=[_FakeChild("pytest", ["pytest", "-v", "tests/"])])
    assert shell_child_activity([proc]) == (True, ["pytest", "-v", "tests/"])


def test_no_matching_child_is_inactive_and_silent():
    proc = _FakeProc(children=[_FakeChild("some_random_daemon", ["whatever"])])
    assert shell_child_activity([proc]) == (False, None)


def test_wrapper_shell_alone_is_active_but_unreportable():
    """The wrapper being alive is real evidence a tool is running (so
    active stays True -- this is has_active_shell_children's contract),
    but its cmdline is the useless snapshot-sourcing preamble, so there
    is nothing to report."""
    wrapper = _FakeChild("bash", ["/bin/bash", "-c", "source snapshot.sh && ..."])
    assert shell_child_activity([_FakeProc(children=[wrapper])]) == (True, None)


def test_walks_past_the_wrapper_to_the_real_command():
    wrapper = _FakeChild("zsh", ["/bin/zsh", "-c", "source snapshot.sh && eval 'git push'"])
    real_cmd = _FakeChild("git", ["git", "push", "origin", "main"])
    proc = _FakeProc(children=[wrapper, real_cmd])
    assert shell_child_activity([proc]) == (True, ["git", "push", "origin", "main"])


def test_matching_child_with_empty_cmdline_is_active_but_silent():
    """Name matched, so a tool IS running; an empty cmdline is not
    reportable and must not surface as an empty list."""
    proc = _FakeProc(children=[_FakeChild("git", [])])
    assert shell_child_activity([proc]) == (True, None)


def test_finds_the_command_across_multiple_processes():
    proc1 = _FakeProc(children=[_FakeChild("unrelated", ["x"])])
    proc2 = _FakeProc(children=[_FakeChild("git", ["git", "push", "origin", "main"])])
    assert shell_child_activity([proc1, proc2]) == (True, ["git", "push", "origin", "main"])


def test_fake_process_without_children_method_does_not_crash():
    class _Bare:
        pid = 1234

    assert shell_child_activity([_Bare()]) == (False, None)


def test_a_confirmed_match_survives_a_later_unwalkable_process():
    """Regression guard for the merge itself: the old bool function
    returned True the instant it matched, so a broken process object
    later in the list could not undo it. The merged walk keeps going
    (it still wants a cmdline), so it must return what it already
    proved rather than collapsing to False."""
    class _Bare:
        pid = 1234

    good = _FakeProc(children=[_FakeChild("bash", ["/bin/bash", "-c", "..."])])
    assert shell_child_activity([good, _Bare()]) == (True, None)


def test_walks_each_process_tree_once():
    """The whole point of the merge: one traversal per process, not two."""
    proc = _FakeProc(children=[_FakeChild("pytest", ["pytest"])])
    shell_child_activity([proc])
    assert proc.walks == 1
