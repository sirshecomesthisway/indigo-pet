# Design -- post-e2e-polish-2026-06-27

## Decision 1: celebrate hold = 20s default (was 4s)

### Context
Pink's verbatim feedback: "you have finished a long complex task --
why didn't Squid look 'celebrating'?" After ~5 commits and pushes
during today's E2E, she glanced at Squid each time and saw "thinking"
or "idle" -- never "celebrating".

Root cause: both GitDetector and TPADetector hold the celebrate
state for only 4 seconds after the trigger event (commit/push/CPU-drop).
By the time a human finishes reading a multi-paragraph reply (~10-15s),
finishes the next typing action (~3-5s), and glances at the pet
(~1-2s), the celebrate window is long closed.

We verified GitDetector is firing correctly (test commit at tick 11800
produced state.json `"state": "celebrating"` immediately). The bug is
purely the hold duration.

### Decision
Bump default celebrate hold from 4s -> 20s across all three sites:
1. `CELEBRATE_DURATION_SEC` (module const in detectors.py, TPA
   uses it)
2. `CELEBRATE_HOLD_SEC` (class const on GitDetector)
3. `CELEBRATE_DURATION_SEC` (module const in watcher.py, StateMachine
   uses it)

Add config knob `celebrate_hold_sec` so Pink can tune without code
edits. Default 20, valid range 4-60, read via existing config.get()
pattern. Detectors re-read it on each reset (cheap).

### Rejected alternatives
- **Scale by busy duration**: long task -> long celebrate. Tempting,
  but adds complexity (need to track busy duration) and the 20s
  baseline already covers most cases. Defer until proven needed.
- **Stack across multiple events**: commit + push within 5s gives
  extended window. Already happens automatically because each edge
  resets `_celebrate_until = now + hold`, and 20s of overlap is plenty.

### Why 20s
- Average reading speed: ~250 wpm. A 100-word reply takes 24s.
- Glance-after-reply latency: ~2-5s.
- 20s covers ~80% of "user notices Squid right after I reply" cases
  without being so long that Squid feels stuck-celebrating.
- Easy to tune via config if Pink wants more/less.

## Decision 2: install.sh REPO_URL default = SSH

### Context
Pink's README uses SSH (git@internal-ghe.example.com...) for the initial clone.
install.sh defaulted to HTTPS, requiring a cached PAT that no
internal user has by default. Result: `git clone` hangs forever on
credential prompt, --non-interactive doesn't help.

### Decision
Change REPO_URL default to SSH. Pink already has SSH working
(she pushes via SSH). New users will hit a known-friendly auth path
(corporate IT manages SSH key sync via an MDM agent); HTTPS PAT setup
remains the exotic case via `SQUID_REPO=https://...` override.

### Why not auto-detect
Could probe `ssh -T git@internal-ghe.example.com...` first. Adds 5s per install
and the SSH server prints WARNINGs that pollute logs. Not worth it
when SSH is the safe default.

## Decision 3: verify_alive cold vs warm timeout split

### Context
install.sh's verify_alive polls state.json with 5s timeout. Cold
install loads ~13 sprite PNGs (10MB total), spins up WKWebView, and
snaps the window -- takes ~10s on Pink's M1. Result: false-alarm
warning on every healthy cold install.

### Decision
- Detect cold vs warm by checking if ~/.squid-pet/state.json existed
  at install.sh script start (capture in $WAS_COLD).
- WAS_COLD=true -> 20s timeout
- WAS_COLD=false -> 5s timeout (unchanged warm path)
- Print which mode was used in the verify line for transparency.

## Decision 4: squid update skip uv resolve on no-op pull

### Context
`squid update` always runs `uv pip install -e .` even when git pull
is a no-op. uv resolves 13 packages in ~42s for zero benefit.

### Decision
In bin/squid:cmd_update():
1. Capture `LOCAL_HEAD=$(git rev-parse HEAD)` before pull.
2. Run `git pull --ff-only`.
3. Capture `NEW_HEAD=$(git rev-parse HEAD)` after.
4. If equal AND .venv/bin/squid-pet exists AND .venv is non-empty:
   skip both the reinstall AND the restart entirely. Print
   "squid: already up to date; skipping reinstall + restart".
5. Else: proceed with reinstall + restart as today.

### Target performance
- No-op update: ~55s -> <3s (just git ls-remote + compare)
- Real update: unchanged ~55s

## Decision 5: README + install.sh clone-location handling

### Context
README says
```
git clone git@internal-ghe.example.com:pink/squid-pet.git
cd squid-pet && ./install.sh
```
Doesn't specify CWD. install.sh internally insists on
~/Projects/squid-pet/. If user is in ~/, they clone to
~/squid-pet/, then install.sh falls into HTTPS-clone path
(Decision 2 fixes the worst of this, but UX is still wrong).

### Decision
- README change: prepend `mkdir -p ~/Projects && cd ~/Projects` to
  the install snippet. Add a one-sentence "Squid lives in
  ~/Projects/squid-pet by default" callout below.
- install.sh change: in clone_or_update, if `~/Projects/squid-pet`
  doesn't exist BUT `$PWD/.git` does AND $(git remote get-url origin)
  matches squid-pet, MOVE the repo to ~/Projects/squid-pet rather
  than re-clone. Print what happened so the user isn't surprised.
- This makes the README workflow forgiving: clone anywhere, run
  install.sh, it'll relocate the repo into the canonical location.

### Why MOVE rather than symlink
Symlinks create surprises later (rm -rf squid-pet from CWD deletes
the canonical install). A move is unambiguous and idempotent (next
install.sh run finds the canonical location and reuses it).
