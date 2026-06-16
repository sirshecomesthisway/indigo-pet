# Tasks — distribution-installer

## 1. install.sh (root, new)

- [ ] 1.1 Shebang `#!/usr/bin/env bash`, `set -euo pipefail`, color helpers
- [ ] 1.2 `preflight()` — check macOS ≥12 (`sw_vers -productVersion`), git
      present, brew present; helpful error+exit for each missing dep
- [ ] 1.3 `ensure_uv()` — `command -v uv || brew install uv`
- [ ] 1.4 `clone_or_update()` — if `~/Projects/squid-pet` exists, `cd` + `git pull`;
      else `git clone https://github.com/sirshecomesthisway/squid-pet.git`
      (HTTPS, not SSH — VPN blocks github.com:22)
- [ ] 1.5 `setup_venv()` — `uv venv` if missing
- [ ] 1.6 `install_package()` — `uv pip install -e . --index-url
      https://pypi.internal.example.com/artifactory/api/pypi/external-pypi/simple
      --allow-insecure-host pypi.internal.example.com`
- [ ] 1.7 `migrate_legacy()` — if `~/.indigo-pet/` exists AND `~/.squid-pet/`
      missing, `cp -a` over; print migration notice
- [ ] 1.8 `render_plist()` — substitute `__HOME__` and `__PROJECT__` in
      template, write to `~/Library/LaunchAgents/com.pink.squid-pet.plist`
- [ ] 1.9 `install_launcher()` — copy `bin/squid` to `~/.local/bin/squid` +
      `chmod +x`; warn if `~/.local/bin` not on PATH
- [ ] 1.10 `first_run_wizard()` — skip if `~/.squid-pet/settings.json` exists OR
      `[ ! -t 0 ]` (non-interactive); else prompt corner/stroll/spaces; write
      `~/.squid-pet/settings.json`
- [ ] 1.11 `boot_launchd()` — `launchctl bootout` (cleanup) then `launchctl
      bootstrap gui/$(id -u) <plist>`
- [ ] 1.12 `verify_alive()` — poll `~/.squid-pet/state.json` mtime for up to
      10s; succeed when timestamp is fresh
- [ ] 1.13 `permission_walkthrough()` — print Accessibility checklist + `open`
      URL to System Settings; wait for Enter (or auto-continue after 30s if
      non-TTY)
- [ ] 1.14 `print_summary()` — squid CLI cheatsheet, log paths, uninstall hint

## 2. uninstall.sh (root, new)

- [ ] 2.1 Argument parsing: `--yes` (skip prompts), `--all` (also remove
      ~/.squid-pet + project dir)
- [ ] 2.2 `confirm(question, default)` helper — TTY check + read
- [ ] 2.3 Stop Squid: `launchctl bootout gui/$(id -u)/com.pink.squid-pet`,
      verify no `python -m squid_pet` procs remain
- [ ] 2.4 Remove plist if user confirms
- [ ] 2.5 Remove ~/.local/bin/squid + backward-compat ~/.local/bin/indigo symlink
- [ ] 2.6 Remove ~/.squid-pet (default NO; sticky default)
- [ ] 2.7 Remove ~/Projects/squid-pet (default NO; sticky default)
- [ ] 2.8 Cleanup /tmp/squid-pet.{out,err}.log
- [ ] 2.9 Print "Squid uninstalled. Thanks for trying her!"

## 3. bin/squid (launcher, modify existing)

- [ ] 3.1 Add `update` subcommand: `git pull`, `uv pip install -e .`,
      `launchctl kickstart -k gui/$(id -u)/com.pink.squid-pet`
- [ ] 3.2 Add `uninstall` subcommand that execs `~/Projects/squid-pet/uninstall.sh "$@"`
- [ ] 3.3 Update `help` case to list new commands
- [ ] 3.4 Fix existing `status` health check: detect launchd-managed pids
      (current script only looks at /tmp/squid-pet.log, but launchd writes
      to /tmp/squid-pet.out.log — different file, mtime-comparison fails)

## 4. launchagent/ (templates, new layout)

- [ ] 4.1 Create `launchagent/com.pink.squid-pet.plist.template` with
      `__HOME__` and `__PROJECT__` placeholders
- [ ] 4.2 Delete `launchagent/install.sh` (consolidated into root install.sh)
- [ ] 4.3 Delete `launchagent/com.pink.indigo-pet.plist` (stale, wrong name)
- [ ] 4.4 Verify the rendered plist matches what Pink has running today

## 5. docs/INSTALL.md (new)

- [ ] 5.1 Manual install steps (numbered, each maps to one install.sh function)
- [ ] 5.2 "What install.sh modifies on your system" — exhaustive list:
      `~/Projects/squid-pet/`, `~/.squid-pet/`, `~/Library/LaunchAgents/...plist`,
      `~/.local/bin/squid`, `/tmp/squid-pet.*.log`
- [ ] 5.3 Troubleshooting: SSH port-22 blocked (use HTTPS), missing
      `~/.local/bin` on PATH, Accessibility not granted, multiple Squids
      running, `uv` not found
- [ ] 5.4 "How to verify install was clean" — checksum/diff hints

## 6. README.md (revamp)

- [ ] 6.1 Move install section to top, immediately after title/tagline
- [ ] 6.2 Show one-line curl install command (fenced bash)
- [ ] 6.3 Show one-line uninstall command
- [ ] 6.4 Link to `docs/INSTALL.md` for manual install
- [ ] 6.5 Move architecture, states, contributing further down
- [ ] 6.6 Add "Requirements" callout: macOS 12+, corporate VPN, brew/uv

## 7. Verification

- [ ] 7.1 Run `uninstall.sh --yes --all` on Pink's machine (snapshot first)
- [ ] 7.2 Run new `install.sh` end-to-end; verify <120s wall-clock to live Squid
- [ ] 7.3 Run `install.sh` again — verify idempotent (no dup Squids, no perms re-prompt)
- [ ] 7.4 Run `squid update` — verify Squid restarts cleanly
- [ ] 7.5 Run `squid uninstall` — verify all install artifacts gone
- [ ] 7.6 121/121 tests still pass (no runtime regression)
- [ ] 7.7 README curl one-liner copy-pastes cleanly into a fresh terminal

## 8. Commit + memory

- [ ] 8.1 One commit per logical group above (install.sh / uninstall.sh /
      launcher updates / template / docs / README)
- [ ] 8.2 Push to origin (HTTPS) + internal (SSH)
- [ ] 8.3 File kennel memory documenting the install pipeline + gotchas hit
- [ ] 8.4 Update `pink-pm-memory.md` squid-pet entry: add "install: curl
      https://.../install.sh | bash" and "uninstall: squid uninstall"
