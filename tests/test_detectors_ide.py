"""Tests for IDEDetector via synthetic procs + injected recent_files_fn."""
from __future__ import annotations
import time
from pathlib import Path
from squid_pet.detectors import IDEDetector


class _Proc:
    def __init__(self, name, cpu=0.0):
        self._name = name
        self._cpu = cpu
        self.info = {"name": name}
    def name(self): return self._name
    def cpu_percent(self): return self._cpu


def _detector(procs=None, recent_ages=None, project_dirs=None,
              ide_processes=None, enabled=True):
    return IDEDetector(
        project_dirs=project_dirs or ["/nonexistent-test-path"],
        ide_processes=ide_processes,
        enabled=enabled,
        process_iter_fn=lambda: iter(procs or []),
        recent_files_fn=lambda window: list(recent_ages or []),
    )


def test_no_ide_no_files_is_quiet():
    d = _detector(procs=[], recent_ages=[])
    assert d.is_busy(now=1.0) is False
    assert d.is_grooving(now=1.0) is False


def test_cpu_busy_alone_does_not_fire():
    """Per design: VS Code indexing on its own shouldn't trigger busy."""
    d = _detector(procs=[_Proc("Code", cpu=50.0)], recent_ages=[])
    assert d.is_busy(now=1.0) is False


def test_recent_file_with_cpu_fires_busy():
    d = _detector(procs=[_Proc("Code", cpu=10.0)], recent_ages=[2.0])
    assert d.is_busy(now=1.0) is True


def test_recent_file_no_cpu_fires_busy_autosave():
    """Quiet CPU + recent autosave -> reflection mode, fire busy."""
    d = _detector(procs=[_Proc("Code", cpu=0.5)], recent_ages=[2.0])
    assert d.is_busy(now=1.0) is True


def test_old_recent_file_no_busy():
    d = _detector(procs=[_Proc("Code", cpu=10.0)], recent_ages=[20.0])
    assert d.is_busy(now=1.0) is False  # >5s window


def test_grooving_fires_at_5plus_files_within_30s():
    d = _detector(procs=[], recent_ages=[5.0, 10.0, 15.0, 20.0, 25.0])
    assert d.is_grooving(now=1.0) is True


def test_grooving_under_threshold_no_fire():
    d = _detector(procs=[], recent_ages=[5.0, 10.0])
    assert d.is_grooving(now=1.0) is False


def test_celebrating_always_false():
    d = _detector(procs=[_Proc("Code", cpu=80.0)], recent_ages=[1.0])
    assert d.is_celebrating(now=1.0) is False


def test_disabled_returns_false():
    d = _detector(procs=[_Proc("Code", cpu=80.0)], recent_ages=[1.0], enabled=False)
    assert d.is_busy(now=1.0) is False
    assert d.is_grooving(now=1.0) is False


def test_unmatched_process_name_ignored():
    """Slack burning CPU shouldnt trigger IDE detector."""
    d = _detector(
        procs=[_Proc("Slack", cpu=80.0)],
        recent_ages=[1.0],
        ide_processes=["Code", "Cursor"],
    )
    # Has recent file but cpu attributed to Slack is not Code/Cursor.
    # cpu_percent will be 0 (slack ignored), recent file fires autosave branch.
    assert d.is_busy(now=1.0) is True  # autosave branch -- this matches design
    # But importantly, cpu_percent didnt aggregate Slack
    assert d.cpu_percent == 0.0


def test_custom_ide_processes_recognized():
    d = _detector(
        procs=[_Proc("MyCustomEditor", cpu=10.0)],
        recent_ages=[1.0],
        ide_processes=["MyCustomEditor"],
    )
    d.is_busy(now=1.0)
    assert d.cpu_percent == 10.0


def test_diagnostic_shape():
    d = _detector(procs=[_Proc("Code", cpu=15.0)], recent_ages=[1.0, 10.0])
    d.is_busy(now=1.0); d.is_grooving(now=1.0)
    diag = d.diagnostic()
    for k in ("name", "enabled", "cpu_percent",
              "recent_file_count_5s", "recent_file_count_30s",
              "ide_processes", "project_dirs"):
        assert k in diag
    assert diag["name"] == "ide"


def test_default_recent_files_walks_real_tmp_path(tmp_path):
    """Smoke test of the default os.walk-based scanner."""
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.py").write_text("y = 2")
    d = IDEDetector(
        project_dirs=[str(tmp_path)],
        process_iter_fn=lambda: iter([]),
    )
    ages = d._default_recent_files(window_sec=10.0)
    assert len(ages) == 2  # both files just written


def test_default_recent_files_skips_junk_dirs(tmp_path):
    (tmp_path / "src" / "a.py").parent.mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1")
    (tmp_path / "node_modules" / "junk.js").parent.mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("y = 2")
    d = IDEDetector(
        project_dirs=[str(tmp_path)],
        process_iter_fn=lambda: iter([]),
    )
    ages = d._default_recent_files(window_sec=10.0)
    assert len(ages) == 1  # node_modules skipped


def test_scan_runs_once_per_watcher_tick():
    """One tick == one scan.

    ClaudeCodeDetector and CodexDetector both short-circuit on
    _last_scan_ts; IDEDetector did not, so a single watcher tick paid
    for two full process enumerations and two project-tree walks.
    """
    calls = {"procs": 0, "files": 0}

    def procs():
        calls["procs"] += 1
        return iter([_Proc("Code", cpu=10.0)])

    def files(window):
        calls["files"] += 1
        return [2.0]

    d = IDEDetector(
        project_dirs=["/nonexistent-test-path"],
        process_iter_fn=procs,
        recent_files_fn=files,
    )
    now = 1.0
    d.is_busy(now)
    d.is_celebrating(now)
    d.is_grooving(now)
    d.diagnostic()

    assert calls["procs"] == 1, f"process scan ran {calls['procs']}x in one tick"
    assert calls["files"] == 1, f"project-tree walk ran {calls['files']}x in one tick"


def test_next_tick_does_rescan():
    """The guard must not freeze state: a later tick scans again."""
    calls = {"n": 0}

    def procs():
        calls["n"] += 1
        return iter([_Proc("Code", cpu=10.0)])

    d = IDEDetector(
        project_dirs=["/nonexistent-test-path"],
        process_iter_fn=procs,
        recent_files_fn=lambda w: [2.0],
    )
    d.is_busy(1.0)
    d.is_busy(2.0)
    assert calls["n"] == 2


# ── CPU fix 4 (2026-09-04): stop paying for psutil's attr prefetch ────
# `process_iter(["name"])` measured 30.95ms across 520 processes, once a
# tick, because psutil's macOS name() falls back to reading the whole
# cmdline for any name at the 15-char truncation limit -- so asking for
# "name" quietly asks for every process's argv. A bare process_iter() is
# 0.995ms, and argv[0]'s basename is already cached per pid by the agent
# lookup (watcher._cmdline_cached).
#
# Checked before switching: across the live process table, name() and
# basename(argv[0]) agreed for 307 processes and differed for 9 -- the
# `claude` binary (name is its version), login shells (-zsh) and python
# version suffixes. All 75 GUI .app processes agreed, and every entry in
# DEFAULT_IDE_PROCESSES is a GUI app.
class _CmdlineProc:
    """A process the way psutil hands it over with no attrs prefetched:
    no .info, cmdline read on demand."""

    def __init__(self, pid, argv, cpu=0.0):
        self.pid = pid
        self._argv = argv
        self._cpu = cpu

    def cmdline(self):
        return self._argv

    def cpu_percent(self):
        return self._cpu


def test_matches_ide_on_argv0_basename_without_prefetched_info():
    procs = [
        _CmdlineProc(1, ["/Applications/Cursor.app/Contents/MacOS/Cursor"], cpu=7.5),
        _CmdlineProc(2, ["/usr/bin/some_daemon"], cpu=99.0),
    ]
    d = IDEDetector(process_iter_fn=lambda: iter(procs),
                    recent_files_fn=lambda w: [])
    d._scan(now=1000.0)

    assert d.cpu_percent == 7.5


def test_iterates_processes_without_asking_psutil_to_prefetch_attributes():
    """The prefetch IS the cost -- 31ms of the tick. Guard it."""
    seen = {}

    class _Spy:
        def __call__(self, *a, **k):
            seen["args"] = (a, k)
            return iter([])

    import psutil
    real_iter = psutil.process_iter
    spy = _Spy()
    psutil.process_iter = spy
    try:
        d = IDEDetector(recent_files_fn=lambda w: [])   # no seam: real psutil
        d._scan(now=1000.0)
    finally:
        psutil.process_iter = real_iter

    assert seen["args"] == ((), {}), (
        f"process_iter must be called with no attrs, got {seen['args']}"
    )
