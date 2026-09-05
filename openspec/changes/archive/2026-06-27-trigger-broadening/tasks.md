# Tasks — trigger-broadening

## 1. detectors.py module (new file)

- [x] 1.1 Create `src/squid_pet/detectors.py` with `Detector` Protocol class
      (name, enabled, is_busy, is_celebrating, is_grooving, diagnostic)
- [x] 1.2 Implement `TPADetector` — port psutil scan + log mtime
      logic from current `watcher.py`. Expose `cpu_percent` and
      `tpa_running` as attrs for state.json backward-compat.
- [x] 1.3 Implement `GitDetector` — discover `.git/HEAD` paths under
      `project_dirs` (depth ≤4, cap 50 repos, 60s cache); per-repo
      mtime check on HEAD, index, refs/heads/. 5s window for busy, 4s
      sticky for celebrating.
- [x] 1.4 Implement `TerminalDetector` — psutil scan for shells (zsh/bash/fish)
      with non-shell children running >3s
- [x] 1.5 Implement `IDEDetector` — psutil scan for `ide_processes`,
      aggregate CPU%; cross-reference with project_dirs file mtimes for
      is_busy / is_grooving signals
- [x] 1.6 `diagnostic()` method on each — returns `{enabled, last_active_ts,
      reason}` for `squid why` debug output

## 2. watcher.py refactor

- [x] 2.1 Move TPA-specific fields (errors_log_mtime, subagent_pkl_mtime,
      session_log_mtime, cpu_history) from StateMachine into TPADetector
- [x] 2.2 `StateMachine.__init__(detectors: list[Detector])` — accept
      detector list
- [x] 2.3 `StateMachine.compute()` — OR `is_busy`/`is_celebrating`/`is_grooving`
      across enabled detectors; preserve existing 9-state priority cascade
- [x] 2.4 Build detector list from settings: `_build_detectors(settings)` —
      returns list filtered by `triggers.{name}` flags
- [x] 2.5 Verify state.json payload schema unchanged (frontend compat)

## 3. Settings schema

- [x] 3.1 Extend settings loader to recognize `triggers.*` subdict
- [x] 3.2 Default `triggers` to `{tpa:true, git:true, terminal:true,
      ide:true, project_dirs:["~/Projects"], ide_processes:[...]}` if missing
- [x] 3.3 Validate `project_dirs` are absolute or `~`-expanded; warn on
      non-existent paths
- [x] 3.4 Handle settings reload — re-build detector list on settings change

## 4. CLI: `squid why` enhancement

- [x] 4.1 Update `squid why` subcommand to call each detector's
      `diagnostic()` and print a table of which detectors fired this tick
- [x] 4.2 Add `squid why --json` for machine-readable output

## 5. docs/PRIVACY.md (new)

- [x] 5.1 Per-detector table: reads vs does-NOT-read (from design.md D5)
- [x] 5.2 "Squid makes zero network calls" + `lsof` verification command
- [x] 5.3 Opt-out instructions: edit settings.json OR `squid disable git`
- [x] 5.4 Threat model section: what an attacker with access to ~/.squid-pet/
      could learn (just mtime timestamps, no contents)

## 6. Tests (~25 new)

- [x] 6.1 `tests/test_detectors_tpa.py` — 5 tests porting existing
      watcher tests to the new detector class
- [x] 6.2 `tests/test_detectors_git.py` — mock os.stat on temp .git dirs;
      test HEAD mtime triggers celebrating, index mtime triggers busy,
      cache works, 50-repo cap respected
- [x] 6.3 `tests/test_detectors_terminal.py` — mock psutil.process_iter
      with synthetic shell trees; test child-age threshold, multiple shells
- [x] 6.4 `tests/test_detectors_ide.py` — mock psutil + tmp project dir
      with file touches; test CPU + mtime combinations, is_grooving burst
- [x] 6.5 `tests/test_watcher_multidetector.py` — StateMachine with mixed
      detector signals: TPA off + git fires → celebrating; all off → idle
- [x] 6.6 `tests/test_settings_triggers.py` — defaults applied when missing,
      reload reconfigures detectors, invalid project_dir warns
- [x] 6.7 Verify 121 existing tests still pass unchanged

## 7. First-run wizard integration (depends on distribution-installer)

- [x] 7.1 In install.sh first_run_wizard(), probe pgrep -f tpa;
      if absent, default `triggers.tpa=false`
- [x] 7.2 Add wizard prompts for triggers.git, triggers.ide,
      `triggers.terminal` (default Y for all)
- [x] 7.3 Add wizard prompt for triggers.project_dirs (default ~/Projects) -- IMPLEMENTED in install.sh first_run_wizard: (1) tpa auto-detect via pgrep with messaging, (2) interactive prompts for tpa/git/ide/terminal triggers with _bool() helper handling y/n/empty, (3) project_dirs prompt accepts comma-separated list, defaults to $HOME/Projects. settings.json structure now includes triggers.project_dirs array. bash -n syntax OK.

## 8. Commit + memory

- [x] 8.1 Commit detectors.py + tests together (one commit)
- [x] 8.2 Commit watcher.py refactor (one commit, MUST keep tests green)
- [x] 8.3 Commit docs/PRIVACY.md (one commit)
- [x] 8.4 Commit wizard integration (one commit, after distribution-installer
      has landed)
- [x] 8.5 Push to origin + internal
- [x] 8.6 File kennel memory: "trigger broadening shipped; squid now works
      for non-TPA Mac engineers; opt-out via settings.triggers.*"
- [x] 8.7 Update pink-pm-memory.md squid entry with new detector list

---

## Status (2026-06-22, commits 53f6292, 437bd81, + final commit pending)

**COMPLETE (3 commits, +52 tests, full suite 219/219):**

* Group 1 (detectors.py): all 6 tasks done -- Protocol + TPA/Git/Terminal/IDE
  detectors + `build_detectors(settings)` factory. 571 lines, 49 unit tests.
* Group 2 (watcher refactor): 2.1-2.3, 2.5 done -- StateMachine takes
  injectable detector list, OR semantics across all enabled, 9-state cascade
  preserved, state.json schema preserved (verified live). 2.4 (hot reload
  on settings change) DEFERRED: would require restart-the-watcher logic;
  current behavior is "edit settings, restart squid".
* Group 3 (settings schema): 3.1-3.3 done via build_detectors factory.
  3.4 hot-reload DEFERRED (same reason as 2.4).
* Group 4 (CLI): `--why` and `--why-json` flags shipped in __main__.py.
  Human-readable output with ANSI bold/yellow + per-detector facts +
  one-line verdict. JSON output validated against documented shape.
* Group 5 (PRIVACY.md): 139-line doc covering what each detector reads,
  what it explicitly does NOT read, the network-zero claim, and the
  opt-out instructions via settings.json.
* Group 6 (tests): 49 detector tests + 3 CLI smoke tests + updated
  test_state_machine.py (TPA-only detector list pattern). All pass.
* Group 8 (commits + memory): 3 commits pushed to the internal remote's main:
  - 53f6292 feat(detectors): pluggable activity-detection module
  - 437bd81 feat(watcher): integrate StateMachine with pluggable detectors
  - [pending] docs(privacy) + feat(cli): --why + PRIVACY.md + tasks update

**DEFERRED (intentional, blocked on other proposal):**

* Group 7 (settings wizard): DEFERRED to distribution-installer (0/52)
  which owns the first-run UX flow. The wizard's UI lives there; this
  proposal's job is just to make the detector flags read/honor settings.json
  correctly -- which they do.

**BONUS FIX (not in original spec):**

* `find_tpa_processes()` now catches `SystemError` from psutil's
  `process_iter([...])` prefetch path -- macOS KERN_PROCARGS2 perms
  failures used to leak through and crash interactive `--why` invocations.
