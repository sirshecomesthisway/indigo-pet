# observer-mode

## Why

Squid is silent. She has nine moods and three interaction surfaces (poke, LIKE,
right-click) but nothing she "says." This is the **biggest aliveness gap**
identified in the BUDDY pattern analysis (2026-06-08): Claude Code's pet
companion has the same sprite-and-state machinery as Squid, plus an "observer"
layer that emits one-line reactions in a speech bubble. The observer makes the
pet feel *conscious of you*, not just reactive to processes.

This change adds Observer Mode to Squid: a rule-based reaction layer that
publishes short speech-bubble lines on state transitions and user interactions.

Architectural lesson from BUDDY: **observer ≠ assistant**. The observer is a
separate, cheap, deterministic layer that coexists with TPA (the real
agent). It NEVER replaces the agent — it comments on it. v1 is hand-written
rule-based reactions; LLM-generated lines are explicitly out of scope.

## What changes

- Add `observer.py` module owning a `BUBBLE_LINES` dict + dispatch logic
- Add `pending_bubble: Optional[str]` field to `PetApi` state, exposed via
  `get_state()`; frontend polls and calls `clear_bubble()` to ack
- Add CSS speech-bubble component above sprite (frontend-only, follows the
  hearts/poke-hint pattern: absolute-positioned DOM, `pointer-events: none`,
  fade in/out)
- Wire transition triggers in `watcher.py` `StateMachine.compute()`:
  any → thinking, thinking → working, any → celebrating, any → concerned
- Wire interaction triggers in `PetApi`: poke, LIKE (dblclick), sprint start,
  sprint end, drowsy entry
- Add "Mute Squid" menu item that suppresses all bubbles (config-persisted)

## Out of scope (deferred to a later change)

- LLM-generated bubble content (start with hand-written, learn the voice)
- Per-tool reactions (`read_file`, `grep`, `shell`) — needs TPA log-tailing,
  separate change
- Bubble history / queue (one bubble at a time, latest wins)
- Multi-line bubbles (one short line = pet vibe; paragraphs = chatbot vibe)
- Per-user voice variants
