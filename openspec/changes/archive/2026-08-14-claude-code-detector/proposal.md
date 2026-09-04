# claude-code-detector

## Why

Squid's richer working/thinking cascade only lights up when TPA is
running. On Pink's personal Mac, TPA isn't installed — her daily
driver is Claude Code (the `claude` CLI). Today any live `claude` process
is only picked up by the generic `TerminalDetector` (any shell with a
long-lived non-shell child counts as "busy"), which routes through the
flat non-TPA OR-fallback and always labels state `thinking` — regardless
of whether Claude is actually generating, running a tool, or just sitting
open idle. Since `claude` is itself exactly that kind of long-lived
child, Squid gets stuck showing `thinking` almost permanently.

Separately, `install.sh`'s first-run wizard has been unconditionally
writing `triggers.terminal: true` into `settings.json`, overriding the
detector's own documented default (`"terminal": False, # off by default:
misfires on any dev machine` in `detectors.py`). That's the direct cause
of the reported symptom and gets fixed alongside this change.

## Goal

Add a `ClaudeCodeDetector`, a sibling to `TPADetector`, and promote
it into the same TPA-style rich cascade (branch 4 of
`StateMachine._compute_inner`) so Squid distinguishes `working` (a live
tool subprocess under `claude`) from `thinking` (a transcript file was
written very recently — Claude is generating or a tool just returned)
instead of falling through the flat non-TPA fallback. Also correct
`install.sh` to stop force-enabling the terminal trigger.

## Non-goals

- `celebrating` / `grooving` / `concerned` states for Claude Code — no
  reliable signal exists yet (no exit-code hook, no subagent files, no
  error log). Revisit if Claude Code ever exposes one.
- Parsing transcript JSONL content beyond mtime — the on-disk schema is
  internal/undocumented and observed to include non-conversational
  bookkeeping lines; depending on message shape would be fragile.
  Non-content, mtime-only, same restraint as every existing detector.
- CPU%-based busy heuristics for the `claude` process — it's mostly
  network-I/O-bound while streaming, so CPU occupancy doesn't track
  activity the way it does for TPA's TUI. Transcript-write
  recency is used instead.
- Distribution/first-run wizard prompts beyond parity with the existing
  `tpa` trigger prompt.

## What changes

- **New `ClaudeCodeDetector`** in `src/squid_pet/detectors.py`: process
  presence (cmdline-basename match — `Process.name()` was empirically
  found unreliable for this binary on macOS, see design.md), live-tool-subprocess detection
  (reuses `has_active_shell_children`), and a transcript-write-recency
  signal scanning `~/.claude/projects/*/*.jsonl` (60s discovery cache,
  same pattern as `GitDetector`).
- **`watcher.py`**: new `find_claude_code_processes()`; `StateMachine`
  gains a `_claude_detector` ref; branch 4's gate broadens from
  `tpa_running` to `tpa_running or claude_code_running`,
  with shell/streaming signals OR-merged across both detectors so either
  can drive `working`/`thinking`. `PetState` gains a new
  `claude_code_running` field (additive; `tpa_running` keeps its
  existing TPA-only meaning).
- **Settings**: new `triggers.claude_code` flag (default `true`).
  `install.sh` fixes its hardcoded `triggers.terminal: true` to match
  the documented `false` default, and writes `claude_code: true`.
- **Tests**: `tests/test_detectors_claude_code.py` (mirrors
  `test_detectors_tpa.py`), plus watcher-cascade coverage for
  claude-only working/thinking.

## Success criteria

- With TPA absent and Claude Code actively running a Bash tool
  call, Squid shows `working`; mid-generation with no tool subprocess,
  `thinking`; idle terminal with a stale transcript, `idle`.
- Existing TPA behavior is byte-for-byte unchanged when the
  Claude Code detector is absent, disabled, or `claude` isn't running.
- Full existing test suite (307 tests) still passes; new detector tests
  added following the same injected-dependency, no-real-psutil style.
