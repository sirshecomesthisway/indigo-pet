# 💙 Indigo Pet

A tiny floating desktop companion that watches TPA and reacts to what's happening.

Named **Indigo** — chosen by Pink Tan, June 2026.

## States

| State | Trigger | Look |
|---|---|---|
| 👀 **idle** | No active work | Gentle breathing, occasional blink |
| 💭 **thinking** | TPA CPU busy, no log writes (LLM call) | Head tilt, floating dots, cyan aura |
| ⌨️ **working** | TPA busy + recent log activity | Typing arms, focused eyes, yellow aura |
| 🤹 **grooving** | Subagent file modified < 30s ago | Spinning sway, rainbow aura |
| 🎉 **celebrating** | Busy → idle transition (task complete) | Bounce, confetti, big smile |
| 😟 **concerned** | Recent error in errors.log | Tremble, red aura, raised eyes |
| 😴 **sleeping** | macOS idle > 5 min | Closed eyes, Zz floating, dim aura |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ Watcher (Python thread, 1Hz)                        │
│  • psutil → find tpa processes, CPU%         │
│  • ioreg  → macOS idle time                         │
│  • Watch ~/.tpa/logs/ + subagent_sessions/   │
│  • Compute state via priority-ordered rules         │
│  ↓                                                  │
│  api.update(state)  +  write ~/.indigo-pet/state.json│
└─────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────┐
│ Window (pywebview, transparent, on-top, draggable)  │
│  • Loads frontend/index.html (SVG critter + CSS)    │
│  • Polls window.pywebview.api.get_state() at 800ms  │
│  • CSS state classes drive animations               │
└─────────────────────────────────────────────────────┘
```

## Install / run

```bash
cd ~/Projects/indigo-pet
uv venv
uv pip install psutil pywebview

# Quick state check (no window)
.venv/bin/python -m indigo_pet --check

# Watcher only (writes state.json, no window)
.venv/bin/python -m indigo_pet --watcher-only

# Full pet (default)
.venv/bin/python -m indigo_pet
```

## Auto-start at login

```bash
cp com.pink.indigo-pet.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pink.indigo-pet.plist
```

To stop:

```bash
launchctl unload ~/Library/LaunchAgents/com.pink.indigo-pet.plist
```

## Dev / test

Open `src/indigo_pet/frontend/index.html` directly in a browser. Right-click on
the pet to cycle through all 8 states.

## Files

```
src/indigo_pet/
├── __init__.py
├── __main__.py         # CLI entry point
├── watcher.py          # state detection (psutil + ioreg + log mtime)
├── window.py           # pywebview floating window + JS API bridge
└── frontend/
    └── index.html      # SVG critter, CSS animations, JS state poller
```

## State file

`~/.indigo-pet/state.json` is rewritten every second. Schema:

```json
{
  "state": "thinking",
  "sub_state": "",
  "cpu_percent": 18.7,
  "idle_seconds": 3.2,
  "tpa_running": true,
  "timestamp": 1780819113.12,
  "message": "💭 thinking"
}
```

## Tuning

Edit constants at the top of `watcher.py`:
- `IDLE_THRESHOLD_SEC = 300` — when to switch to sleeping
- `CPU_BUSY_THRESHOLD = 5.0` — minimum CPU% to count as working/thinking
- `POLL_INTERVAL_SEC = 1.0` — how often the watcher fires
