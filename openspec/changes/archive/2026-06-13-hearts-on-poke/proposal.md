## Why

Pink shipped 8+ user-facing interactions today (poke, sprint, drag, swing-to-wake,
drowsy state, edge-routing, etc.). Squid does a lot but provides minimal visual
feedback that she "received" affection. The "boop!" hint pill helps but is a
small text label in the corner — it doesn't feel rewarding.

Hearts emoji floating up from Squid on poke gives a cheap, instantly-readable
"she likes that" signal that complements (does not replace) the boop hint.

## What Changes

- Introduce a new capability `pet-reactions` covering ephemeral visual effects
  spawned by user gestures (distinct from state-driven sprite animations).
- Add `spawnHearts(x, y)` JS function in `frontend/index.html` that creates
  3 floating heart emoji that rise + fade over ~1 second.
- Wire `spawnHearts()` into the dblclick handler (LIKE gesture). Single-click
  remains wake-only; dblclick fires poke + heart together.
- Hearts are pure frontend — zero Python changes, zero new dependencies.

## Goal

- Provide an instantly-readable visual reward for the poke gesture so Pink
  feels like Squid acknowledges affection.
- Establish a reusable `pet-reactions` capability so future ephemeral effects
  (sparkles on sprint, zzz puffs on drowsy entry, etc.) follow a consistent
  pattern.

## Non-goals

- NOT triggered by swing-to-wake (already has "wheee!" hint — avoid sensory pile-up).
- NOT triggered by drag/drop (boring action, no reward needed).
- NOT triggered by TPA-state transitions (would be visual noise).
- NO speech bubble (parked separately — requires TPA-context awareness first).
- NO sound — Squid stays silent.
- NO heart counter / petting tracker — purely ephemeral.

## Capabilities

### New Capabilities

- `pet-reactions`: Ephemeral visual effects (emoji, sparkles, particles) spawned
  by user gestures. Distinct from `pet-animations` (state-driven sprite frames)
  in that reactions are short-lived, gesture-triggered, and rendered as floating
  DOM elements rather than sprite swaps.

### Modified Capabilities

_None._

## Impact

- **Code**: `src/squid_pet/frontend/index.html` only (CSS keyframes + ~25 lines JS).
- **Dependencies**: None.
- **State files**: None.
- **Performance**: Each poke creates 3 DOM nodes that auto-remove after 900 ms.
  Max 12 concurrent on screen.
- **OS**: No new OS calls.
