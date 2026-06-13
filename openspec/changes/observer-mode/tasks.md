# Tasks — observer-mode

## 1. observer.py module

- [ ] 1.1 Create `src/indigo_pet/observer.py` with `BUBBLE_LINES` dict
      (state-transition + interaction keys per design.md)
- [ ] 1.2 `Observer` class with `__init__(self, get_muted: Callable[[], bool])`
- [ ] 1.3 `on_state_change(self, old: str, new: str) -> Optional[str]`:
      returns picked line for relevant transition keys, None otherwise
- [ ] 1.4 `on_interaction(self, kind: str) -> Optional[str]`:
      returns picked line for interaction keys (poke, like, sprint, …)
- [ ] 1.5 Enforce 32-char max — if a line exceeds, log a warning and return
      None (defensive against future edits)
- [ ] 1.6 Mute check at top of every emit path (`if self.get_muted(): return None`)

## 2. PetApi wiring

- [ ] 2.1 Add `_pending_bubble: Optional[str] = None` field to PetApi
- [ ] 2.2 Add `_observer: Observer` instance, init in PetApi `__init__`
- [ ] 2.3 Extend `get_state()` payload with `pending_bubble` field
- [ ] 2.4 Add `clear_bubble()` method exposed via JS bridge (sets to None)
- [ ] 2.5 Wire `update(state)` to call `observer.on_state_change(old, new)`
      and overwrite `_pending_bubble` on non-None return
- [ ] 2.6 Wire `poke()` to call `observer.on_interaction("poke")`
- [ ] 2.7 Wire `like()` (or wherever LIKE/dblclick lands) to
      `observer.on_interaction("like")`
- [ ] 2.8 Wire `sprint_perimeter()` start → `"sprint"`, end → `"sprint_end"`
- [ ] 2.9 Wire `notify_mood(mood)` — drowsy → `"drowsy"`, sleeping →
      `"sleeping"`, stretch → `"waking"`

## 3. Frontend speech bubble

- [ ] 3.1 Add `<div id="bubble" class="hidden"><span></span></div>` to
      `index.html` above the sprite
- [ ] 3.2 CSS: position absolute, centered above `#pet`, white background,
      black border, tail pointing down, max-width 200px, word-wrap, font ~14px
- [ ] 3.3 CSS @keyframes `bubble-pop`: scale(0.7) → scale(1), 150 ms in
- [ ] 3.4 CSS @keyframes `bubble-fade`: opacity 1 → 0, 400 ms out
- [ ] 3.5 JS: in the 800 ms polling tick, read `state.pending_bubble`
- [ ] 3.6 JS: if non-null AND not already showing same text → render, animate
      in, hold 2500 ms, animate out, then call `api.clear_bubble()`
- [ ] 3.7 JS: if mid-render and a NEW non-null pending_bubble arrives → swap
      text immediately, restart the hold timer (latest-wins per D3)
- [ ] 3.8 Bubble has `pointer-events: none` (passthrough rule)

## 4. Mute toggle

- [ ] 4.1 Read/write `~/.indigo-pet/config.json` with `muted: bool` field
      (extend `passthrough.py`'s config-load pattern OR new `config.py` module)
- [ ] 4.2 `Observer.get_muted()` reads the config flag (refreshed on each call)
- [ ] 4.3 Add "Mute Squid" menu item in `menu.py` (checkbox-style with current
      state shown); on click → flip flag + persist + rebuild menu
- [ ] 4.4 When mute flips from True → False, the current pending bubble (if
      any) is cleared so muted reactions don't replay

## 5. Tests

- [ ] 5.1 Create `tests/test_observer.py`
- [ ] 5.2 Test: `BUBBLE_LINES` has all required keys (per design.md taxonomy)
- [ ] 5.3 Test: every line ≤ 32 chars (parametrize over the dict)
- [ ] 5.4 Test: `on_state_change(old, old)` returns None for every state
- [ ] 5.5 Test: `on_state_change("idle", "thinking")` returns one of the
      `BUBBLE_LINES["thinking"]` entries (parametrize all transitions)
- [ ] 5.6 Test: `on_interaction("poke")` returns one of `BUBBLE_LINES["poke"]`
- [ ] 5.7 Test: mute=True → every emit path returns None
- [ ] 5.8 Test: unknown trigger key returns None gracefully
- [ ] 5.9 Extend `test_state_machine.py`: assert StateMachine drives the
      observer with correct old/new args (via a spy)
- [ ] 5.10 All tests pass: `.venv/bin/pytest tests/ -v`

## 6. Manual UI validation (Pink validates after restart)

- [ ] 6.1 Squid restarts cleanly with observer wired (no startup errors in
      `/tmp/indigo-pet.{out,err}.log`)
- [ ] 6.2 Start TPA in another terminal — observe bubble appears on
      first sustained CPU spike: "ooh, thinking..." or similar
- [ ] 6.3 Run a shell command in TPA — bubble flips to one of the
      working lines
- [ ] 6.4 Wait for TPA to finish — celebration bubble appears
- [ ] 6.5 Single-click Squid (poke) — bubble shows poke line
- [ ] 6.6 Double-click Squid (LIKE) — bubble shows like line (+ existing heart)
- [ ] 6.7 Right-click → "Sprint!" — bubble shows "wheee!" then "*pant pant*"
- [ ] 6.8 Right-click → "Mute Squid" — checkbox flips, no more bubbles fire
- [ ] 6.9 Right-click → "Mute Squid" again — bubbles resume
- [ ] 6.10 Long inactivity → drowsy → bubble shows yawn line on entry only
        (not repeating every tick)

## 7. Spec + ship

- [ ] 7.1 `openspec validate observer-mode` passes
- [ ] 7.2 Commit + push to both remotes
- [ ] 7.3 Bubble line voice review with Pink (edit `BUBBLE_LINES` dict in
      one sitting; this is the product surface)
- [ ] 7.4 Once 6.x and 7.3 are done → `openspec archive observer-mode`
