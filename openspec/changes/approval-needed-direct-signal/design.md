## Context

Squid's `state-detection` capability defines 8 emotional states and a
priority cascade for selecting one per tick. None of those states cover
the moment that matters most to Pink during agentic work: when Code
Puppy has finished thinking and is sitting at the prompt awaiting her
reply. Historically the watcher inferred this from CPU heuristics
(`agent_idle_seconds`), but those produced two distinct UX failures during
2026-06-23 to 2026-06-29:

1. **Multi-TPA masking.** Aggregate CPU stays nonzero while any one TPA
   is busy, so the watcher never noticed a sibling TPA that had gone
   quiet awaiting input.
2. **Stuck wave.** Once the flag-wave fired, it had no notion of
   "Pink already replied"; it kept waving until the TPA went busy again.

Two earlier 2026-06-29 commits (per-process idle + 2-minute snooze)
narrowed the heuristic. The third (`a37c05a`) replaced the heuristic
with a direct signal from TPA itself. This design captures the
resulting architecture.

## Goals / Non-Goals

**Goals:**
- Make `approval_needed` a first-class state in the cascade with the
  highest TPA-active priority (above `thinking`/`working`).
- Use a DIRECT signal from TPA as the primary trigger so the watcher
  never has to guess from CPU.
- Keep a fallback path so TPA versions without the patch still get the
  best-available behavior (per-process idle with snooze cap).
- Give Pink a kill switch she can flip from the tray menu without
  losing visibility (surface state in `squid why`).
- Support multi-TPA transparently (per-PID flag file directory).
- Never leak a stuck signal if TPA crashes mid-prompt.

**Non-Goals:**
- This change does not modify the existing 8-state cascade for
  `sleeping`/`celebrating`/`grooving`/`concerned`/`working`/`thinking`/
  `idle`/`drowsy`.
- It does not change drowsy-entry rules or the user-wake override.
- It does not move per-process flag detection into a separate module
  (the watcher owns it for now; could be extracted later if more
  consumers appear).

## Decisions

### Direct signal over heuristic

**Decision:** Treat presence of any alive-PID file at
`~/.tpa/awaiting_input/<pid>` as the canonical "TPA is awaiting
input" signal. Fire `approval_needed` immediately on detection, with
no threshold, no debounce, no snooze.

**Why:** TPA knows exactly when its prompt loop is active. Any latency
or filtering Squid adds is a regression vs. asking TPA directly.

**Alternatives considered:**
- **Continue CPU heuristics with better tuning.** Rejected — every
  heuristic refinement created new edge cases (never-busy CPs, snooze
  vs. re-fire). The proxy is fundamentally lossy.
- **Single-file flag (`awaiting_input.flag`).** Rejected — would not
  distinguish which of several CPs is asking. Per-PID directory is the
  same cost and supports multi-TPA for free.

### Per-PID flag file directory (mirror of `llm_active.flag`)

**Decision:** Each TPA process owns one file at
`~/.tpa/awaiting_input/<pid>`. The file is touched on entry to
`get_input_with_combined_completion`, deleted on every exit path
(normal return, KeyboardInterrupt, EOFError, any exception).

**Why:**
- Multi-TPA: each process self-reports independently; the watcher OR's
  across them.
- Consistent with the existing `llm_active.flag` convention added in
  Fix 10b (2026-06-27). Same monkey-patch lives in
  `cpts_patch/sitecustomize.py`.
- Cheap to inspect: `os.listdir` + `psutil.pid_exists`.

**Alternatives considered:**
- **Named pipe / Unix socket.** Rejected — more moving parts, harder
  to dead-PID-evict, no readability win.
- **Shared memory segment.** Rejected — overkill for one bit of state.

### Dead-PID eviction at read time

**Decision:** When `tpa_pids_awaiting_input()` finds a flag file whose
PID is no longer alive, it `os.unlink`s the file before returning.

**Why:** If TPA crashes mid-prompt (SIGKILL, Python crash, OOM) the
finally clause never runs and the flag would persist forever, leaving
Squid stuck on `approval_needed`. Read-time eviction self-heals
without needing a separate sweeper thread.

### Fallback retains snooze + ever-busy filter

**Decision:** The CPU-heuristic path (`per_process_pending_approval_idle`)
stays as a fallback only. The state machine reads the direct signal
first; if no flag files exist, it falls through to the heuristic.

**Why:** TPA versions older than `a37c05a` won't write the flag. The
heuristic with its newer guards (only fires for CPs ever observed busy,
2-minute snooze cap) gives the best-available behavior in that case
without being noisy.

### Kill switch surfaced in `squid why`

**Decision:** `approval_alert_enabled` is read from
`~/.squid-pet/config.json`. When false, BOTH the direct signal and the
fallback are suppressed. `squid why` reports the current value plus
the live per-proc idle so Pink can tell at a glance whether the alert
is muted vs. simply not firing.

**Why:** Pink toggled this off and forgot, then wondered why the wave
stopped firing. Visibility in the diagnostic CLI is cheap and prevents
the silent-failure mode from recurring.

## Risks / Trade-offs

- **Risk:** A TPA process patched by an older `sitecustomize.py`
  imports BEFORE the new one is on `PYTHONPATH`. The new wrapper would
  not apply, and the TPA would never write its flag.
  **Mitigation:** The patch is loaded at Python startup via
  `sitecustomize.py` on the `cpts` PYTHONPATH. Existing CPs simply
  use the fallback path. Restarting TPA picks up the new patch.

- **Risk:** Restarting the squid daemon clears the `_PER_PID_EVER_BUSY`
  set, so a TPA that was pending approval *before* the restart will not
  re-fire via the fallback until it next cycles busy.
  **Mitigation:** Acceptable. The direct signal still works across
  restarts because it's filesystem state. If this becomes painful,
  persist the set to a JSON file alongside `state.json`.

- **Risk:** Filesystem hiccups (full disk, permission errors) could
  prevent flag writes/deletes.
  **Mitigation:** All flag operations are best-effort (try/except
  around every IO). Failures degrade to the fallback, never crash TPA
  or the watcher.

- **Trade-off:** Two trigger paths means two code paths to maintain.
  We could remove the fallback once every Pink-installed TPA has the
  patch. For now the parallel paths are explicit and tested.

## Migration Plan

1. Squid-side code + tests land in `main` (already done in `a37c05a`).
2. TPA-side `sitecustomize.py` updated in `~/.tpa/cpts_patch/`
   (already done; lives outside the repo).
3. Pink restarts her TPA windows; new processes inherit the patch via
   `sitecustomize.py` at import time. Old processes continue on the
   CPU fallback until restarted.
4. No data migration required. Existing `~/.tpa/llm_active.flag`
   and `~/.squid-pet/config.json` semantics are unchanged.

## Open Questions

- Should `_PER_PID_EVER_BUSY` be persisted to disk so the fallback
  survives daemon restarts? (Deferred — low value while the direct
  signal works.)
- Should we add a heartbeat to the flag file (rewrite mtime every N
  seconds) so a *very* long prompt doesn't look stale to future
  consumers? (Deferred — current consumer treats presence as binary,
  not freshness.)
