# Squid Pet — "Squid"

A tiny floating desktop companion that watches your AI coding agent and
reacts to what's happening. Named **Squid** (chosen by Pink Tan, June
2026), nicknamed **Squid** because she looks like one.

She lives in a transparent, frameless window pinned to a corner of the
screen. A background watcher polls a pluggable set of activity detectors
— TPA, Claude Code, Codex, git, terminal, IDE — every 800 ms and
computes her mood from whichever ones are enabled and running. Her
animations are pure CSS keyframes; the Python side drives state +
window position only.

Squid started life watching **TPA** (`~/.tpa/` — process
CPU, subagent files, error logs, shell children) but now watches
**Claude Code** (the `claude` CLI) and **Codex** (the `codex` CLI) just
as richly: live tool-subprocess detection, recent project-file writes
(catches in-process edits that never spawn a subprocess), and
transcript-write recency together give the same working/thinking
distinction TPA gets. Git, terminal, and IDE activity feed a
simpler busy/idle signal on top. See [Detectors & triggers](#detectors--triggers) below.

---

## Install

```bash
# Clone + install:
mkdir -p ~/Projects && cd ~/Projects
git clone https://github.com/sirshecomesthisway/squid-pet.git
cd squid-pet && ./install.sh
```

> **Where Squid lives:** her source is in `~/Projects/squid-pet/`, runtime state in `~/.squid-pet/`, launcher at `~/.local/bin/squid`, LaunchAgent plist at `~/Library/LaunchAgents/com.pink.squid-pet.plist`. The installer is idempotent — re-running it from `~/Projects/squid-pet/` is the supported update path (or `squid update`). If you cloned somewhere else, the installer detects that and relocates the repo to the canonical location for you (post-e2e-polish 2026-06-27 Fix 5).

That sets up `uv venv`, installs the package from the committed `uv.lock`
against public PyPI (no dependency resolution — fast), renders the
LaunchAgent plist, drops `~/.local/bin/squid` on your PATH, writes
sensible default settings, and boots Squid.

**Measured on M1:**

| Scenario | Wall time |
|---|---|
| Warm install (`./install.sh` again) | **~30 seconds** |
| `squid update` (re-pull + reinstall) | **~30 seconds** |
| Cold install (fresh clone, empty `~/.cache/uv`) | **~3 minutes** |

The slow bit on a true cold install is downloading wheels for `pillow`,
`psutil`, and the `pyobjc-*` frameworks from PyPI (~15 MiB total,
throughput-bound). Every subsequent install reuses uv's wheel cache and
the committed lockfile, so resolution + downloads both get skipped.

If a clean install ever takes more than 5 minutes, run
`./install.sh --profile` and share the table from
`/tmp/squid-pet-install-profile-*.txt` — that's a regression worth
investigating.

> **Want the corner/stroll prompts back?** Run `./install.sh --wizard`.
> Otherwise you get sensible defaults (bottom-right corner, edges stroll,
> show on all spaces) — edit `~/.squid-pet/settings.json` any time to
> customize; changes are picked up live, no restart needed.

```bash
# Re-run any time to upgrade in place (idempotent):
cd ~/Projects/squid-pet && ./install.sh

# Daily commands:
squid status         # is she alive? is the watcher ticking?
squid why            # which detector fired? what state and why?
squid doctor         # 6-check self-diagnostic
squid restart        # atomic bounce
squid update         # git pull + reinstall + restart
squid logs -f        # tail stdout+stderr live

# Uninstall cleanly:
squid uninstall              # keeps your settings + source
squid uninstall --yes --all  # nukes everything, no prompts
```

**Requirements:** macOS 12+, Homebrew. `uv` is auto-installed if missing.
Full manual install steps + troubleshooting: [`docs/INSTALL.md`](docs/INSTALL.md).
Privacy disclosure: [`docs/PRIVACY.md`](docs/PRIVACY.md).

---

## States

| State | Trigger | Look |
|---|---|---|
| **idle** | Default — nothing else fires | Gentle breathing, occasional blink |
| **thinking** | TPA CPU busy with no recent log writes, OR Claude Code/Codex wrote a session transcript in the last 20s with no shell/file evidence | Head tilt, floating dots, cyan aura |
| **working** | Sustained CPU + tool activity, OR active shell child, OR a project file was just written (TPA, Claude Code, or Codex) | Typing arms, focused eyes, yellow aura |
| **grooving** | Subagent `.pkl` modified < 30 s ago | Spinning sway, rainbow aura |
| **celebrating** | Busy → idle transition (task likely complete) | Bounce, confetti, big smile (4 s window) |
| **concerned** | Recent line in `errors.log` (60 s for hard, 20 s for transient/network) | Tremble, red aura, raised eyes |
| **sleeping** | macOS HID idle > 5 min | Closed eyes, Zz floating, dim aura |
| **drowsy** | TPA idle 300–359 s (frontend-driven) | Slumped sprite, paused routine |
| **stretch** | Wake transition (~1.6 s, frontend-driven) | Wake-up stretch animation |

Priority order is fixed (`watcher.py:StateMachine.compute`): sleeping >
celebrating-held > no-TPA-idle > grooving > concerned > working > thinking >
celebrating-transition > idle. See `tests/test_state_machine.py` for the
contract.

---

## Detectors & triggers

Squid reads activity from a pluggable list of detectors
(`src/squid_pet/detectors.py`), each independently toggleable via
`~/.squid-pet/settings.json`:

| Detector | Signal | Feeds |
|---|---|---|
| `tpa` | TPA process CPU, session-log mtimes, subagent `.pkl`, `errors.log`, `llm_active.flag` | working / thinking / grooving / concerned / celebrating |
| `claude_code` | `claude` process presence, live tool subprocess, recent writes under `project_dirs`, `~/.claude/projects/*/*.jsonl` write recency | working / thinking |
| `codex` | `codex`/`codex-tui` process presence, live tool subprocess, recent writes under `project_dirs`, `~/.codex/sessions/**/*.jsonl` write recency | working / thinking |
| `git` | `.git/{HEAD,index,refs/heads/}` mtimes under `project_dirs` | busy / celebrating |
| `terminal` | any shell with a long-lived non-shell child | busy (off by default — misfires on any dev machine with a long-running foreground process, e.g. an editor or a REPL) |
| `ide` | VS Code / Cursor / JetBrains CPU + recent file mtimes under `project_dirs` | busy / grooving |

`tpa`, `claude_code`, and `codex` get the full working/thinking
distinction (same cascade, OR-merged across all three); the rest feed a
flatter busy/idle signal. For Claude Code and Codex, "working" fires on
either a live tool subprocess (e.g. a shell command) *or* a recent file
write under `project_dirs` — the latter is what catches in-process
Edit/Write/apply_patch-style tool calls, which never spawn a subprocess
and would otherwise only ever show as "thinking". Defaults:

```json
{
  "triggers": {
    "tpa": true,
    "claude_code": true,
    "codex": true,
    "git": true,
    "terminal": false,
    "ide": true,
    "project_dirs": ["~/Projects"]
  }
}
```

Edit any flag to `false` to disable that detector entirely — no scans,
no process iteration, no filesystem walks for that source. Changes are
picked up live (settings.json is hot-reloaded). Every detector reads
only metadata (process names, CPU%, file mtimes) — never file contents,
never network. Full per-detector data-access table: [`docs/PRIVACY.md`](docs/PRIVACY.md).
Run `squid why` to see exactly which detector fired on the current tick.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ watcher.py     (background thread, 1 Hz)                               │
│   detectors.py → pluggable Detector list (tpa, claude_code,     │
│                  codex, git, terminal, ide) — see "Detectors &          │
│                  triggers"                                             │
│   psutil → find tpa / claude / codex procs, aggregate CPU%      │
│   ioreg  → macOS HID idle                                              │
│   mtime  → ~/.tpa/{…}, ~/.claude/projects/…, ~/.codex/…, .git/… │
│   ────────────────────────────────────────────────────                 │
│   StateMachine.compute() — priority cascade over detector signals      │
│   ↓                                                                    │
│   api.update(state)  +  write ~/.squid-pet/state.json (atomic)        │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ window.py      (main thread — pywebview window)                        │
│   ┌──────────────────────────┐  ┌──────────────────────────────────┐   │
│   │ routine.py               │  │ passthrough.py                   │   │
│   │  RoutineController       │  │  PassthroughController           │   │
│   │  IDLE_ROUTINE: rest →    │  │  PIL alpha masks at 30 ms;       │   │
│   │  look → walk-short →     │  │  toggles NSWindow                │   │
│   │  rest → walk-medium →    │  │  ignoresMouseEvents based on     │   │
│   │  look → rest → walk-edge │  │  cursor-over-transparent pixel.  │   │
│   │  Pauses on mood ∈        │  │                                  │   │
│   │  {drowsy, sleeping,      │  │                                  │   │
│   │  stretch}; resets to     │  │                                  │   │
│   │  idx=0 on sleep wake.    │  │                                  │   │
│   └──────────────┬───────────┘  └──────────────────────────────────┘   │
│                  ↓ dispatches                                          │
│   ┌──────────────────────────────────────────────────────────────────┐ │
│   │ wanderer.py  (service mode — no internal scheduler)              │ │
│   │   request_walk(band)          band ∈ {short, medium, edge}       │ │
│   │   request_look_around()       look-around with direction flip    │ │
│   │   sprint_perimeter()          right-click → "sprint!" easter egg │ │
│   └──────────────────────────────────────────────────────────────────┘ │
│                                                                        │
│   menu.py    right-click NSMenu (corners, pause, sprint, quit)         │
│   PetApi     JS bridge: get_state / next_corner / move_window_by /     │
│              force_state / drag_start / drag_end / notify_mood / quit  │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ frontend/index.html   (transparent webview content)                    │
│   <img id="pet">  + 9 CSS @keyframes (one per state)                   │
│   800 ms poll → api.get_state() → flip [data-state="…"]                │
│   Mood transitions (drowsy/sleeping/stretch) → api.notify_mood(mood)   │
│   Mouse: drag → move_window_by, contextmenu → next_corner, dbl → cycle │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Project layout

```
src/squid_pet/
├── __init__.py
├── __main__.py              # CLI entry: --check, --watcher-only, default=full
├── watcher.py               # state detection + StateMachine (priority cascade)
├── detectors.py             # pluggable Detector classes (tpa, claude_code, codex, git, terminal, ide)
├── window.py                # pywebview window + PetApi (JS bridge)
├── routine.py               # RoutineController — IDLE_ROUTINE scheduler
├── wanderer.py              # service-mode walks + look-around + sprint
├── passthrough.py           # NSWindow click-through via PIL alpha masks
├── menu.py                  # right-click NSMenu (corners, pause, sprint)
└── frontend/
    ├── index.html           # sprite element + CSS keyframes + JS poller
    └── sprites/             # PNG art for every state
        └── _originals_with_bg/   # before-bg-removal originals (back-up)

tools/
└── remove_bg.py             # flood-fill alpha removal for sprite art

launchagent/
├── com.pink.squid-pet.plist
└── install.sh

tests/
├── test_state_machine.py    # priority-cascade branches + cross-tick memory
└── test_detectors_*.py      # one file per detector, injected dependencies

openspec/                    # OpenSpec specs + changes (see "Specs" below)
```

---

## Tests

```bash
.venv/bin/pytest
```

362 tests, ~30 s. Covers every state-machine branch + cross-tick memory
(burst-suppression busy_streak, `agent_idle_seconds` tracking, celebration
transition window) plus each detector in isolation. I/O is monkey-patched
or dependency-injected so the suite never touches psutil / filesystem /
ioreg in real life.

---

## Sprite tooling

The artwork generator produces PNGs with solid backgrounds. `tools/remove_bg.py`
flood-fills from all 4 corners with a colour-tolerance and sets matching pixels'
alpha to 0:

```bash
# Strip background from one or many sprites (backs up originals first)
python tools/remove_bg.py src/squid_pet/frontend/sprites/idle.png \
    --backup-to src/squid_pet/frontend/sprites/_originals_with_bg

# Bulk-process every PNG in a directory
python tools/remove_bg.py src/squid_pet/frontend/sprites/ --recursive \
    --backup-to src/squid_pet/frontend/sprites/_originals_with_bg

# Verify (non-destructive): check that all 4 corner pixels have alpha=0
python tools/remove_bg.py --verify src/squid_pet/frontend/sprites/*.png
```

Tolerance defaults to 30 (Euclidean RGB distance). Bump it up for noisier
backgrounds.

---

## State file

`~/.squid-pet/state.json` is rewritten atomically every second. Schema:

```json
{
  "state": "thinking",
  "sub_state": "",
  "cpu_percent": 18.7,
  "idle_seconds": 3.2,
  "agent_idle_seconds": 12.4,
  "tpa_running": true,
  "claude_code_running": false,
  "codex_running": false,
  "timestamp": 1780819113.12,
  "message": "thinking",
  "concern_reason": "",
  "concern_severity": ""
}
```

---

## Tuning

Edit the constants near the top of `watcher.py`:

| Constant | Default | Meaning |
|---|---|---|
| `POLL_INTERVAL_SEC` | 1.0 | How often the watcher fires |
| `IDLE_THRESHOLD_SEC` | 300 | macOS idle → sleeping |
| `CPU_BUSY_THRESHOLD` | 5.0 | Min CPU% to count as busy |
| `TOOL_ACTIVE_WINDOW_SEC` | 8 | Recent tool-file write → working (vs thinking) |
| `SUBAGENT_ACTIVE_WINDOW_SEC` | 30 | Subagent `.pkl` written within N sec → grooving |
| `CELEBRATE_DURATION_SEC` | 4 | How long celebrating sticks after CPU drops |
| `CONCERN_LOOKBACK_SEC` | 60 | Hard errors stay concerned this long |
| `CONCERN_TRANSIENT_LOOKBACK_SEC` | 20 | Network/timeout errors auto-clear faster |

---

## Specs

This project uses **OpenSpec** to track behavior contracts. Canonical specs
live in `openspec/specs/` and any proposed change ships as an `openspec/changes/<name>/`
folder (proposal + design + tasks + spec delta) before being archived.

```bash
openspec list              # see active changes
openspec validate <name>   # validate a change
openspec archive <name>    # merge delta into canonical spec
```

Current canonical specs:
- `autonomous-motion` — wandering, look-arounds, idle routine, mood gating
- `user-interactions` — drag, right-click menu, double-click, pokes
- `pet-reactions` — hearts/celebrations on user interaction
- `state-detection` — watcher signal sources + priority cascade
- `pet-window` — frameless transparent window, corner snap, persistence
- `pet-animations` — sprite + CSS keyframe contract
- `click-passthrough` — transparent-pixel click-through mechanism

## Troubleshooting

If Squid seems missing, run the doctor:

```bash
python -m squid_pet --doctor
```

This runs 6 checks (process, state.json freshness, launchd, window
visibility, window-not-wedged, startup log markers). Exit code 0 =
healthy; otherwise the failing check number tells you what's broken.

See [docs/STARTUP_SAFETY.md](docs/STARTUP_SAFETY.md) for the full
four-layer defense documentation.
