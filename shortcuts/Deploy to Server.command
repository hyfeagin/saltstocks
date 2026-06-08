#!/bin/bash
echo "🚀 Deploying SaltStocks to server..."
ssh holly@162.0.222.94 ./deploy.sh
echo ""
echo "Press any key to close."
read -n 1
