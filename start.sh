#!/bin/bash

echo "🔥 Starting Rogue Nomad Bot..."
echo "📱 Bot: @roguenomad_bot"
echo "🔗 Channel: https://t.me/+ckfO94UHyhllODg0"
echo "👤 Admin: 1875307475"
echo ""

# Install dependencies
pip install -r requirements.txt

# Run the bot
python bot.py

chmod +x start.sh

./start.sh
