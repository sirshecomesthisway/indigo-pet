#!/usr/bin/env bash
# install.sh -- one-shot installer for squid-pet
#
# Usage:
#   ./install.sh                 # interactive (recommended)
#   ./install.sh --non-interactive  # skip wizard + perms walkthrough (CI/curl)
#   ./install.sh --wizard        # prompt for starting corner / stroll mode
#   ./install.sh --profile       # capture per-stage timing -> /tmp + summary
#
# What this does (in order):
#   1.  preflight              macOS 12+, git, brew
#   2.  ensure_uv              brew install uv if missing
#   3.  clone_or_update        ~/Projects/squid-pet up to date (HTTPS)
#   4.  setup_venv             uv venv in project
#   5.  install_package        uv pip install -e . (public PyPI)
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
REPO_URL="${SQUID_REPO:-https://github.com/sirshecomesthisway/squid-pet.git}"

# post-e2e-polish 2026-06-27 Fix 3: detect cold vs warm install at script start
# so verify_alive can use the right polling timeout. Cold = first ever install
# (no state.json yet). Warm = reinstall.
if [ -f "$HOME/.squid-pet/state.json" ]; then
    WAS_COLD="false"
else
    WAS_COLD="true"
fi
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LAUNCHER_DST="$HOME/.local/bin/squid"
STATE_FILE="$HOME/.squid-pet/state.json"
SETTINGS_FILE="$HOME/.squid-pet/settings.json"

NON_INTERACTIVE=0
RUN_WIZARD=0
PROFILE=0
for arg in "$@"; do
    case "$arg" in
        --non-interactive|--yes|-y) NON_INTERACTIVE=1 ;;
        --wizard)                   RUN_WIZARD=1 ;;
        --profile)                  PROFILE=1 ;;
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
    if command -v brew >/dev/null; then ok "brew $(brew --version | head -1 | cut -d' ' -f2)"; else warn "brew not found -- only needed if uv must be installed"; fi
    # done (real ok-line is in the if-branch above)                   
    true
}

# ─── 2. ensure_uv ─────────────────────────────────────────────────────
ensure_uv() {
    # Phase 2: silent fast-path when uv is already on PATH (no header, no [ok])
    if command -v uv >/dev/null; then
        return
    fi
    step "ensure uv"
    if ! command -v brew >/dev/null; then                   
        die "uv missing AND brew missing. Install uv via: curl -LsSf https://astral.sh/uv/install.sh | sh, then re-run"
    fi                                                                
    warn "uv not found -- installing via brew (this can take ~30s)"
    brew install uv || die "brew install uv failed"
    ok "uv installed"
}

# ─── 3. clone_or_update ───────────────────────────────────────────────
clone_or_update() {
    step "clone_or_update -> $PROJECT"
    mkdir -p "$(dirname "$PROJECT")"

    # post-e2e-polish 2026-06-27 Fix 5: if user already cloned to a non-canonical
    # location and is running ./install.sh from inside it, MOVE the repo to
    # $PROJECT rather than re-clone. Avoids needless network round-trip AND the
    # surprise of two squid-pet checkouts on disk.
    if [ ! -d "$PROJECT/.git" ] && [ -d "$PWD/.git" ]; then
        local pwd_origin
        pwd_origin=$(cd "$PWD" && git remote get-url origin 2>/dev/null || echo "")
        case "$pwd_origin" in
            *squid-pet*)
                if [ "$PWD" != "$PROJECT" ]; then
                    ok "found squid-pet clone at $PWD -> moving to canonical $PROJECT"
                    mv "$PWD" "$PROJECT" || die "failed to relocate $PWD to $PROJECT (perms? existing dir?)"
                    cd "$PROJECT" || die "failed to cd into $PROJECT after move"
                fi
                ;;
        esac
    fi

    if [ -d "$PROJECT/.git" ]; then
        # Phase 2: skip the git pull entirely when local HEAD already
        # matches origin/main. Saves a multi-second network round-trip
        # on warm installs. ls-remote is one round-trip; pull is two
        # plus full pack data for any new commits.
        local local_head remote_head
        local_head=$(cd "$PROJECT" && git rev-parse HEAD 2>/dev/null || echo "")
        remote_head=$(cd "$PROJECT" && git ls-remote origin main 2>/dev/null | cut -f1 || echo "")
        if [ -n "$local_head" ] && [ "$local_head" = "$remote_head" ]; then
            ok "repo is up to date (HEAD matches origin/main, skipped pull)"
        else
            ok "repo exists, pulling latest"
            (cd "$PROJECT" && git pull --ff-only) || \
                warn "git pull failed (local changes?) -- continuing with current checkout"
        fi
    else
        if [ -e "$PROJECT" ] && [ ! -d "$PROJECT/.git" ]; then
            die "$PROJECT exists but is not a git repo. Move it aside first."
        fi
        # Public HTTPS clone by default; override with SQUID_REPO=git@host:you/squid-pet.git
        # to install from your own fork over SSH.
        git clone "$REPO_URL" "$PROJECT" || \
            die "git clone failed. Check your network, or override the source with SQUID_REPO=<url>. Tried: $REPO_URL"
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
    # Phase 2: prefer `uv sync --frozen` when uv.lock exists. Lockfile-driven
    # installs skip dependency resolution entirely (the slow part on cold
    # cache). Fall back to pip install -e only if the lockfile is missing or
    # out of sync (which would be a maintainer bug, not a user problem --
    # they should regenerate uv.lock).
    if [ -f "$PROJECT/uv.lock" ]; then
        step "install_package (uv sync --frozen, lockfile-driven)"
        (cd "$PROJECT" && uv sync --frozen) || {
            warn "uv sync --frozen failed -- lockfile may be out of date"
            warn "Falling back to uv pip install -e . (this resolves + downloads, can take a few min)"
            (cd "$PROJECT" && uv pip install -e .) || die "uv pip install also failed."
            warn "Maintainer: regenerate uv.lock with 'uv lock' and commit it"
        }
        ok "squid_pet installed (from lockfile) in $PROJECT/.venv"
    else
        step "install_package (uv pip install -e ., no lockfile)"
        warn "uv.lock missing -- using slow resolver path. Run 'uv lock' to fix."
        (cd "$PROJECT" && uv pip install -e .) || die "uv pip install failed."
        ok "squid_pet installed in $PROJECT/.venv"
    fi
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
    step "write_settings"
    if [ -f "$SETTINGS_FILE" ]; then
        ok "settings.json already exists, leaving alone"
        return
    fi

    # claude-code-detector / codex-detector: neither has an observed
    # misfire risk (see detectors.py's ClaudeCodeDetector/CodexDetector),
    # so both default on unconditionally -- there's no cost to leaving
    # them on when the tool isn't running (they just report nothing).
    #
    # Pink-2026-08-27: there used to be a third probe+prompt here for a
    # "tpa" trigger (TPA, a separate CLI coding agent this
    # project originally watched). TPADetector was removed
    # entirely 2026-08-22/27 -- TPA was never actually installed/
    # run on this machine, so it never fired anything in practice, and
    # detectors.py no longer has a "tpa" trigger key to read at
    # all. Squid's approval-needed "flag wave" is still available for
    # TPA via its own sitecustomize-driven signal, independent of
    # this triggers block -- see docs/PRIVACY.md.
    local claude_default="true"
    local codex_default="true"

    # trigger-broadening 7.3: default project_dirs to ~/Projects (matches the
    # canonical clone location from distribution-installer Phase 5).
    local proj_default="$HOME/Projects"

    # Default to silent + sensible. Power users can opt into interactive
    # configuration with --wizard, or edit ~/.squid-pet/settings.json
    # later (changes are picked up live -- no restart needed).
    #
    # NOTE: terminal defaults to false here to match detectors.py's
    # DEFAULT_TRIGGERS (documented there as "off by default: misfires on
    # any dev machine" -- any long-lived shell child, e.g. an editor or a
    # long-running REPL, makes the naive terminal detector permanently
    # report busy).
    cat > "$SETTINGS_FILE" <<EOF
{
  "stroll_mode": "edges",
  "starting_corner": "bottom-right",
  "show_on_all_spaces": true,
  "triggers": {
    "claude_code": ${claude_default},
    "codex": ${codex_default},
    "git": true,
    "terminal": false,
    "ide": true,
    "project_dirs": ["${proj_default}"]
  }
}
EOF
    if [ "$RUN_WIZARD" = 1 ] && [ "$NON_INTERACTIVE" != 1 ]; then
        echo ""
        echo "Optional setup (Enter = keep default):"
        local corner stroll spaces
        read -r -p "  starting corner [bottom-right]: " corner
        read -r -p "  stroll mode [edges] (edges|free|still): " stroll
        read -r -p "  show on all spaces [y]: " spaces
        # trigger-broadening 7.2: trigger prompts (default Y for all)
        local trig_claude trig_codex trig_git trig_ide trig_term trig_proj
        read -r -p "  trigger: react to Claude Code activity [$claude_default]: " trig_claude
        read -r -p "  trigger: react to Codex activity [$codex_default]: " trig_codex
        read -r -p "  trigger: react to git activity (commits, refs) [y]: " trig_git
        read -r -p "  trigger: react to IDE focus (VSCode, IntelliJ) [y]: " trig_ide
        read -r -p "  trigger: react to terminal activity [n]: " trig_term
        # trigger-broadening 7.3: project_dirs prompt
        read -r -p "  project dirs to watch for git activity [$proj_default]: " trig_proj
        python3 - "$SETTINGS_FILE" "${corner:-}" "${stroll:-}" "${spaces:-}" "${trig_claude:-}" "${trig_codex:-}" "${trig_git:-}" "${trig_ide:-}" "${trig_term:-}" "${trig_proj:-}" <<PYEOF2
import json, sys
fp = sys.argv[1]
corner, stroll, spaces, trig_claude, trig_codex, trig_git, trig_ide, trig_term, trig_proj = sys.argv[2:11]
with open(fp) as f: d = json.load(f)
if corner: d["starting_corner"] = corner
if stroll: d["stroll_mode"] = stroll
if spaces and spaces.lower() in ("n","no"): d["show_on_all_spaces"] = False
def _bool(s, default):
    s = s.strip().lower()
    if not s: return default
    return s in ("y","yes","true","1","on")
# Trigger overrides (only if user typed something)
if trig_claude.strip():
    d["triggers"]["claude_code"] = _bool(trig_claude, d["triggers"]["claude_code"])
if trig_codex.strip():
    d["triggers"]["codex"] = _bool(trig_codex, d["triggers"]["codex"])
if trig_git.strip():
    d["triggers"]["git"] = _bool(trig_git, d["triggers"]["git"])
if trig_ide.strip():
    d["triggers"]["ide"] = _bool(trig_ide, d["triggers"]["ide"])
if trig_term.strip():
    d["triggers"]["terminal"] = _bool(trig_term, d["triggers"]["terminal"])
if trig_proj.strip():
    # Allow comma-separated list of dirs
    dirs = [s.strip() for s in trig_proj.split(",") if s.strip()]
    d["triggers"]["project_dirs"] = dirs
with open(fp, "w") as f: json.dump(d, f, indent=2)
PYEOF2
        ok "settings.json written (interactive: triggers + project_dirs configured)"
    else
        ok "settings.json written (defaults: project_dirs=[$proj_default]). Edit ~/.squid-pet/settings.json to customize."
    fi
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
    # post-e2e-polish 2026-06-27 Fix 3: cold install needs ~10s (sprite load +
    # WKWebView spin-up on M1). Warm reinstall stays at 5s. WAS_COLD is set
    # at script start before any state.json gets written.
    local timeout_sec mode_label
    if [ "$WAS_COLD" = "true" ]; then
        timeout_sec=20
        mode_label="cold install, polling ${timeout_sec}s for first state.json write"
    else
        timeout_sec=5
        mode_label="warm reinstall, polling ${timeout_sec}s"
    fi
    step "verify_alive ($mode_label)"
    local i mtime now age
    i=0
    while [ "$i" -lt "$timeout_sec" ]; do
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
        i=$((i + 1))
    done
    warn "state.json not fresh after ${timeout_sec}s. Check: tail /tmp/squid-pet.err.log"
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
        echo "(System Settings should have opened in the background -- grant when convenient)"
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

# ─── install history (always on, lightweight) ────────────────────────
# Every install appends one ISO8601-stamped line to
#   ~/.squid-pet/logs/install-history.log
# so we can spot speed regressions over time. Format:
#   2026-06-24T18:21:30Z  31.56s  warm  c467139
INSTALL_T0_S=$(date +%s)
INSTALL_MODE=""   # set in main() before any stage runs

record_install_history() {
    local total_s=$(($(date +%s) - INSTALL_T0_S))
    local commit_sha
    commit_sha=$(cd "$PROJECT" 2>/dev/null && git rev-parse --short HEAD 2>/dev/null || echo "?")
    local logdir="$HOME/.squid-pet/logs"
    mkdir -p "$logdir" 2>/dev/null || return 0
    printf "%s  %ds  %s  %s\n" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$total_s" \
        "${INSTALL_MODE:-unknown}" \
        "$commit_sha" \
        >> "$logdir/install-history.log" 2>/dev/null || true
    # Print summary line (always, even without --profile)
    echo "${C_CYA}(install took ${total_s}s, mode=${INSTALL_MODE:-unknown}, recorded to $logdir/install-history.log)${C_RST}"
}

# ─── profile helpers (only active with --profile) ─────────────────────
# Floating-point wall time via python (BSD date has no %N).
__now_ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }

# Append-only profile data: each entry is "STAGE_NAME=DURATION_MS"
PROFILE_DATA=()
PROFILE_T0=0

time_stage() {
    local fn=$1
    if [ "$PROFILE" != 1 ]; then
        "$fn"
        return $?
    fi
    local t0 t1 rc
    t0=$(__now_ms)
    "$fn"
    rc=$?
    t1=$(__now_ms)
    PROFILE_DATA+=("$fn=$((t1 - t0))")
    return $rc
}

print_profile() {
    [ "$PROFILE" != 1 ] && return 0
    local total_ms=$(($(__now_ms) - PROFILE_T0))
    local ts
    ts=$(date -u +%Y%m%dT%H%M%SZ)
    local outfile="/tmp/squid-pet-install-profile-${ts}.txt"

    # Build report -- header + sorted rows + total -- via python (clean math)
    python3 - "$total_ms" "$outfile" "${PROFILE_DATA[@]}" <<'PYREPORT'
import sys
total = int(sys.argv[1])
outfile = sys.argv[2]
rows = []
for entry in sys.argv[3:]:
    name, dur = entry.split("=")
    dur = int(dur)
    rows.append((name, dur, 100.0 * dur / total if total else 0.0))
rows.sort(key=lambda r: -r[1])

lines = []
lines.append("")
lines.append("install profile  (per-stage wall time, sorted descending)")
lines.append("-" * 60)
lines.append(f"  {'STAGE':<28} {'DURATION':>12} {'%TOTAL':>8}")
lines.append("-" * 60)
for name, dur, pct in rows:
    if dur >= 1000:
        dur_s = f"{dur/1000:.2f} s"
    else:
        dur_s = f"{dur} ms"
    lines.append(f"  {name:<28} {dur_s:>12} {pct:>7.1f}%")
lines.append("-" * 60)
lines.append(f"  {'TOTAL':<28} {total/1000:>10.2f} s {100.0:>7.1f}%")
lines.append("")
lines.append(f"saved to: {outfile}")
lines.append("")

report = "\n".join(lines)
print(report)
with open(outfile, "w") as f:
    f.write(report)
PYREPORT
}

# ─── main ─────────────────────────────────────────────────────────────
main() {
    echo "${C_BLD}squid-pet installer${C_RST}"
    [ "$PROFILE" = 1 ] && echo "(profile mode: per-stage timing will print at end)"
    # Detect warm-vs-cold BEFORE any stage runs (after stages, .venv always exists)
    if [ -d "$PROJECT/.venv" ] && [ -f "$PROJECT/uv.lock" ]; then
        INSTALL_MODE="warm"
    else
        INSTALL_MODE="cold"
    fi
    echo ""
    PROFILE_T0=$(__now_ms 2>/dev/null || echo 0)
    time_stage preflight
    time_stage ensure_uv
    time_stage clone_or_update
    time_stage setup_venv
    time_stage install_package
    time_stage migrate_legacy
    time_stage render_plist
    time_stage install_launcher
    time_stage first_run_wizard
    time_stage boot_launchd
    time_stage verify_alive || true   # don't fail install if first tick is slow
    time_stage permission_walkthrough
    time_stage print_summary
    print_profile
    record_install_history
}
main "$@"
