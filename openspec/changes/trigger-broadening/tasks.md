# Tasks — trigger-broadening

## 1. detectors.py module (new file)

- [ ] 1.1 Create `src/squid_pet/detectors.py` with `Detector` Protocol class
      (name, enabled, is_busy, is_celebrating, is_grooving, diagnostic)
- [ ] 1.2 Implement `TPADetector` — port psutil scan + log mtime
      logic from current `watcher.py`. Expose `cpu_percent` and
      `tpa_running` as attrs for state.json backward-compat.
- [ ] 1.3 Implement `GitDetector` — discover `.git/HEAD` paths under
      `project_dirs` (depth ≤4, cap 50 repos, 60s cache); per-repo
      mtime check on HEAD, index, refs/heads/. 5s window for busy, 4s
      sticky for celebrating.
- [ ] 1.4 Implement `TerminalDetector` — psutil scan for shells (zsh/bash/fish)
      with non-shell children running >3s
- [ ] 1.5 Implement `IDEDetector` — psutil scan for `ide_processes`,
      aggregate CPU%; cross-reference with project_dirs file mtimes for
      is_busy / is_grooving signals
- [ ] 1.6 `diagnostic()` method on each — returns `{enabled, last_active_ts,
      reason}` for `squid why` debug output

## 2. watcher.py refactor

- [ ] 2.1 Move TPA-specific fields (errors_log_mtime, subagent_pkl_mtime,
      session_log_mtime, cpu_history) from StateMachine into TPADetector
- [ ] 2.2 `StateMachine.__init__(detectors: list[Detector])` — accept
      detector list
- [ ] 2.3 `StateMachine.compute()` — OR `is_busy`/`is_celebrating`/`is_grooving`
      across enabled detectors; preserve existing 9-state priority cascade
- [ ] 2.4 Build detector list from settings: `_build_detectors(settings)` —
      returns list filtered by `triggers.{name}` flags
- [ ] 2.5 Verify state.json payload schema unchanged (frontend compat)

## 3. Settings schema

- [ ] 3.1 Extend settings loader to recognize `triggers.*` subdict
- [ ] 3.2 Default `triggers` to `{tpa:true, git:true, terminal:true,
      ide:true, project_dirs:["~/Projects"], ide_processes:[...]}` if missing
- [ ] 3.3 Validate `project_dirs` are absolute or `~`-expanded; warn on
      non-existent paths
- [ ] 3.4 Handle settings reload — re-build detector list on settings change

## 4. CLI: `squid why` enhancement

- [ ] 4.1 Update `squid why` subcommand to call each detector's
      `diagnostic()` and print a table of which detectors fired this tick
- [ ] 4.2 Add `squid why --json` for machine-readable output

## 5. docs/PRIVACY.md (new)

- [ ] 5.1 Per-detector table: reads vs does-NOT-read (from design.md D5)
- [ ] 5.2 "Squid makes zero network calls" + `lsof` verification command
- [ ] 5.3 Opt-out instructions: edit settings.json OR `squid disable git`
- [ ] 5.4 Threat model section: what an attacker with access to ~/.squid-pet/
      could learn (just mtime timestamps, no contents)

## 6. Tests (~25 new)

- [ ] 6.1 `tests/test_detectors_tpa.py` — 5 tests porting existing
      watcher tests to the new detector class
- [ ] 6.2 `tests/test_detectors_git.py` — mock os.stat on temp .git dirs;
      test HEAD mtime triggers celebrating, index mtime triggers busy,
      cache works, 50-repo cap respected
- [ ] 6.3 `tests/test_detectors_terminal.py` — mock psutil.process_iter
      with synthetic shell trees; test child-age threshold, multiple shells
- [ ] 6.4 `tests/test_detectors_ide.py` — mock psutil + tmp project dir
      with file touches; test CPU + mtime combinations, is_grooving burst
- [ ] 6.5 `tests/test_watcher_multidetector.py` — StateMachine with mixed
      detector signals: TPA off + git fires → celebrating; all off → idle
- [ ] 6.6 `tests/test_settings_triggers.py` — defaults applied when missing,
      reload reconfigures detectors, invalid project_dir warns
- [ ] 6.7 Verify 121 existing tests still pass unchanged

## 7. First-run wizard integration (depends on distribution-installer)

- [ ] 7.1 In install.sh `first_run_wizard()`, probe `pgrep -f tpa`;
      if absent, default `triggers.tpa=false`
- [ ] 7.2 Add wizard prompts for `triggers.git`, `triggers.ide`,
      `triggers.terminal` (default Y for all)
- [ ] 7.3 Add wizard prompt for `triggers.project_dirs` (default `~/Projects`)

## 8. Commit + memory

- [ ] 8.1 Commit detectors.py + tests together (one commit)
- [ ] 8.2 Commit watcher.py refactor (one commit, MUST keep tests green)
- [ ] 8.3 Commit docs/PRIVACY.md (one commit)
- [ ] 8.4 Commit wizard integration (one commit, after distribution-installer
      has landed)
- [ ] 8.5 Push to origin + internal
- [ ] 8.6 File kennel memory: "trigger broadening shipped; squid now works
      for non-TPA Mac engineers; opt-out via settings.triggers.*"
- [ ] 8.7 Update pink-pm-memory.md squid entry with new detector list
