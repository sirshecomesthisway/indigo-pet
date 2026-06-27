# Manual Test Checklist for Pink -- unify-idle-rhythm

These tests can only be done by you, Pink, with eyeballs on Squid.
Tick the boxes as you go. When all 4 pass, run `openspec archive unify-idle-rhythm` (or have me do it).

## Setup
- [ ] Squid is currently running (`squid status` shows RUNNING + TICKING)
- [ ] You can see Squid on screen (sitting somewhere visible)
- [ ] Open `/tmp/squid-pet.out.log` in a tail window for diagnostics:
       `tail -f /tmp/squid-pet.out.log`

## Test 1 (Task 6.5): Menu Pause + Resume
Goal: confirm the idle-rhythm cycle freezes on pause and resumes at the same index.

- [ ] Wait until Squid is idle (no TPA running, no recent commit) and
       you can see her rhythm doing something (a gentle bob, a stroll, etc.)
- [ ] Right-click her -> Menu -> "Pause"
- [ ] Watch for 30 seconds. She should freeze. No movement.
- [ ] Right-click -> "Resume"
- [ ] She should continue from EXACTLY where she paused -- same animation,
       same direction, no jump.
- [ ] If she resumes cleanly: TASK 6.5 PASS

## Test 2 (Task 6.7): Sprint-perimeter + idle-rhythm interaction
Goal: confirm the rhythm pauses cleanly when you trigger a sprint.

- [ ] Wait until Squid is in mid-rhythm (idle cycle running)
- [ ] Right-click -> Menu -> "Sprint perimeter"
- [ ] She should immediately drop the rhythm and start sprinting around
       the screen edges
- [ ] When the sprint completes, she should return to the rhythm cycle
       (probably at index 0, not where she was before -- a "reset" is fine)
- [ ] No crash, no log errors in /tmp/squid-pet.out.log
- [ ] If sprint + rhythm coexist cleanly: TASK 6.7 PASS

## Test 3 (Task 8.9): 13-min sleep + auto-wake
Goal: confirm Squid auto-sleeps after 12 min of system idle and wakes on activity.

This one requires patience (13 wall-clock minutes) and STILLNESS.

- [ ] Note the current time: _______________
- [ ] Stop touching keyboard + mouse for 13 minutes (lock screen optional;
       sleep detection works either way)
- [ ] At ~t+12 min, Squid should transition to "sleeping" state
       (state.json -> "sleeping", message -> snoozing emoji)
- [ ] At t+13 min, move the mouse OR touch the keyboard
- [ ] Squid should auto-wake within 2 seconds, then briefly do "yawning" / 
       "stretching" / "rhythm cycle" before returning to idle
- [ ] If she wakes + cycles + back to idle: TASK 8.9 PASS

## Test 4 (Task 7.2): Archive go-ahead
After tests 1-3 all pass:
- [ ] Tell Indigo: "archive unify-idle-rhythm"

I'll then:
1. Tick the 4 manual tasks in tasks.md (with your verbatim observations if any)
2. Move openspec/changes/unify-idle-rhythm/ -> openspec/changes/archive/2026-MM-DD-unify-idle-rhythm/
3. Sync the delta spec into openspec/specs/autonomous-motion/spec.md (canonical)
4. Commit + push
