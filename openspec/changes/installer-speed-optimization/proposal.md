# installer-speed-optimization

## Why

Pink ran `install.sh` end-to-end on 2026-06-24 and it took **~20 minutes**.
The `distribution-installer` proposal's success criteria was "<120 seconds
fresh install" — we missed by ~10x. That's not "the README was optimistic",
it's "the install pipeline is fundamentally too slow to ship to other
engineers". A 20-minute install pipeline that costs them 20 min of confusion
+ context switch each is worse than no installer at all.

Before this proposal, we don't actually know WHERE the time went. The
terminal crashed before logs were captured. Best guess: `uv pip install -e .`
building wheels for `psutil` + `pywebview` over internal's slow artifactory
(`pypi.internal.example.com`). Other suspects: brew install of uv
(though Pink had uv already), git clone of the 16 MiB repo over VPN,
`verify_alive` polling. We need data before optimization.

## Goal

Get fresh-Mac → living-Squid down to a target Pink will respect:

- **Fresh install (cold cache):** <5 min, target 2-3 min
- **Idempotent re-run (warm):** <30 seconds
- **`squid update`:** <60 seconds
- **`squid uninstall && install.sh`:** <2 minutes (most state cached)

Plus: honest progress reporting so users know what's happening when a step
takes >10 seconds. A 5-min install that *shows progress* feels much better
than a 90-second install that hangs silently.

## Non-goals

- Code signing / notarization (separate `code-signing` change, governance-blocked)
- Pre-built binary distribution / `.pkg` (separate `binary-distribution` change)
- Self-hosted PyPI mirror (internal artifactory is the corporate-mandated source)
- Distribution outside internal (separate `external-release` change, far future)
- Replacing `uv` with `pip` / `poetry` / `pdm` (uv is correct, just needs tuning)
- Removing the launchd plist render step (already fast, ~0.1s)

## What changes

### Phase 1 — Measure (no behavior change, ship instrumentation first)

- Add `--profile` flag to `install.sh` that writes per-stage wall times
  to `/tmp/squid-pet-install-profile.txt` (`time` around each stage
  function, formatted as a table at the end).
- Capture profile data from Pink + one other engineer's fresh install to
  confirm the bottleneck.

### Phase 2 — Skip work that doesn't need doing

- Generate `uv.lock` from current `pyproject.toml` and commit it. Add
  `install.sh` logic to prefer `uv sync` (lockfile-driven, no dep
  resolution) over `uv pip install -e .` (resolves every time).
- `clone_or_update` skips `git pull` when HEAD already matches `origin/main`.
- `ensure_uv` short-circuits on `command -v uv` *before* announcing the step
  (avoids the "checking for uv..." flash when it's already installed).
- Optional Phase 2.5: investigate WHY `psutil` + `pywebview` would build
  from source against internal artifactory. Both have macOS arm64 wheels
  upstream. If artifactory doesn't have them, file a ticket with Mint /
  artifactory team to mirror them. If they DO have them but uv is
  resolving against sdist, fix the resolver hint.

### Phase 3 — Run work in parallel where safe

- `clone_or_update` and `ensure_uv` are independent and can run in
  parallel (one needs network to internal GHE, one needs network to brew /
  Astral). Wrap both in `&` and `wait`. Saves ~30s typical, more on slow
  brew install.
- `permission_walkthrough`'s `open settings:Accessibility` already runs
  in the background per `aede27f` — keep, document timing.

### Phase 4 — User-perception fixes

- For any stage that takes >5s, print a one-line "this can take ~Ns"
  warning upfront so users know to wait.
- After `install_package`, print "uv cache primed — re-runs will be fast"
  so first-time users understand subsequent installs.
- Add a `time` line to the final summary showing total install duration
  so users have a reference point.

### Phase 5 — Documentation update

- Update README "Typically 3-5 minutes" with profiled numbers.
- Add a "Speed expectations" section to `docs/INSTALL.md` distinguishing
  cold / warm / update times.
- Cross-link to `~/.squid-pet/logs/install-history.log` (new) showing
  every install + duration, so users can spot regressions over time.

## Success criteria

- Phase 1: install profile captured + posted in this proposal's notes,
  identifies the actual bottleneck with hard numbers (not guesses).
- Phase 2: cold install drops by >=2 minutes vs `aede27f`.
- Phase 3: warm `./install.sh` (re-run on existing setup) completes in <30s.
- Phase 4: every stage that takes >5s prints "this can take ~Ns" first.
- Phase 5: README + `docs/INSTALL.md` updated with profiled numbers, not guesses.
- 219/219 tests still pass (this is pure install plumbing — no runtime change).
- Pink's verdict: "yes I'd give this to another engineer now."
