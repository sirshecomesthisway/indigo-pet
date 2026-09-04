"""Tests for the passthrough heartbeat log line (2026-09-03).

The ~3s heartbeat printed `dwelling={dwelling}`, a variable left behind
by the hover-fade rewrite. Python only evaluates the f-string when the
line runs, so it raised NameError once every 100 ticks -- swallowed by
the loop's blanket `except Exception`, which turned Squid's only
passthrough diagnostic into `passthrough error: name 'dwelling' is not
defined` forever. Building the line in a plain function makes that
failure a test away instead of a runtime surprise.
"""
from __future__ import annotations

from squid_pet.passthrough import _heartbeat_line


def _line(**over):
    kwargs = dict(
        tick=100, cursor=(239.4, 505.6), win=(1282.0, 688.0, 200.0, 300.0),
        inside=True, sprite=(80, 92), state="working", opaque=True,
        faded=False, click_through=False, ignore=True,
    )
    kwargs.update(over)
    return _heartbeat_line(**kwargs)


def test_reports_every_field_the_diagnostic_exists_for():
    line = _line()
    for fragment in ("tick 100", "cursor=(239,506)", "win=(1282,688,200x300)",
                     "inside=True", "sprite=(80,92)", "state=working",
                     "opaque=True", "ignore=True"):
        assert fragment in line, f"{fragment!r} missing from {line!r}"


def test_reports_the_dwell_outcome():
    """`dwelling` was the pre-rewrite name for this. What the loop
    actually computes now is the hover tracker's two outputs, so the
    heartbeat reports those."""
    line = _line(faded=True, click_through=True)
    assert "faded=True" in line
    assert "click_through=True" in line
