#!/bin/bash
echo "⚠️  This will OVERWRITE the production database on the server with your local copy."
echo "Are you sure? (y/n)"
read -n 1 confirm
echo ""
if [[ "$confirm" != "y" ]]; then
  echo "Cancelled."
  exit 0
fi

echo "Pushing local database to server..."
scp "$(dirname "$0")/../data/saltstocks.db" holly@162.0.222.94:~/saltstocks/data/saltstocks.db
ssh holly@162.0.222.94 "sudo systemctl restart saltstocks"
echo ""
echo "✅ Done! Production database updated."
echo "Press any key to close."
read -n 1
