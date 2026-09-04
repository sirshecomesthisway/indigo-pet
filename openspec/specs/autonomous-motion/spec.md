# autonomous-motion Specification

## Purpose
Define how Squid moves on her own without user input. Covers the wanderer
thread that periodically picks new screen positions, the idle routine that
paces walks and rests, stroll modes (anywhere vs edges-only),
sprint-perimeter animation, the nudge and corner-flee reactions to being
shooed, the busy-gate that suppresses motion when the user is actively
driving their coding agent, and the drowsy entry trigger after prolonged
idleness.

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

### Requirement: The idle routine paces walks, rests, and glances

A `RoutineController` thread SHALL drive Squid's idle rhythm by stepping
through a fixed, jittered sequence `IDLE_ROUTINE` of `(action, lo, hi)`
entries, sleeping a random duration in `[lo, hi]` per step and wrapping at
the end. The action vocabulary is `rest`, `look-around`, `walk-short`,
`walk-medium`, and `walk-edge`.

The routine SHALL NOT start until `IDLE_BEFORE_ROUTINE_SEC` (6 s) after a
wake, so she does not begin cycling the instant she opens her eyes.

The cycle SHALL keep its original ~91 s average pacing.
- **Why**: 2026-08-17 briefly stretched every phase ~2.2x (to a ~200 s
  cycle) so the routine would feel less repetitive, but that just
  elongated the gap between actions -- individual walks and rests felt
  sluggish, which is worse than a snappier cycle repeating more often.
  Reverted; `DROWSY_IDLE_SEC` was pushed to 300 s instead, so the same
  short cycle repeats 2-3+ times before she winds down.

A hard-pause gate SHALL suspend the routine when Squid is disabled, pinned,
user-paused (menu timer), busy, mid-drag, or in a mood that pauses
(`drowsy`, `sleeping`, `stretch`).

Idle chatter SHALL run on its own `CHATTER_MIN/MAX_INTERVAL_SEC` (26-34 s)
timer, sharing the hard-pause gate but NOT the `state == "idle"` gate that
gates the action routine.
- **Why**: Pink-2026-08-30, "not feeling her speak every 30s". Chatter used
  to share the idle gate, so during an active Claude Code session -- where
  `working_hold_sec` pins the state near-continuously to `working` -- the
  chatter timer was wiped every tick and effectively never fired.

#### Scenario: Squid is left alone at her desk
- **WHEN** no agent activity and no user gesture occurs
- **THEN** she cycles rest → look-around → walk → rest → ... with jittered
  durations, ~91 s per lap

#### Scenario: The user pauses her from the menu
- **WHEN** the user selects Pause Squid ▸ 15 minutes
- **THEN** the action routine stops for 15 minutes
- **AND** it resumes on its own afterwards, or immediately on Resume now

#### Scenario: She talks while the agent works
- **WHEN** Claude Code has pinned the state to `working` for several minutes
- **THEN** idle chatter still fires on its 26-34 s cadence
- **AND** the walk/rest action routine stays paused

### Requirement: Being shooed makes her hop, then leave

`WanderController` SHALL expose `request_nudge(cursor_x, cursor_y)` and a
corner-flee path, triggered by `passthrough`'s approach tracking (see
`click-passthrough` for the thresholds). A nudge SHALL be a short hop away
from the cursor; a flee SHALL be a longer move to the corner.

A nudge or flee SHALL preempt any hop already animating, via a generation
counter bumped on each new request, so a newer reaction always wins rather
than two animations fighting over the window position.

While a nudge or flee hop is animating, the ambient idle-wander walk SHALL
NOT start.
- **Why**: the idle-wander thread and the nudge thread had no interlock, so
  an ambient walk could begin mid-nudge and the two would tug the window in
  different directions. The generation counter already protected nudges
  from each other; this covers nudge versus wander.

A nudge SHALL be able to fire in ANY backend state, not just idle, and the
frontend SHALL be told to show the walking cue for it (see `pet-window`'s
`nudge-*` sub-state exemption).

#### Scenario: Cursor bounces onto her twice
- **WHEN** the approach tracker reports a nudge trigger
- **THEN** she hops a short distance away from the cursor
- **AND** the frontend shows the walking animation even if she is `working`

#### Scenario: A second nudge lands mid-hop
- **WHEN** a new nudge is requested while a hop is animating
- **THEN** the in-flight hop is abandoned and the new one runs

### Requirement: Drowsy entry after prolonged agent idle

The frontend SHALL transition Squid to the `drowsy` state via a slump
animation when Squid has been in the `idle` state continuously and the
state-machine idle time (`agent_idle_seconds`) exceeds 300 seconds (raised
from 120s on 2026-08-17, so the idle-routine cycle in `routine.py`'s
`IDLE_ROUTINE` -- which stays at its original ~91s-average pacing rather
than being slowed down -- gets to repeat 2-3+ times before drowsy,
instead of cutting it off mid-cycle). The drowsy state SHALL persist
until the backend reports `sleeping` at `IDLE_THRESHOLD_SEC` (315 s, so
the slump plays for about fifteen seconds -- Pink-2026-09-04), a wake
event fires (user gesture), the agent resumes activity, or the periodic
auto-wake fires every 15 minutes (see `state-detection`, which carries
the two constants this staging depends on).

#### Scenario: Drowsy entry after idle threshold
- **WHEN** Squid has been in the idle state continuously
- **AND** agent_idle_seconds exceeds 300
- **AND** no user_wake_override is active
- **THEN** the frontend swaps to the drowsy sprite via the slump animation

