"""Pink-2026-06-29: replace CPU-heuristic approval detection with a
DIRECT signal from TPA itself.

Background:
* `~/.tpa/llm_active.flag` already exists -- written by TPA's
  sitecustomize.py monkey-patch when an LLM stream is in progress.
* This adds the SIBLING signal for the OTHER transition: when TPA is
  sitting at the interactive prompt awaiting Pink's input.

Format: `~/.tpa/awaiting_input/<pid>` file per TPA process.
Presence == that TPA is at the prompt right now. Absence == it's not.

Squid scans the directory; any file whose PID is still alive
triggers `approval_needed` immediately, no CPU guessing needed.
Dead-PID files are evicted (TPA crashed mid-prompt).
"""
from __future__ import annotations
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from squid_pet import watcher


@pytest.fixture
def tmp_awaiting_dir(tmp_path, monkeypatch):
    """Redirect the awaiting-input dir to a tmp path for the test."""
    d = tmp_path / "awaiting_input"
    d.mkdir()
    monkeypatch.setattr(watcher, "_AWAITING_INPUT_DIR", str(d))
    return d


def test_no_flag_dir_returns_empty(tmp_path, monkeypatch):
    """If the awaiting_input dir doesn't even exist, function returns []."""
    monkeypatch.setattr(watcher, "_AWAITING_INPUT_DIR",
                        str(tmp_path / "nope"))
    assert watcher.tpa_pids_awaiting_input() == []


def test_empty_dir_returns_empty(tmp_awaiting_dir):
    """Empty dir == no TPA awaiting input."""
    assert watcher.tpa_pids_awaiting_input() == []


def test_alive_pid_flag_is_reported(tmp_awaiting_dir):
    """A flag file whose PID is alive should be reported."""
    my_pid = os.getpid()
    (tmp_awaiting_dir / str(my_pid)).write_text(str(time.time()))
    assert watcher.tpa_pids_awaiting_input() == [my_pid]


def test_dead_pid_flag_is_evicted(tmp_awaiting_dir):
    """A flag file whose PID is dead should be removed AND not reported.

    TPA crashed mid-prompt -> stale flag -> Squid shouldn't wave forever.
    """
    dead_pid = 999999            # essentially guaranteed not to exist
    flag = tmp_awaiting_dir / str(dead_pid)
    flag.write_text(str(time.time()))
    pids = watcher.tpa_pids_awaiting_input()
    assert dead_pid not in pids
    assert not flag.exists(), "dead-PID flag should have been evicted"


def test_multiple_alive_pids_all_reported(tmp_awaiting_dir):
    """Multi-TPA: every flag with an alive PID is reported."""
    my_pid = os.getpid()
    parent_pid = os.getppid()
    (tmp_awaiting_dir / str(my_pid)).write_text("a")
    (tmp_awaiting_dir / str(parent_pid)).write_text("b")
    pids = sorted(watcher.tpa_pids_awaiting_input())
    assert pids == sorted([my_pid, parent_pid])


def test_non_integer_filenames_ignored(tmp_awaiting_dir):
    """Random files in the dir (e.g. .DS_Store) shouldn't crash us."""
    (tmp_awaiting_dir / ".DS_Store").write_text("junk")
    (tmp_awaiting_dir / "README.md").write_text("hi")
    (tmp_awaiting_dir / str(os.getpid())).write_text("real")
    pids = watcher.tpa_pids_awaiting_input()
    assert pids == [os.getpid()]


# ── Integration: compute() should fire approval_needed instantly when
#                a flag is present AND the TPA has been engaged. ──────

def test_compute_fires_approval_needed_when_flag_present(
    tmp_awaiting_dir, monkeypatch,
):
    """When the direct signal is available AND the TPA has actually been
    engaged, flag presence alone (no CPU guessing) fires approval_needed.
    """
    my_pid = os.getpid()
    (tmp_awaiting_dir / str(my_pid)).write_text(str(time.time()))
    # Pink-2026-06-30 v3: engagement gate. Seed the pid as "ever busy"
    # so we're testing a mid-session TPA (Pink has used it) rather than
    # a fresh-startup TPA (which should NOT fire -- see next test).
    watcher._PER_PID_EVER_BUSY.add(my_pid)

    from unittest.mock import MagicMock
    sm = watcher.StateMachine()
    sm._tpa_detector = MagicMock(tpa_running=True)
    # Force the inner cascade to pick a TPA-active state -- the override
    # should kick in REGARDLESS of what _compute_inner returned.
    sm._compute_inner = lambda: watcher.PetState(
        state="working", message="x", tpa_running=True,
    )
    try:
        # No procs / per-proc idle at all -- direct signal alone fires.
        with patch.object(watcher, "find_tpa_processes",
                          return_value=[]):
            with patch("squid_pet.config.get") as mg:
                mg.side_effect = lambda k, default=None: {
                    "approval_alert_enabled": True,
                    "approval_alert_threshold_sec": 10.0,
                    "approval_alert_sound": "Glass",
                    "approval_alert_text": "your turn",
                }.get(k, default)
                st = sm.compute()
    finally:
        watcher._PER_PID_EVER_BUSY.discard(my_pid)
        watcher._PER_PID_FLAG_FIRST_SEEN.pop(my_pid, None)
    assert st.state == "approval_needed", (
        f"flag present + engaged should fire approval_needed; got {st.state!r}, "
        f"reason={st.state_reason!r}"
    )
    assert "awaiting_input" in st.state_reason.lower() or \
           "flag" in st.state_reason.lower()


# ── NEW: engagement gate (Bug #2 -- fresh-startup false-fire) ─────────

def test_fresh_startup_tpa_with_flag_but_never_busy_does_NOT_fire(
    tmp_awaiting_dir, monkeypatch,
):
    """Pink-2026-06-30 v3: BUG #2 -- TPA writes its awaiting_input flag
    the very first time it hits its prompt loop, which happens IMMEDIATELY
    at startup before Pink has ever engaged with it. Squid used to fire
    approval_needed for these fresh-startup flags, which is a false-fire
    (Pink didn't ask TPA anything, why is it asking her for input?).

    With the engagement gate, a PID that is NOT in _PER_PID_EVER_BUSY
    is treated as fresh-startup and its flag is IGNORED.
    """
    my_pid = os.getpid()
    (tmp_awaiting_dir / str(my_pid)).write_text(str(time.time()))
    # Explicitly do NOT add to _PER_PID_EVER_BUSY -- this is a fresh TPA.
    watcher._PER_PID_EVER_BUSY.discard(my_pid)
    watcher._PER_PID_FLAG_FIRST_SEEN.pop(my_pid, None)

    from unittest.mock import MagicMock
    sm = watcher.StateMachine()
    sm._tpa_detector = MagicMock(tpa_running=True)
    sm._compute_inner = lambda: watcher.PetState(
        state="working", message="x", tpa_running=True,
    )
    try:
        with patch.object(watcher, "find_tpa_processes",
                          return_value=[]):
            with patch("squid_pet.config.get") as mg:
                mg.side_effect = lambda k, default=None: {
                    "approval_alert_enabled": True,
                    "approval_alert_threshold_sec": 10.0,
                    "approval_alert_sound": "Glass",
                    "approval_alert_text": "your turn",
                }.get(k, default)
                st = sm.compute()
    finally:
        watcher._PER_PID_FLAG_FIRST_SEEN.pop(my_pid, None)
    assert st.state != "approval_needed", (
        "fresh-startup TPA (flag present, never engaged) must NOT fire; "
        f"got {st.state!r}, reason={st.state_reason!r}"
    )


# ── NEW: direct-signal snooze (Bug #1 -- wave forever) ───────────────

def test_direct_signal_filter_snoozes_after_window():
    """Pink-2026-06-30 v3: BUG #1 -- the direct-signal path had NO snooze.
    Once TPA wrote its flag, Squid would wave until TPA crashed or Pink typed,
    even if she'd already seen the wave and consciously deferred (going to
    lunch, in a meeting, etc.). Now we snooze after
    _PENDING_APPROVAL_DIRECT_SNOOZE_SEC, matching the fallback path.
    """
    import time as _time
    pid = 12345
    # Simulate: PID has been engaged and its flag has been present
    # for LONGER than the direct-signal snooze window.
    watcher._PER_PID_EVER_BUSY.add(pid)
    watcher._PER_PID_FLAG_FIRST_SEEN[pid] = (
        _time.time() - watcher._PENDING_APPROVAL_DIRECT_SNOOZE_SEC - 10.0
    )
    try:
        eligible = watcher.filter_eligible_awaiting_pids([pid])
    finally:
        watcher._PER_PID_EVER_BUSY.discard(pid)
        watcher._PER_PID_FLAG_FIRST_SEEN.pop(pid, None)
    assert eligible == [], (
        f"stale flag past snooze window should be filtered out; got {eligible}"
    )


def test_direct_signal_filter_rearms_after_flag_disappears():
    """Snooze must RESET when the flag file disappears (= Pink replied,
    TPA is busy responding). The next time the flag reappears (= new
    prompt), the wave should fire again with a fresh clock."""
    import time as _time
    pid = 12346
    watcher._PER_PID_EVER_BUSY.add(pid)
    # Stale birth time -- would be snoozed if we didn't reset.
    watcher._PER_PID_FLAG_FIRST_SEEN[pid] = _time.time() - 999.0

    try:
        # Call 1: flag is GONE (Pink replied). filter should evict the
        # stale first-seen entry.
        watcher.filter_eligible_awaiting_pids([])
        assert pid not in watcher._PER_PID_FLAG_FIRST_SEEN, (
            "flag disappearance must evict FIRST_SEEN entry (snooze reset)"
        )
        # Call 2: flag REAPPEARS (TPA finished, new prompt). Now the birth
        # time is fresh -- should NOT be snoozed.
        eligible = watcher.filter_eligible_awaiting_pids([pid])
    finally:
        watcher._PER_PID_EVER_BUSY.discard(pid)
        watcher._PER_PID_FLAG_FIRST_SEEN.pop(pid, None)
    assert eligible == [pid], (
        f"reappeared flag should fire with fresh snooze clock; got {eligible}"
    )


def test_direct_signal_filter_engagement_gate_alone():
    """Just the engagement gate, unit-level. A PID with a flag but not in
    _PER_PID_EVER_BUSY is filtered out."""
    pid = 12347
    watcher._PER_PID_EVER_BUSY.discard(pid)
    watcher._PER_PID_FLAG_FIRST_SEEN.pop(pid, None)
    try:
        eligible = watcher.filter_eligible_awaiting_pids([pid])
    finally:
        watcher._PER_PID_FLAG_FIRST_SEEN.pop(pid, None)
    assert eligible == [], (
        f"never-engaged PID must be filtered by engagement gate; got {eligible}"
    )



# ── NEW: manual calm-Squid (right-click menu button) ─────────────────

def test_manual_snooze_immediately_filters_current_flag(tmp_awaiting_dir):
    """Pink-2026-06-30 v3: MANUAL de-escalate. When Pink clicks
    'Calm Squid' from the right-click menu, every currently-waving PID
    must be filtered out on the very next tick, without waiting for
    the 5-min auto-snooze window."""
    my_pid = os.getpid()
    # Set up: PID is engaged AND has a live flag AND its flag was just
    # observed (so auto-snooze would NOT have kicked in yet).
    (tmp_awaiting_dir / str(my_pid)).write_text(str(time.time()))
    watcher._PER_PID_EVER_BUSY.add(my_pid)
    watcher._PER_PID_FLAG_FIRST_SEEN[my_pid] = time.time()

    try:
        # Baseline: without the manual snooze, this PID should be eligible.
        eligible_before = watcher.filter_eligible_awaiting_pids([my_pid])
        assert eligible_before == [my_pid], (
            "sanity: engaged + fresh flag must be eligible pre-calm; "
            f"got {eligible_before}"
        )

        # Click "Calm Squid".
        n = watcher.snooze_all_awaiting_now()
        assert n >= 1, f"snooze should report at least 1 PID; got {n}"

        # Now the same PID must be filtered out.
        eligible_after = watcher.filter_eligible_awaiting_pids([my_pid])
        assert eligible_after == [], (
            f"post-calm should filter out {my_pid}; got {eligible_after}"
        )
    finally:
        watcher._PER_PID_EVER_BUSY.discard(my_pid)
        watcher._PER_PID_FLAG_FIRST_SEEN.pop(my_pid, None)


def test_manual_snooze_reports_zero_when_no_waves(tmp_awaiting_dir):
    """No waves in flight -> snooze_all_awaiting_now() returns 0 so the
    menu can show 'nothing to calm' feedback. tmp_awaiting_dir keeps
    us out of the real ~/.tpa/awaiting_input which may have
    stale flags from other TPA sessions."""
    watcher._PER_PID_FLAG_FIRST_SEEN.clear()
    # tmp_awaiting_dir exists but is empty -- no PIDs are waving.
    n = watcher.snooze_all_awaiting_now()
    assert n == 0, f"empty state should report 0 snoozed; got {n}"


def test_manual_snooze_rearms_when_flag_disappears_and_reappears(
    tmp_awaiting_dir,
):
    """The manual snooze must NOT be sticky. After Pink calms the wave
    and TPA later hits a NEW prompt (flag disappears then reappears with
    a fresh birth time), the wave must fire again for the new request."""
    my_pid = os.getpid()
    flag = tmp_awaiting_dir / str(my_pid)
    flag.write_text(str(time.time()))
    watcher._PER_PID_EVER_BUSY.add(my_pid)
    # Register the flag birth-time by observing it once.
    watcher.filter_eligible_awaiting_pids([my_pid])

    try:
        # Calm it.
        watcher.snooze_all_awaiting_now()
        assert watcher.filter_eligible_awaiting_pids([my_pid]) == []

        # Pink replies -> TPA removes the flag. filter observes empty
        # awaiting list -> evicts FIRST_SEEN.
        flag.unlink()
        watcher.filter_eligible_awaiting_pids([])
        assert my_pid not in watcher._PER_PID_FLAG_FIRST_SEEN, (
            "flag disappearance must evict FIRST_SEEN (re-arm snooze)"
        )

        # TPA finishes the reply and hits its next prompt -> flag reappears.
        flag.write_text(str(time.time()))
        eligible = watcher.filter_eligible_awaiting_pids([my_pid])
        assert eligible == [my_pid], (
            "post-calm re-arm: fresh flag should fire again; "
            f"got {eligible}"
        )
    finally:
        watcher._PER_PID_EVER_BUSY.discard(my_pid)
        watcher._PER_PID_FLAG_FIRST_SEEN.pop(my_pid, None)


def test_count_currently_waving_pids_matches_filter(tmp_awaiting_dir):
    """count_currently_waving_pids() is what the menu uses to decide
    if 'Calm Squid' should be enabled. It must equal the length of
    the filtered eligible list for the currently-scanned awaiting dir."""
    my_pid = os.getpid()
    (tmp_awaiting_dir / str(my_pid)).write_text(str(time.time()))
    watcher._PER_PID_EVER_BUSY.add(my_pid)

    try:
        raw = watcher.tpa_pids_awaiting_input()
        assert my_pid in raw, "sanity: awaiting scan should see our PID"
        c = watcher.count_currently_waving_pids()
        assert c == len(watcher.filter_eligible_awaiting_pids(raw)), (
            f"count ({c}) must equal eligible-filter len"
        )
        assert c >= 1
    finally:
        watcher._PER_PID_EVER_BUSY.discard(my_pid)
        watcher._PER_PID_FLAG_FIRST_SEEN.pop(my_pid, None)
