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
    # All 4 default detectors should be listed
    for name in ("tpa", "git", "terminal", "ide"):
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


def test_why_help_advertises_both_flags():
    result = _run("--help")
    assert result.returncode == 0
    assert "--why" in result.stdout
    assert "--why-json" in result.stdout
