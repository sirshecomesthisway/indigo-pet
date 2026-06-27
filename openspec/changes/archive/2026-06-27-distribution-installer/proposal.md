# distribution-installer

## Why

Today Squid's "setup process" is whatever Indigo did interactively on Pink's
Mac on 2026-06-16. There is no documented install, no uninstall, no update,
no first-run config. The two existing scripts (`install.sh`,
`launchagent/install.sh`) only stamp the launchd plist and both still
reference the old `indigo-pet` name.

Pink wants Squid distributable to all internal Mac engineers. For that to be
safe the install must be: one-line curlable behind corporate VPN, idempotent
(re-runs upgrade without duplicating Squids), uninstallable (leaves the Mac
clean), updatable (`squid update` pulls + restarts), and honest about
macOS Accessibility (opens the right pane, tells the user what to click).

This change packages what already works. It does not touch runtime behavior.

## Goal

Ship a Mac engineer-grade install pipeline with `install.sh`, `uninstall.sh`,
and `squid update` as first-class commands. Replace stale `indigo-pet` install
scripts. Revamp README install section.

## Non-goals

- Windows support (separate `windows-port` change)
- Homebrew tap (separate `brew-tap` change, depends on this one)
- corporate MDM self-service packaging (defer until governance settled)
- Apple Developer code signing (use unsigned; user grants TCC manually)
- Trigger broadening for non-TPA users (separate `trigger-broadening` change)
- Telemetry, GUI installer, `.pkg` — CLI-only for v1

## What changes

- **`install.sh`** at repo root — preflight → clone-or-update → uv venv → install
  → migrate `~/.indigo-pet/` if present → templated launchd plist → CLI launcher
  → first-run config wizard → permissions walkthrough → health check → summary
- **`uninstall.sh`** at repo root — unload launchd, remove plist, launcher,
  settings dir (with confirmation), optional repo deletion
- **`squid update`** subcommand on launcher — `git pull && uv pip install -e .
  && launchctl kickstart -k`
- **`squid uninstall`** subcommand shells into `uninstall.sh`
- **First-run wizard** — prompts for starting corner, stroll mode, show-on-all-spaces;
  writes `~/.squid-pet/settings.json`
- **Templated plist** — `launchagent/com.pink.squid-pet.plist.template` with
  `__HOME__` / `__PROJECT__` placeholders the installer substitutes
- **`docs/INSTALL.md`** — auditable per-step manual install for the paranoid
- **README revamp** — top-of-README curl one-liner, architecture moves down
- **Delete** — stale `install.sh` (old root), `launchagent/install.sh`,
  `launchagent/com.pink.indigo-pet.plist`

## Success criteria

- Fresh Mac → living Squid in <120 s via curl one-liner
- Existing user → `install.sh` upgrades in-place without dup processes or lost settings
- `squid uninstall` → all three install artifacts gone (`~/.squid-pet/`, plist, launcher)
- No-Accessibility user gets a one-click `open` URL to the right settings pane
- 121/121 tests still pass — runtime unchanged
