## [0.2.1] - 2026-06-28

The "tap me on the shoulder" release. Single-feature hotfix on top of
0.2.0 -- when TPA stops mid-session waiting for user input, you
were not noticing because Squid is small and TPA is not in focus. Now
Squid speaks up.

### Added
- **Approval-needed alert** -- when `tpa_running` is True AND
  the state machine sits idle for >= 10 seconds (default threshold),
  Squid signals you visually (silent, no OS-level interruptions):
  1. Sticky bubble overrides the normal message and reads "your turn"
     until TPA transitions back to a busy state
  2. Squid jumps in place every 1.1s with a pulsing golden drop-shadow
     glow that hugs her silhouette (new CSS state "approval_needed"
     maps to "approval-bounce-glow" keyframe; drop-shadow respects
     alpha so no ugly square box)
  
  Latched per idle session -- one signal per busy->idle cycle, no
  re-firing during long pondering pauses. macOS notification banner
  + Glass chime helper exists but is disabled by default (Pink's
  preference: do not interrupt Zoom/meetings).

- **Bubbles submenu toggle** -- "'Your turn' alerts (on/off)" with a
  bell icon. Default ON. Persists to settings.json.

- **New config keys** (all optional, sensible defaults):
  ```
  approval_alert_enabled        bool   true
  approval_alert_threshold_sec  float  10.0
  approval_alert_sound          str    "Glass"   ("" = silent)
  approval_alert_text           str    "your turn"
  ```

- **`squid why` enrichment** -- when alert is active, the WHY line
  reads "approval needed (NNs idle)" instead of the previous
  "cp idle, listening".


### Fixed
- **Broken-sprite when backend sent unknown states** (pre-existing
  latent bug, triggered by the new "approval_needed" state): the
  frontend's `spriteUrl(state)` blindly returned `sprites/${state}.png`,
  so any backend state without a matching PNG (now including
  approval_needed) loaded macOS's broken-image placeholder. Fixed with
  a whitelist that falls back to `idle.png` for unknown states.

### Internal
- New `_fire_approval_notification(text, sound)` helper in watcher.py
  runs osascript in a background thread (~50ms cost) so the watcher
  loop never blocks on the notification subprocess.
- New `_approval_alert_fired` latch on StateMachine prevents re-fire
  during a single idle session.

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
