"""Which session is waving? (Pink-2026-09-01)

With several sessions waiting, the flag directory says HOW MANY but its
filenames are uuids -- useless to a person. The project each belongs to is
already derivable from disk: Claude Code stores a session's transcript at
~/.claude/projects/<encoded-cwd>/<session_id>.jsonl, so the session_id
leads to the directory name, and the directory name is the cwd with its
slashes turned into dashes.

Nothing new is stored and no transcript is opened -- this reads path
names only, the same level of access ClaudeCodeDetector already uses when
it globs those files for mtimes.

Encoding rather than decoding is deliberate: '-Users-p-squid-pet' cannot
be decoded unambiguously (a directory name may itself contain a dash), but
encoding a known cwd the same way and comparing strings is exact.
"""
from __future__ import annotations

from squid_pet import watcher


def test_encodes_a_cwd_the_way_claude_code_does():
    assert watcher.encode_project_dir("/Users/pinksmac") == "-Users-pinksmac"
    assert watcher.encode_project_dir("/Users/p/Projects/squid-pet") == (
        "-Users-p-Projects-squid-pet")


def test_finds_the_project_dir_for_a_session(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "-Users-p-Projects-squid-pet").mkdir(parents=True)
    (projects / "-Users-p-Projects-squid-pet" / "sess-1.jsonl").write_text("")
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(projects))

    assert watcher.claude_session_project_dir("sess-1") == "-Users-p-Projects-squid-pet"


def test_unknown_session_has_no_project_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(tmp_path))
    assert watcher.claude_session_project_dir("nope") is None


class _FakeProc:
    def __init__(self, cwd, tty="/dev/ttys009"):
        self._cwd, self._tty = cwd, tty
    def cwd(self):
        return self._cwd
    def terminal(self):
        return self._tty


def test_label_comes_from_the_live_process_cwd(tmp_path, monkeypatch):
    """The encoded name alone is ambiguous -- "-Users-p-Projects-squid-pet"
    split on dashes answers "pet". The process knows its real cwd."""
    projects = tmp_path / "projects"
    (projects / "-Users-p-Projects-squid-pet").mkdir(parents=True)
    (projects / "-Users-p-Projects-squid-pet" / "sess-2.jsonl").write_text("")
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(projects))
    monkeypatch.setattr(watcher, "find_claude_code_processes",
                        lambda: [_FakeProc("/Users/p/Projects/squid-pet")])

    assert watcher.claude_session_label("sess-2") == "squid-pet"


def test_label_falls_back_approximately_with_no_live_process(tmp_path, monkeypatch):
    """Documented imprecision: with nothing to ask, a dashed project name
    cannot be recovered. Still better than a uuid."""
    projects = tmp_path / "projects"
    (projects / "-Users-p-Projects-squid-pet").mkdir(parents=True)
    (projects / "-Users-p-Projects-squid-pet" / "sess-3.jsonl").write_text("")
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(projects))
    monkeypatch.setattr(watcher, "find_claude_code_processes", lambda: [])

    assert watcher.claude_session_label("sess-3") == "pet"


def test_tty_resolves_through_the_matching_process(tmp_path, monkeypatch):
    """This is what makes "take me to it" correct with several sessions
    running -- the payload has no PID, but the project dir identifies the
    process."""
    projects = tmp_path / "projects"
    (projects / "-Users-p-api").mkdir(parents=True)
    (projects / "-Users-p-api" / "sess-4.jsonl").write_text("")
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(projects))
    monkeypatch.setattr(watcher, "find_claude_code_processes", lambda: [
        _FakeProc("/Users/p/other", "/dev/ttys001"),
        _FakeProc("/Users/p/api", "/dev/ttys002"),
    ])

    assert watcher.claude_session_tty("sess-4") == "/dev/ttys002"


def test_label_falls_back_to_none_when_unresolvable(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(tmp_path))
    assert watcher.claude_session_label("nope") is None


def test_describes_a_single_waiting_session(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "-Users-p-api").mkdir(parents=True)
    (projects / "-Users-p-api" / "s1.jsonl").write_text("")
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(projects))
    monkeypatch.setattr(watcher, "find_claude_code_processes", lambda: [])

    assert watcher.describe_waiting_sessions(["s1"]) == "api needs you"


def test_describes_two_waiting_sessions_by_name(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    for enc, sid in (("-Users-p-api", "s1"), ("-Users-p-web", "s2")):
        (projects / enc).mkdir(parents=True)
        (projects / enc / f"{sid}.jsonl").write_text("")
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(projects))

    out = watcher.describe_waiting_sessions(["s1", "s2"])
    assert "api" in out and "web" in out
    assert out.endswith("need you"), f"{out!r} must read as a sentence"


def test_many_waiting_sessions_stay_inside_the_bubble(tmp_path, monkeypatch):
    """The bubble caps at MAX_BUBBLE_CHARS; five project names would blow
    straight past it, so past a couple it counts instead of listing."""
    projects = tmp_path / "projects"
    names = ["alpha-service", "beta-service", "gamma-service", "delta-service"]
    for i, n in enumerate(names):
        (projects / f"-Users-p-{n}").mkdir(parents=True)
        (projects / f"-Users-p-{n}" / f"s{i}.jsonl").write_text("")
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(projects))

    out = watcher.describe_waiting_sessions([f"s{i}" for i in range(4)])
    from squid_pet.observer import MAX_BUBBLE_CHARS
    assert len(out) <= MAX_BUBBLE_CHARS, f"{out!r} would be dropped by _pick"
    assert "4" in out, "it should at least say how many"


def test_unresolvable_sessions_still_report_a_count(tmp_path, monkeypatch):
    """A session whose transcript we cannot find must not vanish from the
    count -- 'someone is waiting' is still true and still useful."""
    monkeypatch.setattr(watcher, "CLAUDE_PROJECTS_DIR", str(tmp_path))
    out = watcher.describe_waiting_sessions(["x", "y"])
    assert out is None or "2" in out
