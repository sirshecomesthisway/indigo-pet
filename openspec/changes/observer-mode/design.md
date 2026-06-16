# Design — observer-mode

## Architectural decision

**Observer is a Python module, not a process.** It runs inside the existing
`watcher.py` thread and the `PetApi` JS bridge. No new daemon, no IPC, no
subprocess. Reactions are computed where the triggers fire and pushed into a
single shared `_pending_bubble` slot on PetApi.

This keeps the surface area tiny: the entire Observer subsystem is one module
+ one state field + one frontend component.

```
StateMachine.compute()  ─────┐
                              │ on state change
                              ↓
                       observer.on_state_change(old, new)
                              │ returns Optional[str]
                              ↓
PetApi (interactions) ───→  PetApi._pending_bubble  ←─── frontend poll
                              │                          (800 ms tick)
                              ↓
                       frontend renders #bubble div
                              │ on fade-out complete
                              ↓
                       api.clear_bubble()  →  _pending_bubble = None
```

## Why one bubble at a time, latest wins

Three reasons:

1. **Pet vibe, not chat vibe.** A queue would spam — Squid would be talking
   AT you instead of TO you. One bubble == one reaction == one moment.
2. **Frontend gets it for free.** The poll-based "latest state" model is
   already how mood works. No queue logic to add.
3. **Race-free.** Concurrent triggers (e.g. mood transition AND poke fire
   in the same 800 ms tick) just overwrite each other. Whichever PetApi
   call lands last wins. Acceptable — neither is "lost" in any meaningful
   sense because the other was about to be 800 ms old anyway.

Tradeoff: occasional dropped reactions. We accept this — bubbles are
ephemeral commentary, not state.

## Why hand-written lines (not LLM)

Three reasons:

1. **The voice IS the product.** Pink owns `BUBBLE_LINES`. Editing it is the
   product surface. An LLM intermediary means iterating Squid's tone means
   iterating a prompt, which is harder to A/B and version.
2. **LLM cost + latency** for an ambient companion would be absurd. Even a
   tiny model on every state transition would dominate TPA's own LLM
   spend.
3. **Anthropic stripped the LLM observer code from the BUDDY leak.** They
   considered the reaction prompt the IP. Reproducing it is the most
   fragile-to-clone version. Building a hand-written rule layer first lets
   us own the voice cleanly.

LLM can come later as a v2 enrichment — same `pending_bubble` slot, new
generator. Not in this change.

## Trigger taxonomy

Two trigger families:

| Family | Source | Examples |
|---|---|---|
| **State transitions** | `StateMachine.compute()` — old_state ≠ new_state | `idle→thinking`, `thinking→working`, `*→celebrating`, `*→concerned`, `*→sleeping`, `sleeping→idle` |
| **Interactions** | `PetApi` methods (called from JS) | `poke()`, `like()` (dblclick), `sprint_perimeter()`, mood notify (drowsy, sleeping, stretch) |

Each trigger is a single string key in `BUBBLE_LINES`. Value can be a single
string OR a list (random pick). v1 ships ~12 keys.

## BUBBLE_LINES — proposed v1 voice

```python
BUBBLE_LINES = {
    # state transitions
    "thinking": ["ooh, thinking...", "let me think", "hmm..."],
    "working":  ["okay, on it", "doing the thing", "*types*"],
    "celebrating": ["nice!", "yes!!", "got it"],
    "concerned": ["uh oh", "yikes", "that's not right"],
    "sleeping": ["zzz...", "*snore*"],
    "waking":   ["mmf...", "huh? oh"],

    # interactions
    "poke":     ["boop?", "hi", "?"],
    "like":     ["~", " "],   # ASCII-friendly: tilde for sparkle, blank for heart
    "sprint":   ["wheee!", "weeee!"],
    "sprint_end": ["*pant pant*", "phew"],
    "drowsy":   ["*yawn*", "sleepy..."],
}
```

Pink reviews + edits before implementation. The dict IS the voice contract.

## Why the bubble is in the frontend, not a tooltip

NSWindow tooltips are slow, ugly, and OS-styled. A CSS bubble in the same
DOM as the sprite:
- Renders instantly (no OS round-trip)
- Animates smoothly (CSS keyframes, same engine as hearts)
- Inherits the click-passthrough rules already in place
- Can use the sprite's existing position as reference (`position: absolute`
  above `#pet`)

Tradeoff: bubble lives inside the 220 px window box. Long lines wrap to 2
lines max; we enforce a 32-char max in `observer.py` to guarantee one-line
fit at default sprite width.

## Mute behavior

`config.json` gains `muted: bool` (default False). When True:
- `observer.maybe_emit()` short-circuits and returns None
- All transition + interaction triggers no-op into the bubble slot
- The mood-state pipeline is unaffected (Squid still walks, sleeps, etc.)

Menu item is a checkbox-style toggle in the right-click menu. State persists
across restarts via `~/.squid-pet/config.json`.

## Test strategy

- Unit tests in `tests/test_observer.py`:
  * `BUBBLE_LINES` dict has all required keys
  * Every value is `<= 32` chars (after pick)
  * `on_state_change()` returns None when old == new
  * `on_state_change()` returns a string for each documented transition
  * Mute flag short-circuits every emit path
- StateMachine integration: extend `test_state_machine.py` with one
  test asserting `observer.on_state_change` is called with correct args
  on a state flip
- Manual UI validation in tasks.md — bubble appears/fades for each trigger

## Decisions

### D1: `pending_bubble` lives on PetApi, not in state.json
**Decision:** It's an in-memory field on `PetApi`, not persisted to
`~/.squid-pet/state.json`.
**Rationale:** Bubbles are ephemeral chat-style events; persisting them
means a restart could replay a stale bubble. State.json stays the
durable mood snapshot.

### D2: Frontend ack via `clear_bubble()`, not timeout-only
**Decision:** Frontend calls `api.clear_bubble()` after the fade-out
animation completes. Backend does NOT auto-clear on a timer.
**Rationale:** The frontend owns the fade duration (CSS), so it's the
authoritative "I'm done with this bubble" signal. Backend auto-clearing
would race the animation.

### D3: No queue, latest-wins
**Decision:** New bubbles overwrite the pending slot unconditionally.
**Rationale:** See "Why one bubble at a time" above.

### D4: Mute is a hard kill, not a dim
**Decision:** Mute toggles the entire observer off — no muted-bubble
indicator, no faded bubbles.
**Rationale:** "Mute" should mean "be invisible commentary-wise." A
dimmed bubble would still be a visual interrupt, defeating the purpose.

### D5: Lines are picked at emit time, not at trigger time
**Decision:** When a trigger fires, `random.choice(BUBBLE_LINES[key])`
runs in `observer.py`. The picked string is what goes into
`pending_bubble`.
**Rationale:** Simple, deterministic-per-call. Frontend gets a finished
string to render — no client-side rolling. Pink can swap lines without
touching frontend.
