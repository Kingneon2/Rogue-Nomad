```bash
#!/bin/bash

# Rogue Nomad - Start Script

echo "🔥 Starting Rogue Nomad Bot..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found!"
    echo "Copy .env.example to .env and add your bot token."
    exit 1
fi

# Load environment variables
source .env

# Check if BOT_TOKEN is set
if [ -z "$BOT_TOKEN" ] || [ "$BOT_TOKEN" = "8279300523:AAGC71G8Dd9QmmF2Yhn6MUSTKq7i-4q6p7w" ]; then
    echo "❌ BOT_TOKEN not set in .env file!"
    exit 1
fi

# Install dependencies if needed
pip install -r requirements.txt

# Run the bot
echo "🚀 Bot is running..."
python bot.py
```

Make it executable:

```bash
chmod +x start.sh
```

---
