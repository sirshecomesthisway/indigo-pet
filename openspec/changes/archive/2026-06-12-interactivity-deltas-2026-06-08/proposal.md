## Why

Between the foundational baseline and today's session, Squid grew from a
passive state-mirror into an interactive companion: she now wanders, sprints,
sleeps, can be poked, shaken awake, paused, persists across macOS Spaces,
refuses to launch twice, and ships with a control CLI. None of this is
captured in canonical specs — the foundational change covers only the day-0
baseline. This change backfills the deltas as a single coherent record so
future contributors (and future-Pink) can see what the system actually does.

## What Changes

- **NEW capability `user-interactions`**: Direct gestures Pink performs on
  Squid (poke, swing-to-wake, dblclick, right-click menu items).
- **NEW capability `autonomous-motion`**: Self-directed window motion (wander,
  sprint perimeter, drowsy entry), distinct from state-driven sprite frames.
- **MODIFIED `pet-window`**: Add multi-Space visibility, atomic singleton
  guard via fcntl, CLI control surface (`squid` binary).
- **MODIFIED `state-detection`**: Add 8th state `drowsy`, add user-wake
  override channel that suppresses drowsy entry for 60 seconds after a
  user gesture.

## Goal

- Bring canonical specs in sync with shipped behavior.
- Establish two new capabilities that group future related work cleanly
  (user-interactions for new gestures, autonomous-motion for behaviors that
  move the window without user input).

## Non-goals

- NOT respec-ing every micro-tweak. This change captures behavioral
  requirements, not the chronology of bug-fixes (those live in agent memory).
- NOT specifying the CLI's full argument surface — only that the CLI exists
  and provides start/stop/restart/status. The CLI's internal structure is
  operational, not architectural.
- NOT documenting the WKWebView startup flakiness — that's a known
  environmental quirk handled by retries, not a requirement.
- NOT covering hearts-on-poke — that change has its own proposal.

## Capabilities

### New Capabilities

- `user-interactions`: User-initiated gestures on Squid and the menu
  surfaces they invoke.
- `autonomous-motion`: Self-directed window-position changes that occur
  without user input.

### Modified Capabilities

- `pet-window`: Multi-Space visibility, atomic singleton, CLI control.
- `state-detection`: Drowsy state, user-wake override channel.

## Impact

- **Code**: `src/squid_pet/window.py`, `src/squid_pet/wanderer.py`,
  `src/squid_pet/menu.py`, `src/squid_pet/__main__.py`,
  `src/squid_pet/frontend/index.html`, `~/.local/bin/squid`.
- **Dependencies**: No new deps. Uses already-imported `fcntl`, `AppKit`,
  `threading`, `random`.
- **State files**: New `~/.squid-pet/lock` (fcntl flock file).
  Existing `~/.squid-pet/pid`, `position.json`, `state.json` unchanged.
- **OS**: Adds `NSWindow.setCollectionBehavior_` call (multi-Space).
