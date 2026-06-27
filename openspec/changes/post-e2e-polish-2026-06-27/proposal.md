# post-e2e-polish-2026-06-27

## Why

The 2026-06-27 distribution-installer end-to-end test surfaced 4 real
installer UX bugs, and Pink's question "you just finished a long complex
task -- why didn't Squid look celebrating?" exposed a 5th feedback-loop
bug. None of these block the original distribution-installer change from
archiving, but all 5 are real UX cuts that erode trust in the pet/tooling.

Bundling them as a single follow-up polish change because they were all
surfaced from the same session and share a "make-the-happy-path-obvious"
theme: brand-new users shouldn't trip over auth + CWD assumptions, no-op
updates should be sub-5s instead of 55s, and Squid's celebration should
linger long enough for a human to actually *see* it after they finish
reading a reply.

## What changes

### Fix 1 -- Celebrate hold too short (feedback loop)
- `CELEBRATE_DURATION_SEC` (TPADetector) 4 -> 20
- `CELEBRATE_HOLD_SEC` (GitDetector) 4.0 -> 20.0
- `CELEBRATE_DURATION_SEC` (watcher.StateMachine) 4 -> 20
- New config knob `celebrate_hold_sec` in `~/.squid-pet/config.json`
  (default 20) so Pink can tune without code edits. Hot-reload-able
  via the same mtime pattern as triggers.

### Fix 2 -- install.sh defaults to HTTPS clone -> hangs without PAT
- Change `REPO_URL` default in install.sh from
  `https://internal-ghe.example.com...` to `git@internal-ghe.example.com...` (SSH).
- Add note in install.sh's clone error message: "If SSH fails too,
  override with SQUID_REPO=https://... and ensure you have HTTPS PAT
  cached via `git config --global credential.helper osxkeychain`."

### Fix 3 -- install.sh verify_alive 5s too tight for cold start
- Split the timeout: 5s for warm reinstall (idempotent path, Squid
  was already running), 15s for cold install (first launch loads
  images + WebView).
- Detect "cold" by checking if a state.json existed at install.sh
  start (warm) vs not (cold).

### Fix 4 -- `squid update` runs full uv resolve on no-op pull
- In `bin/squid`'s `cmd_update`: capture local HEAD before pull, compare
  after pull. If unchanged, print "already up to date" + skip the
  `uv pip install -e .` step entirely. Only restart the daemon if
  package was actually rebuilt.
- Target: 55s -> sub-5s for no-op updates.

### Fix 5 -- README clone-anywhere mismatches install.sh's ~/Projects/ assumption
- Update README install instructions to explicitly say
  `cd ~/Projects && git clone ...` (currently no CWD specified).
- Add a "Where Squid lives" callout box explaining the
  ~/Projects/squid-pet/ convention.
- In install.sh's clone_or_update: if PWD is inside a git repo whose
  top-level matches `squid-pet` AND ~/Projects/squid-pet doesn't exist,
  offer to move it (or symlink) rather than fall back to HTTPS clone.

## Impact

- Affected specs: detector-cascade (celebrate timing), installer
- Affected files:
  - src/squid_pet/detectors.py
  - src/squid_pet/watcher.py
  - src/squid_pet/config.py
  - install.sh
  - bin/squid
  - README.md
  - tests/test_celebrate_hold.py (new)
  - tests/test_install_update_skip.py (new)
- Risk: LOW. Celebrate change is constant-tuning + new config knob.
  Installer changes target known broken paths only; happy path
  unchanged.
- No state.json schema changes. Existing user configs unaffected
  (celebrate_hold_sec optional; absent means default 20).
