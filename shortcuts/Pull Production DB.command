#!/bin/bash
echo "⬇️  Pulling production database from server..."
scp holly@162.0.222.94:~/saltstocks/data/saltstocks.db "$(dirname "$0")/../data/saltstocks.db"
echo ""
echo "✅ Done! Local DB updated."
echo "Press any key to close."
read -n 1
