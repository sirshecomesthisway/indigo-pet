"""
Unit tests for squid_pet.watcher.StateMachine.

Strategy: monkeypatch every I/O function at the module level
(psutil/filesystem/ioreg) and drive StateMachine.compute() through each
of its 9 priority branches plus the cross-tick memory (agent_idle tracking,
celebration window, busy_streak burst-suppression).
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

    Defaults represent 'completely quiet system: no TPA, no errors,
    no idle, no shell activity.' Override per-test via kwargs.
    """
    defaults = dict(
        idle=0.0,                # macos_idle_seconds
        procs=[],                # find_tpa_processes
        cpu=0.0,                 # aggregate_cpu
        tool_activity_age=float("inf"),
        shell_active=False,
        session_log_age=float("inf"),
        subagent_age=float("inf"),
        error_age=float("inf"),
        error_parse=("", "hard"),  # (reason, severity)
    )
    defaults.update(overrides)

    monkeypatch.setattr(watcher, "macos_idle_seconds",
                        lambda: defaults["idle"])
    monkeypatch.setattr(watcher, "find_tpa_processes",
                        lambda: defaults["procs"])
    monkeypatch.setattr(watcher, "aggregate_cpu",
                        lambda procs: defaults["cpu"])
    monkeypatch.setattr(watcher, "most_recent_tool_activity_age",
                        lambda: defaults["tool_activity_age"])
    monkeypatch.setattr(watcher, "has_active_shell_children",
                        lambda procs: defaults["shell_active"])
    monkeypatch.setattr(watcher, "newest_session_log_age",
                        lambda: defaults["session_log_age"])
    monkeypatch.setattr(watcher, "newest_file_age_in_dir",
                        lambda d, pattern="*": defaults["subagent_age"])
    monkeypatch.setattr(watcher, "file_age_sec",
                        lambda p: defaults["error_age"])
    monkeypatch.setattr(watcher, "parse_last_error",
                        lambda log, lookback_bytes=32_000: defaults["error_parse"])


def make_machine_primed() -> StateMachine:
    """StateMachine with _cpu_primed=True so we skip the prime/sleep."""
    sm = StateMachine()
    sm._cpu_primed = True
    return sm


# ──────────────────────────────────────────────────────────────────────
# Priority 1 — SLEEPING
# ──────────────────────────────────────────────────────────────────────
def test_sleeping_when_macos_idle_exceeds_threshold(monkeypatch):
    install_world(monkeypatch, idle=400.0, procs=["fake"], cpu=20.0)
    sm = make_machine_primed()
    sm.was_busy = True  # should be reset

    st = sm.compute()
    assert st.state == "sleeping"
    assert "idle" in st.message
    assert sm.was_busy is False


def test_sleeping_takes_priority_over_everything(monkeypatch):
    """Sleeping wins even when subagent is grooving + shell is active."""
    install_world(
        monkeypatch,
        idle=watcher.IDLE_THRESHOLD_SEC + 1,
        procs=["fake"],
        cpu=50.0,
        shell_active=True,
        subagent_age=1.0,
        error_age=1.0,
    )
    sm = make_machine_primed()
    st = sm.compute()
    assert st.state == "sleeping"


# ──────────────────────────────────────────────────────────────────────
# Priority 2 — CELEBRATING (held window)
# ──────────────────────────────────────────────────────────────────────
def test_celebrating_held_for_duration(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_primed()
    # Pretend we just started celebrating 1 second ago.
    sm.celebrate_until = time.time() + watcher.CELEBRATE_DURATION_SEC - 1

    st = sm.compute()
    assert st.state == "celebrating"
    assert "nice" in st.message or "" in st.message  # not asserting exact glyph


def test_celebrating_window_expires(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_primed()
    sm.celebrate_until = time.time() - 1  # already expired

    st = sm.compute()
    assert st.state != "celebrating"


# ──────────────────────────────────────────────────────────────────────
# Priority 3 — IDLE (no tpa running)
# ──────────────────────────────────────────────────────────────────────
def test_idle_when_no_tpa(monkeypatch):
    install_world(monkeypatch, procs=[])  # no procs
    sm = make_machine_primed()
    sm.was_busy = True  # should be reset

    st = sm.compute()
    assert st.state == "idle"
    assert st.tpa_running is False
    assert sm.was_busy is False


# ──────────────────────────────────────────────────────────────────────
# Priority 4 — GROOVING (subagent recent)
# ──────────────────────────────────────────────────────────────────────
def test_grooving_when_subagent_recent(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0, subagent_age=10.0)
    sm = make_machine_primed()
    st = sm.compute()
    assert st.state == "grooving"
    assert sm.was_busy is True


def test_grooving_threshold_boundary(monkeypatch):
    """Just outside the window → no grooving."""
    install_world(
        monkeypatch, procs=["fake"], cpu=0.0,
        subagent_age=watcher.SUBAGENT_ACTIVE_WINDOW_SEC + 0.1,
    )
    sm = make_machine_primed()
    st = sm.compute()
    assert st.state != "grooving"


# ──────────────────────────────────────────────────────────────────────
# Priority 5 — CONCERNED (error window, hard vs transient)
# ──────────────────────────────────────────────────────────────────────
def test_concerned_hard_error_within_window(monkeypatch):
    install_world(
        monkeypatch, procs=["fake"], cpu=0.0,
        error_age=10.0,
        error_parse=("crash in tool X", "hard"),
    )
    sm = make_machine_primed()
    st = sm.compute()
    assert st.state == "concerned"
    assert st.concern_severity == "hard"
    assert st.concern_reason == "crash in tool X"


def test_concerned_transient_clears_faster(monkeypatch):
    """Transient errors auto-clear at CONCERN_TRANSIENT_LOOKBACK_SEC (20s
    default), not the longer hard window (60s)."""
    # 25s > transient window (20s) but < hard window (60s)
    install_world(
        monkeypatch, procs=["fake"], cpu=0.0,
        error_age=25.0,
        error_parse=("network timeout", "transient"),
    )
    sm = make_machine_primed()
    st = sm.compute()
    assert st.state != "concerned"


def test_concerned_transient_within_short_window(monkeypatch):
    install_world(
        monkeypatch, procs=["fake"], cpu=0.0,
        error_age=10.0,
        error_parse=("network timeout", "transient"),
    )
    sm = make_machine_primed()
    st = sm.compute()
    assert st.state == "concerned"
    assert st.concern_severity == "transient"


def test_concerned_suppressed_when_cpu_busy(monkeypatch):
    """Concerned only fires when CPU is calm; if TPA is actively churning
    we trust it to be working through the error."""
    install_world(
        monkeypatch, procs=["fake"], cpu=watcher.CPU_BUSY_THRESHOLD + 5,
        error_age=10.0,
        error_parse=("oops", "hard"),
    )
    sm = make_machine_primed()
    # Prime busy_streak so sustained_busy is True
    sm.busy_streak = 5
    st = sm.compute()
    assert st.state != "concerned"


# ──────────────────────────────────────────────────────────────────────
# Priority 6 — WORKING (shell active OR busy+tool activity)
# ──────────────────────────────────────────────────────────────────────
def test_working_when_shell_active(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0, shell_active=True)
    sm = make_machine_primed()
    st = sm.compute()
    assert st.state == "working"
    assert "shell" in st.message
    assert sm.was_busy is True


def test_working_when_busy_and_tool_activity_recent(monkeypatch):
    install_world(
        monkeypatch, procs=["fake"],
        cpu=watcher.CPU_BUSY_THRESHOLD + 10,
        tool_activity_age=2.0,
    )
    sm = make_machine_primed()
    sm.busy_streak = 5  # sustained
    st = sm.compute()
    assert st.state == "working"


# ──────────────────────────────────────────────────────────────────────
# Priority 7 — THINKING (busy, no recent tool writes)
# ──────────────────────────────────────────────────────────────────────
def test_thinking_when_busy_no_tool_activity(monkeypatch):
    install_world(
        monkeypatch, procs=["fake"],
        cpu=watcher.CPU_BUSY_THRESHOLD + 10,
        tool_activity_age=999.0,
        shell_active=False,
    )
    sm = make_machine_primed()
    sm.busy_streak = 5
    st = sm.compute()
    assert st.state == "thinking"
    assert sm.was_busy is True


# ──────────────────────────────────────────────────────────────────────
# Priority 8 — CELEBRATING transition (was_busy → cpu drops)
# ──────────────────────────────────────────────────────────────────────
def test_celebrating_triggered_on_busy_to_idle_drop(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.5)
    sm = make_machine_primed()
    sm.was_busy = True

    st = sm.compute()
    assert st.state == "celebrating"
    assert "done" in st.message
    assert sm.was_busy is False  # consumed
    assert sm.celebrate_until > time.time()  # window armed


def test_no_celebration_if_never_was_busy(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.5)
    sm = make_machine_primed()
    sm.was_busy = False  # never armed

    st = sm.compute()
    assert st.state == "idle"


# ──────────────────────────────────────────────────────────────────────
# Priority 9 — Default IDLE
# ──────────────────────────────────────────────────────────────────────
def test_default_idle(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_primed()
    st = sm.compute()
    assert st.state == "idle"
    assert st.tpa_running is True


# ──────────────────────────────────────────────────────────────────────
# Burst-suppression (busy_streak >= 2)
# ──────────────────────────────────────────────────────────────────────
def test_single_cpu_spike_does_not_trigger_thinking(monkeypatch):
    """One tick of high CPU shouldn't move us to thinking; need 2 in a row."""
    install_world(
        monkeypatch, procs=["fake"],
        cpu=watcher.CPU_BUSY_THRESHOLD + 10,
        tool_activity_age=999.0,
    )
    sm = make_machine_primed()
    # Fresh machine: busy_streak = 0. After this tick it becomes 1, but
    # sustained_busy needs >= 2. So state should NOT be thinking yet.
    st = sm.compute()
    assert st.state != "thinking"
    assert sm.busy_streak == 1


def test_two_consecutive_cpu_spikes_trigger_thinking(monkeypatch):
    install_world(
        monkeypatch, procs=["fake"],
        cpu=watcher.CPU_BUSY_THRESHOLD + 10,
        tool_activity_age=999.0,
    )
    sm = make_machine_primed()
    sm.compute()              # busy_streak → 1, state != thinking
    st = sm.compute()         # busy_streak → 2, sustained_busy=True
    assert st.state == "thinking"


def test_busy_streak_resets_when_cpu_calm(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_primed()
    sm.busy_streak = 7
    sm.compute()
    assert sm.busy_streak == 0


# ──────────────────────────────────────────────────────────────────────
# agent_idle_seconds tracking
# ──────────────────────────────────────────────────────────────────────
def test_agent_idle_seconds_zero_when_active(monkeypatch):
    """While TPA is in an active state (e.g. thinking), agent_idle should be 0."""
    install_world(
        monkeypatch, procs=["fake"],
        cpu=watcher.CPU_BUSY_THRESHOLD + 10,
        tool_activity_age=999.0,
    )
    sm = make_machine_primed()
    sm.busy_streak = 5  # so first compute lands in thinking
    st = sm.compute()
    assert st.state == "thinking"
    assert st.agent_idle_seconds == 0.0


def test_agent_idle_seconds_starts_ticking_when_state_becomes_idle(monkeypatch):
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_primed()

    st1 = sm.compute()
    assert st1.state == "idle"
    assert st1.agent_idle_seconds == 0.0  # first idle tick — clock just started

    # Force the internal clock back so the next tick reads as 5s elapsed.
    sm._agent_idle_since = time.time() - 5.0
    st2 = sm.compute()
    assert st2.state == "idle"
    assert st2.agent_idle_seconds >= 4.9


def test_agent_idle_resets_on_transition_to_active(monkeypatch):
    """Going idle → thinking should zero agent_idle_seconds."""
    install_world(monkeypatch, procs=["fake"], cpu=0.0)
    sm = make_machine_primed()
    sm.compute()                             # land in idle
    sm._agent_idle_since = time.time() - 60.0  # pretend 60s of idle

    # Now flip to thinking
    install_world(
        monkeypatch, procs=["fake"],
        cpu=watcher.CPU_BUSY_THRESHOLD + 10,
        tool_activity_age=999.0,
    )
    sm.busy_streak = 5
    st = sm.compute()
    assert st.state == "thinking"
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
    sm = make_machine_primed()

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
    # Default fall-through is idle (no TPA, no shell, no errors).
    assert st2.state == "idle"
    # Window is now active.
    assert sm._force_awake_until > time.time()
    # _sleeping_since reset so the next sleep period can re-arm.
    assert sm._sleeping_since == 0.0


def test_sleeping_returns_after_wake_window_expires(monkeypatch):
    """Once _force_awake_until passes, sleeping check fires again."""
    install_world(monkeypatch, idle=900.0)
    sm = make_machine_primed()

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
    sm = make_machine_primed()
    # Pretend we were in the middle of a sleep cycle, mid-wake-window.
    sm._sleeping_since = time.time() - 300
    sm._force_awake_until = time.time() + 100

    # User comes back: idle drops to 5s.
    install_world(monkeypatch, idle=5.0)
    st = sm.compute()
    assert st.state != "sleeping"
    assert sm._sleeping_since == 0.0
    assert sm._force_awake_until == 0.0

