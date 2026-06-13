#!/usr/bin/env bash
# Install Squid's LaunchAgent so she auto-starts on login.
#
# Usage:
#   ./launchagent/install.sh         # install + load
#   ./launchagent/install.sh status  # check whether loaded
#   ./launchagent/install.sh remove  # unload + delete

set -euo pipefail

PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/com.pink.indigo-pet.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.pink.indigo-pet.plist"
LABEL="com.pink.indigo-pet"

cmd="${1:-install}"

case "$cmd" in
  install)
    if [[ ! -f "$PLIST_SRC" ]]; then
      echo "ERROR: source plist not found at $PLIST_SRC" >&2
      exit 1
    fi
    # Unload first if already present (idempotent reinstall).
    if launchctl list | grep -q "$LABEL"; then
      echo "-- unloading existing agent"
      launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi
    echo "-- copying plist to $PLIST_DST"
    cp "$PLIST_SRC" "$PLIST_DST"
    echo "-- loading"
    launchctl load "$PLIST_DST"
    echo "-- done. Status:"
    launchctl list | grep "$LABEL" || echo "(not yet visible -- give it a sec)"
    ;;
  status)
    launchctl list | grep "$LABEL" || { echo "not loaded"; exit 1; }
    ;;
  remove)
    if [[ -f "$PLIST_DST" ]]; then
      launchctl unload "$PLIST_DST" 2>/dev/null || true
      rm "$PLIST_DST"
      echo "-- removed $PLIST_DST"
    else
      echo "-- nothing to remove ($PLIST_DST not found)"
    fi
    ;;
  *)
    echo "Usage: $0 [install|status|remove]" >&2
    exit 1
    ;;
esac
