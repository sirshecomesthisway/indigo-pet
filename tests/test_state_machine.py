"""
Unit tests for squid_pet.watcher.StateMachine.

Strategy: monkeypatch every I/O function at the module level
(psutil/filesystem/ioreg) and drive StateMachine.compute() through the
detector-agnostic parts of its priority cascade (sleeping, celebrating,
default idle) plus cross-tick memory.

Pink-2026-08-22/27: the legacy agent's detector, and later the entire
approval mechanism it drove, were removed (that agent was never actually
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


def _agents_quiet_for(sm, seconds: float) -> None:
    """Backdate the agent-quiet clock compute() maintains."""
    import time as _t
    sm._agent_idle_since = _t.time() - seconds

def make_machine_bare() -> StateMachine:
    """StateMachine with no detectors at all -- exercises the sleeping /
    celebrating / default-idle branches without any Claude/Codex
    involvement."""
    return StateMachine(detectors=[])


# ──────────────────────────────────────────────────────────────────────
# Priority 1 — SLEEPING
# ──────────────────────────────────────────────────────────────────────
def test_sleeping_when_the_agents_have_been_quiet_past_the_threshold(monkeypatch):
    install_world(monkeypatch, idle=400.0)
    sm = make_machine_bare()
    _agents_quiet_for(sm, watcher.IDLE_THRESHOLD_SEC + 1)

    st = sm.compute()
    assert st.state == "sleeping"
    assert "idle" in st.message


def test_sleeping_still_leads_the_cascade(monkeypatch):
    """Sleeping is still priority 1 -- for its own scenario.

    Until 2026-09-03 this test was named "takes priority over
    everything" and asserted that an armed celebrate window LOST to
    sleeping. That swallowed every completion announced while the user
    was away from the keyboard, so celebration now suppresses sleeping
    the same way an active agent already did (2026-08-27g). See
    test_armed_celebrate_window_beats_sleeping and
    test_completed_task_beats_sleeping.
    """
    install_world(monkeypatch, idle=watcher.IDLE_THRESHOLD_SEC + 1)
    sm = make_machine_bare()
    _agents_quiet_for(sm, watcher.IDLE_THRESHOLD_SEC + 1)
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
# agent_idle_seconds tracking (time since the state machine last left any
# active state -- not tied to any one agent)
# ──────────────────────────────────────────────────────────────────────
def test_agent_idle_seconds_zero_when_active(monkeypatch):
    """While state is active (e.g. celebrating), agent-idle should be 0."""
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


# ──────────────────────────────────────────────────────────────────────
# Finishing is news even after a long quiet stretch
# ──────────────────────────────────────────────────────────────────────
def test_completed_task_beats_sleeping(monkeypatch):
    """A finished task must celebrate even past the quiet threshold.

    An agent *busy* signal already suppresses sleeping (2026-08-27g).
    Celebration was left on the wrong side of that fence: the sleeping
    branch returns before the celebrating branch is ever evaluated, so a
    task finishing while the user was away for 5+ minutes was swallowed
    entirely -- the marker's 20s freshness window expired unseen and the
    completion was never announced.
    """
    install_world(monkeypatch, idle=watcher.IDLE_THRESHOLD_SEC + 1)
    monkeypatch.setattr(
        watcher, "claude_task_marked_complete_recently", lambda now=None: True
    )
    sm = make_machine_bare()
    _agents_quiet_for(sm, watcher.IDLE_THRESHOLD_SEC + 1)

    st = sm.compute()
    assert st.state == "celebrating"


def test_armed_celebrate_window_beats_sleeping(monkeypatch):
    """The held celebrate window survives a long quiet stretch too."""
    install_world(monkeypatch, idle=watcher.IDLE_THRESHOLD_SEC + 1)
    sm = make_machine_bare()
    _agents_quiet_for(sm, watcher.IDLE_THRESHOLD_SEC + 1)
    sm.celebrate_until = time.time() + 20

    st = sm.compute()
    assert st.state == "celebrating"


def test_sleeping_still_wins_with_nothing_to_celebrate(monkeypatch):
    """Guard rail: suppressing sleeping is scoped to celebration only."""
    install_world(monkeypatch, idle=watcher.IDLE_THRESHOLD_SEC + 1)
    sm = make_machine_bare()
    _agents_quiet_for(sm, watcher.IDLE_THRESHOLD_SEC + 1)
    sm.celebrate_until = 0.0

    st = sm.compute()
    assert st.state == "sleeping"


# ──────────────────────────────────────────────────────────────────────
# Sleeping is about AGENT quiet, not user presence (2026-09-03)
# ──────────────────────────────────────────────────────────────────────
# Until now sleeping required the HUMAN to be away: macOS HID idle >= 5
# min. Pink's rule is the opposite -- Squid watches the agents, so she
# should doze when THEY have been quiet for five minutes, whether or not
# anyone is at the keyboard. The clock is the one compute() already
# maintains for agent_idle_seconds (_agent_idle_since: when she last left an
# active state).
def test_sleeps_when_agents_go_quiet_even_while_the_user_is_typing(monkeypatch):
    """The case the old rule got wrong: Pink is writing docs at the
    keyboard, nothing has run for five minutes -- she should doze."""
    install_world(monkeypatch, idle=0.0)
    sm = make_machine_bare()
    _agents_quiet_for(sm, watcher.IDLE_THRESHOLD_SEC + 1)

    assert sm.compute().state == "sleeping"


def test_stays_awake_when_the_user_is_away_but_agents_only_just_stopped(monkeypatch):
    """The mirror image, which the old rule also got wrong: Pink has
    been gone an hour, but a run finished thirty seconds ago -- that is
    not five minutes of quiet."""
    install_world(monkeypatch, idle=3600.0)
    sm = make_machine_bare()
    _agents_quiet_for(sm, 30.0)

    assert sm.compute().state != "sleeping"


def test_a_fresh_machine_does_not_sleep_on_its_first_tick(monkeypatch):
    """_agent_idle_since starts at 0.0 and only compute() sets it. Reading
    that zero as a timestamp would mean 'quiet since 1970' and put her
    to sleep the instant she boots."""
    install_world(monkeypatch, idle=0.0)
    sm = make_machine_bare()
    assert sm._agent_idle_since == 0.0

    assert sm.compute().state != "sleeping"
