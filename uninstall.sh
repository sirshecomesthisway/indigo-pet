#!/usr/bin/env bash
# Stop and remove Indigo Pet LaunchAgent
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.pink.indigo-pet.plist"
[ -f "$PLIST" ] && launchctl unload "$PLIST" 2>/dev/null || true
[ -f "$PLIST" ] && rm -f "$PLIST"
pkill -f "indigo_pet" 2>/dev/null || true
echo "✓ Indigo uninstalled. Goodbye for now 👋"
