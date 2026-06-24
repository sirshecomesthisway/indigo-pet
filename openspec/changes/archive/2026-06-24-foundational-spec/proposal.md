## Why

TPA (Pink's AI coding agent) runs in a TUI for long sessions. Pink wanted
an ambient, glanceable indicator of what the agent is doing — thinking, hitting
tools, running subagents, erroring out, or idle — without needing to flip back
to the terminal. "Squid" is a pink chibi-octopus desktop pet that lives in a
corner of the screen and changes pose/animation in real time to reflect agent
state.

## What Changes

- **Establish baseline specs** for the four capabilities the project already implements.
- Document the agent state machine and the seven supported emotional states.
- Document the native macOS windowing approach (NSWindow direct, no admin / no
  accessibility permission).
- Document the pixel-perfect click-passthrough technique using alpha-mask hit
  testing.
- Document the sprite + CSS-animation pipeline.

## Capabilities

### New Capabilities

- `state-detection`: Background watcher that observes TPA process activity, macOS idle time, and TPA log files, and emits one of seven emotional states each tick.
- `pet-window`: Always-on-top, transparent, frameless pywebview window positioned and dragged through direct NSWindow control (no accessibility permission required).
- `click-passthrough`: Pixel-perfect alpha hit-testing that toggles `setIgnoresMouseEvents_` so transparent regions of the window do not block clicks behind it.
- `pet-animations`: State-driven sprite + CSS keyframe animation system that swaps the displayed PNG and applies a per-state animation.

### Modified Capabilities

_None — this is the initial baseline._

## Impact

- **Code**: All files under `src/squid_pet/` and `src/squid_pet/frontend/`.
- **Dependencies**: `pywebview`, `psutil`, `Pillow`, `PyObjC` (transitively via pywebview).
- **State files**: `~/.squid-pet/state.json`, `~/.squid-pet/position.json`.
- **Other systems**: Reads (does not write) `~/.tpa/logs/*` and `~/.tpa/subagent_sessions/*.pkl`.
- **OS**: macOS only (uses Cocoa APIs).
