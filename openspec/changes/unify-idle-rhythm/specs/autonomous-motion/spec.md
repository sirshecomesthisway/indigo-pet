## MODIFIED Requirements

### Requirement: Wanderer thread moves the window without user input

The wanderer SHALL be a stateless service whose movement is driven by an
external scheduler (the `RoutineController`). The wanderer SHALL expose
`request_walk(distance_band)` and `request_look_around()` methods that
synchronously initiate motion to a target picked according to the band
(`"short"` = nearby cluster, `"medium"` = anywhere in visible frame,
`"edge"` = on the screen perimeter). The wanderer SHALL NOT contain
internal RNG scheduling for when to move next; that responsibility moves
to the routine controller.

The wanderer SHALL continue to honor the `is_busy()` callback semantics
indirectly: the routine controller checks `is_busy` before invoking the
wanderer, so when the user is actively driving TPA the wanderer
receives no requests and Squid remains stationary. The wanderer SHALL
also continue to expose `sprint_perimeter()` as a one-off, externally-
triggered action invoked by the right-click menu.

#### Scenario: Routine fires a short walk
- **WHEN** the routine controller's current action is `walk-short`
- **AND** the routine calls `wanderer.request_walk("short")`
- **THEN** the wanderer picks a target within a small radius of the
  current position
- **AND** the wanderer animates the window to that target

#### Scenario: Routine fires a look-around during a walk
- **WHEN** the routine's previous action was `walk-medium`
- **AND** the wanderer has not yet finished animating to its target
- **THEN** the routine waits for the current action's duration window
  before advancing
- **AND** look-around does not fire mid-walk (no animation overlap)

#### Scenario: User is actively driving TPA
- **WHEN** TPA is busy
- **AND** the routine controller checks `is_busy()` before firing
- **THEN** the routine pauses without calling the wanderer
- **AND** Squid remains stationary

## ADDED Requirements

### Requirement: Routine controller drives idle rhythm via deterministic sequence

The `RoutineController` SHALL own the idle rhythm via a fixed
`IDLE_ROUTINE` list of `(action_name, min_duration_s, max_duration_s)`
tuples. The controller SHALL iterate the list circularly: each tick
selects a uniform random duration within the band, dispatches the action
via the wanderer service (or no-op for `rest`), sleeps the duration, then
advances to the next index.

The controller SHALL skip dispatch entirely when `is_busy()` returns
True, OR when the frontend mood is `drowsy`, `sleeping`, or `stretch`.
While gated, the controller polls once per second until the gate clears.

On resume from a `drowsy` pause, the controller SHALL continue at the
saved index. On resume from a `sleeping` pause, the controller SHALL
reset `_idx = 0` so that Squid begins a fresh cycle starting with
`rest`. Distinguishing the two wake paths is implemented via a
`_wake_from_sleeping_pending` flag set on `sleeping` mood entry and
consumed on the first post-gate dispatch.

The `IDLE_ROUTINE` SHALL contain a mix of `rest`, `look-around`, and
`walk-<band>` actions in a sequence that produces one full cycle every
approximately 110 to 130 seconds. The controller SHALL expose `start()`,
`stop()`, `pause()`, and `resume()` lifecycle methods.

#### Scenario: Routine cycle plays in defined order
- **WHEN** Squid starts with no user gestures and no TPA activity
- **THEN** the first action is `rest` for 15-18 seconds
- **AND** the second action is `look-around` for 1.5-2.5 seconds
- **AND** subsequent actions follow `IDLE_ROUTINE` in declared order
- **AND** the sequence loops back to index 0 after the last entry

#### Scenario: Routine pauses while TPA is busy
- **WHEN** the routine is in the middle of a `rest` action
- **AND** the user starts a TPA prompt (TPA becomes busy)
- **THEN** the next tick observes `is_busy()` returns True
- **AND** the routine skips dispatch and polls
- **AND** the current index does NOT advance until the gate clears

#### Scenario: Menu pause and resume
- **WHEN** the routine is partway through cycle (index = 3, mid-action)
- **AND** the user selects "Pause Squid" from the menu
- **THEN** the controller's `_stop` event is set
- **AND** Squid stops at the current position
- **WHEN** the user selects "Resume Squid"
- **THEN** the routine continues at index 3 (not reset to 0)

#### Scenario: Wake from drowsy preserves cycle position
- **WHEN** TPA idle reaches 120 seconds while routine is at index 5
- **THEN** frontend mood becomes `drowsy`
- **AND** the routine observes mood gate and pauses
- **AND** `_idx` stays at 5
- **WHEN** the user wiggles the mouse before TPA idle reaches 300 seconds
- **THEN** the stretch animation plays (mood = stretch transiently)
- **AND** mood returns to `""` after ~1.6s
- **AND** `_wake_from_sleeping_pending` is False (sleeping was never entered)
- **AND** the routine resumes dispatch at index 5

#### Scenario: Wake from sleeping resets to start of cycle
- **WHEN** TPA idle crosses 300 seconds while routine is paused at index 7
- **THEN** frontend mood transitions from `drowsy` to `sleeping`
- **AND** the controller's `notify_mood_entered("sleeping")` hook sets
  `_wake_from_sleeping_pending = True`
- **WHEN** the user returns and wiggles the mouse after a long absence
- **THEN** the stretch animation plays
- **AND** mood returns to `""`
- **AND** the routine's next poll sees the gate cleared
- **AND** the routine resets `_idx = 0` and clears the flag
- **AND** the first post-wake action is `rest` (index 0), not whatever
  was queued before the sleep

#### Scenario: No "stationary but awake" gap between 60s and 120s of TPA idle
- **WHEN** TPA idle is between 60 and 119 seconds
- **AND** mood is still `""` (drowsy entry is at 120s, sleeping at 300s)
- **THEN** the routine continues dispatch normally
- **AND** Squid walks/looks-around/rests per the routine
- **AND** there is no implicit motion pause based on a backend timer
