#!/usr/bin/env bash
# install.sh -- one-shot installer for squid-pet
#
# Usage:
#   ./install.sh                 # interactive (recommended)
#   ./install.sh --non-interactive  # skip wizard + perms walkthrough (CI/curl)
#
# What this does (in order):
#   1.  preflight              macOS 12+, git, brew
#   2.  ensure_uv              brew install uv if missing
#   3.  clone_or_update        ~/Projects/squid-pet up to date (HTTPS, not SSH)
#   4.  setup_venv             uv venv in project
#   5.  install_package        uv pip install -e . (internal artifactory)
#   6.  migrate_legacy         copy ~/.indigo-pet -> ~/.squid-pet if needed
#   7.  render_plist           substitute __PROJECT__ in template
#   8.  install_launcher       bin/squid -> ~/.local/bin/squid
#   9.  first_run_wizard       prompt corner/stroll if no settings.json
#   10. boot_launchd           bootout + bootstrap (idempotent)
#   11. verify_alive           poll state.json mtime <=10s
#   12. permission_walkthrough open Accessibility settings pane
#   13. print_summary          cheatsheet
#
# Re-runnable (idempotent): existing repo gets git pull, existing plist gets
# replaced, existing settings.json is honored as-is.

set -euo pipefail

# ─── color helpers ────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
    C_CYA=$'\033[36m'; C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
    C_RED=''; C_GRN=''; C_YEL=''; C_CYA=''; C_BLD=''; C_RST=''
fi
step() { echo "${C_CYA}${C_BLD}==> $*${C_RST}"; }
ok()   { echo "${C_GRN}[ok]${C_RST} $*"; }
warn() { echo "${C_YEL}[!!]${C_RST} $*"; }
die()  { echo "${C_RED}[XX]${C_RST} $*" >&2; exit 1; }

# ─── configuration ────────────────────────────────────────────────────
LABEL="com.pink.squid-pet"
PROJECT="${SQUID_PROJECT:-$HOME/Projects/squid-pet}"
REPO_URL="${SQUID_REPO:-https://internal-ghe.example.com/pink/squid-pet.git}"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LAUNCHER_DST="$HOME/.local/bin/squid"
STATE_FILE="$HOME/.squid-pet/state.json"
SETTINGS_FILE="$HOME/.squid-pet/settings.json"

NON_INTERACTIVE=0
for arg in "$@"; do
    case "$arg" in
        --non-interactive|--yes|-y) NON_INTERACTIVE=1 ;;
        -h|--help)
            head -25 "$0" | tail -22 | sed 's/^# \{0,1\}//'
            exit 0
            ;;
    esac
done

# Non-interactive if no TTY (e.g. curl|bash)
if [ ! -t 0 ]; then NON_INTERACTIVE=1; fi

# ─── 1. preflight ─────────────────────────────────────────────────────
preflight() {
    step "preflight"
    if [ "$(uname)" != "Darwin" ]; then
        die "macOS required (this is $(uname))."
    fi
    local ver maj
    ver=$(sw_vers -productVersion)
    maj=${ver%%.*}
    if [ "$maj" -lt 12 ]; then
        die "macOS 12+ required (you're on $ver). NSWindow APIs squid uses need it."
    fi
    ok "macOS $ver"
    command -v git >/dev/null || die "git missing. Install Xcode CLT: xcode-select --install"
    ok "git $(git --version | cut -d' ' -f3)"
    command -v brew >/dev/null || die "Homebrew missing. Install from https://brew.sh first."
    ok "brew $(brew --version | head -1 | cut -d' ' -f2)"
}

# ─── 2. ensure_uv ─────────────────────────────────────────────────────
ensure_uv() {
    step "ensure uv"
    if command -v uv >/dev/null; then
        ok "uv $(uv --version | cut -d' ' -f2) already installed"
        return
    fi
    warn "uv not found -- installing via brew (this can take ~30s)"
    HTTP_PROXY=http://sysproxy.wal-mart.com:8080 \
    HTTPS_PROXY=http://sysproxy.wal-mart.com:8080 \
        brew install uv || die "brew install uv failed"
    ok "uv installed"
}

# ─── 3. clone_or_update ───────────────────────────────────────────────
clone_or_update() {
    step "clone_or_update -> $PROJECT"
    mkdir -p "$(dirname "$PROJECT")"
    if [ -d "$PROJECT/.git" ]; then
        ok "repo exists, pulling latest"
        (cd "$PROJECT" && git pull --ff-only) || \
            warn "git pull failed (local changes?) -- continuing with current checkout"
    else
        if [ -e "$PROJECT" ] && [ ! -d "$PROJECT/.git" ]; then
            die "$PROJECT exists but is not a git repo. Move it aside first."
        fi
        # HTTPS only -- corporate VPN blocks github.com:22 (SSH).
        git clone "$REPO_URL" "$PROJECT" || \
            die "git clone failed. Are you on corporate VPN? Repo: $REPO_URL"
        ok "cloned $REPO_URL"
    fi
}

# ─── 4. setup_venv ────────────────────────────────────────────────────
setup_venv() {
    step "setup_venv"
    if [ -x "$PROJECT/.venv/bin/python" ]; then
        ok "venv exists at $PROJECT/.venv"
        return
    fi
    (cd "$PROJECT" && uv venv) || die "uv venv failed"
    ok "venv created"
}

# ─── 5. install_package ───────────────────────────────────────────────
install_package() {
    step "install_package (editable, internal artifactory)"
    (cd "$PROJECT" && uv pip install -e . \
        --index-url https://pypi.internal.example.com/artifactory/api/pypi/external-pypi/simple \
        --allow-insecure-host pypi.internal.example.com \
    ) || die "uv pip install failed. Are you on corporate VPN?"
    ok "squid_pet installed in $PROJECT/.venv"
}

# ─── 6. migrate_legacy ────────────────────────────────────────────────
migrate_legacy() {
    step "migrate_legacy"
    if [ -d "$HOME/.indigo-pet" ] && [ ! -d "$HOME/.squid-pet" ]; then
        warn "found legacy ~/.indigo-pet/ -- migrating to ~/.squid-pet/"
        cp -a "$HOME/.indigo-pet" "$HOME/.squid-pet"
        ok "settings migrated (original left in place; you can rm -rf ~/.indigo-pet later)"
    else
        ok "nothing to migrate"
    fi
    mkdir -p "$HOME/.squid-pet/logs"
}

# ─── 7. render_plist ──────────────────────────────────────────────────
render_plist() {
    step "render_plist -> $PLIST_DST"
    local template="$PROJECT/launchagent/com.pink.squid-pet.plist.template"
    [ -f "$template" ] || die "template missing: $template"
    mkdir -p "$(dirname "$PLIST_DST")"
    sed "s|__PROJECT__|$PROJECT|g; s|__HOME__|$HOME|g" "$template" > "$PLIST_DST"
    ok "plist rendered"
}

# ─── 8. install_launcher ──────────────────────────────────────────────
install_launcher() {
    step "install_launcher -> $LAUNCHER_DST"
    mkdir -p "$(dirname "$LAUNCHER_DST")"
    cp "$PROJECT/bin/squid" "$LAUNCHER_DST"
    chmod +x "$LAUNCHER_DST"
    ok "launcher installed"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) : ;;
        *) warn "~/.local/bin is not on your PATH. Add to ~/.zshrc:"
           warn "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
}

# ─── 9. first_run_wizard ──────────────────────────────────────────────
first_run_wizard() {
    step "first_run_wizard"
    if [ -f "$SETTINGS_FILE" ]; then
        ok "settings.json exists, leaving alone"
        return
    fi
    if [ "$NON_INTERACTIVE" = 1 ]; then
        warn "non-interactive: writing default settings.json"
        cat > "$SETTINGS_FILE" <<EOF
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
        ok "default settings written"
        return
    fi
    echo ""
    echo "Quick setup (3 questions, defaults are sensible):"
    local corner stroll spaces
    read -r -p "  starting corner [bottom-right] (top-left|top-right|bottom-left|bottom-right): " corner
    corner=${corner:-bottom-right}
    read -r -p "  stroll mode [edges] (edges|free|still): " stroll
    stroll=${stroll:-edges}
    read -r -p "  show on all spaces (y/N): " spaces
    case "${spaces:-n}" in y|Y|yes|YES) spaces=true ;; *) spaces=false ;; esac
    cat > "$SETTINGS_FILE" <<EOF
{
  "stroll_mode": "$stroll",
  "starting_corner": "$corner",
  "show_on_all_spaces": $spaces,
  "triggers": {
    "tpa": true,
    "git": true,
    "terminal": true,
    "ide": true
  }
}
EOF
    ok "settings.json written"
}

# ─── 10. boot_launchd ─────────────────────────────────────────────────
boot_launchd() {
    step "boot_launchd"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true   # ignore "not loaded"
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" || \
        die "launchctl bootstrap failed. Check syntax: plutil -lint $PLIST_DST"
    ok "LaunchAgent loaded"
}

# ─── 11. verify_alive ─────────────────────────────────────────────────
verify_alive() {
    step "verify_alive (polling state.json for up to 10s)"
    local i mtime now age
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if [ -f "$STATE_FILE" ]; then
            mtime=$(stat -f %m "$STATE_FILE")
            now=$(date +%s)
            age=$((now - mtime))
            if [ "$age" -le 3 ]; then
                ok "squid is alive (state.json updated ${age}s ago)"
                return 0
            fi
        fi
        sleep 1
    done
    warn "state.json not fresh after 10s. Check: tail /tmp/squid-pet.err.log"
    return 1
}

# ─── 12. permission_walkthrough ───────────────────────────────────────
permission_walkthrough() {
    step "permission_walkthrough"
    cat <<'PERMS'
Squid needs a couple macOS perms for the best experience:

  1. Accessibility   -- so she can sit above other windows + receive clicks
                        (the wanderer falls back gracefully without it)
  2. Input Monitoring -- only if you want her to wake from sleep when you
                         touch the keyboard. Optional.

Opening System Settings -> Privacy & Security -> Accessibility now...
PERMS
    if [ "$NON_INTERACTIVE" != 1 ]; then
        open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" || true
        echo ""
        read -r -p "Press Enter once you've granted the permission (or skip with Ctrl-C)..." _
    else
        warn "non-interactive: skipping perms walkthrough"
    fi
}

# ─── 13. print_summary ────────────────────────────────────────────────
print_summary() {
    step "summary"
    cat <<SUMMARY

${C_GRN}${C_BLD}Squid is installed.${C_RST}

  ${C_BLD}CLI cheatsheet${C_RST}
    squid status        is she alive? is the watcher ticking?
    squid why           why is she in this state? which detector fired?
    squid doctor        6-check self-diagnostic
    squid restart       atomic bounce
    squid update        git pull + reinstall + restart
    squid uninstall     get rid of her cleanly
    squid logs -f       tail stdout+stderr live

  ${C_BLD}Files${C_RST}
    project:  $PROJECT
    plist:    $PLIST_DST
    settings: $SETTINGS_FILE
    state:    $STATE_FILE
    logs:     /tmp/squid-pet.{out,err}.log

  ${C_BLD}Privacy${C_RST}
    Squid scans local processes, CPU%, and file mtimes only. No network
    calls. Full disclosure: $PROJECT/docs/PRIVACY.md

Have fun!
SUMMARY
}

# ─── main ─────────────────────────────────────────────────────────────
main() {
    echo "${C_BLD}squid-pet installer${C_RST}"
    echo ""
    preflight
    ensure_uv
    clone_or_update
    setup_venv
    install_package
    migrate_legacy
    render_plist
    install_launcher
    first_run_wizard
    boot_launchd
    verify_alive || true   # don't fail install if first tick is slow
    permission_walkthrough
    print_summary
}
main "$@"
