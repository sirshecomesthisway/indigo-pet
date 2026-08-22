"""Smoke tests for the `--why` and `--why-json` CLI flags.

These don't try to assert exact emoji output (which depends on
runtime state); they just verify the command exits 0 and produces
parseable output containing the expected sections.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).parent.parent


def _run(*flags) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "squid_pet", *flags],
        capture_output=True, text=True, timeout=15,
        cwd=str(PROJECT),
    )


def test_why_human_output_contains_expected_sections():
    """--why prints state header, DETECTORS section, and VERDICT line."""
    result = _run("--why")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = result.stdout
    assert "squid-pet state:" in out
    assert "DETECTORS:" in out
    assert "VERDICT:" in out
    # Default detectors should be listed
    for name in ("claude_code", "codex", "git", "terminal", "ide"):
        assert name in out, f"missing detector {name} in --why output"


def test_why_json_is_valid_json_with_expected_shape():
    """--why-json output must be parseable and have the documented shape."""
    result = _run("--why-json")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    report = json.loads(result.stdout)
    assert "state" in report
    assert "detectors" in report
    assert "verdict" in report
    # State dict has the schema fields
    for k in ("state", "cpu_percent", "tpa_running",
              "idle_seconds", "agent_idle_seconds", "timestamp"):
        assert k in report["state"], f"missing state.{k}"
    # Each detector entry has the trigger flags
    assert len(report["detectors"]) >= 1
    for d in report["detectors"]:
        for k in ("name", "enabled", "fired_busy",
                  "fired_celebrating", "fired_grooving"):
            assert k in d, f"detector {d.get('name')} missing {k}"
    # Verdict is a non-empty string
    assert isinstance(report["verdict"], str)
    assert len(report["verdict"]) > 0


def test_why_human_surfaces_approval_alert_toggle():
    """Pink-2026-06-29 silent-kill-switch fix: --why MUST surface whether
    'Your turn' alerts are enabled. We learned the hard way that a False
    flag in config.json silently disables every approval_needed override
    in the cascade -- with no visible cue. --why is the diagnostic of last
    resort and must show this."""
    result = _run("--why")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = result.stdout.lower()
    assert "approval alert" in out, (
        "--why output must mention 'approval alert' status. "
        "Got:\n" + result.stdout
    )
    # Should be marked as on/off so Pink can spot the kill switch instantly
    assert ("on" in out) or ("off" in out)


def test_why_json_includes_approval_alert_fields():
    """--why-json must expose the approval-alert config + live per-proc idle
    so scripts/agents can diagnose 'why didn't Squid wave her flag?' without
    reading config.json directly."""
    result = _run("--why-json")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    report = json.loads(result.stdout)
    assert "approval_alert" in report, (
        "--why-json must include a top-level 'approval_alert' block. "
        "Got keys: " + str(list(report.keys()))
    )
    aa = report["approval_alert"]
    for k in ("enabled", "threshold_sec", "per_proc_max_idle_sec"):
        assert k in aa, f"approval_alert.{k} missing; got {aa}"
    assert isinstance(aa["enabled"], bool)
    assert isinstance(aa["threshold_sec"], (int, float))
    assert isinstance(aa["per_proc_max_idle_sec"], (int, float))


def test_why_help_advertises_both_flags():
    result = _run("--help")
    assert result.returncode == 0
    assert "--why" in result.stdout
    assert "--why-json" in result.stdout
