"""
Unit tests for squid_pet.watcher.StateMachine.

Strategy: monkeypatch every I/O function at the module level
(psutil/filesystem/ioreg) and drive StateMachine.compute() through the
detector-agnostic parts of its priority cascade (sleeping, celebrating,
auto-wake, default idle) plus cross-tick memory.

Pink-2026-08-22: TPADetector removed (TPA was never
actually installed/run on this machine, so its busy/thinking/working/
celebrating/grooving/concerned role never fired anything in practice).
The rich working/thinking/celebrating tests for Claude Code and Codex
live in test_watcher_claude_code_cascade.py / test_watcher_codex_cascade.py.
Tests here that exercised TPA-only branches (grooving, concerned, CPU-
heuristic working/thinking, busy_streak) were removed along with them.
"""
from __future__ import annotations

import time
import pytest

from squid_pet import watcher
from squid_pet.watcher import StateMachine


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def install_world(monkeypatch, **overrides):
    """Stub out every external signal the StateMachine consults.

    Defaults represent 'completely quiet system: no TPA, no idle'.
    Override per-test via kwargs.
    """
    defaults = dict(
        idle=0.0,                # macos_idle_seconds
        procs=[],                # find_tpa_processes
        cpu=0.0,                 # aggregate_cpu
    )
    defaults.update(overrides)

    monkeypatch.setattr(watcher, "macos_idle_seconds",
                        lambda: defaults["idle"])
    monkeypatch.setattr(watcher, "find_tpa_processes",
                        lambda: defaults["procs"])
    monkeypatch.setattr(watcher, "aggregate_cpu",
                        lambda procs: defaults["cpu"])


def make_machine_bare() -> StateMachine:
    """StateMachine with no detectors at all -- exercises the sleeping /
    celebrating / default-idle branches without any TPA/Claude/Codex
    involvement."""
    return StateMachine(detectors=[])


# ──────────────────────────────────────────────────────────────────────
# Priority 1 — SLEEPING
# ──────────────────────────────────────────────────────────────────────
def test_sleeping_when_macos_idle_exceeds_threshold(monkeypatch):
    install_world(monkeypatch, idle=400.0, procs=["fake"], cpu=20.0)
    sm = make_machine_bare()

    st = sm.compute()
    assert st.state == "sleeping"
    assert "idle" in st.message


def test_sleeping_takes_priority_over_everything(monkeypatch):
    """Sleeping wins even when a celebrate window is armed."""
    install_world(
        monkeypatch,
        idle=watcher.IDLE_THRESHOLD_SEC + 1,
        procs=["fake"],
        cpu=50.0,
    )
    sm = make_machine_bare()
    sm.celebrate_until = time.time() + 20
    st = sm.compute()
    assert st.state == "sleeping"


# ──────────────────────────────────────────────────────────────────────
# Priority 2 — CELEBRATING (held window)
# ──────────────────────────────────────────────────────────────────────
def test_celebrating_held_for_duration(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_bare()
    sm.celebrate_until = time.time() + 19  # armed 1s ago, 20s hold

    st = sm.compute()
    assert st.state == "celebrating"
    assert "nice" in st.message


def test_celebrating_window_expires(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_bare()
    sm.celebrate_until = time.time() - 1  # already expired

    st = sm.compute()
    assert st.state != "celebrating"


# ──────────────────────────────────────────────────────────────────────
# Priority 3 — IDLE (no tpa running)
# ──────────────────────────────────────────────────────────────────────
def test_idle_when_no_tpa(monkeypatch):
    install_world(monkeypatch, procs=[])  # no procs
    sm = make_machine_bare()

    st = sm.compute()
    assert st.state == "idle"
    assert st.tpa_running is False


def test_default_idle_when_tpa_running_with_no_signals(monkeypatch):
    """tpa_running is still populated accurately even though TPA
    no longer drives working/thinking/etc -- the approval_needed alert
    still keys off it."""
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_bare()
    st = sm.compute()
    assert st.state == "idle"
    assert st.tpa_running is True


# ──────────────────────────────────────────────────────────────────────
# agent_idle_seconds tracking (generic: tracks time since any active state,
# not specific to TPA despite the field name)
# ──────────────────────────────────────────────────────────────────────
def test_agent_idle_seconds_zero_when_active(monkeypatch):
    """While state is active (e.g. celebrating), agent_idle should be 0."""
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_bare()
    sm.celebrate_until = time.time() + 20
    st = sm.compute()
    assert st.state == "celebrating"
    assert st.agent_idle_seconds == 0.0


def test_agent_idle_seconds_starts_ticking_when_state_becomes_idle(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
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
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
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
    assert st.cpu_percent == 0.0
    assert st.idle_seconds == 0.0
    assert st.agent_idle_seconds == 0.0
    assert st.tpa_running is False
    assert st.timestamp == 0.0
    assert st.message == ""
    assert st.concern_reason == ""
    assert st.concern_severity == ""


# ----------------------------------------------------------------------
# Auto-wake from sleeping (dumb-wake): sleep 10 min -> 3-min wake window
# ----------------------------------------------------------------------
def test_auto_wake_opens_window_after_10_min_sleeping(monkeypatch):
    """After AUTO_WAKE_AFTER_SLEEPING_SEC in sleeping, the next tick suppresses
    sleeping and falls through to a non-sleeping state (idle by default)."""
    install_world(monkeypatch, idle=900.0)  # 15 min idle (well past threshold)
    sm = make_machine_bare()

    # First tick: enters sleeping, _sleeping_since stamped.
    st1 = sm.compute()
    assert st1.state == "sleeping"
    assert sm._sleeping_since > 0.0

    # Backdate _sleeping_since to simulate 11 min of continuous sleeping.
    sm._sleeping_since = time.time() - (watcher.AUTO_WAKE_AFTER_SLEEPING_SEC + 60)

    # Next tick: auto-wake window opens, sleeping is suppressed.
    st2 = sm.compute()
    assert st2.state != "sleeping", \
        f"expected auto-wake to suppress sleeping, got state={st2.state}"
    # Default fall-through is idle (no TPA, no detectors).
    assert st2.state == "idle"
    # Window is now active.
    assert sm._force_awake_until > time.time()
    # _sleeping_since reset so the next sleep period can re-arm.
    assert sm._sleeping_since == 0.0


def test_sleeping_returns_after_wake_window_expires(monkeypatch):
    """Once _force_awake_until passes, sleeping check fires again."""
    install_world(monkeypatch, idle=900.0)
    sm = make_machine_bare()

    # Simulate: wake window opened in the past, now expired.
    sm._force_awake_until = time.time() - 1.0
    sm._sleeping_since = 0.0  # post-wake state

    st = sm.compute()
    assert st.state == "sleeping"
    # New sleeping period started -> _sleeping_since freshly stamped.
    assert sm._sleeping_since > 0.0


def test_user_return_clears_auto_wake_state(monkeypatch):
    """When idle drops below threshold (user came back), both bookkeeping
    flags clear so the next sleeping period starts from a fresh timer."""
    install_world(monkeypatch, idle=900.0)
    sm = make_machine_bare()
    # Pretend we were in the middle of a sleep cycle, mid-wake-window.
    sm._sleeping_since = time.time() - 300
    sm._force_awake_until = time.time() + 100

    # User comes back: idle drops to 5s.
    install_world(monkeypatch, idle=5.0)
    st = sm.compute()
    assert st.state != "sleeping"
    assert sm._sleeping_since == 0.0
    assert sm._force_awake_until == 0.0
