# Squid Pet — "Squid"

A tiny floating desktop companion that watches TPA and reacts to what's
happening. Named **Squid** (chosen by Pink Tan, June 2026), nicknamed **Squid**
because she looks like one.

She lives in a transparent, frameless window pinned to a corner of the screen.
A background watcher reads `~/.tpa/` activity — process CPU, subagent
files, error logs, shell children, macOS HID idle — and computes her mood every
800 ms. Her animations are pure CSS keyframes; the Python side drives state +
window position only.

---

## States

| State | Trigger | Look |
|---|---|---|
| **idle** | Default — nothing else fires | Gentle breathing, occasional blink |
| **thinking** | TPA CPU busy ≥ 2 ticks, no recent log writes (LLM call) | Head tilt, floating dots, cyan aura |
| **working** | Sustained CPU + tool activity, OR active shell child | Typing arms, focused eyes, yellow aura |
| **grooving** | Subagent `.pkl` modified < 30 s ago | Spinning sway, rainbow aura |
| **celebrating** | Busy → idle transition (task likely complete) | Bounce, confetti, big smile (4 s window) |
| **concerned** | Recent line in `errors.log` (60 s for hard, 20 s for transient/network) | Tremble, red aura, raised eyes |
| **sleeping** | macOS HID idle > 5 min | Closed eyes, Zz floating, dim aura |
| **drowsy** | TPA idle 120–299 s (frontend-driven) | Slumped sprite, paused routine |
| **stretch** | Wake transition (~1.6 s, frontend-driven) | Wake-up stretch animation |

Priority order is fixed (`watcher.py:StateMachine.compute`): sleeping >
celebrating-held > no-TPA-idle > grooving > concerned > working > thinking >
celebrating-transition > idle. See `tests/test_state_machine.py` for the
contract.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ watcher.py     (background thread, 1 Hz)                               │
│   psutil → find tpa procs, aggregate CPU%                       │
│   ioreg  → macOS HID idle                                              │
│   mtime  → ~/.tpa/{autosaves,subagent_sessions,errors.log,…}    │
│   ────────────────────────────────────────────────────                 │
│   StateMachine.compute() — 9-branch priority cascade                   │
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

## Install & first run

```bash
cd ~/Projects/squid-pet
uv venv
uv sync --index-url https://pypi.internal.example.com/artifactory/api/pypi/external-pypi/simple --allow-insecure-host pypi.internal.example.com

# Sanity-check the state machine without a window
.venv/bin/python -m squid_pet --check

# Watcher only — writes state.json, no GUI
.venv/bin/python -m squid_pet --watcher-only

# Full pet
.venv/bin/python -m squid_pet
```

### Daily commands (`squid` CLI wrapper)

A shell wrapper at `~/.local/bin/squid` (alias `squid`) gives you:

```bash
squid status     # is she running + healthy? + what tpa sessions are live
squid why        # explain current mood (which trigger fired, recent file mtimes)
squid restart    # force-kill + relaunch (auto-recovers from stuck WKWebView)
squid tail       # follow Squid's stdout log
squid errors     # last 50 lines of tpa errors.log
squid stop       # shut her down
```

---

## Auto-start at login

A LaunchAgent plist ships in `launchagent/`. Install it once and Squid will
launch automatically on every login + restart herself on crash.

```bash
./launchagent/install.sh         # copy plist + load
./launchagent/install.sh status  # show launchctl status
./launchagent/install.sh remove  # unload + delete
```

The plist lives at `~/Library/LaunchAgents/com.pink.squid-pet.plist` after
install. Logs are redirected to `/tmp/squid-pet.{out,err}.log`.

---

## Project layout

```
src/squid_pet/
├── __init__.py
├── __main__.py              # CLI entry: --check, --watcher-only, default=full
├── watcher.py               # state detection + StateMachine (priority cascade)
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
└── test_state_machine.py    # 24 unit tests covering all 9 priority branches

openspec/                    # OpenSpec specs + changes (see "Specs" below)
```

---

## Tests

```bash
.venv/bin/pytest
```

24 tests, ~0.1 s. Covers every state-machine branch + cross-tick memory
(burst-suppression busy_streak, `agent_idle_seconds` tracking, celebration
transition window). I/O is monkey-patched at module-level so the suite never
touches psutil / filesystem / ioreg in real life.

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
