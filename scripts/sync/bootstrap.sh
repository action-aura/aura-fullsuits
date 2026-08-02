#!/usr/bin/env bash
# aura-sync bootstrap -- one command, first-time setup on this device.
# Checks git identity, runs one real sync pass so you see it work, then installs
# the cron schedule. Re-runnable safely (each step is idempotent).
#
# Usage: ./bootstrap.sh [interval_minutes]

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$SCRIPT_DIR/aura-sync.sh"
INTERVAL_MINUTES="${1:-5}"

echo "aura-sync bootstrap"
echo ""

echo "1/3 checking git identity..."
NAME="$(git config user.name 2>/dev/null)"
EMAIL="$(git config user.email 2>/dev/null)"
if [ -z "$NAME" ] || [ -z "$EMAIL" ]; then
    echo "  missing. Set it first, then re-run bootstrap:"
    echo '    git config --global user.name "Your Name"'
    echo '    git config --global user.email "you@example.com"'
    exit 1
fi
echo "  ok: $NAME <$EMAIL>"

echo ""
echo "2/3 running first sync pass..."
"$ENGINE" --once
rc=$?
if [ "$rc" -eq 3 ]; then
    echo ""
    echo "First pass hit a conflict or blocking issue (see message above / .autosync/CONFLICT.md)."
    echo "Resolve it, then re-run bootstrap to finish installing the schedule."
    exit 1
fi
if [ "$rc" -ge 4 ]; then
    echo ""
    echo "First pass failed on a config/setup problem (see message above). Fix it, then re-run bootstrap."
    exit 1
fi

echo ""
echo "3/3 installing cron schedule (every $INTERVAL_MINUTES min)..."
"$ENGINE" --install --interval-minutes "$INTERVAL_MINUTES"

echo ""
echo "Done. This device now auto-syncs every $INTERVAL_MINUTES min."
echo "Check any time with:  ./scripts/sync/aura-sync.sh --status"
echo "Pause before risky work with:  ./scripts/sync/aura-sync.sh --pause --minutes 30"
