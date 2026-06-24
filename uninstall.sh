#!/usr/bin/env bash
# uninstall.sh -- remove squid-pet from this Mac.
#
# Usage:
#   ./uninstall.sh           # interactive (asks before removing settings + project)
#   ./uninstall.sh --yes     # skip confirmations (defaults: NO to settings/project)
#   ./uninstall.sh --all     # also remove ~/.squid-pet AND ~/Projects/squid-pet
#   ./uninstall.sh --yes --all  # nuke everything, no prompts
#
# What gets removed unconditionally:
#   - LaunchAgent loaded state (launchctl bootout)
#   - ~/Library/LaunchAgents/com.pink.squid-pet.plist
#   - ~/.local/bin/squid launcher
#   - /tmp/squid-pet.{out,err}.log
#
# What gets prompted (default NO):
#   - ~/.squid-pet/  (settings, state, logs)
#   - ~/Projects/squid-pet/  (source + venv)

set -euo pipefail

if [ -t 1 ]; then
    C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
    C_CYA=$'\033[36m'; C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
    C_RED=''; C_GRN=''; C_YEL=''; C_CYA=''; C_BLD=''; C_RST=''
fi
step() { echo "${C_CYA}${C_BLD}==> $*${C_RST}"; }
ok()   { echo "${C_GRN}[ok]${C_RST} $*"; }
warn() { echo "${C_YEL}[!!]${C_RST} $*"; }
skip() { echo "${C_YEL}[--]${C_RST} $*"; }

LABEL="com.pink.squid-pet"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LAUNCHER="$HOME/.local/bin/squid"
SETTINGS_DIR="$HOME/.squid-pet"
PROJECT="${SQUID_PROJECT:-$HOME/Projects/squid-pet}"
OUT_LOG="/tmp/squid-pet.out.log"
ERR_LOG="/tmp/squid-pet.err.log"

# ─── arg parsing ──────────────────────────────────────────────────────
YES=0
ALL=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes)   YES=1 ;;
        -a|--all)   ALL=1 ;;
        -h|--help)
            head -20 "$0" | tail -17 | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "uninstall.sh: unknown arg '$arg'" >&2; exit 2 ;;
    esac
done

# Non-interactive if no TTY
if [ ! -t 0 ]; then YES=1; fi

# ─── confirm helper ───────────────────────────────────────────────────
# confirm "question" "default-y|n"  -> returns 0 if yes, 1 if no
confirm() {
    local question="$1"
    local default="${2:-n}"
    local reply prompt
    if [ "$YES" = 1 ]; then
        # In --yes mode, --all overrides defaults; otherwise honor default
        if [ "$ALL" = 1 ]; then return 0; fi
        [ "$default" = "y" ] && return 0 || return 1
    fi
    if [ "$default" = "y" ]; then prompt="[Y/n]"; else prompt="[y/N]"; fi
    read -r -p "  $question $prompt: " reply
    reply=${reply:-$default}
    case "$reply" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# ─── 1. stop squid ────────────────────────────────────────────────────
stop_squid() {
    step "stop squid"
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || \
            warn "bootout returned non-zero (may already be gone)"
        ok "LaunchAgent unloaded"
    else
        skip "LaunchAgent not loaded"
    fi
    # Belt-and-suspenders: kill any lingering python -m squid_pet that escaped launchd
    local pids
    pids=$(pgrep -f 'python.*-m squid_pet' 2>/dev/null || true)
    if [ -n "$pids" ]; then
        warn "killing orphan squid_pet processes: $pids"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 1
        # shellcheck disable=SC2086
        kill -9 $pids 2>/dev/null || true
    fi
}

# ─── 2. remove plist ──────────────────────────────────────────────────
remove_plist() {
    step "remove plist"
    if [ -f "$PLIST" ]; then
        rm "$PLIST"
        ok "removed $PLIST"
    else
        skip "plist not present"
    fi
}

# ─── 3. remove launcher ───────────────────────────────────────────────
remove_launcher() {
    step "remove launcher"
    if [ -f "$LAUNCHER" ]; then
        rm "$LAUNCHER"
        ok "removed $LAUNCHER"
    else
        skip "launcher not present"
    fi
    # Back-compat: an old indigo install may have left this around
    if [ -L "$HOME/.local/bin/indigo" ] || [ -f "$HOME/.local/bin/indigo" ]; then
        rm "$HOME/.local/bin/indigo"
        ok "removed legacy ~/.local/bin/indigo"
    fi
}

# ─── 4. remove logs ───────────────────────────────────────────────────
remove_logs() {
    step "remove logs"
    rm -f "$OUT_LOG" "$ERR_LOG"
    ok "/tmp/squid-pet.*.log cleaned"
}

# ─── 5. remove settings dir (prompted, default NO) ────────────────────
remove_settings() {
    step "remove ~/.squid-pet/"
    if [ ! -d "$SETTINGS_DIR" ]; then
        skip "~/.squid-pet/ not present"
        return
    fi
    if confirm "delete ~/.squid-pet/ (settings, state, logs)?" "n"; then
        rm -rf "$SETTINGS_DIR"
        ok "removed $SETTINGS_DIR"
    else
        skip "kept $SETTINGS_DIR (your settings survive)"
    fi
}

# ─── 6. remove project dir (prompted, default NO) ─────────────────────
remove_project() {
    step "remove $PROJECT/"
    if [ ! -d "$PROJECT" ]; then
        skip "$PROJECT not present"
        return
    fi
    if confirm "delete $PROJECT/ (source + venv + your git history)?" "n"; then
        rm -rf "$PROJECT"
        ok "removed $PROJECT"
    else
        skip "kept $PROJECT (rerun with --all to nuke)"
    fi
}

# ─── 7. summary ───────────────────────────────────────────────────────
summary() {
    echo ""
    echo "${C_GRN}${C_BLD}Squid uninstalled.${C_RST} Thanks for trying her!"
    echo ""
    if [ -d "$SETTINGS_DIR" ]; then
        echo "  Settings preserved at: $SETTINGS_DIR"
        echo "  Source preserved at:   $PROJECT (if you kept it)"
        echo ""
        echo "  Reinstall any time: cd $PROJECT && ./install.sh"
    fi
}

# ─── main ─────────────────────────────────────────────────────────────
echo "${C_BLD}squid-pet uninstaller${C_RST}"
[ "$YES" = 1 ] && echo "  (non-interactive mode$([ "$ALL" = 1 ] && echo ", --all"))"
echo ""
stop_squid
remove_plist
remove_launcher
remove_logs
remove_settings
remove_project
summary
