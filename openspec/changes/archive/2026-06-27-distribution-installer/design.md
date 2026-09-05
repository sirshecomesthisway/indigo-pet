# Design — distribution-installer

## Install flow (high-level)

```mermaid
graph TD
    A[curl install.sh | bash] --> B{preflight}
    B -->|macOS &lt; 12| Z1[abort: unsupported]
    B -->|no git| Z2[abort: install Xcode CLT]
    B -->|no brew| Z3[abort: install brew first]
    B -->|no uv| C[brew install uv]
    B -->|all ok| D
    C --> D[clone or git pull squid-pet]
    D --> E[uv venv .venv]
    E --> F[uv pip install -e .]
    F --> G{~/.indigo-pet/ exists?}
    G -->|yes| H[cp -a to ~/.squid-pet/]
    G -->|no| I
    H --> I[first-run wizard if no settings.json]
    I --> J[render templated plist to ~/Library/LaunchAgents/]
    J --> K[install ~/.local/bin/squid]
    K --> L[launchctl bootstrap]
    L --> M[poll state.json for liveness up to 10s]
    M --> N[open Accessibility pane + print checklist]
    N --> O[print success + squid --help]
```

## D1: install.sh is a shell script, not a Python tool

Bootstrapping Python tooling FROM Python is a chicken-egg trap (user has no
venv yet, system Python may be wrong version). The installer must run in plain
zsh/bash with only POSIX utilities + `curl` + `git` + `brew` (which the company
engineers already have via Homebrew or AI Launchpad onboarding). Once `uv venv`
is created, all heavy lifting moves into Python land via the package's existing
entry points.

## D2: launchd plist is generated, not committed verbatim

Hardcoding `/Users/pink/Projects/squid-pet/.venv/bin/python` in a checked-in
plist worked for Pink alone but breaks immediately when anyone else installs.
The repo will contain `launchagent/com.pink.squid-pet.plist.template`:

```xml
<string>__PROJECT__/.venv/bin/python</string>
<string>__HOME__/Library/LaunchAgents/com.pink.squid-pet.plist</string>
```

`install.sh` does `sed -e "s|__HOME__|$HOME|g" -e "s|__PROJECT__|$PROJECT_DIR|g"
template > ~/Library/LaunchAgents/com.pink.squid-pet.plist`. The on-disk plist
is per-user; the template is the source of truth.

## D3: Settings dir migration is one-way and lossless

If `~/.indigo-pet/` exists at install time, `cp -a` (preserves perms, mtimes)
to `~/.squid-pet/` then leave `~/.indigo-pet/` untouched. Don't `mv` — if a
later process is mid-write to the old dir (unlikely but possible), the user
can recover. The post-install hint message tells the user to `rm -rf
~/.indigo-pet/` once they've verified Squid is happy. Tests run on fresh
`~/.squid-pet/` so migration is non-blocking for new users.

## D4: First-run wizard is optional, defaults are reasonable

If `~/.squid-pet/settings.json` already exists (upgrade case OR repeat
install), skip the wizard. Otherwise prompt with sensible defaults shown in
brackets:

```
Starting corner [top-right]:
Stroll mode (edges/anywhere) [edges]:
Show on all Spaces (y/N) [y]:
```

Hitting Enter accepts the default. Non-interactive shells (curl-piped, CI)
detect `[ ! -t 0 ]` and skip the wizard entirely (use defaults). This means
the curl one-liner Just Works for the impatient.

## D5: Permission walkthrough opens System Settings, doesn't try to grant

Apple TCC explicitly forbids programmatic grants without a code-signing
entitlement. install.sh prints:

```
Squid needs Accessibility permission to track cursor position for
click-passthrough. Opening System Settings now…

  1. Find 'squid-pet' (or 'Python') in the Accessibility list
  2. Toggle it ON
  3. If it's not there, click '+' and add:
     ~/Projects/squid-pet/.venv/bin/python

Press Enter once granted (or Ctrl-C to skip and grant later).
```

Then runs `open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"`.
This is the cleanest UX achievable without code signing.

## D6: `squid update` is git-based, not package-based

`uv pip install -U squid-pet` would require us to publish to a Python index
(PyPI or internal artifactory). For v1, install IS a git clone, so update IS
a `git pull`. Future versions can add `squid update --channel pypi` once we
have a publishing pipeline. Update flow:

```bash
cd ~/Projects/squid-pet
git pull origin main || { echo "pull failed; abort"; exit 1; }
uv pip install -e . --quiet
launchctl kickstart -k gui/$(id -u)/com.pink.squid-pet
```

`kickstart -k` is the launchd primitive for "kill + restart this label" — it
preserves the existing job definition. Zero-downtime modulo the ~2s WKWebView
startup.

## D7: Uninstall is interactive by default, `--yes` for scripted

`uninstall.sh` confirms each destructive step unless `--yes` is passed:

```
Stop Squid? [Y/n]
Remove launchd plist (~/Library/LaunchAgents/com.pink.squid-pet.plist)? [Y/n]
Remove CLI launcher (~/.local/bin/squid)? [Y/n]
Remove settings + state (~/.squid-pet/)? [y/N]    # default NO — preserve user data
Remove project directory (~/Projects/squid-pet)? [y/N]    # default NO
```

The settings-and-project defaults are NO because they contain user state
(stroll preference, sprite customizations if Pink ships those). Sticky default
matches the principle of least surprise.

## Decisions

### D1: install.sh in shell, not Python
Avoid chicken-egg; assume only `zsh + curl + git + brew` exist.

### D2: Generated plist with `__HOME__` / `__PROJECT__` templating
Per-user paths must be baked at install time.

### D3: `cp -a` migration from `~/.indigo-pet/`, don't delete source
Preserve fallback until user verifies.

### D4: Interactive wizard only on TTY, defaults otherwise
Curl one-liner stays one-liner.

### D5: Open System Settings, don't auto-grant TCC
Apple forbids it without code signing.

### D6: `squid update` = `git pull + reinstall + kickstart`
Defer pypi publishing until v2.

### D7: Uninstall preserves user data by default
Sticky default for `~/.squid-pet/` and project dir is NO.
