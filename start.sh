#!/bin/bash

echo "🔥 Starting Rogue Nomad Bot..."
echo "📱 Bot: @roguenomad_bot"
echo "🔗 Channel: https://t.me/+ckfO94UHyhllODg0"
echo "👤 Admin: 1875307475"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 is not installed. Please install Python 3.11 or higher."
    exit 1
fi

echo "📦 Installing dependencies..."
pip install -r requirements.txt

if [ ! -f "bot.py" ]; then
    echo "❌ bot.py not found! Make sure you're in the right directory."
    exit 1
fi

echo "🚀 Bot is starting..."
python3 bot.py
