"""StateMachine hot-reloads detector list when settings.json changes.

Task 3.4 of trigger-broadening: changing ~/.squid-pet/settings.json must
take effect without a Squid restart. Implementation in watcher.py polls
the file's mtime on every compute() and rebuilds detectors when it
changes. Tests below pin every observable behavior.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from squid_pet import watcher


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Redirect StateMachine to a tmp settings.json we control."""
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(watcher.StateMachine, "_SETTINGS_FILE", settings_file)
    return settings_file


def _write(settings_file: Path, payload: dict) -> None:
    """Write settings.json and bump mtime far enough to be detected."""
    settings_file.write_text(json.dumps(payload))
    # mtime granularity on some filesystems is whole seconds -- nudge it
    # forward so the watcher's equality check definitively sees a change.
    future = time.time() + 5
    os.utime(settings_file, (future, future))


def test_no_settings_file_yields_defaults(isolated_settings):
    """Missing settings.json -> defaults (4 enabled: tpa/
    claude_code/git/ide, terminal off). And nothing crashes."""
    sm = watcher.StateMachine()
    by_name = {d.name: d.enabled for d in sm.detectors}
    assert by_name == {
        "tpa": True, "claude_code": True, "git": True,
        "terminal": False, "ide": True,
    }


def test_hot_reload_picks_up_new_setting(isolated_settings):
    """Toggle a setting on disk; next compute() rebuilds detectors."""
    # Initial state: defaults.
    sm = watcher.StateMachine()
    assert {d.name: d.enabled for d in sm.detectors}["git"] is True

    # Write a new settings.json disabling git.
    _write(isolated_settings, {"triggers": {"git": False}})

    # Trigger reload -- it's called from compute() at the top of each tick.
    sm._maybe_reload_settings()

    assert {d.name: d.enabled for d in sm.detectors}["git"] is False


def test_hot_reload_enables_terminal_when_user_opts_in(isolated_settings):
    """terminal defaults off; user opts in via settings.json -> on."""
    sm = watcher.StateMachine()
    assert {d.name: d.enabled for d in sm.detectors}["terminal"] is False

    _write(isolated_settings, {"triggers": {"terminal": True}})
    sm._maybe_reload_settings()

    assert {d.name: d.enabled for d in sm.detectors}["terminal"] is True


def test_no_reload_when_mtime_unchanged(isolated_settings):
    """Identical mtime -> no rebuild (cheap path: just one stat() call)."""
    _write(isolated_settings, {"triggers": {"git": True}})
    sm = watcher.StateMachine()
    original_ids = [id(d) for d in sm.detectors]

    # Call reload several times without touching the file.
    for _ in range(5):
        sm._maybe_reload_settings()

    new_ids = [id(d) for d in sm.detectors]
    assert original_ids == new_ids, \
        "detectors should be the same object instances (no rebuild)"


def test_explicit_detector_list_never_hot_reloads(isolated_settings):
    """If caller passed detectors=[...], we don't own the list -- the
    user-supplied detectors stay put even if settings.json changes."""
    from squid_pet.detectors import TPADetector
    custom = [TPADetector(enabled=False)]
    sm = watcher.StateMachine(detectors=custom)
    assert sm._owns_detectors is False

    _write(isolated_settings, {"triggers": {"git": True, "tpa": True}})
    sm._maybe_reload_settings()

    # Detector list still the custom one; not rebuilt from settings.
    assert len(sm.detectors) == 1
    assert sm.detectors[0] is custom[0]
    assert sm.detectors[0].enabled is False


def test_reload_refreshes_tpa_detector_ref(isolated_settings):
    """After a reload, _tpa_detector points to the NEW TPA detector
    instance, not the old one. Otherwise back-compat proxies leak."""
    sm = watcher.StateMachine()
    old_cp = sm._tpa_detector
    assert old_cp is not None

    _write(isolated_settings, {"triggers": {"tpa": True}})
    sm._maybe_reload_settings()

    new_cp = sm._tpa_detector
    assert new_cp is not None
    assert new_cp is not old_cp, "TPA detector should be a fresh instance"


def test_compute_invokes_reload(isolated_settings):
    """End-to-end: compute() actually calls reload as part of its tick."""
    sm = watcher.StateMachine()
    calls = [0]
    original = sm._maybe_reload_settings

    def counting():
        calls[0] += 1
        original()

    sm._maybe_reload_settings = counting
    sm.compute()
    assert calls[0] == 1


def test_corrupt_settings_file_does_not_crash(isolated_settings):
    """Garbage JSON in settings.json -> reload silently treats as
    empty dict (defaults applied), never raises."""
    isolated_settings.write_text("{not json")
    sm = watcher.StateMachine()  # initial load with corrupt file
    # Should still have built defaults despite bad JSON.
    assert {d.name for d in sm.detectors} == {
        "tpa", "claude_code", "git", "terminal", "ide",
    }
    # And reload on a still-corrupt file shouldn't raise.
    isolated_settings.write_text("still not json :(")
    future = time.time() + 10
    os.utime(isolated_settings, (future, future))
    sm._maybe_reload_settings()  # must not raise
    assert {d.name for d in sm.detectors} == {
        "tpa", "claude_code", "git", "terminal", "ide",
    }
