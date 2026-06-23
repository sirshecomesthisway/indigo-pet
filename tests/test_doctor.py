"""
Tests for squid_pet.doctor.

Strategy: each check is independently tested with pass + fail paths
via injection (pid_path / state_path / log_path arguments + lambdas
for window_lookup and corner_origin_fn). No live process / launchctl
/ Quartz needed in the test environment.

The orchestrator (run_doctor / run_all_checks) is tested by
monkeypatching CHECK_ORDER to a fake-checks list, so we verify the
exit-code logic without depending on the real check internals.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from squid_pet import doctor


# ----------------------------------------------------------------------
# check_process_running
# ----------------------------------------------------------------------
def test_process_running_pass(tmp_path):
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(os.getpid()))  # we are alive by definition
    r = doctor.check_process_running(pid_path=pid_file)
    assert r.passed
    assert "alive" in r.diagnostic


def test_process_running_no_pid_file(tmp_path):
    r = doctor.check_process_running(pid_path=tmp_path / "missing")
    assert not r.passed
    assert "no pid file" in r.diagnostic
    assert r.suggested_fix


def test_process_running_dead_pid(tmp_path):
    pid_file = tmp_path / "pid"
    # Use a pid almost-certain to be dead: 0x7FFFFFFE is way above ulimit
    pid_file.write_text("2147483646")
    r = doctor.check_process_running(pid_path=pid_file)
    assert not r.passed
    assert "not alive" in r.diagnostic


def test_process_running_garbage_pid_file(tmp_path):
    pid_file = tmp_path / "pid"
    pid_file.write_text("not-a-number")
    r = doctor.check_process_running(pid_path=pid_file)
    assert not r.passed
    assert "unreadable" in r.diagnostic


# ----------------------------------------------------------------------
# check_state_json_fresh
# ----------------------------------------------------------------------
def test_state_json_fresh_pass(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    r = doctor.check_state_json_fresh(state_path=state, max_age_sec=10.0)
    assert r.passed
    assert "ago" in r.diagnostic


def test_state_json_fresh_stale(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    # Pretend "now" is 999 seconds in the future
    future_now = time.time() + 999
    r = doctor.check_state_json_fresh(
        state_path=state, max_age_sec=5.0, now_fn=lambda: future_now
    )
    assert not r.passed
    assert "stalled" in r.suggested_fix.lower() or "watcher" in r.suggested_fix.lower()


def test_state_json_fresh_missing(tmp_path):
    r = doctor.check_state_json_fresh(state_path=tmp_path / "missing.json")
    assert not r.passed
    assert "no state file" in r.diagnostic


# ----------------------------------------------------------------------
# check_launchd_loaded (subprocess monkeypatched)
# ----------------------------------------------------------------------
class _FakeRun:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def test_launchd_loaded_pass(monkeypatch):
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **kw: _FakeRun(returncode=0),
    )
    r = doctor.check_launchd_loaded(label="com.test.fake")
    assert r.passed
    assert "loaded" in r.diagnostic


def test_launchd_loaded_not_loaded(monkeypatch):
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **kw: _FakeRun(returncode=113),
    )
    r = doctor.check_launchd_loaded(label="com.test.fake")
    assert not r.passed
    assert "rc=113" in r.diagnostic
    assert "launchctl bootstrap" in r.suggested_fix


def test_launchd_loaded_launchctl_missing(monkeypatch):
    def _raise(*a, **kw):
        raise FileNotFoundError("launchctl not found")
    monkeypatch.setattr(doctor.subprocess, "run", _raise)
    r = doctor.check_launchd_loaded(label="com.test.fake")
    assert not r.passed
    assert "launchctl call failed" in r.diagnostic


# ----------------------------------------------------------------------
# check_window_visible (window_lookup injected)
# ----------------------------------------------------------------------
def _fake_window(x=1500, y=100, w=200, h=300, alpha=1.0):
    return {"x": x, "y": y, "w": w, "h": h, "alpha": alpha}


def test_window_visible_pass(tmp_path):
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(os.getpid()))
    r = doctor.check_window_visible(
        pid_path=pid_file,
        window_lookup=lambda pid: _fake_window(),
    )
    assert r.passed
    assert "window at" in r.diagnostic


def test_window_visible_none_found(tmp_path):
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(os.getpid()))
    r = doctor.check_window_visible(
        pid_path=pid_file,
        window_lookup=lambda pid: None,
    )
    assert not r.passed
    assert "no visible window" in r.diagnostic
    assert "thread-safety" in r.suggested_fix


def test_window_visible_no_pid_file(tmp_path):
    r = doctor.check_window_visible(
        pid_path=tmp_path / "missing",
        window_lookup=lambda pid: _fake_window(),
    )
    assert not r.passed
    assert "no pid file" in r.diagnostic


# ----------------------------------------------------------------------
# check_window_in_expected_corner (renamed: window NOT wedged)
# ----------------------------------------------------------------------
def test_window_not_wedged_pass(tmp_path):
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(os.getpid()))
    r = doctor.check_window_in_expected_corner(
        pid_path=pid_file,
        position_path=tmp_path / "position.json",
        window_lookup=lambda pid: _fake_window(x=1500, y=50),
    )
    assert r.passed
    assert "not at" in r.diagnostic


def test_window_wedged_at_pywebview_default(tmp_path):
    """Direct reproduction of 2026-06-16 wedge: window at (100, 100)."""
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(os.getpid()))
    r = doctor.check_window_in_expected_corner(
        pid_path=pid_file,
        position_path=tmp_path / "position.json",
        window_lookup=lambda pid: _fake_window(x=100, y=100),
    )
    assert not r.passed
    assert "wedge" in r.diagnostic
    assert "kennel drawer 239" in r.drawer_ref
    assert "thread-safety" in r.suggested_fix or "cocoa_main_thread" in r.suggested_fix


def test_window_wedged_within_tolerance(tmp_path):
    """Window at (105, 95) -- still within 10px of (100,100), still flagged."""
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(os.getpid()))
    r = doctor.check_window_in_expected_corner(
        pid_path=pid_file,
        position_path=tmp_path / "position.json",
        window_lookup=lambda pid: _fake_window(x=105, y=95),
    )
    assert not r.passed


def test_window_not_wedged_no_visible_window(tmp_path):
    pid_file = tmp_path / "pid"
    pid_file.write_text(str(os.getpid()))
    r = doctor.check_window_in_expected_corner(
        pid_path=pid_file,
        position_path=tmp_path / "position.json",
        window_lookup=lambda pid: None,
    )
    assert not r.passed
    assert "no visible window" in r.diagnostic


# ----------------------------------------------------------------------
# check_startup_log_complete
# ----------------------------------------------------------------------
def test_startup_log_all_markers_pass(tmp_path):
    log = tmp_path / "out.log"
    body = "\n".join(
        f"[squid-pet] {m}" for m in doctor.REQUIRED_STARTUP_MARKERS
    )
    log.write_text(body + "\n[squid-pet] tick 100: ...\n")
    r = doctor.check_startup_log_complete(log_path=log)
    assert r.passed
    assert "all" in r.diagnostic and "markers present" in r.diagnostic


def test_startup_log_missing_one_marker(tmp_path):
    log = tmp_path / "out.log"
    # Omit "routine thread started"
    keep = [m for m in doctor.REQUIRED_STARTUP_MARKERS if m != "routine thread started"]
    log.write_text("\n".join(f"[squid-pet] {m}" for m in keep) + "\n")
    r = doctor.check_startup_log_complete(log_path=log)
    assert not r.passed
    assert "routine thread started" in r.diagnostic
    assert "missing startup markers" in r.diagnostic


def test_startup_log_missing_file(tmp_path):
    r = doctor.check_startup_log_complete(log_path=tmp_path / "missing.log")
    assert not r.passed
    assert "no log file" in r.diagnostic


def test_startup_log_only_reads_head(tmp_path):
    """If markers are present only AFTER the head_bytes budget, must fail."""
    log = tmp_path / "out.log"
    # 20 KB of padding, then markers
    padding = ("[squid-pet] tick X: lots of runtime noise here " * 200)
    body = padding + "\n" + "\n".join(
        f"[squid-pet] {m}" for m in doctor.REQUIRED_STARTUP_MARKERS
    )
    log.write_text(body)
    r = doctor.check_startup_log_complete(log_path=log, head_bytes=4_000)
    assert not r.passed, "should not find markers past the head budget"


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------
def _make_check(name, passed, diagnostic=""):
    def _fn():
        return doctor.CheckResult(name=name, passed=passed, diagnostic=diagnostic)
    return _fn


def test_run_doctor_all_pass_returns_0(monkeypatch, capsys):
    fake_order = (
        ("a", _make_check("a", True, "ok")),
        ("b", _make_check("b", True, "ok")),
    )
    monkeypatch.setattr(doctor, "CHECK_ORDER", fake_order)
    rc = doctor.run_doctor()
    out = capsys.readouterr().out
    assert rc == 0
    assert "2/2 checks passed" in out


def test_run_doctor_first_fail_returns_index(monkeypatch, capsys):
    fake_order = (
        ("a", _make_check("a", True)),
        ("b", _make_check("b", False, "broken")),
        ("c", _make_check("c", False, "also broken")),
    )
    monkeypatch.setattr(doctor, "CHECK_ORDER", fake_order)
    rc = doctor.run_doctor()
    assert rc == 2  # first failing check is index 2 (1-based)


def test_run_doctor_json_output(monkeypatch, capsys):
    fake_order = (
        ("a", _make_check("a", True, "ok")),
        ("b", _make_check("b", False, "fail")),
    )
    monkeypatch.setattr(doctor, "CHECK_ORDER", fake_order)
    rc = doctor.run_doctor(json_output=True)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["healthy"] is False
    assert len(payload["checks"]) == 2
    assert payload["checks"][0]["passed"] is True
    assert payload["checks"][1]["passed"] is False
    assert rc == 2
