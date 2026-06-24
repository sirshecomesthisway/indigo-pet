# squid-pet Manual Install + Troubleshooting

For most users, the one-liner in the README is what you want:

```bash
curl -fsSL https://internal-ghe.example.com/raw/pink/squid-pet/main/install.sh | bash
```

This page is for the paranoid (who want to see every step) and the unlucky
(whose install broke somewhere).

## Manual install (step-by-step)

Each step maps to one function in `install.sh` — you can compare against the
script if anything looks off.

### 1. Preflight

```bash
sw_vers -productVersion        # need 12.0 or higher
git --version                  # need any modern git
brew --version                 # need Homebrew (https://brew.sh)
```

If `brew` is missing, install it first. The squid installer will NOT install
Homebrew for you (security: it's a one-line curl|bash too, but you should
opt into that explicitly).

### 2. Install `uv`

```bash
brew install uv
```

If your shell can't reach `formulae.brew.sh`, prefix with internal proxies:

```bash
HTTP_PROXY=http://sysproxy.wal-mart.com:8080 \
HTTPS_PROXY=http://sysproxy.wal-mart.com:8080 \
brew install uv
```

### 3. Clone the repo

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://internal-ghe.example.com/pink/squid-pet.git
cd squid-pet
```

**Important:** clone via HTTPS, NOT SSH. corporate VPN blocks `github.com:22` so
`git clone git@internal-ghe.example.com...:` will hang forever.

### 4. Make the venv

```bash
uv venv
```

### 5. Install the package (editable)

```bash
uv pip install -e . \
    --index-url https://pypi.internal.example.com/artifactory/api/pypi/external-pypi/simple \
    --allow-insecure-host pypi.internal.example.com
```

### 6. Migrate legacy settings (only if you ever ran the old indigo-pet)

```bash
if [ -d ~/.indigo-pet ] && [ ! -d ~/.squid-pet ]; then
    cp -a ~/.indigo-pet ~/.squid-pet
fi
mkdir -p ~/.squid-pet/logs
```

### 7. Render the LaunchAgent plist

```bash
PROJECT="$HOME/Projects/squid-pet"
sed "s|__PROJECT__|$PROJECT|g; s|__HOME__|$HOME|g" \
    "$PROJECT/launchagent/com.pink.squid-pet.plist.template" \
    > ~/Library/LaunchAgents/com.pink.squid-pet.plist

# Sanity check:
plutil -lint ~/Library/LaunchAgents/com.pink.squid-pet.plist
```

### 8. Install the launcher

```bash
mkdir -p ~/.local/bin
cp ~/Projects/squid-pet/bin/squid ~/.local/bin/squid
chmod +x ~/.local/bin/squid

# Make sure ~/.local/bin is on your PATH:
echo $PATH | grep -q "$HOME/.local/bin" || \
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

### 9. Write initial settings (or run wizard)

If you don't have `~/.squid-pet/settings.json` yet, the installer's wizard
prompts you. Manually:

```bash
cat > ~/.squid-pet/settings.json <<EOF
{
  "stroll_mode": "edges",
  "starting_corner": "bottom-right",
  "show_on_all_spaces": true,
  "triggers": {
    "tpa": true,
    "git": true,
    "terminal": true,
    "ide": true
  }
}
EOF
```

### 10. Boot the LaunchAgent

```bash
launchctl bootout gui/$(id -u)/com.pink.squid-pet 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pink.squid-pet.plist
```

### 11. Verify she's alive

```bash
squid status
```

Should report `RUNNING` and `watcher: TICKING (state.json updated Ns ago)`.

### 12. Grant macOS Accessibility (optional but recommended)

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
```

Drag the venv python binary into the allowed list. Squid works without it
(she falls back to a less-clever positioning mode), but with it she sits
above other windows and receives clicks more reliably.

## What `install.sh` modifies on your system

Exhaustive list. Nothing else.

| Path | What | Created/modified by |
|---|---|---|
| `~/Projects/squid-pet/` | Source clone + venv | git clone, uv venv, uv pip install |
| `~/.squid-pet/` | Settings, state.json, logs | wizard (settings.json), watcher (state.json, logs/) |
| `~/Library/LaunchAgents/com.pink.squid-pet.plist` | LaunchAgent definition | render_plist (sed substitution from template) |
| `~/.local/bin/squid` | Launcher CLI | install_launcher (cp + chmod) |
| `/tmp/squid-pet.out.log`, `/tmp/squid-pet.err.log` | Daemon stdout/stderr | launchd (per StandardOutPath / StandardErrorPath in plist) |

No system-wide files, no `/etc/`, no `sudo` calls, no rcfiles other than the
`~/.zshrc` PATH note above (which is suggested, not auto-applied).

## Troubleshooting

### `git clone` hangs forever

You probably used SSH. corporate VPN blocks `github.com:22`. Use HTTPS:

```bash
git clone https://internal-ghe.example.com/pink/squid-pet.git
```

### `uv: command not found` after install

`brew install uv` succeeded but the new binary isn't on your PATH. Either
restart your shell or add Homebrew's bin to PATH:

```bash
eval "$(brew shellenv)"   # adds /opt/homebrew/bin (Apple Silicon) or /usr/local/bin (Intel)
```

### `~/.local/bin/squid: command not found` after install

`~/.local/bin` isn't on your PATH. Add it:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
exec zsh
```

### `squid status` says NOT LOADED

The plist failed to bootstrap. Check:

```bash
plutil -lint ~/Library/LaunchAgents/com.pink.squid-pet.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pink.squid-pet.plist
```

If `bootstrap` errors with `Bootstrap failed: 5: Input/output error`, you
already have a LaunchAgent loaded under that label. Bounce it:

```bash
launchctl bootout gui/$(id -u)/com.pink.squid-pet
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pink.squid-pet.plist
```

### `squid status` says STALE (watcher wedged)

The daemon is running but state.json hasn't updated in >5s. Run the
diagnostic to see which check fails:

```bash
squid doctor
```

If checks 4 (window visible) or 5 (not wedged) fail, restart:

```bash
squid restart
```

### Accessibility prompt never appeared

macOS only shows the prompt on first run. Open the settings pane manually:

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
```

Drag your venv's python into the allowed list:
`~/Projects/squid-pet/.venv/bin/python`

### Multiple Squids running at once

You shouldn't be able to hit this (Squid has an `fcntl.flock()` singleton
guard), but if you do:

```bash
pkill -f 'python.*-m squid_pet'
squid restart
```

### How to verify the install was clean

After running `install.sh`, every artifact should match the table above
and nothing else should have been touched. Quick check:

```bash
# These should be the ONLY squid-related files:
ls -la ~/Library/LaunchAgents/ | grep squid
ls -la ~/.local/bin/squid
ls -la ~/.squid-pet/
ls /tmp/squid-pet.*.log

# No /etc/ touched:
sudo find /etc -name '*squid*' -mtime -1 2>/dev/null   # should be empty

# No new homebrew formulas other than uv:
brew list --installed-on-request | tail -5
```

## Reporting bugs

Open an issue at https://internal-ghe.example.com/pink/squid-pet/issues
or ping Pink in `#squid-pet` on Slack.

Include the output of:

```bash
squid doctor --doctor-json
squid why --why-json
```

Both are designed to be safe to paste — they don't contain file contents,
prompt text, or anything private.
