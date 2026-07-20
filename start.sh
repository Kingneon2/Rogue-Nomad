#!/bin/bash

# ============================================
# ROGUE NOMAD - START SCRIPT
# ============================================

echo "🔥 Starting Rogue Nomad Bot..."
echo "📱 Bot: @roguenomad_bot"
echo "🔗 Channel: https://t.me/+ckfO94UHyhllODg0"
echo "👤 Admin: 1875307475"
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 is not installed. Please install Python 3.11 or higher."
    exit 1
fi

# Check if requirements are installed
echo "📦 Installing/Checking dependencies..."
pip install -r requirements.txt --quiet

# Check if bot.py exists
if [ ! -f "bot.py" ]; then
    echo "❌ bot.py not found! Make sure you're in the right directory."
    exit 1
fi

# Start the bot
echo "🚀 Bot is starting..."
python3 bot.py
