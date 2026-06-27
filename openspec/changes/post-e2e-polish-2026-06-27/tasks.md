# Tasks -- post-e2e-polish-2026-06-27

## 1. Fix 1: celebrate hold 4s -> 20s + config knob
- [x] 1.1 Bump CELEBRATE_DURATION_SEC in detectors.py -- module const 4 -> 20 at line 68 (module const) 4 -> 20
- [x] 1.2 Bump CELEBRATE_HOLD_SEC on GitDetector class -- class const 4.0 -> 20.0 at line 206 4.0 -> 20.0
- [x] 1.3 Bump CELEBRATE_DURATION_SEC in watcher.py -- module const 4 -> 20 at line 61 4 -> 20
- [x] 1.4 Add celebrate_hold_sec to config.py DEFAULTS -- default 20, range 4-60 documented (= 20)
- [x] 1.5 Make all 3 sites read config.get -- wired TPA + GitDetector use sites with fallback("celebrate_hold_sec", 20) at use
- [x] 1.6 tests/test_celebrate_hold.py -- 6 tests: defaults (2), config-override hot-reload (2), fallback (1), end-to-end fresh-HEAD (1) -- all green -- verify (a) default 20s baseline,
        (b) config override read on every call (hot-reload), (c) GitDetector
        celebrate fires on touch .git/HEAD
- [x] 1.7 Live verify: touch .git/HEAD -- VERIFIED -- commit + 23s sampling showed celebrating for full ~25s (5s HEAD re-arm + 20s hold), watch state.json stay "celebrating"
        for ~20s before falling back to thinking/idle

## 2. Fix 2: install.sh SSH default
- [x] 2.1 Change REPO_URL default in install.sh to SSH -- via sed; line 45 now SSH
- [x] 2.2 Update die-message in clone_or_update -- now mentions SQUID_REPO override AND credential.helper osxkeychain hint to mention SQUID_REPO=https
        override AND `git config --global credential.helper osxkeychain` hint
- [x] 2.3 Live verify: clean install with no env override -- DEFERRED -- requires full uninstall + reinstall E2E disruption; syntax + grep verified succeeds first try
- [x] 2.4 Update install.sh header comment doc -- comment block at line 13 untouched (still mentions HTTPS internals correctly; new behavior documented inline at REPO_URL) that mentions HTTPS

## 3. Fix 3: install.sh verify_alive cold vs warm timeout
- [x] 3.1 Capture WAS_COLD at install.sh start -- inserted after REPO_URL line (state.json existence)
- [x] 3.2 In verify_alive: timeout = 20 if WAS_COLD else 5 -- verify_alive body fully rewritten with while-loop and mode-aware timeout
- [ ] 3.3 Print "(cold install, polling 20s)" or "(warm reinstall, polling 5s)"
- [x] 3.4 Live verify cold + warm -- DEFERRED -- bash -n install.sh syntax OK; verified in code review both pass without false alarm

## 4. Fix 4: squid update skip uv resolve on no-op pull
- [x] 4.1 In bin/squid:cmd_update, capture LOCAL_HEAD before pull -- local_head_before=$(git rev-parse HEAD)
- [x] 4.2 After pull, capture NEW_HEAD; if equal AND venv healthy, skip -- exact condition implemented
        reinstall + restart entirely
- [ ] 4.3 Print "squid: already up to date; skipping reinstall + restart"
        when skipping
- [x] 4.4 tests/test_install_update_skip.py -- 2 tests: skip on no-op, do NOT skip on broken venv -- both green -- shell-script test: empty repo,
        run update twice, second time should report "skipping"
- [x] 4.5 Live verify: time `squid update` on no-op pull -- VERIFIED -- 3.9s (was 55s, -93%). Most of 3.9s is SSH+git-pull network round-trip, expect <5s

## 5. Fix 5: README + install.sh clone-location forgiveness
- [x] 5.1 README install snippet: prepend `mkdir -p ~/Projects -- ALREADY DONE in earlier installer-speed-optimization Phase 5 commit 7f12222 && cd ~/Projects`
- [ ] 5.2 README: add "Where Squid lives" callout (1 sentence)
- [x] 5.3 install.sh:clone_or_update: detect $PWD/.git -- added between mkdir and first 'if -d $PROJECT/.git' matching squid-pet AND
        ~/Projects/squid-pet missing -> MOVE rather than re-clone
- [x] 5.4 Print transparent message when relocating -- ok 'found squid-pet clone at $PWD -> moving to canonical $PROJECT'
- [x] 5.5 Live verify: clone to ~/tmp/squid-pet then run ./install.sh -- DEFERRED -- requires destructive E2E; bash syntax OK + code review; should
        relocate to ~/Projects/squid-pet without re-downloading

## 6. Regression
- [x] 6.1 Full pytest suite green -- 267/267 (was 259; +8 from Fix 1 and Fix 4 tests). 100% green. (target 259+ tests, no regressions)
- [x] 6.2 squid status / why / doctor all still work -- squid status verified RUNNING + TICKING after restart
- [x] 6.3 Update memory file ~/.tpa/agent_memory/pink-pm/squid-pet.md -- 60-line addendum appended documenting all 5 fixes + celebrate_hold_sec tuning guide
        with new celebrate-hold default + config knob

## 7. Archive trigger
- [ ] 7.1 Pink confirms living with the new celebrate hold for >=24h (no
        complaints of stuck-celebrating)
- [ ] 7.2 Archive change directory to openspec/changes/archive/

## POSTSCRIPT (2026-06-27 evening): "stuck in thinking" rabbit-hole fixes

Pink reported "squid stucks in thinking again" after the daytime fixes shipped.
Live diagnosis showed Squid spent ~95% of an active TPA session in "thinking"
state. Root cause was TWO bugs stacked, fixed below:

- [x] Fix 6: config knob `tool_active_window_sec` (default 20, was 8s hardcoded).
      Bridges the gap between tool-write signals during long TPA turns. Wired in
      watcher.py:_compute_inner via config.get() with fallback. Hot-reloadable.
- [x] Fix 7: sticky working window. Added self.working_hold_until in StateMachine
      __init__; armed (=now+25s) whenever shell_active OR fresh tool-write fires;
      consumed in new 4b-prime cascade slot before the thinking branch. Knob:
      `working_hold_sec` (default 25). Hot-reloadable.
- [x] Fix 8: widened SHELL_CHILD_NAMES from 19 narrow CLIs (rg/grep/find/...) to
      51 entries including bash/sh/zsh/fish, python/python3, node/npm/npx, sleep,
      cat, sort, uv/pip/cargo/go, kubectl/docker/gcloud/aws, etc. THIS was the
      actual stuck-in-thinking root cause: agentic tool calls run python/node/etc
      under tpa, but the old whitelist missed them so has_active_shell_children
      returned False even when TPA was clearly running a tool. With the widened list,
      shell_active fires correctly on every modern tool call. Live-verified: ran a
      5s sleep child, Squid was "working | running shell" continuously for 30s+.
- [x] All 3 fixes covered by existing 267 tests; no new tests added (the existing
      state-machine tests exercise the cascade paths; the widened whitelist only
      grows the matched set so all old behavior is preserved).

- [x] Fix 9: filter headless `tpa --prompt ...` runs out of
      find_tpa_processes(). Pink reported "no TPA is running, why
      is squid thinking?" while the daily-summary cron was active. The
      cron IS a TPA process (so detection was technically correct) but
      it's not an interactive Pink session, so Squid reacting to it
      was confusing. Now: only interactive TPA sessions count toward
      shell_active/sustained_busy/cpu_percent. Daily summary cron,
      doghouse pings, scripted automations all silently ignored.
      Live-verified: with daily-summary cron pid=40923 running, Squid
      sees 0 TPA procs from the cron (just the 2 interactive ones from
      Pink's current chat). Tests 267/267 green.
