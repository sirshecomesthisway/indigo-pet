# trigger-broadening

## Why

Squid's state machine only reads TPA signals (CPU + log mtimes +
subagent files + errors.log). Pink wants Squid distributable to all internal
Mac engineers, but the majority don't run TPA daily. For them Squid would be
permanently `idle` — a silent sticker, not a companion.

This change broadens the signal sources so Squid reacts to general dev
activity: git, terminal, IDE. The 9-state vocabulary stays unchanged.
Only the inputs that fire each state grow.

A non-TPA internal engineer should see Squid:
- **`working`** when their IDE is busy AND a project file was just saved
- **`celebrating`** on a `git commit` or successful `git push`
- **`grooving`** when rapid-firing edits across many project files
- **`sleeping`** when they walk away (unchanged)

Privacy must stay airtight: detection is local-only via psutil + filesystem
mtime. NO file contents are read. NO network. NO data leaves the Mac.
Detectors are opt-out via settings.

## Goal

Add three activity detectors (git, terminal, IDE) alongside the existing
TPA detector. Refactor `watcher.py` to consume a pluggable detector
list. Add opt-out settings. Keep state vocabulary unchanged.

## Non-goals

- New states or sprites (existing 9-state set covers it)
- LLM-driven reactions (separate `llm-bubble-layer` change in kennel)
- Reading file contents or commit messages
- Cloud signals: Slack, calendar, GitHub notifications (privacy + auth)
- Per-language IDE awareness or per-repo customization
- Detecting Copilot / Cursor AI inference (unreliable; defer)
- Terminal exit-code detection for `concerned` (needs shell hook; defer)

## What changes

- **New `src/squid_pet/detectors.py`** — `Detector` ABC + four implementations:
  `TPADetector` (refactor of existing logic), `GitDetector`,
  `TerminalDetector`, `IDEDetector`. See design.md for the API contract.
- **`watcher.py` refactor** — `StateMachine` takes a detector list at init.
  `compute()` ORs `is_busy`/`is_celebrating`/`is_grooving` across enabled
  detectors. TPA-specific fields move from `StateMachine` to `TPADetector`.
- **Settings schema** — `triggers.{tpa, git, terminal, ide,
  project_dirs, ide_processes}` in `~/.squid-pet/settings.json`. Schema
  defined in design.md.
- **First-run wizard additions** — if no recent TPA activity detected at
  install time, default `triggers.tpa=false`. Depends on
  `distribution-installer` landing first.
- **`docs/PRIVACY.md`** — exhaustive list of what each detector reads,
  no-network guarantee, opt-out steps
- **Tests** — `tests/test_detectors.py` (each detector mocked), plus
  `tests/test_watcher_multidetector.py` for cascade with mixed signals

## Success criteria

- An internal engineer with NO TPA installed sees Squid react to git
  commit, VS Code activity, rapid file edits, walking away
- A TPA user sees identical behavior to today — no regressions
- Existing 121 tests still pass; ~25 new detector tests added
- `docs/PRIVACY.md` lists every filesystem path and OS API call per detector
- Each detector independently opt-out via settings.json; turning all four
  off leaves Squid permanently `idle` (no crash)
