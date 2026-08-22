# autonomous-motion Specification

## Purpose
Define how Squid moves on her own without user input. Covers the wanderer
thread that periodically picks new screen positions, stroll modes (anywhere
vs edges-only), sprint-perimeter animation, the busy-gate that suppresses
motion when the user is actively driving their coding agent, and the drowsy
entry trigger after prolonged idleness.

## Requirements
### Requirement: Wanderer thread moves the window without user input

A background `WanderController` thread SHALL periodically move Squid to new
positions on screen without any user gesture. The wander tick interval SHALL
be approximately 800 milliseconds. Each wander step SHALL animate the window
position from current to target with smooth interpolation over the step
duration.

The wanderer SHALL respect a configurable `is_busy` callback supplied at
construction. When `is_busy()` returns True, the wanderer SHALL skip its
tick and remain stationary. The standard busy gate semantics are:
- The agent is actively thinking or working (genuine activity), OR
- An agent process exists AND the user has been driving it within the
  last 30 seconds (idle_seconds < 30)

Otherwise the wanderer SHALL run, even when stale background agent processes
exist.

Pink-2026-08-22 note: this gate is currently wired to `is_busy=lambda: False`
in window.py (a 2026-06-08 decision, predating and unrelated to the
TPADetector removal) -- Squid wanders unconditionally today. The
contract below documents the intended semantics if/when the gate is
re-enabled, not current runtime behavior.

#### Scenario: User is actively driving the agent
- **WHEN** the agent is running
- **AND** the user has typed in the terminal within the last 30 seconds
- **THEN** the wanderer skips its tick
- **AND** Squid remains stationary

#### Scenario: Only stale agent processes exist
- **WHEN** one or more agent processes exist
- **AND** none of them are thinking or working (low CPU)
- **AND** the user has been idle for more than 30 seconds
- **THEN** the wanderer is permitted to run
- **AND** Squid wanders normally

### Requirement: Stroll modes - anywhere and edges-only

The wanderer SHALL support at least two stroll modes selectable at runtime
via `set_stroll_mode(mode)`:
- `"anywhere"` — wander targets may be anywhere in the visible frame
- `"edges"` — wander targets always lie on the screen perimeter (within
  `EDGE_BAND_PX` of an edge)

The active mode SHALL be queryable via `get_stroll_mode()`.

#### Scenario: User switches to edges-only mode
- **WHEN** `set_stroll_mode("edges")` is called while Squid is mid-screen
- **THEN** the next wander target lies on the nearest edge
- **AND** subsequent targets stay on edges

### Requirement: Edge-mode wander stays glued to the perimeter

When stroll mode is `"edges"`, wander steps SHALL NOT cut diagonally across
the open screen. Adjacent-edge transitions SHALL be routed via a CORNER of
the current edge, never via a random point on a non-adjacent edge.

#### Scenario: Edge-hop is routed through a corner
- **WHEN** Squid is at `(left_edge, near_top)` in edges-only mode
- **AND** the wanderer rolls an edge-hop transition
- **THEN** the next target is a corner of the LEFT edge (top-left or bottom-left)
- **AND** Squid walks along the left edge to that corner
- **AND** the subsequent wander pick routes her onto the adjacent edge

#### Scenario: Wander step interpolation does not leave the perimeter
- **WHEN** any wander step is executing in edges-only mode
- **THEN** at every intermediate frame, Squid's position is within
       `EDGE_BAND_PX` of at least one screen edge

### Requirement: Sprint perimeter walks a full clockwise lap

`WanderController.sprint_perimeter()` SHALL run a complete clockwise lap
around the visible frame perimeter. The implementation SHALL track cumulative
clockwise degrees (0 to 360+) rather than waypoint count, so partial laps
from interior starting points still complete a full circuit. The sprint
SHALL:
- Begin with a stretch transition animation before the first sprint step.
- Poll motion at 80 milliseconds for smoother visible movement than wander.
- End at the BOTTOM edge regardless of starting position (snap-to-bottom).
- Block subsequent wander ticks until complete.

#### Scenario: Sprint from interior position
- **WHEN** Squid is at the center of the screen
- **AND** sprint_perimeter is invoked
- **THEN** Squid plays the stretch animation
- **AND** Squid walks to the nearest edge, then clockwise around the perimeter
       for a full 360 degrees of cumulative travel
- **AND** Squid ends at the bottom edge

### Requirement: Drowsy entry after prolonged agent idle

The frontend SHALL transition Squid to the `drowsy` state via a slump
animation when Squid has been in the `idle` state continuously and the
state-machine idle time (`agent_idle_seconds`) exceeds 300 seconds (raised
from 120s on 2026-08-17, so the idle-routine cycle in `routine.py`'s
`IDLE_ROUTINE` -- which stays at its original ~91s-average pacing rather
than being slowed down -- gets to repeat 2-3+ times before drowsy,
instead of cutting it off mid-cycle). The drowsy state SHALL persist
until either a wake event fires (user gesture) or the agent resumes
activity.

#### Scenario: Drowsy entry after idle threshold
- **WHEN** Squid has been in the idle state continuously
- **AND** agent_idle_seconds exceeds 300
- **AND** no user_wake_override is active
- **THEN** the frontend swaps to the drowsy sprite via the slump animation

