# Design — installer-speed-optimization

## Where is the time going? (hypotheses, to be confirmed in Phase 1)

```
distribution-installer measured (~20 min, Pink 2026-06-24):
  preflight              ~0.2 s     fast, leave alone
  ensure_uv              0 s        (Pink had uv already)
  clone_or_update        ~30-120 s  network-bound, 16 MiB over VPN
  setup_venv             ~2-5 s     uv venv is fast
  install_package        ~10-15 MIN <-- almost certainly the bottleneck
  migrate_legacy         ~0.1 s     filesystem-only
  render_plist           ~0.1 s     sed substitution
  install_launcher       ~0.1 s     cp + chmod
  write_settings         ~0.1 s     (silent default per aede27f)
  boot_launchd           ~0.5 s     launchctl call
  verify_alive           ~1-10 s    polling, 0-5s after aede27f
  permission_walkthrough ~0.1 s     non-blocking after aede27f
  print_summary          ~0.1 s     heredoc
                         ─────────
                         ~15-20 min  ~95% in install_package
```

If the bottleneck is what we think, Phase 2's `uv sync` + lockfile + wheel
investigation is where 90% of the win comes from. Phase 3 parallelism is
nice-to-have (saves ~30s, helps perception more than wall time).

## Why is `uv pip install -e .` so slow?

Three plausible causes, in order of likelihood:

1. **uv is downloading & building sdists** because the internal artifactory doesn't
   carry the macOS arm64 wheels. `psutil` (C extension) building from source
   is multi-minute on its own; `pywebview` pulls `pyobjc-*` which is
   notoriously slow on first install.
2. **Internal artifactory throughput is just slow** — the artifactory proxy
   serves wheels but with very low throughput, so even cached wheels take
   minutes to download.
3. **uv re-resolves the entire dep tree** every invocation because we don't
   have a lockfile.

### How to distinguish

Phase 1 instrumentation should capture:
- `uv pip install -e . --verbose 2>&1 | tee /tmp/uv-install.log`
- Look for `Building wheel for psutil` (cause #1) vs `Downloading psutil`
  with a low MB/s rate (cause #2) vs `Resolved N packages in Ns` (cause #3)

### Mitigations per cause

| Cause | Fix |
|---|---|
| Building wheels from sdist | File ticket with Mint to add wheels to artifactory mirror; in the meantime, `uv pip install --no-build psutil pywebview` with our own pinned wheel URLs |
| Slow artifactory throughput | Outside our control, but `uv` does parallel downloads — make sure we're not serializing |
| Re-resolution overhead | Commit `uv.lock`, switch to `uv sync` (lockfile-driven, skips resolution) |

## Lockfile decision: `uv.lock` not `requirements.txt`

`uv sync` against a `uv.lock` is:
- Hash-pinned (security)
- Cross-platform aware (works on Pink's M1 + future Intel/Linux engineers)
- Skips resolution (the slow part of `uv pip install`)
- Maintained alongside `pyproject.toml` automatically

We already use `uv venv` + `uv pip install`; adding `uv.lock` is one
`uv lock` command in CI/precommit. Cost: one new file to keep in git;
benefit: deterministic + fast installs forever.

## Parallelism: which steps can run concurrently?

```
        clone_or_update  ━━━━━━━━━━━━┓
                                      ┣━ setup_venv ━ install_package ━ ...
        ensure_uv        ━━━━━━━━━━━━┛
```

`clone_or_update` and `ensure_uv` are truly independent:
- `clone_or_update` only needs git + network to internal GHE
- `ensure_uv` only needs brew/curl + network to brew CDN or astral.sh

Everything downstream of `setup_venv` needs the cloned repo AND uv, so
they have to wait for both. Bash idiom:

```bash
clone_or_update &
CLONE_PID=$!
ensure_uv &
ENSURE_PID=$!
wait $CLONE_PID || die "clone failed"
wait $ENSURE_PID || die "uv install failed"
```

Caveat: output interleaves chaotically. Solution: buffer each to its own
log file and replay sequentially when both finish. OR just accept
interleaved output and use distinct prefixes (`[clone]`, `[uv]`) so users
can tell what's happening.

### Anti-pattern: do NOT parallelize install_package with anything

`install_package` is by far the slow step, but it's also the one that
needs everything else done first. No upstream work to parallelize against
unless we want to start preheating the launchd plist render or the
launcher copy — both are <0.1s, not worth the complexity.

## Progress reporting — pure-bash spinner

We won't pull in a dep just for a progress bar. A pure-bash spinner is
fine:

```bash
spinner() {
    local pid=$1
    local msg=$2
    local chars="|/-\\"
    local i=0
    while kill -0 $pid 2>/dev/null; do
        printf "\r%s %s" "${chars:$((i%4)):1}" "$msg"
        sleep 0.2
        i=$((i+1))
    done
    printf "\r"  # clear the line
}
```

Wrap the slow stages:

```bash
install_package_async() {
    install_package > /tmp/squid-install-package.log 2>&1 &
    spinner $! "installing packages (can take 2-5 min, longer on first run)"
    wait $!
}
```

If exit non-zero, dump the log so users see the error.

## Cache strategy

`uv` keeps its own cache at `~/.cache/uv/`. After one successful install,
subsequent installs that don't change `pyproject.toml` should be near-instant
because `uv sync` reuses cached wheels. We can verify with:

```bash
# Should be << 1s on warm cache:
time uv sync --frozen
```

No additional cache layer needed from us — just lean on uv's.

## Failure modes new to this proposal

1. **Parallel stages race + scramble error output**. Mitigation: each
   parallel stage writes to its own log; on failure, cat that specific log.
2. **`uv.lock` drifts from `pyproject.toml`**. Mitigation: precommit hook
   (or just doc convention) that runs `uv lock` after any dep change.
3. **Spinner orphaned if parent killed mid-install**. Mitigation: `trap
   "kill $(jobs -p)" EXIT` at script top to clean up children.
4. **`uv sync` fails on a Mac that doesn't have the lockfile's exact Python
   version**. Mitigation: `.python-version` file pinning Python 3.12.x
   (already implicit in pyproject `requires-python`), with friendly error
   pointing to `uv python install 3.12` if missing.

## What stays unchanged

- The 13-stage pipeline structure from `distribution-installer`
- The plist template, launcher script, settings.json schema
- All runtime python code (this is install-only)
- `uninstall.sh` (already fast)
- `bin/squid` subcommands (already fast)
