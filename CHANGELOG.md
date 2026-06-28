# Changelog

All notable changes to squid-pet. Follows [Keep a Changelog](https://keepachangelog.com).

## [0.2.0] - 2026-06-28

The "menu bar + smarter bubbles" release. Three intertwined themes:
1. Bubbles became less repetitive and more grounded in actual state
2. Squid gained a menu bar icon (her actual sprite!) so she's always reachable
3. New "Hide Squid" toggle for the screen-share-to-VPs scenario

### Added
- **Menu bar status item** — Squid's actual sprite (cropped & zoomed) lives in the
  macOS menu bar. Always reachable, even when she's hidden on screen. Icon swaps
  between `idle.png` (visible) and `sleeping.png` (hidden).
- **Hide Squid** menu item — toggles `NSWindow.alpha=0` so Squid is invisible
  but the watcher, state machine, and bubbles all keep running. Cheap, instant,
  no re-init cost on show. Restart brings her back visible by design.
- **Confirm-on-Quit dialog** — `NSAlert` with Cancel as default, prevents
  misclick disaster.
- **`state_reason` field on PetState** — every state machine branch now
  populates a short human sentence ("shell child active", "llm streaming",
  "writing (cpu 28%, tool 4s ago)"). Surfaced via `squid why` as a new `WHY:`
  line and 50% of the time used verbatim as the bubble for personality.
- **`SQUID_DEV` env var** — hides the mood-force submenu unless set
  (regular users never needed it).

### Changed
- **Bubble selection priority** — specific rule-based bubbles ("running git commit",
  "running pytest") now WIN over the LLM. Previously the LLM ran 3s later and
  overwrote your specific context with vague mood phrases like "settle in".
- **LLM bubble context** — observer now passes `subagent`, `llm_streaming`,
  `git_active`, `cpu_pct`, `tool_age` into the LLM prompt. System prompt got
  explicit example mappings (git push → "shipping it", subagent → "ohhh subagent",
  etc.). Result: LLM call rate jumped from 1/day to 35/day with usable verbs
  instead of moods.
- **Menu IA restructured** — Hide Squid at the top. Position / Bubbles / Pause
  wandering / Diagnostics organized as submenus. "Pause Squid" renamed to
  "Pause wandering" (the old name implied it paused the pet, but it only
  stopped wandering). Mute label changed to "Muted (click to unmute)" for
  clarity.

### Fixed
- Bubbles like "running git commit" no longer overwritten 3s later by the LLM
  selecting a generic mood phrase.
- Heredoc-corrupted emoji filter no longer mangles the bubble selection logic.

### Internal
- Cropped sprite assets `idle_menubar.png` and `sleeping_menubar.png` added
  for menu bar use (43% of the 1254x1254 originals was transparent padding).
- New patcher pattern: emoji-using patches live in `/tmp/*.py` files invoked
  via `python3`, never via heredoc — avoids the puppy emoji filter corrupting
  source code.

## [0.1.0] - earlier

Initial release: watcher, state machine, sprite rendering, wanderer,
right-click menu, doctor self-diagnostic, `squid why` CLI.
