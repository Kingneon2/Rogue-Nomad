```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rogue Nomad - Admin Commands
Separate file for admin functionality
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot import checker_engine, proxy_manager, get_db

logger = logging.getLogger(__name__)
ADMIN_ID = 1875307475

async def is_admin(user_id: int) -> bool:
    """Check if a user is admin."""
    return user_id == ADMIN_ID

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel."""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📊 Full Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("🌐 Proxy Management", callback_data="admin_proxy")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔄 Reset Stats", callback_data="admin_reset")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_start")]
    ]
    
    await update.message.reply_text(
        "🔐 *Admin Panel*\n\n"
        "Welcome to the admin panel, Butter.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a broadcast message to all users."""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("📢 Usage: /broadcast <message>")
        return
    
    message = " ".join(context.args)
    
    # Get all unique users from database
    async with get_db() as db:
        cursor = await db.execute("SELECT DISTINCT user_id FROM checks")
        users = await cursor.fetchall()
    
    sent = 0
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0],
                text=f"📢 *Broadcast from Admin*\n\n{message}",
                parse_mode=ParseMode.MARKDOWN
            )
            sent += 1
            await asyncio.sleep(0.1)  # Rate limit
        except:
            pass
    
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")

async def reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset all statistics."""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    # Reset in-memory stats
    checker_engine.stats = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "errors": 0,
        "by_service": {}
    }
    
    # Reset database stats
    async with get_db() as db:
        await db.execute("DELETE FROM checks")
        await db.execute("DELETE FROM user_stats")
        await db.execute("DELETE FROM global_stats")
        await db.commit()
    
    await update.message.reply_text("✅ All statistics have been reset.")
```
