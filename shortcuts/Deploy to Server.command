#!/bin/bash
echo "🚀 Deploying SaltStocks to server..."
ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=10 holly@162.0.222.94 ./deploy.sh
echo ""
echo "Press any key to close."
read -n 1
