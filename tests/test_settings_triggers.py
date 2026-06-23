"""Tests for the build_detectors factory + DEFAULT_TRIGGERS."""
from __future__ import annotations
from squid_pet.detectors import (
    build_detectors, TPADetector, GitDetector,
    TerminalDetector, IDEDetector, DEFAULT_TRIGGERS,
)


def test_empty_settings_yields_all_four_enabled():
    ds = build_detectors(settings=None)
    assert len(ds) == 4
    assert {d.name for d in ds} == {"tpa", "git", "terminal", "ide"}
    for d in ds:
        assert d.enabled is True


def test_explicit_opt_out_disables_one_detector():
    s = {"triggers": {"git": False}}
    ds = build_detectors(settings=s)
    by_name = {d.name: d for d in ds}
    assert by_name["git"].enabled is False
    assert by_name["tpa"].enabled is True
    assert by_name["terminal"].enabled is True
    assert by_name["ide"].enabled is True


def test_all_off_yields_all_disabled():
    s = {"triggers": {
        "tpa": False, "git": False,
        "terminal": False, "ide": False,
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
    for k in ("tpa", "git", "terminal", "ide",
              "project_dirs", "ide_processes"):
        assert k in DEFAULT_TRIGGERS


def test_detector_order_puts_tpa_first():
    """Important: state.json schema fields cpu_percent +
    tpa_running come from TPADetector."""
    ds = build_detectors(settings=None)
    assert ds[0].name == "tpa"
