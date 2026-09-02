"""
observer.py -- speech-bubble reaction layer for Squid.

Architecture: the Observer is a passive comment layer that:
  1. Watches state transitions reported by the StateMachine
  2. Watches direct user interactions reported by PetApi
  3. Returns short reaction strings (<= 32 chars) for the frontend bubble

It NEVER modifies pet state, NEVER intercepts the coding agent, and NEVER
produces multi-line output. The voice lives entirely in the BUBBLE_LINES
dict below -- editing that dict is the canonical way to evolve Squid's
personality.

Reference: openspec/specs/observer-mode/spec.md
"""
from __future__ import annotations

import random
import logging
import threading
from typing import Callable, Optional, Union

log = logging.getLogger(__name__)

# Hard cap on bubble length. Anything longer would wrap to 2+ lines at the
# default sprite width (~200px @ 14px font). Enforced defensively in
# _pick(): out-of-spec lines return None and log a warning.
MAX_BUBBLE_CHARS = 32

# How often an idle chatter beat becomes an idea prompt instead of
# ordinary filler. 1-in-5: idle chatter fires every 26-34s and she goes
# drowsy after a few minutes, so this lands roughly once or twice per
# awake-idle stretch -- present without hounding you.
IDEA_PROMPT_CHANCE = 0.2

# ----------------------------------------------------------------------
# LLM enrichment layer (llm-bubbles change 2026-06-24)
# ----------------------------------------------------------------------
# When an LLMClient is wired in, certain "rich-context" state transitions
# (working / concerned / celebrating / grooving) ALSO fire a background
# LLM call that may produce a more contextual bubble line. The rule-based
# bubble is published immediately as before -- the LLM line, if it
# arrives in time and respects the 32-char cap, overwrites it. If the LLM
# call fails, times out, returns empty, or returns too-long text, the
# rule-based bubble stays. Squid never goes silent because of LLM issues.
#
# Triggers NOT in this set keep their rule-based-only behavior:
#   - thinking/back_to_idle: too frequent, not enough context
#   - poke/shake/sprint/drowsy/waking: interaction emotes, snappy beats smart
# ----------------------------------------------------------------------
LLM_ENRICH_TRIGGERS = frozenset({"working", "concerned", "celebrating", "grooving"})

# The voice contract for the LLM. Anything beyond a single short
# observation breaks the bubble UI. Empty string = stay silent.
OBSERVER_SYSTEM_PROMPT = (
    "You are Squid, a tiny pink octopus pet who lives on the corner "
    "of the user's screen. You watch them work with their AI coding "
    "agent (Claude Code, Codex, or similar) but you are NOT the agent "
    "and NEVER speak as it. You are a passive observer who occasionally "
    "pipes up.\n\n"
    "VOICE: dry, fond, perceptive. Lowercase. Fragmentary. Like a cat "
    "noticing a thing. Asterisk-actions like *peeks* or *flops* are fine.\n\n"
    "HARD RULES:\n"
    "- Reply with AT MOST 30 characters, including spaces. Shorter is better.\n"
    "- Reply with an EMPTY STRING if there's nothing worth saying.\n"
    "- Never use !, ?, all-caps, emoji, or quotation marks.\n"
    "- Never say 'I' or 'you' or 'we'. Pure observation only.\n"
    "- Never explain yourself, never apologize, never narrate.\n"
    "- Never reference being an AI, model, or language system.\n"
    "- Stay silent more often than you speak. Silence is the default.\n\n"
    "CONTEXT SIGNALS in the user message (use them, do not invent):\n"
    "  shell=git push   -> reply like: shipping it, pushing\n"
    "  shell=git commit -> reply like: sealed it, committed\n"
    "  shell=pytest     -> reply like: pytest, hm   or   watching tests\n"
    "  shell=git diff   -> reply like: *peeks at the diff*\n"
    "  shell=uv pip|npm -> reply like: fetching deps\n"
    "  subagent=NAME    -> reply like: ohhh, subagent   or   help arrived\n"
    "  llm=streaming with no shell -> reply like: thinking out loud, model talking\n"
    "  git=just-changed (no shell) -> reply like: noticed a commit\n"
    "  reason=ratelimit -> reply like: ugh, capped\n"
    "  from=working to=idle        -> reply like: back to idle, *flops*\n"
    "  from=working to=celebrating -> reply like: shipped, finally, done done\n"
    "  no obvious signal           -> reply with EMPTY STRING\n\n"
    "Examples of GOOD outputs (note: no emoji, no !, no punctuation flourish):\n"
    "  pytest, hm\n"
    "  ohhh, a subagent\n"
    "  uhoh\n"
    "  *peeks at the diff*\n"
    "  shipped\n"
    "  back to idle\n"
    "  fetching deps\n"
    "  thinking out loud\n"
    "  sealed it\n"
    "  (empty string)\n"
    "  (empty string)\n"
    "  (empty string)\n\n"
    "Examples of BAD outputs (DO NOT do these):\n"
    "  Great job!  -- has ! and capital letter\n"
    "  \U0001f389 nice push  -- has emoji\n"
    "  Pushed to main, nice!  -- has capital and !\n"
    "  I see you ran pytest  -- says I and you, narrates\n"
    "  Let me know if you need anything  -- offers help, sounds like an assistant"
)


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
#   approval_needed = short attention-grab (Squid waving her flag)
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
    "celebrating":  ["yay!!", "woo!", "!!", "*wiggles*"],
    "concerned":    ["eep", "hmmnn", "urk", "!?"],
    "back_to_idle": ["pheww", "hhh", "*flops*", "*sigh*"],
    "waking":       ["mmf...", "nhg", "wh-", "*stretches*"],

    # TPA is waving flag -- awaiting Pink's input (Pink 2026-07-02)
    "approval_needed": ["your turn!", "psst!!", "yoo??", "input?",
                        "heyy!!", "you? you!", "peek", "hi hi!"],

    # interactions
    "poke":         ["boop?", "hi", "?", "hm?"],
    "shake":        ["wheee", "whoa!", "whee~", "hey hey"],
    "sprint":       ["wheee!", "zoom", "*blurs*", "go!"],
    "sprint_end":   ["*pant pant*", "phew", "x_x"],
    "drowsy":       ["*yawn*", "sleepy...", "mmh"],

    # Pink-2026-08-31: "like" is now wired -- fired by
    # PetApi.acknowledge_approval() when a dblclick/heart lands while
    # she's waving the approval_needed flag ("I saw you, calming
    # down"). "sleeping" stays registered but unwired in v1.
    "like":         ["~", "gotcha!", "okay okay!", "noted~", "seen ya"],
    "sleeping":     ["zzz...", "*snore*"],

    # Idle chatter (2026-08-18) -- fired occasionally by RoutineController
    # during "rest" beats of the idle cycle, NOT on a state transition.
    # Voice: same dry/fond/fragmentary rules as everything else, but
    # there's genuinely nothing happening, so these are small-talk /
    # boredom beats rather than reactions to anything.
    # Pink-2026-08-27k: expanded with funnier lines ("too quiet" report) --
    # same voice, leaning harder into dry/absurdist octopus-desk-pet humor.
    "idle_chatter": ["hmm", "*stares*", "waiting~", "bored?", "tick... tock",
                     "*tentacle wiggle*", "anything?", "still here",
                     "la la la", "*doodles*", "hm hm hm", "nothing yet",
                     "*people-watching*", "quiet today",
                     "*counts pixels*", "ink's dry", "8 arms, 0 tasks",
                     "send help. or snacks", "procrastinating hard",
                     "*polishes suckers*", "*this is fine*",
                     "not stuck. resting", "*naps standing up*",
                     "could use a snack",
                     # Pink-2026-08-31: more of the same idle/bored voice.
                     "*idly floats*", "eight arms, zero plans",
                     "just vibing here", "is this a screensaver?",
                     "*yawns, mostly*", "watching the cursor blink",
                     "*pretends to work*", "so... anything?",
                     "ink levels: fine", "*floats sideways*",
                     "clock is not moving", "*stares at nothing*",
                     "still floating", "low tide today"],

    # Pink-2026-09-01: idle lines that invite the next project rather than
    # just filling silence. Deliberately a SEPARATE pool, not more
    # idle_chatter entries: chatter fires every 26-34s, and a squid asking
    # "what should we build?" at that rate stops reading as company and
    # starts reading as nagging. Blended in at IDEA_PROMPT_CHANCE by
    # on_idle_chatter so it stays an occasional nudge.
    #
    # Same register as the rest of her voice -- lowercase, short, dry,
    # never exclaiming. Half of them offer an idea ("i got an idea") and
    # half ask for one; she should feel like a collaborator with her own
    # half-formed thoughts, not a prompt box.
    "idle_idea_prompt": ["i got an idea", "ok, i got an idea",
                         "*has an idea* ...maybe",
                         "got anything to build?",
                         "what are we making?",
                         "wanna build something?",
                         "any half-baked ideas?",
                         "pitch me something",
                         "*taps a tentacle* ideas?",
                         "i've been thinking...",
                         "got a project in mind?",
                         "something worth making?",
                         "ideas? arms are free",
                         "*brainstorming, allegedly*",
                         "new thing today?"],

    # "Still working" reannounce fallback (2026-08-27k) -- fired by
    # _maybe_reannounce_working when there's no concrete shell command to
    # report (Edit/Write-only tool calls, or plain generation with no
    # tool call in flight). Same percussive "working" sonic signature,
    # just more variety than the 4-line on-entry set. Mixed at pick time
    # with working_wrapup below -- see on_still_working.
    "working_generic": ["writing code", "typing away", "*click clack*",
                        "on it", "building things", "*focused*",
                        "assembling parts", "mid-thought", "*tap tap tap*",
                        "cooking something"],

    # Occasional "sounds like she's wrapping up" flavor, mixed into the
    # SAME reannounce pool as working_generic above rather than gated on
    # any real "is this the last step" detection -- there's no such
    # signal available (no transcript content is ever read, by design).
    # Purely a minority flavor for variety, not a claim of fact.
    "working_wrapup": ["wrapping up", "tying loose ends",
                       "writing the summary", "almost there",
                       "polishing it", "final touches",
                       "closing things out", "buttoning up"],

    # Pink-2026-08-31: octopus-personality flavor for the same
    # working_generic/working_wrapup fallback pool (see on_still_working)
    # -- added after a report that the "working" reannounce beat could
    # get stepped on by idle_chatter's "8 arms, 0 tasks" and friends,
    # which read as bored/idle while she's actually mid-task. These are
    # deliberately the busy inverse of that idle voice (same dry/fond
    # octopus-desk-pet humor, but leaning into "swamped", not "bored").
    "working_squid": ["*eight arms, one task*", "*all arms busy*",
                      "ink's flowing", "no arms free rn",
                      "*tentacle traffic jam*", "grinding away",
                      "*ink-stained already*", "hands full. all 8",
                      "*deep in the ink*", "arms full, no complaints",
                      # Pink-2026-08-31: more of the same busy voice.
                      "*all suckers on deck*", "eight arms, zero rest",
                      "*ink flying*", "*multi-arm multitask*",
                      "swamped (happily)", "*typing with six arms*",
                      "arms everywhere, useful", "*full tentacle sprint*",
                      "busy is an understatement", "*ink trail behind me*",
                      "no time to float", "*eight-armed efficiency*"],
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
    # TPA is waving flag -- awaiting Pink's input (Pink 2026-07-02).
    # Fires on any transition INTO approval_needed. Rule-based lines
    # from BUBBLE_LINES["approval_needed"]; LLM enrich may overwrite.
    ("approval_needed", None, "approval_needed"),

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
# Pink-2026-08-22: the natural trigger for "concerned" (TPADetector
# reading errors.log) was removed along with TPADetector -- no
# equivalent exists for Claude Code / Codex. "concerned" is presently only
# reachable via the ~/.squid-pet/force_state debug override, which never
# populates concern_reason, so this formatting always falls through to the
# generic concerned line below. Kept in case a future detector wires up
# concern_reason again.

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
# Celebrate-reason formatting -- "why is she celebrating?"
# ----------------------------------------------------------------------
# Pink-2026-08-27: celebrating used to ALWAYS show a generic exclamation
# ("yay!!"/"woo!"/...) with zero indication of what actually happened --
# a real user complaint ("squid is celebrating but i don't know why").
# watcher.py's celebrating branch sets a specific state_reason naming
# which detector's signal fired; this maps that to a clear, deterministic
# bubble instead of a random mood-only pick. Unlike _format_concern_reason,
# this is NOT probabilistic and doesn't go through the LLM-enrich path --
# "why" deserves a consistent answer, not personality-driven variety.
#
# Pink-2026-08-30: "claude celebrating" briefly removed from this table
# (Stop fires every turn, not just "the task is done", so it was moved
# entirely to GROOVING -- Pink report: celebrating mid-task on routine
# turns). A same-day settle-window promotion attempt (GROOVING ->
# CELEBRATING after N seconds of silence) turned out to have the same
# bug just delayed -- ordinary reply latency outlasts any reasonable
# window. Re-added for good once watcher.py grew a real way to tell them
# apart: claude_task_complete, an explicit marker Claude itself writes
# (scripts/squid_task_complete.py) only when the whole task is done, no
# timer involved -- see StateMachine._compute_inner branches 2/3.
#
# codex's celebrate signal is UNCHANGED (CodexDetector.is_celebrating()
# is still a hardcoded False, "no reliable signal yet" -- Codex has no
# known equivalent hook, so this branch stays unreachable/aspirational
# for now) -- keep it noncommittal so a future real signal doesn't
# inherit an overclaiming default. GitDetector's celebrate is tied to an
# actual HEAD mtime change -- a real commit happened -- so it's already
# safe to state as fact.
_CELEBRATE_REASON_BUBBLES = {
    # Pink-2026-08-30: re-added -- claude celebrating is real again now
    # that watcher.py distinguishes it from routine turn completion via
    # an explicit marker (see claude_task_complete in
    # StateMachine._compute_inner), not a timer.
    "claude celebrating": "finished with claude!",
    "codex celebrating": "ooh, codex!",
    "git celebrating": "nice, fresh commit!",
}


def _format_celebrate_reason(state_reason: str) -> Optional[str]:
    """Deterministic "why" bubble for a specific celebrate source.
    Returns None for the generic/unspecific "celebrating" reason (e.g.
    force_state debug override) -- falls back to the mood-pick line."""
    return _CELEBRATE_REASON_BUBBLES.get((state_reason or "").strip().lower())


# ----------------------------------------------------------------------
# Reason explanations -- "why did she just do that?"
# ----------------------------------------------------------------------
# Pink-2026-09-01: "she went from idle to a very brief working state, I
# guess because I opened /model, but I'm not sure -- better to explain
# why." The watcher already computes a state_reason for every state (it is
# what `squid why` prints); it just never reached the bubble in a form
# anyone would want to read.
#
# There WAS a path for this ("Fix C", 2026-06-28): a 50% chance to use
# state_reason verbatim if it started with one of
# ("shell ", "subagent", "llm streaming", "error:", "writing", "post-busy").
# Two things were wrong with it. Those prefixes no longer match what the
# watcher emits -- "file write detected (claude_code)", "claude streaming"
# and "claude turn in flight" all miss -- so the path was mostly dead. And
# where it did hit, it published raw internals: "shell child active
# (claude_c..." truncated at MAX_BUBBLE_CHARS. A reason is a log line; an
# explanation is a sentence for the person watching.
#
# So: a real map, always applied (no coin flip -- if she moved, you get to
# know why), phrased in her voice and short enough to survive the cap.
_REASON_EXPLAIN = {
    "shell child active (claude_code)": "claude ran a command",
    "shell child active (codex)":       "codex ran a command",
    "shell child active":               "something ran a command",
    "file write detected (claude_code)": "claude edited a file",
    "file write detected (codex)":       "codex edited a file",
    "claude streaming":       "claude's thinking",
    "codex streaming":        "codex's thinking",
    "claude turn in flight":  "claude's mid-turn",
    "claude recapping":       "claude's recapping",
    "claude grooving":        "claude finished a turn",
    "creative burst":         "something wrapped up",
    "claude celebrating":     "claude finished the task",
    "codex celebrating":      "codex finished",
    "git celebrating":        "fresh commit landed",
    "no signals":             "nothing running",
    "non-agent detector busy": "something's busy",
    # Deliberately NOT mapped: bare "celebrating" (the force_state debug
    # override's unspecific reason). "something good happened" explains
    # nothing, and an invented non-explanation is worse than letting her
    # personality answer -- see
    # test_celebrating_falls_back_to_generic_mood_pick_when_unspecific,
    # which caught exactly that when this entry existed.
}

# Reasons the watcher builds with a variable tail, matched by prefix.
_REASON_EXPLAIN_PREFIX = (
    ("working hold",        "still on it"),
    ("idle ",               "nothing going on"),
    ("force_state override", "pinned by hand"),
    ("awaiting_input",      "claude needs you"),
)


def _explain_reason(state_reason: str) -> Optional[str]:
    """Turn a watcher state_reason into a bubble that says why she moved.

    Returns None for anything unmapped, so the caller falls back to a
    personality line rather than leaking a new internal string into the
    UI the day someone adds one.
    """
    if not state_reason:
        return None
    r = state_reason.strip()
    exact = _REASON_EXPLAIN.get(r)
    if exact is not None:
        return exact
    low = r.lower()
    for prefix, text in _REASON_EXPLAIN_PREFIX:
        if low.startswith(prefix.lower()):
            return text
    return None


# ----------------------------------------------------------------------
# Shell-child detection -- "running pytest" / "running git push"
# ----------------------------------------------------------------------
# When the watcher reports state="working" because has_active_shell_children
# is True, we can read the shell child's cmdline directly from psutil for a
# concrete "what's it doing" bubble. Pink-2026-08-22: window.py currently
# always passes shell_cmdline=None -- the only wiring that ever populated it
# (find_tpa_processes + latest_shell_child_cmdline) was TPA-
# only and was removed. Kept for a future Claude Code / Codex equivalent.
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

    def __init__(
        self,
        get_muted: Callable[[], bool],
        llm_client: Optional["object"] = None,
        publish_cb: Optional[Callable[[str], None]] = None,
        get_llm_enabled: Optional[Callable[[], bool]] = None,
    ):
        """get_muted: callback returning current mute state (queried per call).

        llm_client: optional LLMClient instance. Held regardless of current
            llm_bubbles config state (constructing it is cheap, no network).
            The actual gating happens at dispatch time -- see get_llm_enabled.

        publish_cb: callback the LLM worker uses to publish its result.
            Required if llm_client is provided. Signature: publish_cb(text).
            The window-side implementation typically sets _pending_bubble.

        get_llm_enabled: callback returning the CURRENT value of the
            llm_bubbles config flag. Queried on every dispatch so menu
            toggles take effect without a Squid restart (llm-bubbles polish
            2026-06-27, item 1). If None, defaults to "always enabled when
            llm_client is available and is_available()" -- back-compat
            behavior for older tests/embedders.
        """
        self._get_muted = get_muted
        # Hold the LLM client; gate on get_llm_enabled() at dispatch time
        # rather than caching the "is it enabled" decision at __init__.
        self._llm = llm_client if (llm_client and getattr(llm_client, "is_available", lambda: False)()) else None
        self._publish_cb = publish_cb
        self._get_llm_enabled = get_llm_enabled or (lambda: True)

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
    # LLM enrichment -- fires in background, may overwrite rule-based bubble
    # ------------------------------------------------------------------
    def _async_enrich(self, trigger_key: str, context: str, *, is_specific: bool = False) -> None:
        """Fire a background LLM call. On success, publish via callback.

        Runs in a daemon thread so it never blocks the watcher loop. The
        LLM call itself is rate-limited inside LLMClient.ask() (5s min
        between calls per process), so even a busy state machine won't
        burst-call the gateway. All failures are silent -- the rule-based
        bubble is already published, so the user sees something either way.
        """
        if self._llm is None or self._publish_cb is None:
            return
        if self._get_muted():
            return
        # Hot-reload gate (llm-bubbles polish 2026-06-27, item 1): the
        # llm_bubbles config flag is queried per-dispatch via the
        # get_llm_enabled callback, so flipping the menu toggle (or
        # editing config.json) takes effect on the very next state
        # change. No Squid restart needed.
        if not self._get_llm_enabled():
            return
        if trigger_key not in LLM_ENRICH_TRIGGERS:
            return
        # Fix A (2026-06-28): if the rule-based bubble is already SPECIFIC
        # (e.g. "running git commit", "rate limited"), don't let the LLM
        # overwrite it with a generic mood like "settle in". Concrete info
        # beats poetic emote -- Pink's words: "say something meaningful".
        if is_specific:
            return

        def _worker():
            try:
                # Compact context: trigger + reason. The system prompt
                # does the heavy lifting of voicing the line.
                user_msg = f"trigger={trigger_key}; context={context[:240]}"
                reply = self._llm.ask(
                    system=OBSERVER_SYSTEM_PROMPT,
                    user=user_msg,
                    max_tokens=40,
                )
            except Exception as e:  # noqa: BLE001 -- defensive
                log.warning("observer: async enrich failed (%s)", type(e).__name__)
                return

            if reply is None:
                return  # rate-limited or HTTP failure -- keep rule-based
            reply = reply.strip().strip('"\'')
            if not reply:
                return  # model chose silence -- keep rule-based
            if len(reply) > MAX_BUBBLE_CHARS:
                # Hallucinated past the limit. Drop entirely rather than
                # truncating -- truncation creates ugly mid-word cuts.
                log.warning("observer: llm reply %d chars > %d, dropped",
                            len(reply), MAX_BUBBLE_CHARS)
                return
            # Re-check mute right before publishing -- user may have muted
            # while the call was in-flight.
            if self._get_muted():
                return
            try:
                self._publish_cb(reply)
            except Exception as e:  # noqa: BLE001
                log.warning("observer: publish_cb raised (%s)", type(e).__name__)

        t = threading.Thread(target=_worker, name="squid-llm-enrich", daemon=True)
        t.start()

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
        # Fix B (2026-06-28): richer LLM context. All optional, all
        # back-compat -- existing callers/tests work unchanged.
        subagent_name: Optional[str] = None,
        llm_streaming: bool = False,
        git_active: bool = False,
        cpu_pct: float = 0.0,
        tool_age: float = float("inf"),
        # Fix C (2026-06-28): state machine's "why" string. When non-empty
        # AND starts with an interesting prefix, 50% chance Squid uses it
        # verbatim as the bubble (the "mix mood + reason" path Pink chose).
        state_reason: str = "",
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

        # Pink-2026-08-30: PreCompact-triggered "recapping" needs to
        # announce itself regardless of what state Squid was in right
        # before -- including working -> thinking, which the "thinking"
        # trigger below deliberately suppresses everywhere else ("working
        # IS already a kind of thinking", no bubble needed). A compaction
        # is a genuinely different activity from routine reasoning, and
        # Pink specifically asked for it called out by name, so this
        # bypasses STATE_TRIGGERS's old-state filtering entirely. Not
        # random (see _format_celebrate_reason's rationale) -- Pink wants
        # a consistent, always-shown answer, not a personality coin flip.
        # Naturally rate-limited by the old==new check above: repeated
        # ticks while still recapping never re-fire this (old and new are
        # both "thinking" on every tick after the first).
        if new == "thinking" and state_reason == "claude recapping":
            return "📝 recapping..."

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
        elif trigger_key == "celebrating":
            specific = _format_celebrate_reason(state_reason)
            if specific is not None:
                return specific
        elif trigger_key == "working" and shell_cmdline:
            specific = _shell_cmd_bubble(shell_cmdline)
            if specific is not None and len(specific) <= MAX_BUBBLE_CHARS:
                # Still fire LLM enrich -- the shell-cmd bubble is fine
                # but LLM may produce something contextually nicer.
                context = f"old={old} new={new} shell={' '.join(shell_cmdline[:6])}"
                self._async_enrich(trigger_key, context, is_specific=True)
                return specific

        # Explain the move (Pink-2026-09-01). Replaces the old 50%
        # raw-state_reason path -- see _explain_reason for why that one was
        # both mostly dead and, when alive, published truncated internals.
        # Not a coin flip: if she changed state, you get to know why. Falls
        # through to a personality line only when the reason is unmapped,
        # so a newly-added internal string can never leak into a bubble.
        #
        # Returns without _async_enrich for the same reason the concern and
        # celebrate paths do: the LLM would overwrite a true, specific
        # answer with mood-mush.
        explained = _explain_reason(state_reason)
        if explained is not None:
            return explained

        rule_bubble = self._pick(trigger_key)
        # Background LLM enrich: may overwrite the rule-based line if it
        # arrives in time. Context bundles all the signals we have.
        # Fix B (2026-06-28): include subagent name, llm-streaming heartbeat,
        # git activity, cpu, and tool age so the model has enough to say
        # something specific instead of falling back to mood-mush like
        # "settle in".
        if rule_bubble is not None:
            ctx_parts = [f"from={old}", f"to={new}"]
            if shell_cmdline:
                ctx_parts.append(f"shell={' '.join(shell_cmdline[:4])}")
            if subagent_name:
                ctx_parts.append(f"subagent={subagent_name}")
            if llm_streaming:
                ctx_parts.append("llm=streaming")
            if git_active:
                ctx_parts.append("git=just-changed")
            if cpu_pct > 0:
                ctx_parts.append(f"cpu={cpu_pct:.0f}%")
            if tool_age < 30:
                ctx_parts.append(f"tool_age={tool_age:.0f}s")
            if concern_reason:
                ctx_parts.append(f"reason={concern_reason[:120]}")
            self._async_enrich(trigger_key, "; ".join(ctx_parts))
        return rule_bubble

    # ------------------------------------------------------------------
    # "Still working" refresh -- NOT a state transition
    # ------------------------------------------------------------------
    def on_still_working(self, shell_cmdline: Optional[list[str]]) -> Optional[str]:
        """Called periodically (by PetApi, throttled) while state STAYS
        'working' across ticks, to surface what she's currently watching
        even though on_state_change only fires once on entry.

        Prefers a concrete "running X" from a live shell child. Falls
        back to a generic working/wrap-up line (working_generic +
        working_wrapup, mixed) when there's no shell command to report --
        e.g. Edit/Write-only tool calls, or plain generation with no tool
        call in flight. Pink-2026-08-27k: this used to return None in the
        fallback case (repeating a canned line "would read as a glitch,
        not aliveness") -- but the caller (_maybe_reannounce_working)
        already dedupes against the last shown text and only fires on a
        throttle, so a varied generic pool reads as ambient presence
        instead, addressing a "too quiet" report.
        """
        if self._get_muted():
            return None
        if shell_cmdline:
            specific = _shell_cmd_bubble(shell_cmdline)
            if specific is not None:
                return specific
        pool = (BUBBLE_LINES["working_generic"] + BUBBLE_LINES["working_wrapup"]
                + BUBBLE_LINES["working_squid"])
        return random.choice(pool)

    # ------------------------------------------------------------------
    # Interaction trigger
    # ------------------------------------------------------------------
    def on_interaction(self, kind: str) -> Optional[str]:
        """Called when the user interacts with Squid (poke, sprint, etc.)."""
        return self._pick(kind)

    def on_idle_chatter(self) -> Optional[str]:
        """The ~26-34s ambient idle beat (RoutineController's chatter_cb).

        Mostly ordinary idle filler, but IDEA_PROMPT_CHANCE of the time she
        asks what you want to build -- or claims to have thought of
        something herself. Separate pool rather than more idle_chatter
        entries so the rate is a knob: the difference between a pet with
        her own ideas and one that pesters you is entirely frequency.
        """
        if random.random() < IDEA_PROMPT_CHANCE:
            return self._pick("idle_idea_prompt")
        return self._pick("idle_chatter")

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
