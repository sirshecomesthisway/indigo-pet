# squid-pet Privacy

Squid is a desktop pet that watches what you're doing so she can react.
This page tells you EXACTLY what she looks at, what she does NOT look at,
and how to turn any of it off.

## TL;DR

* Squid scans **filesystem metadata** (mtimes), **running process
  names**, and **CPU percentages**.
* Squid never reads file contents, never sends data anywhere, and
  never writes anything outside `~/.squid-pet/`.
* All scanning is **local-only**. No network calls. No telemetry.
* Every detector is **individually toggleable** via
  `~/.squid-pet/settings.json`.

## What each detector observes

Squid started life watching **TPA**, a separate internal
CLI coding agent — process CPU, subagent files, `errors.log` content,
shell children, and a private sitecustomize.py-driven approval-alert
signal. All of that was removed 2026-08-27: TPA was never
actually installed/run in this environment, so none of it ever fired
anything in practice. Nothing described below reads anything under
`~/.tpa/` any more.

### Claude Code's flag wave — `scripts/claude_pet_hook.py`

(2026-08-26) The approval-needed "flag wave" alert, via Claude Code's
OFFICIAL hook system rather than process/file scanning. This is a
**separate execution path** from the detectors below: Claude Code
itself invokes `scripts/claude_pet_hook.py` as a subprocess, once per
`Notification`/`UserPromptSubmit`/`SessionEnd` event, per the `hooks`
block registered in `~/.claude/settings.json` (your own user-level
config, outside this repo).

| Reads (via stdin, from Claude Code itself) | What for |
|-------|----------|
| `session_id` | names the flag file; the only identifier Claude Code's hook payload provides (no PID) |
| `hook_event_name` | branches behavior: write on `Notification`, remove on `UserPromptSubmit`/`SessionEnd` |
| `notification_type` (Notification events only) | only `permission_prompt` and `idle_prompt` create a flag; every other value is ignored |

| Writes | What for |
|--------|----------|
| `~/.squid-pet/claude_awaiting_input/<session_id>` (content: the notification_type string) | direct signal: this Claude Code session is waiting on you right now |
| `~/.squid-pet/claude_hook.log` (one line per hook invocation, auto-truncated past 200KB) | lets you verify the hook is actually firing -- `tail -f` it while using Claude Code |

Does NOT read: the `message` field's human-readable text, `transcript_path`,
`cwd`, `prompt_id`, or any other field Claude Code's hook payload
includes beyond the three above; does not read transcript file contents,
prompt/response text, or tool call arguments/results (same guarantee as
ClaudeCodeDetector below). Never imports the `squid_pet` package and has
zero dependencies beyond the Python stdlib, so a bug in squid-pet proper
can't affect it (or vice versa) -- it's wired up and torn down entirely
through `~/.claude/settings.json`.

Two self-heal reads in `watcher.py` back this mechanism, both metadata-only:
`StateMachine.compute()` deletes a flag outright the moment its own
independently-verified activity signal (real shell/file/streaming
evidence — no new external read, just its own already-computed state) sees
the session genuinely active again, covering the case where Claude
resumes on its own without a fresh `UserPromptSubmit` ever firing; and
(2026-08-27) when the OS notification fires, `psutil` walks the parent-
process chain of any running `claude` process (process names only — e.g.
`Terminal`, `iTerm2` — no cmdline args, no window titles) to find which
terminal app is hosting it, so `terminal-notifier`'s `-activate` can bring
the right app to the front on click instead of a generic/unhelpful target.

### ClaudeCodeDetector — observes the Claude Code CLI

| Reads | What for |
|-------|----------|
| `psutil.process_iter()` cmdline (basename `claude`) | finds the Claude Code CLI process — `Process.name()` was found unreliable for this binary on macOS, so matching goes through cmdline instead |
| CPU% of that process | diagnostic only (`squid why`) — not used to decide state |
| Non-shell descendant processes of `claude` (shared tool-name allowlist, also used by CodexDetector) | detects a live tool call (e.g. a Bash-tool command) → "working" |
| File mtimes under `project_dirs` (default `~/Projects`), same scan as IDEDetector | detects a very recent write (in-process tools like Edit/Write don't spawn a subprocess, so this catches what shell-child detection misses) → "working" |
| `~/.claude/projects/*/*.jsonl` mtime (youngest across all sessions) | detects a recent transcript write → "thinking" (proxy for the LLM generating or a tool call resolving) |

Does NOT read: transcript file contents, prompt/response text, tool call
arguments or results, session IDs beyond their mtime, `~/.claude/`
settings or credentials, or the contents of any file under `project_dirs`.

Caching: the list of transcript files is cached for 60 seconds (same
pattern as GitDetector's repo-discovery cache); files untouched for
15+ minutes are dropped from the cache to keep it small over time.

### CodexDetector — observes the Codex CLI

Same signals and rationale as ClaudeCodeDetector, adapted to Codex's
on-disk layout:

| Reads | What for |
|-------|----------|
| `psutil.process_iter()` cmdline (basename `codex` or `codex-tui`) | finds the native Codex binary — Codex's npm distribution runs a JS shim that spawns this as a child process; the shim itself is not matched |
| CPU% of that process | diagnostic only (`squid why`) — not used to decide state |
| Non-shell descendant processes of `codex`/`codex-tui` | detects a live tool call → "working" |
| File mtimes under `project_dirs`, same scan as IDEDetector | detects a very recent write (catches apply_patch-style edits that don't spawn a subprocess) → "working" |
| `~/.codex/sessions/**/*.jsonl` mtime (youngest across all sessions, nested by date) | detects a recent transcript write → "thinking" |

Does NOT read: transcript file contents, prompt/response text, tool call
arguments or results, `~/.codex/history/` prompt-recall content,
`~/.codex/` auth tokens or config, or the contents of any file under
`project_dirs`.

### GitDetector — observes git activity

| Reads | What for |
|-------|----------|
| Walks `~/Projects/` (and any custom `project_dirs`) up to depth 4 | finds `.git/` directories |
| `.git/HEAD` mtime | detects fresh commit (within 5s) → celebrating |
| `.git/index` mtime | detects active staging → busy |
| `.git/refs/heads/` mtime | detects fresh push (within 5s) → celebrating |

Does NOT read: commit messages, diffs, branch names, remote URLs,
`.gitconfig`, anything inside the working tree.

Caching: the list of `.git/` directories is cached for 60 seconds.
Hard caps: max 50 repos watched, max depth 4 from each project root,
prunes `node_modules/`, `.venv/`, `__pycache__/`, `dist/`, `build/`.

### TerminalDetector — observes shell activity

| Reads | What for |
|-------|----------|
| `psutil.process_iter()` for `zsh`, `bash`, `fish`, `sh` | finds open shells |
| `.children()` of each shell | detects non-shell children running >3s |

Does NOT read: command history, shell aliases, environment variables,
running command arguments, file paths being touched. Only names &
creation times.

The 3-second threshold prevents the shell prompt itself (which is a
brief child) from triggering false-positive busy states.

### IDEDetector — observes editor activity

| Reads | What for |
|-------|----------|
| `psutil.process_iter()` for `Code`, `Cursor`, JetBrains (`idea`, `pycharm`, `webstorm`, `rubymine`, `goland`, `clion`) | finds your editor |
| CPU% of those processes | aggregates editor load |
| File mtimes in `project_dirs` (default `~/Projects`) | detects recent edits / autosaves / grooving bursts |

Does NOT read: file contents, document text, open tabs list, IDE
settings, extension data, language-server traffic.

Walks at most depth 5 per project root and caps at 200 recent files
per scan to stay cheap.

## What's written to disk

Squid writes ONLY to `~/.squid-pet/`:

* `state.json` — current PetState snapshot (state, message, idle_seconds,
  timestamps). Overwritten ~1×/second. **Never contains file paths,
  commit hashes, or process arguments.**
* `settings.json` — your own preferences (stroll_mode, triggers.*).
* `logs/squid.log` — startup + lifecycle log.
* `pid` — the running daemon's PID (for the singleton lock).
* `lock` — file used by `fcntl.flock()` to prevent two Squids from
  running simultaneously. Empty.

That's it. Nothing else is created, modified, or read outside this
directory or the read-only directories listed above per-detector.

## What's sent over the network

**Nothing.** Squid has zero network code. She does not phone home.
She does not check for updates. She does not load images from URLs.
The window/wanderer load static SVGs bundled inside the package.

If you ever see Squid making a network connection, that's a bug —
please file an issue.

## Turning detectors off

Edit `~/.squid-pet/settings.json`:

```json
{
  "stroll_mode": "edges",
  "triggers": {
    "claude_code": true,
    "codex": true,
    "git": true,
    "terminal": false,
    "ide": true,
    "project_dirs": ["~/Projects", "~/work/repos"],
    "ide_processes": ["Code", "Cursor"]
  }
}
```

There's no `tpa` trigger to toggle any more (TPADetector was
removed) -- the flag-wave alert is a separate mechanism with its own
on/off switch, `approval_alert_enabled` in `~/.squid-pet/config.json`
(default `true`), independent of the `triggers` block above.

Set any detector to `false` to disable it entirely (no scans, no
process iteration, no fs walks). Customize `project_dirs` if your
code lives somewhere other than `~/Projects`. Add/remove
`ide_processes` to match your editor.

## How to verify

Run `python -m squid_pet --why` (or `python -m squid_pet --why-json`)
to see exactly what each detector observed on the current tick and
what fired. The JSON output is suitable for piping into `jq` or saving
for later inspection.

If a detector is reporting something you don't expect, the verdict
line at the bottom of `--why` will tell you which signal fired.

## Questions?

Open an issue at https://github.com/sirshecomesthisway/squid-pet/issues.
