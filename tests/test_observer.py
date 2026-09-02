"""Tests for the observer subsystem (observer-mode change 2026-06-13).

Covers:
- BUBBLE_LINES voice-contract invariants (all keys <= MAX, no empty)
- Trigger taxonomy: state transitions, interactions, mood changes
- Mute semantics (every path short-circuits when muted=True)
- Concern-reason enrichment (httpx/anthropic prefix trimming, truncation)
- Shell-cmd enrichment (pytest, git push, sh -c, path-stripping)
- Defensive: unknown keys return None, oversized lines logged + dropped
- Backwards-compat: every state from PetState's domain is reachable in
  some transition (no orphaned state with no bubble path)
"""
from __future__ import annotations

import pytest
from squid_pet import observer
from squid_pet.observer import (
    BUBBLE_LINES,
    MAX_BUBBLE_CHARS,
    Observer,
    _format_concern_reason,
    _shell_cmd_bubble,
)


# ----------------------------------------------------------------------
# BUBBLE_LINES voice-contract invariants
# ----------------------------------------------------------------------

REQUIRED_KEYS = {
    "thinking", "working", "grooving", "celebrating", "concerned",
    "back_to_idle", "waking",
    "approval_needed",  # Pink 2026-07-02: flag-wave bubble
    "poke", "sprint", "sprint_end", "drowsy",
    "like", "sleeping",
}


def test_bubble_lines_has_all_required_keys():
    """Voice contract: every documented trigger key has registered lines."""
    missing = REQUIRED_KEYS - BUBBLE_LINES.keys()
    assert not missing, f"missing keys: {missing}"


@pytest.mark.parametrize(
    "key,line",
    [(k, line)
     for k, spec in BUBBLE_LINES.items()
     for line in ([spec] if isinstance(spec, str) else spec)],
)
def test_every_line_fits_max_chars(key, line):
    """Every bubble line MUST be <= MAX_BUBBLE_CHARS or it gets dropped."""
    assert len(line) <= MAX_BUBBLE_CHARS, (
        f"{key!r} line {line!r} = {len(line)} chars (max {MAX_BUBBLE_CHARS})"
    )


def test_no_empty_lines_in_bubble_lines():
    """Empty lines are silent bugs -- don't ship them."""
    for k, spec in BUBBLE_LINES.items():
        choices = [spec] if isinstance(spec, str) else spec
        for line in choices:
            assert line.strip(), f"{k!r} has empty line {line!r}"


# ----------------------------------------------------------------------
# Observer dispatch -- state transitions
# ----------------------------------------------------------------------

@pytest.fixture
def obs():
    return Observer(get_muted=lambda: False)


@pytest.fixture
def muted_obs():
    return Observer(get_muted=lambda: True)


def test_same_state_returns_none(obs):
    """Steady-state ticks (old == new) MUST be silent no-ops."""
    for state in ["idle", "thinking", "working", "concerned", "celebrating"]:
        assert obs.on_state_change(state, state) is None, \
            f"{state} -> {state} should be silent"


def test_idle_to_thinking_fires_thinking_line(obs):
    result = obs.on_state_change("idle", "thinking")
    assert result is not None
    assert result in BUBBLE_LINES["thinking"]


def test_any_to_working_fires_working_line(obs):
    """Working transitions fire regardless of source state."""
    for source in ["idle", "thinking", "celebrating", "concerned"]:
        result = obs.on_state_change(source, "working")
        assert result is not None, f"{source} -> working should fire"
        assert result in BUBBLE_LINES["working"]


def test_any_to_approval_needed_fires_bubble(obs):
    """Flag-wave (approval_needed) fires from any prior state.

    Pink 2026-07-02: Squid should say what she wants when waving.
    """
    for source in ["idle", "thinking", "working", "celebrating",
                   "concerned", "grooving", "drowsy"]:
        result = obs.on_state_change(source, "approval_needed")
        assert result is not None, f"{source} -> approval_needed should fire"
        assert result in BUBBLE_LINES["approval_needed"]


def test_approval_needed_to_approval_needed_is_silent(obs):
    """Steady-state ticks don't re-fire the wave-bubble."""
    assert obs.on_state_change("approval_needed", "approval_needed") is None


def test_approval_needed_muted_returns_none(muted_obs):
    """Muted user gets no wave-bubble either."""
    assert muted_obs.on_state_change("idle", "approval_needed") is None


def test_concerned_to_idle_fires_back_to_idle(obs):
    """The concerned -> idle transition is the relief moment."""
    result = obs.on_state_change("concerned", "idle")
    assert result is not None
    assert result in BUBBLE_LINES["back_to_idle"]


def test_thinking_to_idle_is_silent(obs):
    """thinking -> idle is normal end-of-turn -- no bubble (would be noisy)."""
    # NOTE: by trigger taxonomy this returns None because 'idle' is only
    # wired as a from_set={concerned}. Random idle drops are silent.
    assert obs.on_state_change("thinking", "idle") is None


def test_any_to_celebrating_fires(obs):
    """Celebration is the dopamine moment -- always fires."""
    result = obs.on_state_change("working", "celebrating")
    assert result is not None
    assert result in BUBBLE_LINES["celebrating"]


def test_any_to_grooving_fires(obs):
    """Subagent appearance -- always fires."""
    result = obs.on_state_change("thinking", "grooving")
    assert result is not None
    assert result in BUBBLE_LINES["grooving"]


# ----------------------------------------------------------------------
# Concerned-state enrichment with concern_reason
# ----------------------------------------------------------------------

def test_concerned_with_empty_reason_uses_generic(obs):
    result = obs.on_state_change("idle", "concerned", concern_reason="")
    assert result is not None
    assert result in BUBBLE_LINES["concerned"]


def test_concerned_with_reason_prefers_reason_verbatim(obs):
    result = obs.on_state_change(
        "idle", "concerned",
        concern_reason="ConnectionRefused: peer closed",
    )
    assert result is not None
    # Should be lowercased and prefix-trimmed
    assert "connectionrefused" in result.lower()


def test_concern_reason_strips_module_prefixes():
    assert _format_concern_reason("anthropic.APIError: rate limit") == \
        "apierror: rate limit"
    assert _format_concern_reason("httpx.ConnectError: refused") == \
        "connecterror: refused"
    assert _format_concern_reason("pydantic_ai.UsageError: bad") == \
        "usageerror: bad"


def test_concern_reason_truncates_long():
    long = "x" * 100
    result = _format_concern_reason(long)
    assert result is not None
    assert len(result) <= MAX_BUBBLE_CHARS


def test_concern_reason_empty_returns_none():
    assert _format_concern_reason("") is None
    assert _format_concern_reason("   ") is None


# ----------------------------------------------------------------------
# Celebrating-state enrichment with state_reason (Pink-2026-08-27:
# "squid is celebrating but i don't know why" -- celebrating used to
# ALWAYS show a generic exclamation with zero explanation of what
# actually happened.)
# ----------------------------------------------------------------------

def test_recapping_bubble_fires_even_from_working(obs):
    """Pink-2026-08-30: unlike routine working->thinking transitions
    (deliberately silent -- "working IS already a kind of thinking"),
    a recap must always announce itself since it's a genuinely different
    activity Pink specifically asked to see called out."""
    result = obs.on_state_change(
        "working", "thinking", state_reason="claude recapping",
    )
    assert result == "📝 recapping..."


def test_recapping_bubble_does_not_repeat_while_still_recapping(obs):
    """old==new short-circuit: a still-recapping tick reports
    thinking->thinking, so this must stay silent, not re-fire on every
    poll while the compaction is still running."""
    result = obs.on_state_change(
        "thinking", "thinking", state_reason="claude recapping",
    )
    assert result is None


def test_celebrating_names_claude_deterministically(obs):
    """Pink-2026-08-30: "claude celebrating" now only fires off an
    explicit marker Claude itself writes (scripts/squid_task_complete.py)
    when it judges the whole task done -- see claude_task_complete in
    StateMachine._compute_inner -- so it no longer means "just any turn
    ended" the way the raw Stop flag did, and never fires off elapsed
    silence alone."""
    result = obs.on_state_change(
        "working", "celebrating", state_reason="claude celebrating",
    )
    assert result == "finished with claude!"


def test_celebrating_names_codex_deterministically(obs):
    result = obs.on_state_change(
        "working", "celebrating", state_reason="codex celebrating",
    )
    assert result == "ooh, codex!"


def test_celebrating_names_git_deterministically(obs):
    result = obs.on_state_change(
        "idle", "celebrating", state_reason="git celebrating",
    )
    assert result == "nice, fresh commit!"


def test_celebrating_falls_back_to_generic_mood_pick_when_unspecific(obs):
    """A generic/unspecific reason (e.g. the force_state debug override)
    must not crash or invent a false explanation -- fall back to the
    existing mood-only line."""
    result = obs.on_state_change("idle", "celebrating", state_reason="celebrating")
    assert result in BUBBLE_LINES["celebrating"]


def test_celebrating_with_no_reason_falls_back_to_generic():
    from squid_pet.observer import _format_celebrate_reason
    assert _format_celebrate_reason("") is None
    assert _format_celebrate_reason("celebrating") is None


def test_celebrating_reason_is_deterministic_not_probabilistic(obs):
    """Unlike the concerned-reason path, this must NOT be a coin flip --
    calling repeatedly with the same reason always returns the same
    explanation."""
    results = {
        obs.on_state_change("working", "celebrating", state_reason="git celebrating")
        for _ in range(20)
    }
    assert results == {"nice, fresh commit!"}


# ----------------------------------------------------------------------
# Working-state enrichment with shell cmdline
# ----------------------------------------------------------------------

def test_working_with_pytest_cmdline(obs):
    result = obs.on_state_change(
        "idle", "working",
        shell_cmdline=["pytest", "tests/", "-v"],
    )
    assert result == "running pytest"


def test_working_with_git_push_includes_subcommand(obs):
    result = obs.on_state_change(
        "idle", "working",
        shell_cmdline=["git", "push", "origin", "main"],
    )
    assert result == "running git push"


def test_working_with_sh_dash_c_extracts_embedded():
    result = _shell_cmd_bubble(["/bin/sh", "-c", "brew install ripgrep && echo done"])
    assert result == "running brew install"


def test_working_with_absolute_path_strips_dir():
    result = _shell_cmd_bubble(["/usr/local/bin/pytest", "-x"])
    assert result == "running pytest"


def test_working_without_cmdline_falls_back_to_generic(obs):
    """When no shell cmd is detected, generic working line fires."""
    result = obs.on_state_change("idle", "working", shell_cmdline=None)
    assert result is not None
    assert result in BUBBLE_LINES["working"]


def test_shell_cmd_skips_flag_args():
    """Don't pick '-v' as the command -- find first non-flag word."""
    assert _shell_cmd_bubble(["-v", "pytest"]) == "running pytest"


def test_shell_cmd_empty_returns_none():
    assert _shell_cmd_bubble([]) is None
    assert _shell_cmd_bubble(["-x"]) is None  # only flags


# ----------------------------------------------------------------------
# Interaction triggers (poke, sprint, etc.)
# ----------------------------------------------------------------------

def test_poke_returns_poke_line(obs):
    result = obs.on_interaction("poke")
    assert result is not None
    assert result in BUBBLE_LINES["poke"]


def test_sprint_returns_sprint_line(obs):
    assert obs.on_interaction("sprint") in BUBBLE_LINES["sprint"]


def test_unknown_interaction_returns_none(obs):
    """Unknown trigger keys MUST gracefully return None, not crash."""
    assert obs.on_interaction("xyzzy") is None
    assert obs.on_interaction("") is None


# ----------------------------------------------------------------------
# Mood changes
# ----------------------------------------------------------------------

def test_mood_to_drowsy_fires(obs):
    result = obs.on_mood_change("", "drowsy")
    assert result in BUBBLE_LINES["drowsy"]


def test_mood_to_stretch_maps_to_waking(obs):
    result = obs.on_mood_change("sleeping", "stretch")
    assert result in BUBBLE_LINES["waking"]


def test_mood_to_sleeping_is_silent(obs):
    """Sleeping bubble would interrupt the calm -- intentionally silent."""
    assert obs.on_mood_change("drowsy", "sleeping") is None


def test_mood_same_returns_none(obs):
    assert obs.on_mood_change("drowsy", "drowsy") is None


# ----------------------------------------------------------------------
# Mute semantics -- the most important contract
# ----------------------------------------------------------------------

def test_mute_silences_state_change(muted_obs):
    assert muted_obs.on_state_change("idle", "thinking") is None
    assert muted_obs.on_state_change("idle", "working") is None
    assert muted_obs.on_state_change(
        "idle", "concerned",
        concern_reason="anthropic.RateLimit: 429",
    ) is None


def test_mute_silences_interactions(muted_obs):
    assert muted_obs.on_interaction("poke") is None
    assert muted_obs.on_interaction("sprint") is None


def test_mute_silences_mood_change(muted_obs):
    assert muted_obs.on_mood_change("", "drowsy") is None


def test_mute_is_live_via_callback():
    """Mute callback is queried per-call so flipping config takes effect
    without restarting the Observer."""
    state = {"muted": False}
    obs = Observer(get_muted=lambda: state["muted"])
    assert obs.on_interaction("poke") is not None

    state["muted"] = True
    assert obs.on_interaction("poke") is None

    state["muted"] = False
    assert obs.on_interaction("poke") is not None


# ----------------------------------------------------------------------
# Defensive guards
# ----------------------------------------------------------------------

def test_oversized_line_logs_warning_and_returns_none(caplog, monkeypatch):
    """If BUBBLE_LINES is corrupted with an oversized line, observer
    should NOT crash -- it should log a warning and return None."""
    monkeypatch.setitem(BUBBLE_LINES, "broken_key", ["this string is intentionally far too long to fit in a bubble"])
    obs = Observer(get_muted=lambda: False)
    with caplog.at_level("WARNING", logger="squid_pet.observer"):
        result = obs._pick("broken_key")
    assert result is None
    assert any("exceeds" in r.message or "exceed" in r.message
               for r in caplog.records), "expected oversize warning"


def test_unknown_state_transition_returns_none(obs):
    """Transitions not in STATE_TRIGGERS return None silently."""
    # idle -> drowsy isn't in the wired triggers (mood layer handles drowsy)
    assert obs.on_state_change("idle", "drowsy") is None
    # made-up state
    assert obs.on_state_change("idle", "nonexistent_state") is None


# ----------------------------------------------------------------------
# on_still_working -- periodic refresh while state STAYS "working"
# ----------------------------------------------------------------------

def test_still_working_with_shell_cmd_returns_shell_bubble(obs):
    result = obs.on_still_working(["pytest", "tests/test_observer.py", "-v"])
    assert result == "running pytest"


def test_still_working_with_no_shell_cmd_falls_back_to_generic_pool(obs):
    """Pink-2026-08-27k: used to return None here (repeating a canned
    mood line on a timer "would read as a glitch, not aliveness") -- but
    the caller (_maybe_reannounce_working) already throttles + dedupes
    against the last shown text, so a varied generic/wrap-up pool reads
    as ambient presence instead, per a "too quiet" report. Falls back for
    both a falsy shell_cmdline (empty list) and None."""
    from squid_pet.observer import BUBBLE_LINES as _BL
    pool = (set(_BL["working_generic"]) | set(_BL["working_wrapup"])
            | set(_BL["working_squid"]))
    assert obs.on_still_working(None) in pool
    assert obs.on_still_working([]) in pool


def test_still_working_muted_returns_none(muted_obs):
    assert muted_obs.on_still_working(["pytest"]) is None


# ----------------------------------------------------------------------
# idle_chatter -- fired by RoutineController during idle rest beats
# ----------------------------------------------------------------------

def test_idle_chatter_registered_and_reachable_via_on_interaction(obs):
    assert "idle_chatter" in BUBBLE_LINES
    result = obs.on_interaction("idle_chatter")
    assert result in BUBBLE_LINES["idle_chatter"]


def test_idle_chatter_muted_returns_none(muted_obs):
    assert muted_obs.on_interaction("idle_chatter") is None


# ── Idle "what are we building?" prompts (Pink-2026-09-01) ─────────────
# Pink asked for idle bubbles that invite the next project -- "I got an
# idea" -- in her own voice. Kept as a SEPARATE pool rather than dumped
# into idle_chatter so the frequency is controllable: idle_chatter fires
# every 26-34s, and a squid asking "what should we build?" that often
# stops reading as company and starts reading as nagging.
def test_idea_prompt_pool_exists_and_fits_the_bubble():
    from squid_pet.observer import BUBBLE_LINES, MAX_BUBBLE_CHARS

    pool = BUBBLE_LINES["idle_idea_prompt"]
    assert len(pool) >= 10, "needs enough variety not to repeat noticeably"
    for line in pool:
        assert len(line) <= MAX_BUBBLE_CHARS, f"{line!r} would be dropped by _pick"


def test_idea_prompts_are_in_her_voice():
    """The pool has a consistent register -- lowercase, short, dry. A line
    that shouts breaks the character more than a boring line does."""
    from squid_pet.observer import BUBBLE_LINES

    for line in BUBBLE_LINES["idle_idea_prompt"]:
        assert line == line.lower(), f"{line!r} breaks the all-lowercase voice"
        assert not line.endswith("!"), f"{line!r} is too loud for her"


def test_idle_chatter_usually_stays_ordinary_chatter(monkeypatch):
    from squid_pet import observer as obs

    o = obs.Observer(get_muted=lambda: False)
    monkeypatch.setattr(obs.random, "random", lambda: 0.99)
    line = o.on_idle_chatter()
    assert line in obs.BUBBLE_LINES["idle_chatter"]


def test_idle_chatter_sometimes_asks_what_to_build(monkeypatch):
    from squid_pet import observer as obs

    o = obs.Observer(get_muted=lambda: False)
    monkeypatch.setattr(obs.random, "random", lambda: 0.0)
    line = o.on_idle_chatter()
    assert line in obs.BUBBLE_LINES["idle_idea_prompt"]


def test_idea_prompts_respect_mute():
    from squid_pet import observer as obs

    o = obs.Observer(get_muted=lambda: True)
    assert o.on_idle_chatter() is None


# ── State-change bubbles explain WHY (Pink-2026-09-01) ─────────────────
# Pink: "she went from idle to a very brief working state, I guess because
# I opened /model in Claude Code, but I'm not sure. So it's better to
# explain why." The watcher already computes a state_reason for every
# state (it is what `squid why` prints) -- it just never reached the
# bubble in readable form.
_REAL_WATCHER_REASONS = [
    "shell child active (claude_code)",
    "shell child active (codex)",
    "file write detected (claude_code)",
    "file write detected (codex)",
    "claude streaming",
    "codex streaming",
    "claude turn in flight",
    "claude grooving",
    "creative burst",
    "no signals",
    "non-agent detector busy",
    "working hold (12s left)",
    "idle 5m",
    "force_state override (celebrating)",
    "awaiting_input flag from Claude Code session(s) abc-123",
]


def test_every_real_watcher_reason_has_an_explanation():
    """Contract: these are the strings watcher.py actually emits. Any
    reason without an explanation silently falls back to mood-mush, which
    is exactly the "I can't tell why she did that" problem."""
    from squid_pet.observer import _explain_reason, MAX_BUBBLE_CHARS

    for reason in _REAL_WATCHER_REASONS:
        out = _explain_reason(reason)
        assert out is not None, f"no explanation for watcher reason {reason!r}"
        assert len(out) <= MAX_BUBBLE_CHARS, f"{out!r} would be dropped by _pick"


def test_explanations_never_leak_internal_jargon():
    """The old path used the raw reason verbatim, so bubbles could read
    'shell child active (claude_c...'. An explanation is for the user, not
    the log."""
    from squid_pet.observer import _explain_reason

    for reason in _REAL_WATCHER_REASONS:
        out = _explain_reason(reason)
        for leak in ("(claude_code)", "(codex)", "_", "flag", "detector"):
            assert leak not in out, f"{out!r} leaks {leak!r}"


def test_state_change_explains_instead_of_emoting(monkeypatch):
    from squid_pet import observer as obs

    o = obs.Observer(get_muted=lambda: False)
    line = o.on_state_change("idle", "working",
                             state_reason="shell child active (claude_code)")
    assert line == obs._explain_reason("shell child active (claude_code)")
    assert line not in obs.BUBBLE_LINES.get("working", [])


def test_the_brief_working_blip_pink_asked_about_is_explained():
    """Her actual case: idle -> working for a moment. Whatever caused it,
    the bubble must name the evidence rather than say 'on it'."""
    from squid_pet import observer as obs

    o = obs.Observer(get_muted=lambda: False)
    line = o.on_state_change("idle", "working",
                             state_reason="file write detected (claude_code)")
    assert line is not None
    assert "file" in line or "wrote" in line or "edited" in line


def test_a_concrete_shell_command_still_wins():
    """'running pytest' is a better explanation than 'claude ran a
    command' -- specificity beats the generic map."""
    from squid_pet import observer as obs

    o = obs.Observer(get_muted=lambda: False)
    line = o.on_state_change("idle", "working",
                             shell_cmdline=["pytest", "tests/"],
                             state_reason="shell child active (claude_code)")
    assert "pytest" in line


def test_unknown_reason_falls_back_to_personality():
    """An unmapped reason must not leak raw text; she just emotes instead."""
    from squid_pet import observer as obs

    o = obs.Observer(get_muted=lambda: False)
    line = o.on_state_change("idle", "working",
                             state_reason="some brand new internal reason")
    assert line is None or "internal reason" not in line
