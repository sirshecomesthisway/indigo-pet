## ADDED Requirements

### Requirement: Watcher auto-wakes from sleeping after 10 minutes

The state machine SHALL track the duration of continuous time spent in the
`sleeping` state via a `_sleeping_since` timestamp. When that duration
reaches `AUTO_WAKE_AFTER_SLEEPING_SEC` (600 seconds), the state machine
SHALL open a wake window of `AUTO_WAKE_DURATION_SEC` (180 seconds) during
which the sleeping branch is suppressed. While the wake window is active,
the state machine SHALL fall through to evaluate all other state branches,
typically landing on `idle` (which allows the `RoutineController` to fire
one idle-rhythm cycle).

Once the wake window expires, the sleeping branch resumes normal
evaluation. If `macos_idle_seconds()` is still >= `IDLE_THRESHOLD_SEC`,
the state machine re-enters `sleeping` and starts a fresh
`_sleeping_since` timer, producing a roughly 13-minute cycle of
sleep-then-wake-then-sleep.

When `macos_idle_seconds()` drops below `IDLE_THRESHOLD_SEC` (user came
back), the state machine SHALL clear both `_sleeping_since` and
`_force_awake_until` so the next sleeping period starts from a fresh
timer.

This behavior is intentionally simple ("dumb wake"): it fires the wake
cycle even when the user is genuinely AFK. This trades a small amount of
wasted animation for the guarantee that Squid never sits frozen for hours
during long focus sessions where the user is at the Mac but not
generating HID events Squid can see.

#### Scenario: First entry into sleeping stamps the timer

- **WHEN** `macos_idle_seconds()` returns 400 (past `IDLE_THRESHOLD_SEC`)
- **AND** `_sleeping_since` is `0.0` (never slept this cycle)
- **THEN** the state machine returns `state = "sleeping"`
- **AND** `_sleeping_since` is set to the current time

#### Scenario: Auto-wake window opens after 10 minutes of sleeping

- **WHEN** the state machine has been continuously in `sleeping` for
  `AUTO_WAKE_AFTER_SLEEPING_SEC` (600 seconds)
- **THEN** the next tick suppresses the `sleeping` branch
- **AND** `_force_awake_until` is set to `now + AUTO_WAKE_DURATION_SEC`
- **AND** `_sleeping_since` is reset to `0.0`
- **AND** the state machine falls through to the default branch (`idle`)

#### Scenario: Wake window expires, sleeping returns

- **WHEN** the wake window has elapsed (`now >= _force_awake_until`)
- **AND** `macos_idle_seconds()` is still past `IDLE_THRESHOLD_SEC`
- **THEN** the state machine returns `state = "sleeping"`
- **AND** a fresh `_sleeping_since` is stamped, re-arming the cycle

#### Scenario: User returns mid-cycle, all bookkeeping clears

- **WHEN** `macos_idle_seconds()` drops below `IDLE_THRESHOLD_SEC`
- **THEN** the state machine clears `_sleeping_since` to `0.0`
- **AND** clears `_force_awake_until` to `0.0`
- **AND** evaluates the remaining state branches normally
