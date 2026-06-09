#!/bin/bash
# Copy a local Python script to the server and run it there.
# Usage: ./run-on-server.sh backfill_ebay_entries.py
set -e

REMOTE="holly@162.0.222.94"
SCRIPT="$1"

if [ -z "$SCRIPT" ]; then
  echo "Usage: $0 <script.py>"
  exit 1
fi

echo "Copying $(basename "$SCRIPT") to server..."
scp "$SCRIPT" "${REMOTE}:~/saltstocks/"

echo "Running on server..."
ssh -t "$REMOTE" "cd ~/saltstocks && source .venv/bin/activate && python $(basename "$SCRIPT")"
