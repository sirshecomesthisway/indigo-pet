"""Pink-2026-08-26: Claude-Code-native sibling of
test_awaiting_input_signal.py's TPA direct-signal tests.

Background: TPA's approval_needed alert is driven by a private
sitecustomize.py patch writing ~/.tpa/awaiting_input/<pid>.
Claude Code has no equivalent private hook, but DOES have an official
Notification hook. scripts/claude_pet_hook.py is wired into
~/.claude/settings.json (Notification/UserPromptSubmit/SessionEnd) and
writes/removes ~/.squid-pet/claude_awaiting_input/<session_id> the same
way -- except keyed by session_id (no PID in the hook payload) instead
of PID, and with NO engagement gate (see filter_eligible_claude_sessions'
docstring for why one isn't needed here, unlike TPA's).

These tests exercise watcher.py's side only (claude_sessions_awaiting_input,
filter_eligible_claude_sessions, the compute() integration, and the
extended snooze_all_awaiting_now/count_currently_waving_pids). The hook
script itself (scripts/claude_pet_hook.py) is tested separately in
tests/test_claude_pet_hook_script.py by invoking it as a real subprocess.
"""
from __future__ import annotations
import time
from unittest.mock import MagicMock, patch

import pytest

from squid_pet import watcher


@pytest.fixture
def tmp_claude_dir(tmp_path, monkeypatch):
    """Redirect the Claude awaiting-input dir to a tmp path for the test."""
    d = tmp_path / "claude_awaiting_input"
    d.mkdir()
    monkeypatch.setattr(watcher, "CLAUDE_AWAITING_INPUT_DIR", str(d))
    return d


@pytest.fixture(autouse=True)
def _clear_claude_session_state():
    """_CLAUDE_SESSION_FLAG_FIRST_SEEN is module-level state -- reset
    around every test so tests can't leak into each other."""
    watcher._CLAUDE_SESSION_FLAG_FIRST_SEEN.clear()
    yield
    watcher._CLAUDE_SESSION_FLAG_FIRST_SEEN.clear()


# ── claude_sessions_awaiting_input() ──────────────────────────────────

def test_no_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "CLAUDE_AWAITING_INPUT_DIR",
                        str(tmp_path / "nope"))
    assert watcher.claude_sessions_awaiting_input() == []


def test_empty_dir_returns_empty(tmp_claude_dir):
    assert watcher.claude_sessions_awaiting_input() == []


def test_fresh_flag_is_reported(tmp_claude_dir):
    (tmp_claude_dir / "sess-abc").write_text("idle_prompt")
    assert watcher.claude_sessions_awaiting_input() == ["sess-abc"]


def test_multiple_sessions_all_reported_sorted(tmp_claude_dir):
    (tmp_claude_dir / "sess-b").write_text("idle_prompt")
    (tmp_claude_dir / "sess-a").write_text("permission_prompt")
    assert watcher.claude_sessions_awaiting_input() == ["sess-a", "sess-b"]


def test_dotfiles_are_ignored(tmp_claude_dir):
    (tmp_claude_dir / "sess-real").write_text("idle_prompt")
    (tmp_claude_dir / ".DS_Store").write_text("junk")
    assert watcher.claude_sessions_awaiting_input() == ["sess-real"]


def test_stale_flag_is_evicted(tmp_claude_dir, monkeypatch):
    """A flag older than CLAUDE_AWAITING_INPUT_STALE_SEC is treated as a
    crashed session (never fired UserPromptSubmit/SessionEnd) and both
    removed from disk and excluded from the result."""
    import os
    f = tmp_claude_dir / "sess-dead"
    f.write_text("idle_prompt")
    old = time.time() - watcher.CLAUDE_AWAITING_INPUT_STALE_SEC - 60
    os.utime(f, (old, old))

    assert watcher.claude_sessions_awaiting_input() == []
    assert not f.exists(), "stale flag should be deleted from disk"


def test_fresh_flag_just_under_stale_threshold_survives(tmp_claude_dir):
    import os
    f = tmp_claude_dir / "sess-almost-stale"
    f.write_text("idle_prompt")
    fresh = time.time() - (watcher.CLAUDE_AWAITING_INPUT_STALE_SEC - 60)
    os.utime(f, (fresh, fresh))
    assert watcher.claude_sessions_awaiting_input() == ["sess-almost-stale"]


# ── filter_eligible_claude_sessions() ─────────────────────────────────

def test_fresh_session_is_eligible():
    assert watcher.filter_eligible_claude_sessions(["sess-1"]) == ["sess-1"]


def test_no_engagement_gate_unlike_cp():
    """Unlike TPA's filter_eligible_awaiting_pids, a session with NO prior
    activity tracking still fires -- Claude's Notification hook only
    ever fires mid/post-turn, so there's no 'fresh, never engaged' class
    of false positive to gate against."""
    assert watcher.filter_eligible_claude_sessions(["brand-new-session"]) \
        == ["brand-new-session"]


def test_session_snoozes_after_window():
    now = 1_000_000.0
    with patch("time.time", return_value=now):
        assert watcher.filter_eligible_claude_sessions(["sess-1"]) == ["sess-1"]
    later = now + watcher._CLAUDE_SESSION_SNOOZE_SEC + 1
    with patch("time.time", return_value=later):
        assert watcher.filter_eligible_claude_sessions(["sess-1"]) == []


def test_session_rearms_after_flag_disappears_and_reappears():
    now = 1_000_000.0
    with patch("time.time", return_value=now):
        watcher.filter_eligible_claude_sessions(["sess-1"])
    stale = now + watcher._CLAUDE_SESSION_SNOOZE_SEC + 1
    with patch("time.time", return_value=stale):
        assert watcher.filter_eligible_claude_sessions(["sess-1"]) == []
        # Flag disappears (user replied) -- eviction happens on next call
        # with sess-1 absent from the live set.
        assert watcher.filter_eligible_claude_sessions([]) == []
        assert "sess-1" not in watcher._CLAUDE_SESSION_FLAG_FIRST_SEEN
    # Fresh prompt/permission wait -> new birth time -> eligible again.
    reappear = stale + 1
    with patch("time.time", return_value=reappear):
        assert watcher.filter_eligible_claude_sessions(["sess-1"]) == ["sess-1"]


def test_independent_sessions_tracked_separately():
    now = 1_000_000.0
    with patch("time.time", return_value=now):
        watcher.filter_eligible_claude_sessions(["sess-old"])
    later = now + watcher._CLAUDE_SESSION_SNOOZE_SEC + 1
    with patch("time.time", return_value=later):
        result = watcher.filter_eligible_claude_sessions(["sess-old", "sess-new"])
    assert result == ["sess-new"], \
        "sess-old should be snoozed, sess-new (first seen this tick) should not"


# ── Integration: compute() fires approval_needed from a Claude session ─

def _patched_config(**overrides):
    defaults = {
        "approval_alert_enabled": True,
        "approval_alert_sound": "Glass",
        "approval_alert_text": "your turn",
    }
    defaults.update(overrides)
    return patch("squid_pet.config.get",
                 side_effect=lambda k, default=None: defaults.get(k, default))


def test_compute_fires_approval_needed_from_claude_session(tmp_claude_dir):
    """Realistic case: Claude has genuinely gone idle (no live shell/file/
    streaming evidence -- exactly what the underlying cascade sees while
    a permission_prompt/idle_prompt wait is actually in effect) and a
    flag is present -> approval_needed fires."""
    (tmp_claude_dir / "sess-xyz").write_text("permission_prompt")

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="idle", message="x")
    with patch.object(watcher, "_fire_approval_notification") as mock_notify, \
         _patched_config():
        st = sm.compute()

    assert st.state == "approval_needed", (
        f"Claude session awaiting_input flag should fire approval_needed; "
        f"got {st.state!r}, reason={st.state_reason!r}"
    )
    assert "sess-xyz" in st.state_reason
    assert "claude" in st.state_reason.lower()
    # Pink-2026-08-26 regression: notification must name "Claude Code",
    # not the previously-hardcoded "TPA" (caught via live testing).
    # source_label defaults to "Claude Code" now that it's the only caller.
    mock_notify.assert_called_once_with("your turn", "Glass")


def test_compute_notify_false_suppresses_notification_but_still_reports_state(tmp_claude_dir):
    """Pink-2026-08-27i: compute(notify=False) is StateMachine's own
    structural opt-out for read-only diagnostic callers (squid why),
    replacing an earlier unittest.mock.patch("_fire_approval_notification")
    reaching into the module from production code (__main__._run_why).
    The state/state_reason report must be unaffected -- only the real OS
    notification call is skipped."""
    (tmp_claude_dir / "sess-xyz").write_text("permission_prompt")

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="idle", message="x")
    with patch.object(watcher, "_fire_approval_notification") as mock_notify, \
         _patched_config():
        st = sm.compute(notify=False)

    assert st.state == "approval_needed"
    assert "sess-xyz" in st.state_reason
    mock_notify.assert_not_called()


def test_compute_does_not_fire_when_no_claude_or_tpa_flag(tmp_claude_dir):
    """Sanity: an otherwise-quiet cascade must not spontaneously fire
    approval_needed just because the (empty) Claude dir exists."""
    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="idle", message="x")
    with _patched_config():
        st = sm.compute()
    assert st.state != "approval_needed"


# ── Regression: 2026-08-27 -- stale flag while genuinely active ────────
# Real bug caught via live use: Claude Code resumed working (a permission
# was granted some other way, or an agentic task continued unattended)
# WITHOUT the user ever submitting a fresh top-level prompt, so
# UserPromptSubmit never fired to clear the flag -- Squid kept showing
# "your turn" and re-firing the OS notification while Claude was visibly,
# actively working. Fix: our own real activity signal (working/thinking)
# self-heals the stale flag.
def _backdate(path, seconds):
    """Set a flag file's mtime `seconds` into the past, past self-heal's
    minimum-age grace window, so it reads as genuinely stale rather than
    just-written."""
    import os
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_stale_flag_self_heals_when_genuinely_working(tmp_claude_dir):
    flag = tmp_claude_dir / "sess-stale"
    flag.write_text("permission_prompt")
    _backdate(flag, watcher.SELF_HEAL_MIN_FLAG_AGE_SEC + 1)

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="working", message="x")
    with patch.object(watcher, "_fire_approval_notification") as mock_notify, \
         patch.object(watcher, "find_claude_code_processes", return_value=["proc1"]), \
         _patched_config():
        st = sm.compute()

    assert st.state == "working", (
        f"genuine current activity must win over a stale flag; got {st.state!r}"
    )
    assert not flag.exists(), "the stale flag should be deleted, not just hidden"
    mock_notify.assert_not_called()


def test_stale_flag_self_heals_when_thinking(tmp_claude_dir):
    """Same self-heal, but for the 'thinking' (streaming) active state --
    not just 'working' (shell/file evidence)."""
    flag = tmp_claude_dir / "sess-stale-2"
    flag.write_text("idle_prompt")
    _backdate(flag, watcher.SELF_HEAL_MIN_FLAG_AGE_SEC + 1)

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="thinking", message="x")
    with patch.object(watcher, "_fire_approval_notification"), \
         patch.object(watcher, "find_claude_code_processes", return_value=["proc1"]), \
         _patched_config():
        st = sm.compute()

    assert st.state == "thinking"
    assert not flag.exists()


# ── Regression: 2026-08-31 -- self-heal ate a flag on the SAME tick it
# was written -- caught live during a demo: Squid never showed
# approval_needed at all (not even for one tick) for a genuinely-pending
# AskUserQuestion prompt, because Claude Code was still visibly
# streaming/active the instant the flag appeared, so self-heal reaped it
# before the approval-needed block ever got to see it. Self-heal exists
# to clear a flag stuck behind ongoing work Pink is actively watching --
# not to reap something raised a moment ago -- so a fresh flag must
# survive at least SELF_HEAL_MIN_FLAG_AGE_SEC before self-heal may act.
def test_fresh_flag_survives_self_heal_and_fires_approval_needed(tmp_claude_dir):
    flag = tmp_claude_dir / "sess-brand-new"
    flag.write_text("permission_prompt")  # mtime == now, well under the grace window

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="thinking", message="x")
    with patch.object(watcher, "_fire_approval_notification") as mock_notify, \
         patch.object(watcher, "find_claude_code_processes", return_value=["proc1"]), \
         _patched_config():
        st = sm.compute()

    assert flag.exists(), (
        "a flag written this tick must not be self-healed away before "
        "it's ever had a chance to be seen"
    )
    assert st.state == "approval_needed", (
        f"a freshly-raised prompt must fire approval_needed on its first "
        f"tick, even if Claude Code still reads as working/thinking; "
        f"got {st.state!r}"
    )
    mock_notify.assert_called_once()


# ── Regression: 2026-08-30 -- self-heal ate a DIFFERENT session's
# genuinely-pending approval -- Pink caught live: session A asked for a
# decision and was waiting, but session B (this very conversation) was
# actively working, so self-heal's aggregate "any Claude Code activity
# means every pending wait is resolved" cleared session A's flag before
# approval_needed ever got a chance to fire. Self-heal's whole premise
# ("you're actively watching it work") only holds for a single active
# session -- with 2+ Claude Code processes alive there's no way to tell
# whose activity is whose (no PID in the hook payload), so it must not
# fire at all in that case; let the real removal paths (UserPromptSubmit,
# SessionEnd, stale-timeout, manual "calm Squid") handle it instead.
def test_stale_flag_does_not_self_heal_with_multiple_sessions_active(tmp_claude_dir):
    flag = tmp_claude_dir / "sess-other-session-waiting"
    flag.write_text("permission_prompt")

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="working", message="x")
    with patch.object(watcher, "_fire_approval_notification") as mock_notify, \
         patch.object(watcher, "find_claude_code_processes",
                       return_value=["proc-a", "proc-b"]), \
         _patched_config():
        st = sm.compute()

    assert flag.exists(), (
        "with 2+ Claude Code processes alive, self-heal can't tell whose "
        "activity resolved whose wait -- must not clear another "
        "session's flag"
    )
    assert st.state == "approval_needed", (
        f"the genuinely-pending session must still get its attention "
        f"alert; got {st.state!r}"
    )
    mock_notify.assert_called_once()


def test_flag_written_after_going_idle_still_fires_normally(tmp_claude_dir):
    """Self-heal must not be overzealous: a flag that shows up while the
    cascade is genuinely idle (the realistic case) must still fire --
    only 'working'/'thinking' ticks self-heal."""
    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="idle", message="x")

    with patch.object(watcher, "_fire_approval_notification"), \
         _patched_config():
        # Tick 1: genuinely idle, no flag yet.
        st1 = sm.compute()
        assert st1.state == "idle"

        # Tick 2: a flag now appears (Notification hook fired between ticks).
        (tmp_claude_dir / "sess-fresh").write_text("idle_prompt")
        st2 = sm.compute()

    assert st2.state == "approval_needed"


def test_approval_alert_disabled_suppresses_claude_signal_too(tmp_claude_dir):
    """approval_alert_enabled=False must silence the Claude direct
    signal -- the one kill switch for the whole mechanism."""
    (tmp_claude_dir / "sess-xyz").write_text("permission_prompt")
    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="working", message="x")
    with _patched_config(approval_alert_enabled=False):
        st = sm.compute()
    assert st.state != "approval_needed"


# ── snooze_all_awaiting_now() / count_currently_waving_sessions() ──────

def test_count_currently_waving_sessions(tmp_claude_dir):
    (tmp_claude_dir / "sess-1").write_text("idle_prompt")
    (tmp_claude_dir / "sess-2").write_text("permission_prompt")
    assert watcher.count_currently_waving_sessions() == 2


def test_snooze_all_awaiting_now_snoozes_claude_sessions(tmp_claude_dir):
    (tmp_claude_dir / "sess-1").write_text("idle_prompt")
    # First establish eligibility (birth time recorded).
    assert watcher.count_currently_waving_sessions() == 1
    n = watcher.snooze_all_awaiting_now()
    assert n == 1
    # Now snoozed -- no longer eligible even though the flag file
    # is still on disk ("seen it, deferred" semantics).
    assert watcher.count_currently_waving_sessions() == 0


def test_snooze_all_awaiting_now_zero_when_nothing_waving(tmp_claude_dir):
    assert watcher.snooze_all_awaiting_now() == 0


# ── Regression: 2026-08-26 "endless notifications" bug ─────────────────
# Found via live testing: compute()'s agent_idle_seconds bookkeeping used to
# unconditionally reset self._approval_alert_fired = False whenever the
# PRE-override cascade state (from _compute_inner()) was in
# _AGENT_ACTIVE_STATES -- which is exactly the situation approval_needed
# fires in. That reset ran on every single tick, re-arming the "fire
# once" latch and causing a real macOS notification to fire once per
# second, forever, for as long as the flag stayed present. The correct
# reset already existed (fired_reason is None -> latch resets) and needed
# nothing added; the fix was deleting the premature one.
#
# Uses state="idle" here (not "working") -- 2026-08-27's separate
# stale-flag self-heal fix means "working"/"thinking" now clears the
# flag before the approval block even runs, which is the CORRECT
# behavior but no longer exercises this specific historical bug. "idle"
# (the realistic state while genuinely awaiting input) still does.

def test_notification_fires_only_once_across_many_ticks_while_flag_persists(tmp_claude_dir):
    """The exact bug: a live awaiting_input flag, ticked repeatedly while
    genuinely idle, must fire the OS notification exactly once, not once
    per tick."""
    (tmp_claude_dir / "sess-persistent").write_text("permission_prompt")

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="idle", message="x")

    with patch.object(watcher, "_fire_approval_notification") as mock_notify, \
         _patched_config():
        states = [sm.compute() for _ in range(10)]

    assert all(st.state == "approval_needed" for st in states)
    assert mock_notify.call_count == 1, (
        f"notification must fire exactly once across 10 ticks with the "
        f"flag continuously present; fired {mock_notify.call_count} times"
    )


def test_notification_refires_after_flag_disappears_and_reappears(tmp_claude_dir):
    """Distinguishing the fix from a blunt 'never fire twice' latch: a
    genuinely NEW wait (user replied, then a fresh prompt/permission
    wait) must still notify again."""
    flag = tmp_claude_dir / "sess-cycle"
    flag.write_text("permission_prompt")

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="idle", message="x")

    with patch.object(watcher, "_fire_approval_notification") as mock_notify, \
         _patched_config():
        st1 = sm.compute()
        assert st1.state == "approval_needed"
        assert mock_notify.call_count == 1

        flag.unlink()  # user replied -- UserPromptSubmit hook removed it
        st2 = sm.compute()
        assert st2.state != "approval_needed"

        flag.write_text("idle_prompt")  # a fresh wait
        st3 = sm.compute()
        assert st3.state == "approval_needed"
        assert mock_notify.call_count == 2, "must notify again for the new wait"


def test_state_reverts_once_flag_removed_between_ticks(tmp_claude_dir):
    """Simulates the UserPromptSubmit hook clearing the flag mid-session:
    the very next tick must fall out of approval_needed, no lingering."""
    flag = tmp_claude_dir / "sess-reply"
    flag.write_text("idle_prompt")

    sm = watcher.StateMachine()
    sm._compute_inner = lambda: watcher.PetState(state="idle", message="x")

    with patch.object(watcher, "_fire_approval_notification"), \
         _patched_config():
        assert sm.compute().state == "approval_needed"
        flag.unlink()
        st = sm.compute()

    assert st.state != "approval_needed", \
        "state must revert immediately once the flag is gone (next tick)"
