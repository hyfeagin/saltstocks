#!/bin/bash
# Push the local database to the VPS, replacing production.
# The app is stopped before the copy and restarted after so no writes land
# on the old file mid-transfer.
set -e

REMOTE_USER_HOST="holly@162.0.222.94"
REMOTE_DB="~/saltstocks/data/saltstocks.db"
LOCAL="$(dirname "$0")/data/saltstocks.db"

echo "Backing up server DB before overwrite..."
BACKUP="~/saltstocks/data/saltstocks.db.bak-$(date +%Y%m%d-%H%M%S)"
ssh "$REMOTE_USER_HOST" "cp ~/saltstocks/data/saltstocks.db $BACKUP"
echo "  Server backup saved to $BACKUP"

echo "Stopping app on server..."
ssh -t "$REMOTE_USER_HOST" "sudo systemctl stop saltstocks"

echo "Pushing local DB to server..."
scp "$LOCAL" "${REMOTE_USER_HOST}:${REMOTE_DB}"

echo "Restarting app on server..."
ssh -t "$REMOTE_USER_HOST" "sudo systemctl start saltstocks"

echo "Done! Production DB updated."
