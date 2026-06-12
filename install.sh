#!/usr/bin/env bash
# Install Indigo Pet as a LaunchAgent (auto-start at login)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.pink.indigo-pet.plist"
SRC_PLIST="$PROJECT_DIR/$PLIST_NAME"
DEST_PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"

if [ ! -f "$SRC_PLIST" ]; then
  echo "❌ plist not found at $SRC_PLIST"; exit 1
fi

if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
  echo "❌ venv not set up. Run:"
  echo "   cd $PROJECT_DIR && uv venv && uv pip install psutil pywebview"
  exit 1
fi

# Unload existing
if launchctl list | grep -q "com.pink.indigo-pet"; then
  echo "→ Unloading existing LaunchAgent…"
  launchctl unload "$DEST_PLIST" 2>/dev/null || true
fi

cp "$SRC_PLIST" "$DEST_PLIST"
echo "✓ Copied plist to $DEST_PLIST"

launchctl load "$DEST_PLIST"
echo "✓ Loaded LaunchAgent"

sleep 1
if launchctl list | grep -q "com.pink.indigo-pet"; then
  echo "✓ Indigo is running 💙"
  echo ""
  echo "  Logs:  tail -f /tmp/indigo-pet.out.log /tmp/indigo-pet.err.log"
  echo "  Stop:  launchctl unload $DEST_PLIST"
else
  echo "⚠️  LaunchAgent loaded but not in running list."
  echo "    Check: tail /tmp/indigo-pet.err.log"
fi
