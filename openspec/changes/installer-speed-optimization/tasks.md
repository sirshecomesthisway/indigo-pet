# Tasks — installer-speed-optimization

## 1. Phase 1: Instrumentation (measure before optimizing)

- [x] 1.1 Add `--profile` flag to `install.sh` arg parser (alongside existing `--wizard`, `--non-interactive`)
- [x] 1.2 Add `time_stage` bash helper that wraps a stage function call, captures wall time, appends `STAGE_NAME=DURATION` to a profile array
- [x] 1.3 Replace each stage call in `main()` with `time_stage <stage_fn>` when `--profile` is set
- [x] 1.4 Print profile table at end (sorted descending, ASCII format) AND write to `/tmp/squid-pet-install-profile-<timestamp>.txt`
- [x] 1.5 Run `./install.sh --profile` on Pink's machine (fresh install, warm install, update) — capture 3 profiles
- [ ] 1.6 Recruit one other engineer to run `./install.sh --profile` on their machine — capture profile (cross-validates Pink's results aren't unique to her setup)
- [x] 1.7 Append profile data + analysis to `proposal.md`'s "What we measured" section (new)
- [x] 1.8 Commit Phase 1 + push

## 2. Phase 2: Skip work that doesn't need doing

- [x] 2.1 Generate `uv.lock` via `uv lock` in repo root; verify it commits cleanly + covers all platforms we care about (macOS arm64 minimum, macOS Intel + Linux nice-to-have for future)
- [x] 2.2 Add `uv.lock` to git, NOT to .gitignore
- [x] 2.3 Add `.python-version` file pinning the version from `pyproject.toml`'s `requires-python` (e.g., `3.12`)
- [x] 2.4 Rewrite `install_package` to prefer `uv sync` when `uv.lock` exists, fall back to `uv pip install -e .` otherwise. Use `uv sync --frozen` to refuse to re-resolve (would be a bug).
- [x] 2.5 `clone_or_update` early-exit: if `git rev-parse HEAD == git ls-remote origin main`, skip `git pull` entirely (saves a network round-trip on warm installs)
- [x] 2.6 `ensure_uv` reorder: check `command -v uv` BEFORE printing the `step` header. If already installed, print nothing (one less line of noise).
- [x] 2.7 Re-run `./install.sh --profile` warm install, verify it drops below 30s
- [x] 2.8 Re-run `./install.sh --profile` cold install (after `uninstall.sh --all`), verify it drops by >=2 min vs Phase 1 baseline
- [x] 2.9 Document the new times in proposal "Results" section
- [x] 2.10 Commit Phase 2 + push

## 3. Phase 2.5: Wheel investigation (optional, only if `psutil`/`pywebview` are building from sdist)

- [x] 3.1 Run uv pip install -e . --verbose on a fresh venv; grep for 'Building wheel' lines -- DONE 2026-06-27. FINDING: 0 'Building wheel' lines in 1416-line log. uv IS using prebuilt wheels (e.g. 'Using cached metadata for: proxy-tools', 'pyobjc_core-12.2.1-cp313-cp313-macosx_10_13_universal2.whl'). So sdist-building is NOT the bottleneck.
- [x] 3.2 Check internal artifactory for wheels -- NOT NEEDED (3.1 confirmed wheels exist). HOWEVER, found a different blocker: 'WARN Range requests not supported for pyobjc_core-12.2.1-cp313-cp313-macosx_10_13_universal2.whl; streaming wheel'. internal artifactory mirror doesn't support HTTP Range requests, forcing uv to STREAM the ~50MB universal2 pyobjc_core wheel instead of downloading in parallel chunks. This is likely the dominant slowness.
- [x] 3.3 File internal support ticket for artifactory -- DRAFTED for Pink. Subject: 'PyPI mirror lacks HTTP Range request support; forces streaming for large wheels'. Component: PyPI mirror. Repro: uv venv test && uv pip install --index-url https://pypi.internal.example.com/... pyobjc-core; observe WARN. Impact: ~50MB streamed sequentially instead of parallel chunks adds ~30s cold install. Workaround: pre-bundle wheels (task 3.5). Pink to actually file the ticket (Indigo can draft the form via msgraph if needed).
- [x] 3.4 uv resolver hints (--prefer-binary etc.) -- NOT APPLICABLE: artifactory already returns wheels (per 3.1). The slowness is the wheel transport, not the resolver.
- [x] 3.5 Pre-built wheel cache fallback -- DEFERRED (decision required). Pros: bypasses range-request issue entirely, sub-10s cold install. Cons: wheels are platform-specific (would need universal2 arm64+x86_64 variants), bumps repo size by ~80MB, requires manual refresh when deps update. Recommend: file internal support ticket FIRST (task 3.3); if no response in 2 weeks, ship the wheel cache as a stop-gap. Pink to decide.

## 4. Phase 3: Parallelism

- [ ] 4.1 Refactor `clone_or_update` and `ensure_uv` to run in parallel with `&` + `wait`
- [ ] 4.2 Buffer each parallel stage's output to its own `/tmp/squid-install-<stage>.log`; replay sequentially on completion
- [ ] 4.3 On any parallel stage failure, cat its log to stderr before `die` so user sees the actual error
- [ ] 4.4 `trap "kill $(jobs -p) 2>/dev/null; true" EXIT` at script top to clean up orphaned children if user Ctrl-C's mid-install
- [ ] 4.5 Re-run `./install.sh --profile` cold install, verify parallel block saves >=20s
- [ ] 4.6 Commit Phase 3 + push

## 5. Phase 4: User-perception fixes

- [ ] 5.1 Implement `spinner $pid "$msg"` bash helper per design.md
- [ ] 5.2 Wrap `install_package` call with spinner — show "installing packages (~2-5 min cold, <30s warm)" while it runs
- [ ] 5.3 Wrap `clone_or_update` call with spinner ("cloning ~16 MiB over VPN")
- [ ] 5.4 Wrap `ensure_uv` install path (the brew/curl branch) with spinner ("installing uv via brew/Astral")
- [ ] 5.5 After successful `install_package`, print "uv cache primed at ~/.cache/uv — subsequent installs will be fast"
- [ ] 5.6 Add total install duration line to `print_summary` ("install took: Xm Ys")
- [ ] 5.7 Verify spinner is suppressed when `! -t 1` (non-TTY, e.g., CI logs)

## 6. Phase 5: Documentation + history

- [x] 6.1 Update README install section with profiled times (replace "3-5 minutes" with actual numbers)
- [x] 6.2 Add "Speed expectations" section to `docs/INSTALL.md` with table: cold / warm / update / uninstall+reinstall
- [x] 6.3 Add `~/.squid-pet/logs/install-history.log` append at end of `install.sh` (timestamp + duration + cold-or-warm)
- [x] 6.4 Add brief "How to spot a regression" note pointing at install-history.log

## 7. Verification

- [ ] 7.1 Full test suite: `.venv/bin/python -m pytest -q` → 219/219 still green (sanity, no runtime touched)
- [ ] 7.2 Pink runs `./install.sh --profile` cold + warm; confirms targets met (<5 min cold, <30s warm)
- [ ] 7.3 Pink runs `./uninstall.sh --yes && ./install.sh` (full cycle), confirms <2 min
- [ ] 7.4 Pink's verdict: "yes I'd give this to another engineer now"

## 8. Commits + housekeeping

- [ ] 8.1 One commit per phase (5-6 commits total)
- [ ] 8.2 Update `openspec/changes/installer-speed-optimization/proposal.md` "Results" section with final profiled numbers
- [ ] 8.3 Kennel memory: profile findings + which mitigations actually moved the needle
- [ ] 8.4 Update `~/.tpa/agent_memory/pink-pm/pink-pm-memory.md` if any cross-project learning (e.g., "always commit uv.lock for internal artifactory")
