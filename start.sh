#!/bin/bash
# Alternative shell script entry point for Discord Bot Server deployment
# This provides additional flexibility for different deployment platforms

echo "🚀 Discord Bot Server - Shell Script Entry Point"
echo "📍 Script: start.sh"
echo "🔄 Launching: python main.py"

# Change to script directory
cd "$(dirname "$0")"

# Install dependencies if needed and start the application
pip install discord.py flask psutil requests 2>/dev/null || true
exec python main.py