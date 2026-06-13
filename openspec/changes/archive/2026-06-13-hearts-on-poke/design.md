## Design

### Trigger

REVISED (2026-06-08): Heart trigger is now DOUBLE-CLICK (the LIKE
gesture), not single-click. Single-click is the quick-wake gesture; dblclick
adds the heart reward on top of the wake. A dblclick on drowsy Squid wakes
her AND shows the heart in one gesture.

Hearts spawn from the dblclick handler in `frontend/index.html`, inside
the `setTimeout(() => { ..., 260)` block where `api.poke()` is currently called.
Calling `spawnHearts()` synchronously alongside `api.poke()` ensures the visual
reward fires the moment the poke is confirmed.

NOTE (2026-06-08 revision): Original design called for 3 hearts rising 50 px.
This was clipped by the 200x220 px window on macOS (only 20 px of headroom
above the sprite). Revised to a single heart that BLINKS in place above
Squid's head: pop-in, gentle pulse, fade out -- no vertical translation, no
clipping, simpler and cleaner.

### Visual specification

| Attribute | Value | Tunable name |
|---|---|---|
| Quantity per poke | 1 heart | `HEART_COUNT` |
| Glyph | `❤️` (emoji, U+2764 U+FE0F) | `HEART_EMOJI` |
| Font size | 18 px | `HEART_SIZE_PX` |
| Spawn x-jitter | ±20 px from sprite center | `HEART_X_JITTER_PX` |
| Spawn y-offset | -10 px above sprite top | `HEART_Y_OFFSET_PX` |
| Rise distance | 0 px (blink in place, no translate) | n/a |
| Total duration | 900 ms | `HEART_DURATION_MS` |
| Stagger delay | 0 ms (moot for single heart) | `HEART_STAGGER_MS` |
| Scale curve | 0.8 → 1.0 → 0.9 | (in @keyframes) |
| Opacity curve | 0 → 1 → 0 | (in @keyframes) |
| Safety cap | 12 concurrent on screen | `HEART_MAX_LIVE` |

Why emoji over SVG: works on every Mac without font/asset choices, scales
crisply, matches the project's "Pink" theme without needing brand-color CSS.

### DOM + CSS approach

A single CSS `@keyframes heart-rise-fade` defines the rise + fade + scale arc.
Each spawned heart is an absolutely-positioned `<div class="heart">` containing
the emoji. The div is appended to the existing sprite container, given an
inline `animation-delay` based on its index, and auto-removed via the
`animationend` event.

`pointer-events: none` on the heart class ensures hearts never block clicks
or interrupt drags even if the cursor passes through them.

### Interaction with existing systems

- **Click-passthrough**: Hearts spawn inside the always-opaque sprite area,
  so they do not affect alpha-mask hit testing.
- **Poke deferral**: The existing `_pokePending` setTimeout already defers
  poke firing by 260 ms (dblclick disambiguation). Hearts fire when that
  timeout resolves — i.e., AFTER dblclick has been ruled out. Good: no
  hearts on accidental dblclicks.
- **Drag**: A drag that gets misclassified as a poke is rare (requires <250 ms
  hold AND <6 px movement). On the rare cases where hearts spawn mid-drag,
  they ride along with the window because they're DOM children. Acceptable.
- **State-driven animations**: Hearts spawn in a different z-layer than the
  sprite and do not interfere with sprite-swap cross-fades.

### Reusability

The `pet-reactions` capability is intentionally generic. Phase B (out of scope
for this change) could add:
- `spawnSparkles()` on sprint completion
- `spawnZzz()` on drowsy entry
- `spawnQuestion()` on right-click

All would follow the same DOM + keyframe + auto-cleanup pattern.

### What this does NOT touch

- No Python changes
- No `get_state` / API surface changes
- No `pet-animations` sprite logic
- No `pet-window` window config
- No new dependencies
