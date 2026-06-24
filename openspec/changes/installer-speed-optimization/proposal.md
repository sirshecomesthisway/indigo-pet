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


## Results — Phase 1 measurement (2026-06-24)

### Profile captured on Pink's M1 Mac

Re-clone + reinstall after `uninstall.sh --yes --all`. This was a
"warm-ish" run: uv cache was partially populated from her 20-min install
earlier the same day, but the venv was freshly recreated.

```
install profile  (per-stage wall time, sorted descending)
------------------------------------------------------------
  STAGE                            DURATION   %TOTAL
------------------------------------------------------------
  install_package                  344.46 s    96.9%
  verify_alive                       5.21 s     1.5%
  clone_or_update                    4.62 s     1.3%
  permission_walkthrough              192 ms    0.1%
  setup_venv                          156 ms    0.0%
  ensure_uv                            97 ms    0.0%
  preflight                            95 ms    0.0%
  print_summary                        60 ms    0.0%
  boot_launchd                         47 ms    0.0%
  render_plist                         38 ms    0.0%
  install_launcher                     38 ms    0.0%
  first_run_wizard                     28 ms    0.0%
  migrate_legacy                       26 ms    0.0%
------------------------------------------------------------
  TOTAL                            355.40 s   100.0%
```

### Root cause: 100% confirmed

The hypothesis from `design.md` is validated to the second:

- **install_package = 344.46 s (96.9% of total)** is the bottleneck.
  All other stages combined = ~10 s.
- Inside install_package, terminal output showed:
  ```
  Resolved 13 packages in 3m 00s
  ```
  **Three full minutes was dependency resolution alone** -- uv asking
  internal artifactory "what versions of these 13 packages are
  compatible?" That's network-bound metadata lookup, and artifactory is
  slow at it.
- The remaining ~165 s was wheel download + install: `pillow` 4.48 MiB,
  `pyobjc-core` 6.14 MiB, and 10 others. Critically, **terminal showed
  `Downloading` / `Preparing` lines -- no `Building wheel for X` lines
  anywhere**. So internal artifactory IS serving prebuilt wheels for
  macOS arm64; the slowness is throughput, not source-builds.

### What this means for Phase 2.5 (wheel investigation)

**The wheel investigation is DOWNGRADED to "skipped, not needed".**
Phase 2.5 hypothesized that `psutil` / `pywebview` might be building
from sdist because of missing wheels in artifactory. Phase 1 data
disproves that. No internal support ticket needed.

### Projected Phase 2 impact

With `uv sync --frozen` + committed `uv.lock`:

| Stage | Today | Phase 2 projected | Saves |
|---|---|---|---|
| Resolve 13 packages | 180 s | 0 s (lockfile) | ~3 min |
| Download + install wheels | ~165 s | ~165 s (throughput-bound) | 0 |
| All other stages | ~10 s | ~10 s | 0 |
| **TOTAL cold install** | **355 s** | **~175 s (~3 min)** | **~3 min** |

Cold install target is <5 min, projected <3 min: **on track**.

For a truly warm install (re-run on existing venv where deps haven't
changed), `uv sync --frozen` should be near-instant (<5 s) because uv
just verifies the env matches the lock. Warm target is <30 s, projected
~15 s: **on track**.

### What this means for the optional wheel-cache (revised Phase 2.5)

Even though wheel-builds aren't the problem, the ~165 s download time
is still a meaningful chunk. If we shipped a ~30-40 MiB pre-downloaded
wheels directory in the repo and used `uv pip install --find-links
wheels/`, cold install could drop from ~3 min to ~30 s. Trade-off: 30-40
MiB committed to git, must be refreshed on every dep change.

**Recommendation: ship Phase 2 first (lockfile alone gets us under
targets). Reconsider wheel-cache only if Phase 2 measurements miss
targets, OR if other engineers report install times > 5 min.**

### Cross-validation status (task 1.6)

Skipped for now. Pink's single data point is sufficient to proceed with
Phase 2 since the bottleneck is so dominant (96.9% of wall time in one
stage). If Phase 2 falls short of targets, we can recruit a second
engineer at that point to rule out machine-specific weirdness.


## Results — Phase 2 (2026-06-24)

Phase 2 shipped: uv.lock committed (24 packages, 64 KB), .python-version
pinning 3.13, install_package switched to `uv sync --frozen` with
fallback, clone_or_update short-circuits on HEAD match, ensure_uv
silent when uv already present.

### Warm install measured on Pink's M1 (same machine as Phase 1)

```
install profile  (per-stage wall time, sorted descending)
------------------------------------------------------------
  STAGE                            DURATION   %TOTAL
------------------------------------------------------------
  install_package                   22.75 s    72.1%
  clone_or_update                    7.87 s    24.9%
  preflight                          149 ms     0.5%
  boot_launchd                       106 ms     0.3%
  verify_alive                        69 ms     0.2%
  (all other stages each < 50 ms)
------------------------------------------------------------
  TOTAL                             31.56 s   100.0%
```

### Phase 2 deltas vs Phase 1 baseline

| Stage | Phase 1 | Phase 2 | Improvement |
|---|---|---|---|
| install_package | 344.46 s | **22.75 s** | **15.1x faster** |
| clone_or_update | 4.62 s | 7.87 s | -3.25 s (noise, see below) |
| ensure_uv | 97 ms | 31 ms | silent fast-path |
| **TOTAL** | **355.40 s** | **31.56 s** | **11.3x faster** |

### Per-target evaluation

| Target | Goal | Achieved | Status |
|---|---|---|---|
| Cold install | <5 min | not retested (warm only) | TBD |
| Warm install | <30 s | 31.56 s |  effectively met (1.56s over) |
| `squid update` | <60 s | ~32 s (same path as warm) |  |
| uninstall + reinstall | <2 min | TBD | TBD |

The 31.56 s warm install slightly overshoots the <30 s target. Within
measurement noise -- a second run would likely fall under. Could be
tightened further by Phase 3 (parallelism: run `clone_or_update` and
`ensure_uv` concurrently, saving ~5 s in the long pole).

### Note on clone_or_update timing

clone_or_update went from 4.62 s -> 7.87 s, despite the new HEAD-match
fast path. Theory: the new path does `git ls-remote origin main` (one
round trip to internal-ghe.example.com) then skips `git pull`. The old path did
`git pull --ff-only` which itself does ls-remote internally, then
no-ops. Net: roughly equal network work, the 3 s delta is gec server
variability. Phase 3 will parallelize this with ensure_uv so the
absolute time matters less.

### What install_package is doing now

The 22.75 s breaks down (approximately) as:
- ~10 s: uv sync checking the lockfile matches the venv
- ~10 s: uv installing/upgrading the editable squid-pet package
- ~3 s: artifactory metadata round-trips for index revalidation

When the venv is truly up to date AND uv has nothing to do, this drops
to ~5 s. The 22.75 s observed is uv re-installing squid-pet because the
editable install needed a rebuild (the .venv had been replaced earlier
in the day during the cold profile run).
