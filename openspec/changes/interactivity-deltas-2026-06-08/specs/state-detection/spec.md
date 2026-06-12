## ADDED Requirements

### Requirement: Drowsy state for prolonged TPA idle

An 8th emotional state, `drowsy`, SHALL be added to the state model. The
drowsy state SHALL be entered by the frontend (not the backend state
machine) when:
- The backend state has been `idle` continuously, AND
- `agent_idle_seconds` (TPA's user-typed idle time) exceeds 120 seconds, AND
- No `user_wake_override` is currently active

The frontend SHALL play a slump animation when entering drowsy, and the
drowsy sprite SHALL persist until either TPA resumes activity OR a
wake gesture fires.

Drowsy is intentionally a frontend-driven derivation rather than a backend
state to avoid coupling the watcher's state machine to user-gesture timing.

#### Scenario: Enter drowsy after prolonged idle
- **WHEN** the backend state is `idle`
- **AND** agent_idle_seconds is 121 or greater
- **AND** user_wake_remaining is 0
- **THEN** the frontend plays the slump animation
- **AND** the displayed sprite is the drowsy sprite

#### Scenario: Drowsy reverts when TPA becomes active
- **WHEN** Squid is in the drowsy state
- **AND** TPA starts a new tool call (CPU rises, log writes)
- **THEN** the backend state transitions to `thinking` or `working`
- **AND** the frontend swaps to the corresponding sprite

### Requirement: User-wake override channel suppresses drowsy

`PetApi` SHALL maintain a `_user_wake_until: float` epoch timestamp. The
`get_state()` response SHALL include a derived `user_wake_remaining`
field equal to `max(0, _user_wake_until - now)` in seconds.

The frontend SHALL treat `user_wake_remaining > 0` as a signal to:
- Suppress drowsy entry from the idle state
- Fire a wake-stretch transition if currently drowsy

The override SHALL be set by poke and swing-to-wake gestures. The override
SHALL NOT modify `agent_idle_seconds` (that field continues to reflect actual
TPA activity).

`PetApi` SHALL also maintain a `_wake_trigger_seq: int` counter incremented
on every wake event. The frontend MAY use this counter to detect
"new wake event since last poll" without needing to compare timestamps.

#### Scenario: Poke during drowsy
- **WHEN** Squid is in the drowsy state
- **AND** the user pokes her
- **THEN** _user_wake_until is set to now + 60 seconds
- **AND** the next get_state response returns user_wake_remaining near 60
- **AND** the frontend fires the wake-stretch transition
- **AND** Squid does NOT re-enter drowsy for the next 60 seconds even if
       agent_idle_seconds remains above 120

#### Scenario: Override expires after 60 seconds
- **WHEN** 60 seconds have elapsed since the last wake gesture
- **AND** agent_idle_seconds is still above 120
- **AND** no new gesture has fired
- **THEN** user_wake_remaining returns 0
- **AND** the frontend re-enters drowsy on the next poll
