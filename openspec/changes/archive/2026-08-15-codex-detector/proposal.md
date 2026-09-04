# codex-detector

## Why

`claude-code-detector` gave Squid a real working/thinking distinction
for Claude Code, but the repo is now public and meant to be usable by
anyone — including engineers whose daily driver is OpenAI's Codex CLI
instead of (or alongside) Claude Code. Without this, a Codex user gets
either nothing (if `terminal` stays off) or the flat, undifferentiated
non-TPA fallback.

Separately, while implementing this, a user reported that Squid stayed
"thinking" while Claude Code was actively *writing code* — traced to
`ClaudeCodeDetector`'s `shell_active` signal only catching Bash-tool
subprocess calls, not in-process tools like Edit/Write, which never
spawn a subprocess. That gap applies identically to Codex's
`apply_patch`-style edits, so this change fixes both detectors' "working"
signal, not just adds Codex.

## Goal

Add `CodexDetector`, a sibling to `ClaudeCodeDetector`, promoted into
the same branch-4 rich cascade in `StateMachine._compute_inner`
(now a 3-way OR across TPA / Claude Code / Codex). Add a
recent-project-file-write signal (reusing `IDEDetector`'s existing
file-mtime scan) to both `ClaudeCodeDetector` and `CodexDetector` so
in-process tool calls also register as "working".

## Non-goals

Same as `claude-code-detector`: no `celebrating`/`grooving`/`concerned`
for Codex (no reliable signal), no transcript-content parsing (only
mtime), no CPU-based busy heuristic. Additionally: no shared base class
between `ClaudeCodeDetector` and `CodexDetector` — kept independent,
matching this module's existing convention of separately-implemented
detectors (see `claude-code-detector/design.md`'s D1 for why that
precedent applies here too).

## What changes

- **New `CodexDetector`** in `detectors.py`: process presence
  (`codex`/`codex-tui` cmdline-basename match — Codex's npm shim spawns
  the native Rust binary as a child, so the shim itself must not match),
  live tool subprocess, recent-file-write, and
  `~/.codex/sessions/**/*.jsonl` write-recency (recursive glob — Codex
  nests sessions by date, unlike Claude Code's flat two-level layout).
- **`watcher.py`**: `find_codex_processes()`, sharing a new
  `_find_processes_by_argv0_basename()` helper with (refactored)
  `find_claude_code_processes()`. `StateMachine` gains a
  `_codex_detector` ref; branch 4's gate becomes a 3-way
  `any_agent_running` OR; `working_evidence_merged` /
  `streaming_merged` extend to include Codex; `PetState` gains
  `codex_running`.
- **File-write signal (both detectors)**: `_scan_recent_file_ages()`
  extracted from `IDEDetector` as a shared module-level helper;
  `ClaudeCodeDetector` and `CodexDetector` each gain a `project_dirs`
  param and a `file_active` field, OR'd into the same "working" branch
  as `shell_active`.
- **Settings**: new `triggers.codex` flag (default `true`, same
  no-misfire-risk rationale as `claude_code`). `install.sh` writes it
  and prompts for it in the wizard.
- **Portability audit**: alongside this, swept `docs/INSTALL.md`,
  `docs/PRIVACY.md`, and `install.sh`'s residual error text for
  leftover corporate-VPN-only instructions from before this repo went
  public — unrelated to Codex mechanically, bundled here because it's
  the same "usable by anyone" motivation.

## Success criteria

- With only Codex running (no TPA, no Claude Code), a live tool
  subprocess or a recent file write yields `working`; a fresh
  transcript with neither yields `thinking`; a stale/absent transcript
  yields `idle`.
- Editing a file via Claude Code's or Codex's in-process tools (no
  subprocess) now yields `working`, not `thinking` — regression test
  for the reported bug.
- Existing TPA and Claude-Code-only behavior is unchanged when
  Codex is absent/disabled. Full suite (362 tests after this change)
  passes.
