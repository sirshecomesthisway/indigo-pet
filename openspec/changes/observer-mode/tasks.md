# Tasks — observer-mode

## 1. observer.py module

- [x] 1.1 Create `src/squid_pet/observer.py` with `BUBBLE_LINES` dict
      (state-transition + interaction keys per design.md)
- [x] 1.2 `Observer` class with `__init__(self, get_muted: Callable[[], bool])`
- [x] 1.3 `on_state_change(self, old: str, new: str) -> Optional[str]`:
      returns picked line for relevant transition keys, None otherwise
- [x] 1.4 `on_interaction(self, kind: str) -> Optional[str]`:
      returns picked line for interaction keys (poke, like, sprint, …)
- [x] 1.5 Enforce 32-char max — if a line exceeds, log a warning and return
      None (defensive against future edits)
- [x] 1.6 Mute check at top of every emit path (`if self.get_muted(): return None`)

## 2. PetApi wiring

- [x] 2.1 Add `_pending_bubble: Optional[str] = None` field to PetApi
- [x] 2.2 Add `_observer: Observer` instance, init in PetApi `__init__`
- [x] 2.3 Extend `get_state()` payload with `pending_bubble` field
- [x] 2.4 Add `clear_bubble()` method exposed via JS bridge (sets to None)
- [x] 2.5 Wire `update(state)` to call `observer.on_state_change(old, new)`
      and overwrite `_pending_bubble` on non-None return
- [x] 2.6 Wire `poke()` to call `observer.on_interaction("poke")`
- [x] 2.7 Wire `like()` (or wherever LIKE/dblclick lands) to
      `observer.on_interaction("like")`
- [x] 2.8 Wire `sprint_perimeter()` start → `"sprint"`, end → `"sprint_end"`
- [x] 2.9 Wire `notify_mood(mood)` — drowsy → `"drowsy"`, sleeping →
      `"sleeping"`, stretch → `"waking"`

## 3. Frontend speech bubble

- [x] 3.1 Add `<div id="bubble" class="hidden"><span></span></div>` to
      `index.html` above the sprite
- [x] 3.2 CSS: position absolute, centered above `#pet`, white background,
      black border, tail pointing down, max-width 200px, word-wrap, font ~14px
- [x] 3.3 CSS @keyframes `bubble-pop`: scale(0.7) → scale(1), 150 ms in
- [x] 3.4 CSS @keyframes `bubble-fade`: opacity 1 → 0, 400 ms out
- [x] 3.5 JS: in the 800 ms polling tick, read `state.pending_bubble`
- [x] 3.6 JS: if non-null AND not already showing same text → render, animate
      in, hold 2500 ms, animate out, then call `api.clear_bubble()`
- [x] 3.7 JS: if mid-render and a NEW non-null pending_bubble arrives → swap
      text immediately, restart the hold timer (latest-wins per D3)
- [x] 3.8 Bubble has `pointer-events: none` (passthrough rule)

## 4. Mute toggle

- [x] 4.1 Read/write `~/.squid-pet/config.json` with `muted: bool` field
      (extend `passthrough.py`'s config-load pattern OR new `config.py` module)
- [x] 4.2 `Observer.get_muted()` reads the config flag (refreshed on each call)
- [x] 4.3 Add "Mute Squid" menu item in `menu.py` (checkbox-style with current
      state shown); on click → flip flag + persist + rebuild menu
- [x] 4.4 When mute flips from True → False, the current pending bubble (if
      any) is cleared so muted reactions don't replay

## 5. Tests

- [x] 5.1 Create `tests/test_observer.py`
- [x] 5.2 Test: `BUBBLE_LINES` has all required keys (per design.md taxonomy)
- [x] 5.3 Test: every line ≤ 32 chars (parametrize over the dict)
- [x] 5.4 Test: `on_state_change(old, old)` returns None for every state
- [x] 5.5 Test: `on_state_change("idle", "thinking")` returns one of the
      `BUBBLE_LINES["thinking"]` entries (parametrize all transitions)
- [x] 5.6 Test: `on_interaction("poke")` returns one of `BUBBLE_LINES["poke"]`
- [x] 5.7 Test: mute=True → every emit path returns None
- [x] 5.8 Test: unknown trigger key returns None gracefully
- [x] 5.9 Extend `test_state_machine.py`: assert StateMachine drives the
      observer with correct old/new args (via a spy)
- [x] 5.10 All tests pass: `.venv/bin/pytest tests/ -v`

## 6. Manual UI validation (Pink validates after restart)

- [x] 6.1 Squid restarts cleanly with observer wired (no startup errors in
      `/tmp/squid-pet.{out,err}.log`)
- [x] 6.2 Start TPA in another terminal — observe bubble appears on
      first sustained CPU spike: "ooh, thinking..." or similar
- [x] 6.3 Run a shell command in TPA — bubble flips to one of the
      working lines
- [x] 6.4 Wait for TPA to finish — celebration bubble appears
- [x] 6.5 Single-click Squid (poke) — bubble shows poke line
- [x] 6.6 Double-click Squid (LIKE) — bubble shows like line (+ existing heart)
- [x] 6.7 Right-click → "Sprint!" — bubble shows "wheee!" then "*pant pant*"
- [x] 6.8 Right-click → "Mute Squid" — checkbox flips, no more bubbles fire
- [x] 6.9 Right-click → "Mute Squid" again — bubbles resume
- [x] 6.10 Long inactivity → drowsy → bubble shows yawn line on entry only
        (not repeating every tick)

## 7. Spec + ship

- [x] 7.1 `openspec validate observer-mode` passes (2026-06-23)
- [x] 7.2 Commit + push: internal GHE pushed (cb5a915); origin github.com blocked by VPN, push when off-VPN
- [x] 7.3 Bubble line voice review with Pink (2026-06-23): celebrating *claps* -> *wiggles*; back_to_idle 'okay' -> *sigh*. Rest of dict approved as-is. (edit `BUBBLE_LINES` dict in
      one sitting; this is the product surface)
- [ ] 7.4 Once 6.x and 7.3 are done → `openspec archive observer-mode`
