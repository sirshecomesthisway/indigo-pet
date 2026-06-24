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

### TPADetector — observes TPA itself

| Reads | What for |
|-------|----------|
| `psutil.process_iter()` cmdline | finds python processes running `tpa` |
| CPU% of those processes | detects "thinking" / "working" |
| `~/.tpa/autosaves/*.pkl` mtime | detects "grooving" (subagent active) |
| `~/.tpa/logs/errors.log` mtime | detects "concerned" (recent error) |
| `~/.tpa/logs/errors.log` content | last few lines parsed for severity hints (transient vs hard) |
| `~/.tpa/subagent_sessions/*.pkl` mtime | secondary grooving signal |
| Shell-child processes of TPA (rg, grep, find, git, ...) | detects "running shell" |

Does NOT read: prompt content, model responses, file contents you edit
with TPA, chat history, API keys, OneDrive/Confluence data.

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

* `state.json` — current PetState snapshot (state, message, cpu_percent,
  idle_seconds, timestamps). Overwritten ~1×/second. **Never contains
  file paths, commit hashes, or process arguments.**
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
    "tpa": true,
    "git": true,
    "terminal": true,
    "ide": true,
    "project_dirs": ["~/Projects", "~/work/repos"],
    "ide_processes": ["Code", "Cursor"]
  }
}
```

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

Open an issue at https://internal-ghe.example.com/pink/squid-pet
or ping Pink in `#squid-pet` (Slack).
