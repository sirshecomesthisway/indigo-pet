# Tasks — claude-code-detector

## 1. detectors.py

- [x] 1.1 Add `CLAUDE_PROJECTS_DIR` constant (`~/.claude/projects`)
- [x] 1.2 Implement `ClaudeCodeDetector` — process presence via injected
      `find_processes_fn`, `shell_active` via injected
      `has_active_shell_children_fn`, transcript-mtime discovery (60s
      cache, 15min candidate-age cap) via injected `glob_fn`/`stat_fn`.
      `is_busy = shell_active or streaming`; `is_celebrating`/
      `is_grooving` always `False`.
- [x] 1.3 `diagnostic()` — `{name, enabled, claude_code_running,
      cpu_percent, shell_active, transcript_age, streaming}`
- [x] 1.4 Add `"claude_code": True` to `DEFAULT_TRIGGERS`
- [x] 1.5 `build_detectors()` — instantiate `ClaudeCodeDetector(enabled=
      s.get("claude_code", True))`

## 2. watcher.py

- [x] 2.1 Add `find_claude_code_processes()` — cmdline-basename match
      (`psutil.Process.name()` empirically found unreliable for this
      binary on macOS — returns the versioned install path's basename,
      not "claude"; see design.md)
- [x] 2.2 `PetState` — add `claude_code_running: bool = False` field
- [x] 2.3 `StateMachine._refresh_tpa_detector_ref()` — also resolve
      `self._claude_detector` (by `name == "claude_code"`)
- [x] 2.4 `StateMachine._other_detectors()` — exclude `claude_code` by
      name (in addition to `tpa`)
- [x] 2.5 `_compute_inner()` — pull claude fields (scan-trigger pattern
      mirrors the existing TPA block); broaden branch 4's gate to
      `tpa_running or claude_code_running`; OR-merge
      `shell_active` / streaming signals per design.md's table; keep 4a
      (concerned) and the TPA-cpu-heuristic 4c explicitly TPA-only; set
      `st.claude_code_running`
- [x] 2.6 Verify `state.json` schema stays backward-compatible
      (`tpa_running` unchanged; new key is additive)

## 3. install.sh

- [x] 3.1 Fix hardcoded `"terminal": true` → `"terminal": false` in the
      generated `settings.json` (matches `DEFAULT_TRIGGERS`'s documented
      "off by default: misfires on any dev machine")
- [x] 3.2 Add `"claude_code": true` to the generated `settings.json`
- [x] 3.3 Add wizard prompt for `triggers.claude_code`, mirroring the
      existing `tpa` prompt

## 4. Tests

- [x] 4.1 `tests/test_detectors_claude_code.py` — no-process quiet,
      shell-child fires busy immediately, fresh-transcript fires
      streaming/busy, stale-transcript is quiet, disabled always False,
      diagnostic keys present, scan-dedupe-per-tick (mirrors
      `test_detectors_tpa.py` structure)
- [x] 4.2 Watcher-cascade test(s): Claude-only (TPA absent) with a live
      tool subprocess → `working`; Claude-only with fresh transcript, no
      subprocess → `thinking`; Claude-only with stale transcript → falls
      through to idle/other-detector fallback; TPA-only behavior
      unchanged when Claude detector absent/disabled
- [x] 4.3 Full suite green (307 existing + new)

## 5. Docs

- [x] 5.1 `docs/PRIVACY.md` — add `ClaudeCodeDetector` row to the
      per-detector reads/does-not-read table
