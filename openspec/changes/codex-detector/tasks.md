# Tasks — codex-detector

## 1. detectors.py: shared file-write signal

- [x] 1.1 Extract `_scan_recent_file_ages(project_dirs, window_sec, ...)`
      as a module-level helper from `IDEDetector._default_recent_files`
      (behavior-preserving refactor)
- [x] 1.2 `ClaudeCodeDetector` — add `project_dirs` param, `file_active`
      field (gated on process presence), OR'd into `is_busy()`
- [x] 1.3 `CodexDetector` — same, from the start

## 2. detectors.py: CodexDetector

- [x] 2.1 Add `CODEX_SESSIONS_DIR` constant (`~/.codex/sessions`)
- [x] 2.2 Implement `CodexDetector` mirroring `ClaudeCodeDetector`:
      process presence, `shell_active`, `file_active`, recursive
      `**/*.jsonl` transcript discovery (60s cache, 15min candidate cap)
- [x] 2.3 `diagnostic()` — `{name, enabled, codex_running, cpu_percent,
      shell_active, file_active, transcript_age, streaming}`
- [x] 2.4 Add `"codex": True` to `DEFAULT_TRIGGERS`
- [x] 2.5 `build_detectors()` — instantiate `CodexDetector` with
      `project_dirs` passed through

## 3. watcher.py

- [x] 3.1 Extract `_find_processes_by_argv0_basename(names)`, refactor
      `find_claude_code_processes` to use it
- [x] 3.2 Add `find_codex_processes()` matching `{"codex", "codex-tui"}`,
      excluding headless subcommands (`app-server`, `exec`,
      `exec-server`, `mcp`, `mcp-server`) -- found live on this machine:
      a third-party tool runs a vendored `codex app-server` as a
      backend component, which would otherwise false-positive
      `codex_running`
- [x] 3.3 `PetState` — add `codex_running: bool = False`
- [x] 3.4 `StateMachine._refresh_tpa_detector_ref()` — resolve
      `self._codex_detector`
- [x] 3.5 `StateMachine._other_detectors()` — exclude `codex` by name too
- [x] 3.6 `_compute_inner()` — codex scan block (mirrors claude block);
      `any_agent_running` / `working_evidence_merged` /
      `streaming_merged` extended to 3-way OR; `_working_reason()` /
      `_streaming_reason()` helpers replace the old 2-way inline ternary;
      `st.codex_running` set

## 4. install.sh

- [x] 4.1 Add `codex_default="true"`, `"codex": ${codex_default}` to
      generated `settings.json`
- [x] 4.2 Add wizard prompt for `triggers.codex`, mirroring `claude_code`

## 5. Tests

- [x] 5.1 `tests/test_detectors_codex.py` — mirrors
      `test_detectors_claude_code.py` (13 tests, including nested-date
      transcript discovery and the file-write signal)
- [x] 5.2 `tests/test_find_codex_processes.py` — cmdline-basename
      matching, node-shim-parent non-match, no cross-match with claude
- [x] 5.3 `tests/test_watcher_codex_cascade.py` — codex-only
      working/thinking/idle, file-write → working, both Claude Code and
      Codex wired simultaneously
- [x] 5.4 Regression tests for the file-write fix in both
      `test_watcher_claude_code_cascade.py` and
      `test_watcher_codex_cascade.py`
- [x] 5.5 Fixed hermetic-test contamination: existing
      `ClaudeCodeDetector`-constructing tests didn't inject
      `recent_file_ages_fn`, so the new default silently walked the
      real `~/Projects` (this repo) during test runs — fixed to pass
      `recent_file_ages_fn=lambda: []`
      (or empty `file_ages`) explicitly
- [x] 5.6 Updated detector-count tests (`test_settings_triggers.py`,
      `test_settings_hot_reload.py`) for the 6th detector
- [x] 5.7 Full suite green (362 tests)

## 6. Docs

- [x] 6.1 `docs/PRIVACY.md` — `CodexDetector` row + file-write-signal
      row on `ClaudeCodeDetector`; corrected the process-match
      description (cmdline, not `Process.name()`)
- [x] 6.2 README — Detectors table, states table, architecture diagram,
      state.json schema, test count

## 7. Portability audit (bundled — same "usable by anyone" motivation)

- [x] 7.1 `docs/INSTALL.md` — removed corporate VPN/artifactory/internal GHE
      instructions, public GitHub + PyPI throughout
- [x] 7.2 `docs/PRIVACY.md` — fixed dead `internal-ghe.example.com` issue
      link
- [x] 7.3 `install.sh` — fixed residual corporate-VPN error message on
      git-clone failure, stale SSH-default comment
