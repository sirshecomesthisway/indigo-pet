"""
observer.py -- speech-bubble reaction layer for Squid.

Architecture: the Observer is a passive comment layer that:
  1. Watches state transitions reported by the StateMachine
  2. Watches direct user interactions reported by PetApi
  3. Returns short reaction strings (<= 32 chars) for the frontend bubble

It NEVER modifies pet state, NEVER intercepts TPA, and NEVER produces
multi-line output. The voice lives entirely in the BUBBLE_LINES dict below
-- editing that dict is the canonical way to evolve Squid's personality.

Reference: openspec/specs/observer-mode/spec.md
"""
from __future__ import annotations

import random
import logging
from typing import Callable, Optional, Union

log = logging.getLogger(__name__)

# Hard cap on bubble length. Anything longer would wrap to 2+ lines at the
# default sprite width (~200px @ 14px font). Enforced defensively in
# _pick(): out-of-spec lines return None and log a warning.
MAX_BUBBLE_CHARS = 32

# ----------------------------------------------------------------------
# BUBBLE_LINES -- the voice contract. Pink owns this dict.
# ----------------------------------------------------------------------
# Sonic signatures per state (diversified after voice review on 2026-06-13):
#   thinking     = m/h closed-mouth pondering
#   working      = percussive activity
#   grooving     = p/s sneaky discovery (a subagent appeared!)
#   celebrating  = vowel-loud joy
#   concerned    = clipped distress
#   back_to_idle = breath of relief
#   waking       = guttural fog-clearing
#
# Interaction signatures:
#   poke / like / sprint / sprint_end / drowsy
#
# Registered but unwired (kept for future, no trigger emits them in v1):
#   like     -- heart animation already says "loved"
#   sleeping -- bubble would interrupt the calm; let sprite + Zz do the work
# ----------------------------------------------------------------------
LineSpec = Union[str, list[str]]
BUBBLE_LINES: dict[str, LineSpec] = {
    # state transitions
    "thinking":     ["hmm", "mmm...", "hrm", "thinky"],
    "working":      ["tap tap", "*types*", "mm-hm", "work work"],
    "grooving":     ["psst!", "who?", "*peeks*", "eee?"],
    "celebrating":  ["yay!!", "woo!", "!!", "*claps*"],
    "concerned":    ["eep", "hmmnn", "urk", "!?"],
    "back_to_idle": ["pheww", "hhh", "*flops*", "okay"],
    "waking":       ["mmf...", "nhg", "wh-", "*stretches*"],

    # interactions
    "poke":         ["boop?", "hi", "?", "hm?"],
    "shake":        ["wheee", "whoa!", "whee~", "hey hey"],
    "sprint":       ["wheee!", "zoom", "*blurs*", "go!"],
    "sprint_end":   ["*pant pant*", "phew", "x_x"],
    "drowsy":       ["*yawn*", "sleepy...", "mmh"],

    # registered but unwired in v1
    "like":         ["~"],
    "sleeping":     ["zzz...", "*snore*"],
}

# ----------------------------------------------------------------------
# State transitions -> trigger keys
# ----------------------------------------------------------------------
# Only certain transitions fire a bubble. Steady state (same -> same) is a
# silent no-op. Some transitions are intentionally NOT wired (e.g. anything
# -> sleeping) because the bubble would interrupt the mood the sprite is
# trying to convey.
#
# Format: (new_state, optional_from_set) -> trigger_key
#   If from_set is None, ANY old_state -> new_state fires the key.
#   If from_set is a set/frozenset, only those old_states fire.
# ----------------------------------------------------------------------
STATE_TRIGGERS: list[tuple[str, Optional[frozenset[str]], str]] = [
    # New thinking turn (covers idle -> thinking, but NOT working -> thinking
    # since working IS already a kind of thinking)
    ("thinking",    frozenset({"idle", "sleeping", "drowsy", "celebrating", "concerned"}), "thinking"),

    # Started using tools / shell
    ("working",     None,                                                                   "working"),

    # Subagent appeared (highest charm-per-LOC)
    ("grooving",    None,                                                                   "grooving"),

    # Task finished
    ("celebrating", None,                                                                   "celebrating"),

    # Error appeared
    ("concerned",   None,                                                                   "concerned"),

    # Error cleared -> relief (NOT idle -> idle, which is silent)
    ("idle",        frozenset({"concerned"}),                                               "back_to_idle"),
]


# ----------------------------------------------------------------------
# Concern-reason formatting
# ----------------------------------------------------------------------
# The watcher already extracts a short reason string from errors.log when
# the concerned state fires (see watcher.parse_last_error). We surface it
# verbatim in the bubble, truncated + lowercased for pet vibes, IFF non-
# empty. If empty, we fall back to a generic concerned line.

_REASON_PREFIX_TRIM = (
    "anthropic.", "openai.", "google.", "pydantic_ai.", "httpx.",
    "TaskGroup", "ExceptionGroup",
)

def _format_concern_reason(reason: str) -> Optional[str]:
    """Turn a raw error-log reason into a bubble-friendly string.

    Returns None if the reason is empty or unsalvageable. Truncates to
    MAX_BUBBLE_CHARS - 1 (leaving room for trailing ellipsis if cut).
    """
    if not reason:
        return None
    r = reason.strip()
    # Strip noisy module prefixes
    for prefix in _REASON_PREFIX_TRIM:
        if r.startswith(prefix):
            r = r[len(prefix):].lstrip(":. ")
            break
    # Common cleanups
    if r.lower().startswith("error: "):
        r = r[7:]
    if r.lower().startswith("exception: "):
        r = r[11:]
    # Lowercase for pet vibes (errors shouldn't SHOUT at you)
    r = r.lower()
    # Truncate
    if len(r) > MAX_BUBBLE_CHARS:
        r = r[:MAX_BUBBLE_CHARS - 3].rstrip() + "..."
    return r or None


# ----------------------------------------------------------------------
# Shell-child detection -- "running pytest" / "running git push"
# ----------------------------------------------------------------------
# When the watcher reports state="working" because has_active_shell_children
# is True, we can read the shell child's cmdline directly from psutil. This
# gives concrete "what is TPA doing" bubbles without parsing autosave pickles.
#
# We trim flags + paths to get a short verb-noun bubble. Examples:
#   pytest tests/test_observer.py -v  ->  "running pytest"
#   git push origin main              ->  "running git push"
#   brew install ripgrep              ->  "running brew install"
#   /bin/sh -c 'cd foo && ls'         ->  "in a shell"
# ----------------------------------------------------------------------

# Two-word commands where the subcommand matters for the bubble
_TWO_WORD_TOOLS = {"git", "brew", "uv", "pip", "npm", "yarn", "pnpm", "docker",
                   "kubectl", "gcloud", "aws", "az", "gh", "go", "cargo"}

def _shell_cmd_bubble(cmdline: list[str]) -> Optional[str]:
    """Format a shell child's cmdline into a 'running X' bubble.

    Strategy: skip wrapper shells (sh -c), find the first non-flag word.
    For known multi-word tools (git, brew, etc.) include the subcommand.
    """
    if not cmdline:
        return None
    args = list(cmdline)
    # Skip sh -c / bash -c wrapper, parse the embedded command instead
    if args and args[0].endswith(("sh", "bash", "zsh")) and len(args) >= 3 and args[1] == "-c":
        # Extract first word of the embedded script
        embedded = args[2].lstrip().split()
        if not embedded:
            return "in a shell"
        args = embedded

    # Find first non-flag arg
    cmd = None
    for arg in args:
        if not arg.startswith("-"):
            # Strip path: /usr/bin/pytest -> pytest
            cmd = arg.rsplit("/", 1)[-1]
            break
    if not cmd:
        return None

    # For known multi-word tools, append the subcommand if present
    if cmd in _TWO_WORD_TOOLS:
        # Find the position of cmd in args, then look at the next non-flag
        try:
            idx = next(i for i, a in enumerate(args)
                       if a.rsplit("/", 1)[-1] == cmd)
            for sub in args[idx + 1:]:
                if not sub.startswith("-"):
                    bubble = f"running {cmd} {sub}"
                    if len(bubble) <= MAX_BUBBLE_CHARS:
                        return bubble
                    return f"running {cmd}"
        except StopIteration:
            pass

    bubble = f"running {cmd}"
    if len(bubble) > MAX_BUBBLE_CHARS:
        # Crude truncate
        bubble = bubble[:MAX_BUBBLE_CHARS - 1] + "..."
    return bubble


# ----------------------------------------------------------------------
# Observer class
# ----------------------------------------------------------------------
class Observer:
    """Generates speech-bubble reactions for state changes + interactions.

    Stateless except for the mute flag (queried via callback so config
    changes are picked up live without restart).
    """

    def __init__(self, get_muted: Callable[[], bool]):
        self._get_muted = get_muted

    # ------------------------------------------------------------------
    # Internal: random pick + length guard
    # ------------------------------------------------------------------
    def _pick(self, key: str) -> Optional[str]:
        """Pick a line for the given key. Returns None if key unknown,
        mute is on, or every candidate exceeds MAX_BUBBLE_CHARS."""
        if self._get_muted():
            return None
        spec = BUBBLE_LINES.get(key)
        if spec is None:
            return None
        choices = [spec] if isinstance(spec, str) else list(spec)
        # Filter out oversized entries defensively
        valid = [c for c in choices if len(c) <= MAX_BUBBLE_CHARS]
        if not valid:
            if choices:
                log.warning(
                    "observer: every line for key=%r exceeds %d chars; "
                    "no bubble will fire. Edit BUBBLE_LINES.",
                    key, MAX_BUBBLE_CHARS,
                )
            return None
        if len(valid) < len(choices):
            log.warning(
                "observer: %d/%d lines for key=%r exceed %d chars",
                len(choices) - len(valid), len(choices), key, MAX_BUBBLE_CHARS,
            )
        return random.choice(valid)

    # ------------------------------------------------------------------
    # State-change trigger
    # ------------------------------------------------------------------
    def on_state_change(
        self,
        old: str,
        new: str,
        *,
        concern_reason: str = "",
        shell_cmdline: Optional[list[str]] = None,
    ) -> Optional[str]:
        """Called when the StateMachine reports a transition.

        - Returns None if old == new (silent no-op)
        - Returns None if mute is on
        - For 'concerned', prefers the concern_reason verbatim if non-empty
        - For 'working' shell-state, prefers the shell command name if known
        - Otherwise picks a generic line from BUBBLE_LINES
        """
        if old == new:
            return None
        if self._get_muted():
            return None

        # Find matching trigger
        trigger_key = None
        for new_state, from_set, key in STATE_TRIGGERS:
            if new == new_state and (from_set is None or old in from_set):
                trigger_key = key
                break
        if trigger_key is None:
            return None

        # Enriched bubbles -- concrete info beats generic emote
        if trigger_key == "concerned":
            specific = _format_concern_reason(concern_reason)
            if specific is not None:
                return specific
        elif trigger_key == "working" and shell_cmdline:
            specific = _shell_cmd_bubble(shell_cmdline)
            if specific is not None and len(specific) <= MAX_BUBBLE_CHARS:
                return specific

        return self._pick(trigger_key)

    # ------------------------------------------------------------------
    # Interaction trigger
    # ------------------------------------------------------------------
    def on_interaction(self, kind: str) -> Optional[str]:
        """Called when the user interacts with Squid (poke, sprint, etc.)."""
        return self._pick(kind)

    # ------------------------------------------------------------------
    # Mood trigger (frontend mood notifications: drowsy, sleeping, stretch)
    # ------------------------------------------------------------------
    def on_mood_change(self, old: str, new: str) -> Optional[str]:
        """Called when the JS mood layer changes (drowsy/sleeping/stretch).

        Only fires for the entry edge -- e.g. (-> drowsy) once, not on every
        tick of drowsiness. Sleeping is silenced (let the sprite speak).
        Stretch maps to 'waking' since that's the wake-transition.
        """
        if old == new:
            return None
        if new == "drowsy":
            return self._pick("drowsy")
        if new == "stretch":
            return self._pick("waking")
        # sleeping -> no bubble (interrupts the calm)
        return None
