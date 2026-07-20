# Rogue Nomad - Premium Checker Bot

**The Ultimate Telegram Checker Bot**

[![Join our community](https://img.shields.io/badge/Join-Telegram-blue)](https://t.me/+ckfO94UHyhllODg0)
[![Bot](https://img.shields.io/badge/Bot-@roguenomad_bot-blue)](https://t.me/roguenomad_bot)

## Features

- ✅ Multi-service credential checking
- ✅ Batch processing with file upload
- ✅ Proxy rotation with intelligent scoring
- ✅ Real-time statistics
- ✅ Group chat support
- ✅ Admin controls
- ✅ Persistent database storage
- ✅ Async processing for speed

## Supported Services

| Category | Services |
|----------|----------|
| 📺 Streaming | Crunchyroll, Netflix, DAZN, Spotify |
| 🔒 VPN/Proxy | ExpressVPN, NordVPN |
| 🧠 AI | OpenAI |

## Commands

- `/start` - Welcome message
- `/checkers` - Show all checker categories
- `/proxy` - Proxy management
- `/stats` - Show statistics
- `/help` - Help menu
- `/about` - About Rogue Nomad
- `/stopcheck` - Stop current check

## Admin Commands

- `/admin` - Admin panel
- `/broadcast` - Send message to all users
- `/resetstats` - Reset all statistics

## Quick Deploy on Render

1. Fork this repository
2. Go to [render.com](https://render.com)
3. Click "New +" → "Web Service"
4. Connect your GitHub repo
5. Click "Deploy"

## Local Development

```bash
# Clone the repo
git clone https://github.com/yourusername/rogue-nomad.git
cd rogue-nomad

# Install dependencies
pip install -r requirements.txt

# Run the bot
python bot.py
