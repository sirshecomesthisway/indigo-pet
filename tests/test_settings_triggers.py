"""Tests for the build_detectors factory + DEFAULT_TRIGGERS."""
from __future__ import annotations
from squid_pet.detectors import (
    build_detectors, TPADetector, GitDetector,
    TerminalDetector, IDEDetector, DEFAULT_TRIGGERS,
)


def test_empty_settings_yields_five_enabled_one_off():
    """Default config: tpa + claude_code + codex + git + ide are
    on, terminal is off (2026-06-25: terminal misfires on any dev
    machine -- see test_explicit_opt_out_disables_one_detector for
    context)."""
    ds = build_detectors(settings=None)
    assert len(ds) == 6
    assert {d.name for d in ds} == {
        "tpa", "claude_code", "codex", "git", "terminal", "ide",
    }
    by_name = {d.name: d for d in ds}
    assert by_name["tpa"].enabled is True
    assert by_name["claude_code"].enabled is True
    assert by_name["codex"].enabled is True
    assert by_name["git"].enabled is True
    assert by_name["terminal"].enabled is False
    assert by_name["ide"].enabled is True


def test_explicit_opt_out_disables_one_detector():
    """terminal default is False (2026-06-25): the TerminalDetector
    misfires on any dev machine -- any long-lived shell child counts
    as 'busy', flooding the state machine with false thinking. Users
    who actually want terminal-based emoting opt in via settings.json.
    """
    s = {"triggers": {"git": False}}
    ds = build_detectors(settings=s)
    by_name = {d.name: d for d in ds}
    assert by_name["git"].enabled is False
    assert by_name["tpa"].enabled is True
    assert by_name["terminal"].enabled is False  # off by default
    assert by_name["ide"].enabled is True

    # And when explicitly turned on, terminal honors that.
    s2 = {"triggers": {"terminal": True}}
    by_name2 = {d.name: d for d in build_detectors(settings=s2)}
    assert by_name2["terminal"].enabled is True


def test_all_off_yields_all_disabled():
    s = {"triggers": {
        "tpa": False, "claude_code": False, "codex": False,
        "git": False, "terminal": False, "ide": False,
    }}
    ds = build_detectors(settings=s)
    for d in ds:
        assert d.enabled is False
    # And none of them should ever fire
    for d in ds:
        assert d.is_busy(now=1.0) is False
        assert d.is_celebrating(now=1.0) is False
        assert d.is_grooving(now=1.0) is False


def test_custom_project_dirs_propagate_to_git_and_ide():
    s = {"triggers": {"project_dirs": ["/some/path", "/another/path"]}}
    ds = build_detectors(settings=s)
    by_name = {d.name: d for d in ds}
    assert len(by_name["git"].project_dirs) == 2
    assert len(by_name["ide"].project_dirs) == 2


def test_custom_ide_processes_propagate():
    s = {"triggers": {"ide_processes": ["MyEditor"]}}
    ds = build_detectors(settings=s)
    by_name = {d.name: d for d in ds}
    assert "MyEditor" in by_name["ide"].ide_processes


def test_default_triggers_contains_expected_keys():
    for k in ("tpa", "claude_code", "codex", "git", "terminal", "ide",
              "project_dirs", "ide_processes"):
        assert k in DEFAULT_TRIGGERS


def test_detector_order_puts_tpa_first():
    """Important: state.json schema fields cpu_percent +
    tpa_running come from TPADetector."""
    ds = build_detectors(settings=None)
    assert ds[0].name == "tpa"
