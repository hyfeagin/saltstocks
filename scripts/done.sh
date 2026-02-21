#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/done.sh \"commit message\""
  exit 1
fi

MSG="$1"

# Ensure we're inside a git repo
git rev-parse --is-inside-work-tree >/dev/null

# Stage everything
git add -A

# If nothing staged, exit quietly
if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

# Commit + push current branch
git commit -m "$MSG"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git push -u origin "$BRANCH"

echo "✅ Committed & pushed on $BRANCH: $MSG"
