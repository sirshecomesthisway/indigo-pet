# Design — codex-detector

See `claude-code-detector/design.md` for the base architecture
(promotion into branch 4, the discovery-cache pattern, why mtime-only).
This doc covers what's specific to Codex, plus the file-write signal
that now applies to both detectors.

## Codex-specific facts (researched, not empirically verified — Codex
isn't installed on the machine this was built on)

- Codex CLI's Rust workspace (`codex-rs`) produces two binaries: `codex`
  (the CLI) and `codex-tui` (the interactive TUI). The npm distribution
  ships a JS shim (`bin/codex.js`) that resolves and spawns the correct
  platform-native binary as a child process — the shim implements no
  business logic itself.
- Session transcripts live at `~/.codex/sessions/YYYY/MM/DD/*.jsonl`,
  one file per session, named with a UTC timestamp + short session ID.
  Nested by date (3 levels), unlike Claude Code's flat
  `~/.claude/projects/<slug>/*.jsonl` (2 levels) — `CodexDetector`'s
  discovery glob is `**/*.jsonl` (recursive) instead of
  `ClaudeCodeDetector`'s `*/*.jsonl`.
- `~/.codex/history/` holds prompt-recall history only (no assistant
  output/tool calls) — not a signal source. `~/.codex/log/codex-tui.log`
  is a runtime log that could become a future streaming-heartbeat
  signal (noted as a possible follow-up, not implemented here).
- `CODEX_HOME` can override `~/.codex` — not supported here, same as
  `ClaudeCodeDetector` not supporting a Claude-side override, for
  consistency. Revisit if requested.

Sources (web research, 2026-08-14/15): openai/codex GitHub issues and
docs pages describing the session-log path and codex-rs binary split.

## Process matching: cmdline-basename, same lesson as Claude Code

`find_claude_code_processes` was originally written to match
`Process.name()`, which turned out to be wrong empirically (macOS
reports the versioned install path's basename for `claude`, not
`claude` itself). Rather than risk the same mistake blind for a binary
that can't be verified locally, `find_codex_processes` goes straight to
the same cmdline-basename approach that's now proven correct, matching
`{"codex", "codex-tui"}`. Extracted the shared logic into
`_find_processes_by_argv0_basename(names)` so both detectors' process
finders are one small, well-tested function apart.

## The file-write signal (fixes both detectors' "working" gap)

Root cause of the bug report: `shell_active` (live subprocess
detection) only fires for tool calls that shell out — Bash-tool calls
for Claude Code, presumably shell-tool calls for Codex. Anything
in-process (Claude's Edit/Write/NotebookEdit/MultiEdit, Codex's
`apply_patch`) writes files directly from the CLI's own process, with
no subprocess to detect. Before this fix, that gap fell through to the
`streaming` signal alone, which only distinguishes "the transcript was
touched recently" — coarser than warranted, and specifically wrong for
the common case of "actively writing code," which a user should see as
`working`, not `thinking`.

Fix: reuse `IDEDetector`'s existing file-mtime scan (`project_dirs`,
skip junk dirs, depth/file-count capped), extracted to a shared
module-level `_scan_recent_file_ages()` so `IDEDetector`,
`ClaudeCodeDetector`, and `CodexDetector` all call the same
implementation. Each detector gates the scan on its own process being
alive (`if procs: ...`) so the (relatively expensive, uncached, full
`os.walk`) scan only runs when the tool is actually running — cheaper
than `IDEDetector`'s unconditional-every-tick scan.

`FILE_ACTIVE_WINDOW_SEC = 10.0` (vs `IDEDetector`'s 5s) — a bit more
generous to allow for an Edit-tool round trip (read, diff, write,
verify) without flickering back to `thinking` between steps.

### Cascade merge, extended to 3-way

```
any_agent_running       = tpa_running or claude_running or codex_running
working_evidence_merged = shell_active(any of the three) or file_active(claude/codex)
streaming_merged        = llm_streaming(tpa) or streaming(claude) or streaming(codex)
```

`_working_reason()` / `_streaming_reason()` helpers in
`_compute_inner` pick which source earns credit in `state_reason` (TPA
shell > claude shell > codex shell > claude file > codex file; llm >
claude > codex for streaming) — priority order, not exclusivity; if
both Claude Code and Codex happen to run simultaneously, whichever
fires first in that list is attributed. Tested explicitly
(`test_claude_and_codex_both_running_shell_evidence_merges`).

## Headless/server-mode exclusion (found during live verification)

Live-testing `find_codex_processes()` on the dev machine (2026-08-15)
surfaced a real false positive: a third-party tool (`openclaw`) runs a
vendored copy of the `codex` binary as `codex app-server --listen
stdio://` — a JSON-RPC/stdio backend other programs drive Codex
through, not an interactive session a human is watching. codex-rs also
ships `codex exec`/`exec-server` (one-shot headless automation, "prompt
in, result out, exit") and `codex mcp`/`mcp-server` subcommands with the
same non-interactive character.

`find_codex_processes()` now excludes any match whose `cmdline()[1]` is
in `CODEX_HEADLESS_SUBCOMMANDS = {"app-server", "exec", "exec-server",
"mcp", "mcp-server"}` — same reasoning `find_tpa_processes`
already applies to skip `--prompt` one-shot cron/automation runs. A
bare `codex` invocation, `codex-tui`, or `codex <initial prompt text>`
(no subcommand) all still match correctly.

## Decisions

### D1: No shared base class between ClaudeCodeDetector and CodexDetector
Matches this module's existing convention (every detector implements
the `Detector` protocol independently, even where structural overlap
exists — e.g. `GitDetector` and `IDEDetector` both do file-mtime-based
busy detection with separate caching). Keeps the two evolvable
independently (e.g. Codex's `codex-tui.log` could become a Codex-only
signal later without touching Claude's code).

### D2: File-write signal is shared via a free function, not inheritance
The scan *logic* (walk + cache-free mtime check) is genuinely identical
across three call sites; the *decision* each detector makes with the
result differs enough (IDEDetector cross-references CPU%; Claude/Codex
detectors gate on process presence and OR it straight into "working")
that duplicating the decision logic but sharing the scan primitive is
the right cut point.

### D3: 10s file-active window vs IDEDetector's 5s
Slightly more generous because a single Edit-tool round trip (read the
file, compute a diff, write it, sometimes re-read to verify) can span
a few seconds even though it's genuinely one continuous "working"
action, and a flicker back to `thinking` mid-edit would be worse than
a slightly wider window.
