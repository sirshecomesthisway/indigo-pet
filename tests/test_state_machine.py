"""
Unit tests for squid_pet.watcher.StateMachine.

Strategy: monkeypatch every I/O function at the module level
(psutil/filesystem/ioreg) and drive StateMachine.compute() through the
detector-agnostic parts of its priority cascade (sleeping, celebrating,
default idle) plus cross-tick memory.

Pink-2026-08-22/27: TPADetector, and later the entire TPA-
driven approval mechanism, were removed (TPA was never actually
installed/run on this machine, so none of it ever fired anything in
practice; the Claude-Code-native replacement -- an official Notification
hook -- has been live since 2026-08-26). The rich working/thinking/
celebrating tests for Claude Code and Codex live in
test_watcher_claude_code_cascade.py / test_watcher_codex_cascade.py.
"""
from __future__ import annotations

import time
import pytest

from squid_pet import watcher
from squid_pet.watcher import StateMachine


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def install_world(monkeypatch, idle=0.0):
    """Stub out the external signals the StateMachine consults directly:
    macOS idle time, and the Claude Code awaiting-input directory (must
    be isolated from the real ~/.squid-pet/claude_awaiting_input/ --
    without this, a real live flag on the developer's own machine makes
    approval_needed override every test here, which is exactly what
    happened once: this file's own tests failed against a real flag
    left by this very session)."""
    monkeypatch.setattr(watcher, "macos_idle_seconds", lambda: idle)
    monkeypatch.setattr(watcher, "CLAUDE_AWAITING_INPUT_DIR", "/nonexistent")


def make_machine_bare() -> StateMachine:
    """StateMachine with no detectors at all -- exercises the sleeping /
    celebrating / default-idle branches without any Claude/Codex
    involvement."""
    return StateMachine(detectors=[])


# ──────────────────────────────────────────────────────────────────────
# Priority 1 — SLEEPING
# ──────────────────────────────────────────────────────────────────────
def test_sleeping_when_macos_idle_exceeds_threshold(monkeypatch):
    install_world(monkeypatch, idle=400.0)
    sm = make_machine_bare()

    st = sm.compute()
    assert st.state == "sleeping"
    assert "idle" in st.message


def test_sleeping_takes_priority_over_everything(monkeypatch):
    """Sleeping wins even when a celebrate window is armed -- as long as
    no agent detector is reporting active busy-ness (with no detectors
    at all, make_machine_bare() has nothing to be busy). See
    test_watcher_claude_code_cascade.py for the 2026-08-27g case where
    a REAL agent busy signal suppresses sleeping instead."""
    install_world(monkeypatch, idle=watcher.IDLE_THRESHOLD_SEC + 1)
    sm = make_machine_bare()
    sm.celebrate_until = time.time() + 20
    st = sm.compute()
    assert st.state == "sleeping"


# ──────────────────────────────────────────────────────────────────────
# Priority 2 — CELEBRATING (held window)
# ──────────────────────────────────────────────────────────────────────
def test_celebrating_held_for_duration(monkeypatch):
    install_world(monkeypatch)
    sm = make_machine_bare()
    sm.celebrate_until = time.time() + 19  # armed 1s ago, 20s hold

    st = sm.compute()
    assert st.state == "celebrating"
    assert "nice" in st.message


def test_celebrating_window_expires(monkeypatch):
    install_world(monkeypatch)
    sm = make_machine_bare()
    sm.celebrate_until = time.time() - 1  # already expired

    st = sm.compute()
    assert st.state != "celebrating"


# ──────────────────────────────────────────────────────────────────────
# Priority 6 — Default IDLE
# ──────────────────────────────────────────────────────────────────────
def test_default_idle_with_no_signals(monkeypatch):
    install_world(monkeypatch)
    sm = make_machine_bare()
    st = sm.compute()
    assert st.state == "idle"


# ──────────────────────────────────────────────────────────────────────
# agent_idle_seconds tracking (generic: tracks time since any active state,
# not specific to TPA despite the field name)
# ──────────────────────────────────────────────────────────────────────
def test_agent_idle_seconds_zero_when_active(monkeypatch):
    """While state is active (e.g. celebrating), agent_idle should be 0."""
    install_world(monkeypatch)
    sm = make_machine_bare()
    sm.celebrate_until = time.time() + 20
    st = sm.compute()
    assert st.state == "celebrating"
    assert st.agent_idle_seconds == 0.0


def test_agent_idle_seconds_starts_ticking_when_state_becomes_idle(monkeypatch):
    install_world(monkeypatch)
    sm = make_machine_bare()

    st1 = sm.compute()
    assert st1.state == "idle"
    assert st1.agent_idle_seconds == 0.0  # first idle tick — clock just started

    # Force the internal clock back so the next tick reads as 5s elapsed.
    sm._agent_idle_since = time.time() - 5.0
    st2 = sm.compute()
    assert st2.state == "idle"
    assert st2.agent_idle_seconds >= 4.9


def test_agent_idle_resets_on_transition_to_active(monkeypatch):
    """Going idle → celebrating should zero agent_idle_seconds."""
    install_world(monkeypatch)
    sm = make_machine_bare()
    sm.compute()                             # land in idle
    sm._agent_idle_since = time.time() - 60.0  # pretend 60s of idle

    # Now flip to celebrating
    sm.celebrate_until = time.time() + 20
    st = sm.compute()
    assert st.state == "celebrating"
    assert st.agent_idle_seconds == 0.0
    assert sm._agent_idle_since == 0.0


# ──────────────────────────────────────────────────────────────────────
# PetState shape sanity
# ──────────────────────────────────────────────────────────────────────
def test_petstate_default_fields():
    """Make sure the dataclass shape doesn't drift without us noticing."""
    from squid_pet.watcher import PetState
    st = PetState()
    assert st.state == "idle"
    assert st.sub_state == ""
    assert st.idle_seconds == 0.0
    assert st.agent_idle_seconds == 0.0
    assert st.claude_code_running is False
    assert st.codex_running is False
    assert st.timestamp == 0.0
    assert st.message == ""
    assert st.concern_reason == ""
    assert st.concern_severity == ""
