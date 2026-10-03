#!/usr/bin/env python3
"""
Consolidated Train Bot - All-in-One Discord Bot
Complete Discord bot with message forwarding, dashboard, and comprehensive features.
Merges all components into a single file for easy deployment and management.
"""

import discord
from discord.ext import commands, tasks
# App commands removed to fix double messaging
import asyncio
import gc
import logging
import os
import sys
import threading
import time
import platform
import psutil
import html
import secrets
import hashlib
import hmac
import re
from functools import wraps
import traceback
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Union
from contextlib import contextmanager

# Flask and web components
from flask import Flask, render_template, render_template_string, jsonify, request, redirect
from werkzeug.middleware.proxy_fix import ProxyFix
import requests

# Database components
from sqlalchemy import Column, Integer, String, DateTime, Boolean, BigInteger, Text, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session
from sqlalchemy.pool import StaticPool

# Import database models
from models import TrainSchedule, TrainParticipant, SystemSettings, User, TrustedUser, TrustedRole, BannedUser, TwitchLinkRequest, ForwardingConfig, Channel, Guild, NotificationSettings, ServerFeaturePermissions

# ========================================
# LOGGING SETUP
# ========================================

def setup_logger():
    """Setup colored logging for the bot."""
    import logging
    import sys
    
    # Color codes for different log levels
    COLOR_CODES = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[32m',     # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[35m', # Magenta
        'RESET': '\033[0m'      # Reset
    }
    
    class ColoredFormatter(logging.Formatter):
        def format(self, record):
            log_color = COLOR_CODES.get(record.levelname, COLOR_CODES['RESET'])
            reset_color = COLOR_CODES['RESET']
            
            # Format the message with colors
            formatted_message = super().format(record)
            return f"{log_color}{formatted_message}{reset_color}"
    
    # Setup logger
    logger = logging.getLogger('discord_bot')
    logger.setLevel(getattr(logging, os.getenv('LOG_LEVEL', 'INFO')))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Create console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    
    # Create formatter
    formatter = ColoredFormatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

# ========================================
# RAID AND MEMORY MONITORING (Integrated)
# ========================================

active_raids = {}     # Stores active raid data per channel
running_tasks = {}    # Stores running asyncio tasks
shutting_down = False # Tracks shutdown state

async def memory_logger():
    """Logs memory usage every 60 seconds to detect leaks."""
    process = psutil.Process(os.getpid())
    while not shutting_down:
        try:
            gc.collect()
            mem_mb = process.memory_info().rss / 1024 ** 2
            logging.getLogger('discord_bot').info(f"[MEMORY CHECK] GC ran - Memory: {mem_mb:.2f} MB")
        except Exception as e:
            logging.getLogger('discord_bot').error(f"Error in memory logger: {e}")
        await asyncio.sleep(60)

async def start_raid(channel_name):
    """Starts a raid train for a channel."""
    if channel_name in active_raids:
        return
    active_raids[channel_name] = {
        "started_at": datetime.utcnow(),
        "attendance": set(),
        "messages_sent": 0
    }
    task = asyncio.create_task(raid_loop(channel_name))
    task.add_done_callback(lambda t: t.exception() if t.exception() else None)
    running_tasks[channel_name] = task

async def raid_loop(channel_name):
    """Runs automated messages during an active raid."""
    try:
        while channel_name in active_raids and not shutting_down:
            active_raids[channel_name]["messages_sent"] += 1
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.getLogger('discord_bot').error(f"Error in raid loop for {channel_name}: {e}")
        await end_raid(channel_name)

async def end_raid(channel_name):
    """Ends a raid and clears related memory."""
    task = running_tasks.pop(channel_name, None)
    if task:
        task.cancel()
    active_raids.pop(channel_name, None)
    gc.collect()

async def cleanup_stale_raids():
    """Automatically ends raids that have been active too long."""
    while not shutting_down:
        try:
            now = datetime.utcnow()
            for channel, data in list(active_raids.items()):
                if (now - data["started_at"]).total_seconds() > 3600:
                    await end_raid(channel)
        except Exception as e:
            logging.getLogger('discord_bot').error(f"Error in raid cleanup: {e}")
        await asyncio.sleep(300)

async def global_shutdown():
    """Gracefully shuts down the bot components."""
    global shutting_down
    shutting_down = True
    for task in running_tasks.values():
        task.cancel()
    running_tasks.clear()
    active_raids.clear()
    gc.collect()

# ========================================
# BOT CONFIGURATION
# ========================================

class BotConfig:
    """Bot configuration class."""
    
    # Bot settings
    COMMAND_PREFIX: str = os.getenv('COMMAND_PREFIX', '!')
    BOT_NAME: str = os.getenv('BOT_NAME', 'Train Bot')
    BOT_DESCRIPTION: str = os.getenv('BOT_DESCRIPTION', 'A comprehensive Discord bot with message forwarding')
    
    # Logging settings
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    
    # Bot permissions
    ADMIN_ROLES: List[str] = os.getenv('ADMIN_ROLES', '').split(',') if os.getenv('ADMIN_ROLES') else []
    MODERATOR_ROLES: List[str] = os.getenv('MODERATOR_ROLES', '').split(',') if os.getenv('MODERATOR_ROLES') else []
    
    # Feature flags
    ENABLE_MUSIC: bool = os.getenv('ENABLE_MUSIC', 'False').lower() == 'true'
    ENABLE_MODERATION: bool = os.getenv('ENABLE_MODERATION', 'True').lower() == 'true'
    ENABLE_ECONOMY: bool = os.getenv('ENABLE_ECONOMY', 'False').lower() == 'true'
    
    # API Keys
    WEATHER_API_KEY: str = os.getenv('WEATHER_API_KEY', '')
    YOUTUBE_API_KEY: str = os.getenv('YOUTUBE_API_KEY', '')
    
    # Database
    DATABASE_URL: str = os.getenv('DATABASE_URL', '')
    
    # Bot owner
    OWNER_ID_DISCORD: int = int(os.getenv('OWNER_ID_DISCORD', '887354716751810560'))
    
    # Message forwarding configuration
    SOURCE_CHANNEL_ID: str = os.getenv('SOURCE_CHANNEL_ID', '1398094211320119358')
    TARGET_CHANNEL_ID: str = os.getenv('TARGET_CHANNEL_ID', '1183143967622168668')
    AUTHORIZED_ROLE_NAME: str = os.getenv('AUTHORIZED_ROLE_NAME', 'supervisor')
    AUTHORIZED_USER_IDS: str = os.getenv('AUTHORIZED_USER_IDS', '887354716751810560')
    
    @classmethod
    def get_required_env_vars(cls) -> List[str]:
        """Get list of required environment variables."""
        return ['DISCORD_BOT_TOKEN']
    
    @classmethod
    def validate_config(cls) -> bool:
        """Validate that all required configuration is present."""
        missing_vars = []
        
        for var in cls.get_required_env_vars():
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            print(f"Missing required environment variables: {', '.join(missing_vars)}")
            return False
        
        return True

# ========================================
# DATABASE MODELS
# ========================================

Base = declarative_base()

class Guild(Base):
    """Represents a Discord guild/server."""
    __tablename__ = 'guilds'
    __table_args__ = {'extend_existing': True}
    
    id = Column(BigInteger, primary_key=True)
    name = Column(String(100), nullable=False)
    owner_id = Column(BigInteger, nullable=False)
    member_count = Column(Integer, default=0)
    joined_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    server_description = Column(Text, nullable=True)
    
    live_role_id = Column(BigInteger, nullable=True)
    live_tracking_enabled = Column(Boolean, default=False)
    live_role_trains_only = Column(Boolean, default=False)
    
    users = relationship("User", back_populates="guild")
    messages = relationship("Message", back_populates="guild")
    channels = relationship("Channel", back_populates="guild")

class User(Base):
    """Represents a Discord user."""
    __tablename__ = 'users'
    
    id = Column(BigInteger, primary_key=True)
    username = Column(String(32), nullable=False)
    display_name = Column(String(32))
    guild_id = Column(BigInteger, ForeignKey('guilds.id'))
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    message_count = Column(Integer, default=0)
    is_bot = Column(Boolean, default=False)
    
    guild = relationship("Guild", back_populates="users")
    messages = relationship("Message", back_populates="user")

class Message(Base):
    """Represents a forwarded message."""
    __tablename__ = 'messages'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_message_id = Column(BigInteger, unique=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey('users.id'))
    guild_id = Column(BigInteger, ForeignKey('guilds.id'))
    channel_id = Column(BigInteger, nullable=False)
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    was_forwarded = Column(Boolean, default=False)
    forwarded_to_channel = Column(BigInteger)
    forwarded_at = Column(DateTime)
    
    user = relationship("User", back_populates="messages")
    guild = relationship("Guild", back_populates="messages")

class ForwardingConfig(Base):
    """Configuration for message forwarding between channels."""
    __tablename__ = 'forwarding_configs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_guild_id = Column(String(50), nullable=False)
    source_channel_id = Column(String(50), nullable=False)
    target_guild_id = Column(String(50), nullable=False)
    target_channel_id = Column(String(50), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    created_by = Column(String(50), nullable=False)

class BotStats(Base):
    """Bot statistics and metrics."""
    __tablename__ = 'bot_stats'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, default=datetime.utcnow)
    guilds_count = Column(Integer, default=0)
    users_count = Column(Integer, default=0)
    messages_processed = Column(Integer, default=0)
    messages_forwarded = Column(Integer, default=0)
    commands_executed = Column(Integer, default=0)
    uptime_seconds = Column(Integer, default=0)

class CommandLog(Base):
    """Log of executed commands."""
    __tablename__ = 'command_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger)
    channel_id = Column(BigInteger, nullable=False)
    command_name = Column(String(50), nullable=False)
    arguments = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    success = Column(Boolean, default=True)
    error_message = Column(Text)

class TrustedUser(Base):
    """Users trusted with owner-level permissions."""
    __tablename__ = 'trusted_users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(32), nullable=False)
    display_name = Column(String(32))
    granted_by = Column(BigInteger, nullable=False)
    granted_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    notes = Column(Text)

class TrustedRole(Base):
    """Discord roles that have trusted user permissions."""
    __tablename__ = 'trusted_roles'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    role_id = Column(BigInteger, nullable=False)  # Discord role ID
    role_name = Column(String(100), nullable=False)  # Role name for reference
    
    # Management info
    granted_by = Column(BigInteger, nullable=False)  # User ID who added this role
    granted_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)  # Optional notes about this role
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Channel(Base):
    """Represents a Discord channel."""
    __tablename__ = 'channels'
    
    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger, ForeignKey('guilds.id'), nullable=False)
    name = Column(String(100), nullable=False)
    type = Column(String(20), default='text')
    position = Column(Integer, default=0)
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    guild = relationship("Guild", back_populates="channels")

# ========================================
# DATABASE MANAGEMENT - Import Enhanced Version
# ========================================

# Import the enhanced database management from database.py
from database import get_db_manager, DatabaseManager

@contextmanager
def DatabaseSession(max_retries: int = 3):
    """Context manager for database sessions with bulletproof error handling and retry logic."""
    from database import get_robust_db_session
    # Use the robust session manager from database.py which has proper cleanup
    with get_robust_db_session(max_retries=max_retries) as session:
        yield session

# ========================================
# FLASK WEB SERVER & DASHBOARD
# ========================================

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.logger.setLevel(logging.WARNING)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

# Reduce werkzeug logging verbosity
logging.getLogger('werkzeug').setLevel(logging.WARNING)


# Global variables for web server
bot_start_time = datetime.utcnow()
last_ping = datetime.utcnow()
bot_instance = None
dashboard_db_available = True

def _derive_guild_token(guild_id_str: str) -> str:
    """Derive a guild-scoped API token from the owner key.

    Token format: "<guild_id>:<HMAC-SHA256(DASHBOARD_OWNER_KEY, 'guild:<guild_id}')>"

    The guild_id is embedded in the token, so the server can extract and verify
    it without relying on any caller-supplied guild_id parameter.  A token
    generated for guild A is mathematically incompatible with guild B.
    """
    owner_key = os.environ.get('DASHBOARD_OWNER_KEY', '')
    if not owner_key:
        raise ValueError("DASHBOARD_OWNER_KEY is not configured")
    mac = hmac.new(
        owner_key.encode('utf-8'),
        f"guild:{guild_id_str}".encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f"{guild_id_str}:{mac}"


def _parse_guild_token(token: str):
    """Parse and cryptographically validate a guild-scoped token.

    Returns the authorized guild_id string if the token is valid, or None
    if it is malformed or the HMAC does not verify.
    """
    owner_key = os.environ.get('DASHBOARD_OWNER_KEY', '')
    if not owner_key or ':' not in token:
        return None
    guild_id_str, _, provided_mac = token.partition(':')
    if not guild_id_str or not provided_mac:
        return None
    try:
        int(guild_id_str)
    except ValueError:
        return None
    expected_mac = hmac.new(
        owner_key.encode('utf-8'),
        f"guild:{guild_id_str}".encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_mac, provided_mac):
        return None
    return guild_id_str


def _classify_token(provided_key: str):
    """Classify the incoming X-API-Key credential.

    Returns one of:
      ("owner", None)        — key matches DASHBOARD_OWNER_KEY
      ("api",   None)        — key matches legacy DASHBOARD_API_KEY
      ("guild", guild_id)    — key is a valid guild-scoped derived token
      (None,    None)        — key is invalid / unrecognised
    """
    if not provided_key:
        return None, None
    owner_key = os.environ.get('DASHBOARD_OWNER_KEY', '')
    api_key   = os.environ.get('DASHBOARD_API_KEY', '')
    if owner_key and hmac.compare_digest(owner_key, provided_key):
        return "owner", None
    if api_key and hmac.compare_digest(api_key, provided_key):
        return "api", None
    guild_id = _parse_guild_token(provided_key)
    if guild_id:
        return "guild", guild_id
    return None, None


def require_api_key(f):
    """Decorator that enforces API key authentication on Flask routes.

    Accepts any of three valid credential types (see _classify_token):
      1. DASHBOARD_OWNER_KEY — full access
      2. DASHBOARD_API_KEY   — legacy full-access key (kept for backward compatibility,
                               but is restricted to owner-level routes going forward)
      3. Guild-scoped token  — HMAC-signed token bound to a specific guild_id;
                               downstream guild-scope enforcement is done in
                               _require_guild_access().

    Fails closed (HTTP 503) when neither DASHBOARD_API_KEY nor DASHBOARD_OWNER_KEY
    is configured in a non-development environment.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key   = os.environ.get('DASHBOARD_API_KEY', '')
        owner_key = os.environ.get('DASHBOARD_OWNER_KEY', '')
        if not api_key and not owner_key:
            flask_env = os.environ.get('FLASK_ENV', '') or os.environ.get('APP_ENV', '')
            if flask_env.lower() != 'development':
                app.logger.warning(
                    "No API keys configured — blocking access to %s in non-development mode",
                    request.path
                )
                return jsonify({'error': 'Service unavailable: API key not configured'}), 503
            app.logger.warning(
                "No API keys configured — allowing unauthenticated access to %s in development mode",
                request.path
            )
            return f(*args, **kwargs)

        provided_key = request.headers.get('X-API-Key', '')
        key_type, _ = _classify_token(provided_key)
        if key_type is None:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def require_owner_key(f):
    """Decorator that enforces owner-level authentication for cross-guild operations.

    These endpoints expose data or capabilities that span all Discord servers managed
    by the bot (global guild enumeration, maintenance mode, cross-guild user lists,
    etc.).  They require the DASHBOARD_OWNER_KEY secret — a separate, higher-privilege
    credential that should only be held by the bot owner.

    Behaviour:
    - If DASHBOARD_OWNER_KEY is set, the request must supply the same value in the
      X-API-Key header.  Guild-scoped tokens and DASHBOARD_API_KEY are rejected.
    - If DASHBOARD_OWNER_KEY is NOT set the endpoint fails closed with HTTP 503.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        owner_key = os.environ.get('DASHBOARD_OWNER_KEY', '')
        if not owner_key:
            app.logger.error(
                "DASHBOARD_OWNER_KEY is not set — blocking owner-only endpoint %s",
                request.path
            )
            return jsonify({'error': 'Service unavailable: Owner key not configured'}), 503
        provided_key = request.headers.get('X-API-Key', '')
        if not hmac.compare_digest(owner_key, provided_key):
            return jsonify({'error': 'Forbidden: Owner-level access required'}), 403
        return f(*args, **kwargs)
    return decorated


def _require_guild_access(guild_id_str: str):
    """Enforce guild-level authorization for the current request.

    This is the core per-guild access control check.  It validates TWO things:

    1. **Credential scope** — the X-API-Key must be cryptographically authorized
       for this specific guild.  Accepted credentials are:
         a. DASHBOARD_OWNER_KEY (owner has universal access)
         b. A guild-scoped derived token whose embedded guild_id matches
            guild_id_str (token was issued by _derive_guild_token(guild_id_str))

       The legacy DASHBOARD_API_KEY grants access to owner-level endpoints only;
       it is intentionally excluded here to prevent a leaked shared key from
       giving cross-guild administrative power.

    2. **Bot membership** — the bot must actually be a member of the requested
       guild (prevents callers from probing guilds the bot does not serve).

    Returns a Flask error response tuple (response, status_code) on denial,
    or None if both checks pass.
    """
    try:
        gid = int(guild_id_str)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid guild ID'}), 400

    provided_key = request.headers.get('X-API-Key', '')
    key_type, authorized_guild = _classify_token(provided_key)

    if key_type == "owner":
        pass  # Owner has universal access — skip scope check
    elif key_type == "guild" and authorized_guild == str(gid):
        pass  # Token is correctly scoped to the requested guild
    else:
        app.logger.warning(
            "Dashboard guild access denied — credential not authorized for guild %s "
            "(key_type=%s, authorized_guild=%s, path=%s)",
            guild_id_str, key_type, authorized_guild, request.path
        )
        return jsonify({
            'error': (
                'Forbidden: your credential is not authorized for guild '
                + guild_id_str
            )
        }), 403

    if bot_instance and bot_instance.is_ready():
        if not bot_instance.get_guild(gid):
            app.logger.warning(
                "Dashboard request rejected — guild %s not found in bot guild list "
                "(path: %s)", guild_id_str, request.path
            )
            return jsonify({
                'error': 'Guild not found or bot is not a member of this server'
            }), 403

    return None


def _get_request_guild_id():
    """Extract and validate the X-Guild-ID header used to scope indirect resource access.

    Returns (guild_id_str, None) on success or (None, error_response_tuple) on failure.
    """
    guild_id = request.headers.get('X-Guild-ID', '').strip()
    if not guild_id:
        return None, (jsonify({'error': 'X-Guild-ID header is required for this operation'}), 400)
    try:
        int(guild_id)
    except ValueError:
        return None, (jsonify({'error': 'Invalid X-Guild-ID value'}), 400)
    return guild_id, None


def set_bot_instance(bot):
    """Set the bot instance for dashboard access."""
    global bot_instance
    bot_instance = bot

def get_bot_internal_status():
    """Get bot status from the bot instance if available."""
    try:
        if bot_instance and hasattr(bot_instance, 'user') and bot_instance.user:
            uptime = datetime.utcnow() - bot_start_time
            return {
                'online': True,
                'status': 'Online',
                'bot_name': str(bot_instance.user.name),
                'bot_id': str(bot_instance.user.id),
                'guild_count': len(bot_instance.guilds) if bot_instance.guilds else 0,
                'uptime_seconds': int(uptime.total_seconds())
            }
        return None
    except Exception:
        return None

def get_dashboard_database_stats():
    """Get statistics from the database for dashboard."""
    if not dashboard_db_available:
        return {'error': 'Database not available'}
        
    try:
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            return {'error': 'DATABASE_URL not configured'}
        engine = create_engine(database_url)
        Session = sessionmaker(bind=engine)
        
        with Session() as db_session:
            guild_count = db_session.query(Guild).count()
            user_count = db_session.query(User).count()
            message_count = db_session.query(Message).count()
            forwarding_count = db_session.query(ForwardingConfig).filter(ForwardingConfig.is_active == True).count()
            channel_count = db_session.query(Channel).count()
            
            return {
                'guilds': guild_count,
                'users': user_count,
                'messages': message_count,
                'active_forwarding': forwarding_count,
                'channels': channel_count
            }
    except Exception as e:
        return {'error': str(e)}

def get_dashboard_forwarding_configs():
    """Get all forwarding configurations for dashboard using Discord bot for names."""
    if not dashboard_db_available:
        return []
        
    try:
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            return []
        engine = create_engine(database_url)
        Session = sessionmaker(bind=engine)
        
        with Session() as db_session:
            configs = db_session.query(ForwardingConfig).filter(
                ForwardingConfig.is_active == True
            ).order_by(ForwardingConfig.created_at.desc()).all()
            
            config_list = []
            for config in configs:
                # Use Discord bot to get actual names
                source_guild_name = f"Server {config.source_guild_id}"
                source_channel_name = f"Channel {config.source_channel_id}"
                target_guild_name = f"Server {config.target_guild_id}"
                target_channel_name = f"Channel {config.target_channel_id}"
                
                if bot_instance and bot_instance.is_ready():
                    try:
                        # Get source guild and channel
                        source_guild = bot_instance.get_guild(int(config.source_guild_id))
                        if source_guild:
                            source_guild_name = source_guild.name
                            source_channel = source_guild.get_channel(int(config.source_channel_id))
                            if source_channel:
                                source_channel_name = source_channel.name
                        
                        # Get target guild and channel
                        target_guild = bot_instance.get_guild(int(config.target_guild_id))
                        if target_guild:
                            target_guild_name = target_guild.name
                            target_channel = target_guild.get_channel(int(config.target_channel_id))
                            if target_channel:
                                target_channel_name = target_channel.name
                    except Exception as e:
                        pass  # Use fallback names
                
                config_list.append({
                    'id': config.id,
                    'source_guild_id': config.source_guild_id,
                    'source_channel_id': config.source_channel_id,
                    'target_guild_id': config.target_guild_id,
                    'target_channel_id': config.target_channel_id,
                    'source_guild_name': source_guild_name,
                    'target_guild_name': target_guild_name,
                    'source_channel_name': source_channel_name,
                    'target_channel_name': target_channel_name,
                    'is_active': config.is_active,
                    'created_at': config.created_at.strftime('%Y-%m-%d %H:%M:%S') if config.created_at is not None else 'Unknown'
                })
            
            return config_list
    except Exception as e:
        return []

# Dashboard HTML template (inline)
DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Train Bot Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        .dashboard-header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 2rem 0; margin-bottom: 2rem; }
        .status-online { color: #28a745; }
        .status-offline { color: #dc3545; }
        .card { margin-bottom: 1rem; }
        .train-icon { color: #667eea; }
        .stat-number { font-size: 2rem; font-weight: bold; }
        .train-slot { border-left: 4px solid #667eea; }
        .participant-item { border-bottom: 1px solid #eee; padding: 0.5rem 0; }
        .participant-item:last-child { border-bottom: none; }
        .train-management { background: #f8f9fa; border-radius: 8px; padding: 1rem; margin: 1rem 0; }
        .btn-train { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; }
        .btn-train:hover { background: linear-gradient(135deg, #5a6fd8 0%, #6a4190 100%); color: white; }
    </style>
</head>
<body>
    <div class="dashboard-header">
        <div class="container">
            <div class="row align-items-center">
                <div class="col-md-8">
                    <h1><i class="fas fa-train train-icon"></i> Train Bot Dashboard</h1>
                    <p class="mb-0">Discord Bot Management & Monitoring</p>
                </div>
                <div class="col-md-4 text-md-end">
                    <div class="status-indicator">
                        <h4><i class="fas fa-circle status-online"></i> Online</h4>
                        <small>Uptime: {{ uptime }}</small>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <div class="container">
        <div class="row">
            <div class="col-lg-2 col-md-4 col-sm-6">
                <div class="card text-center">
                    <div class="card-body">
                        <i class="fas fa-server fa-2x text-primary mb-2"></i>
                        <div class="stat-number text-primary">{{ guilds }}</div>
                        <small class="text-muted">Servers</small>
                    </div>
                </div>
            </div>
            <div class="col-lg-2 col-md-4 col-sm-6">
                <div class="card text-center">
                    <div class="card-body">
                        <i class="fas fa-users fa-2x text-success mb-2"></i>
                        <div class="stat-number text-success">{{ users }}</div>
                        <small class="text-muted">Users</small>
                    </div>
                </div>
            </div>
            <div class="col-lg-2 col-md-4 col-sm-6">
                <div class="card text-center">
                    <div class="card-body">
                        <i class="fas fa-comments fa-2x text-info mb-2"></i>
                        <div class="stat-number text-info">{{ messages }}</div>
                        <small class="text-muted">Messages</small>
                    </div>
                </div>
            </div>
            <div class="col-lg-2 col-md-4 col-sm-6">
                <div class="card text-center">
                    <div class="card-body">
                        <i class="fas fa-share-alt fa-2x text-warning mb-2"></i>
                        <div class="stat-number text-warning">{{ forwarding }}</div>
                        <small class="text-muted">Active Forwarding</small>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Train Management -->
        <div class="row mt-4">
            <div class="col-12">
                <div class="card">
                    <div class="card-header">
                        <h5><i class="fas fa-train"></i> Train Management</h5>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6">
                                <h6><i class="fas fa-calendar-alt"></i> Active Train Schedules</h6>
                                <div id="train-schedules" class="train-management">
                                    <div class="text-center"><i class="fas fa-spinner fa-spin"></i> Loading train schedules...</div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <h6><i class="fas fa-users"></i> Participant Management</h6>
                                <div id="participant-management" class="train-management">
                                    <div class="mb-3">
                                        <label for="schedule-select" class="form-label">Select Train Schedule:</label>
                                        <select class="form-control" id="schedule-select" onchange="loadParticipants()">
                                            <option value="">Loading schedules...</option>
                                        </select>
                                    </div>
                                    <div id="participants-list"></div>
                                    <div class="mt-3">
                                        <h6>Add Participant</h6>
                                        <div class="input-group">
                                            <input type="text" class="form-control" id="participant-input" placeholder="Discord User ID or @username">
                                            <button class="btn btn-train" onclick="addParticipant()"><i class="fas fa-plus"></i> Add</button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Forwarding Configurations -->
        <div class="row mt-4">
            <div class="col-12">
                <h3><i class="fas fa-share-alt"></i> Message Forwarding</h3>
                <div class="card">
                    <div class="card-body">
                        {% if forwarding_configs %}
                        <div class="table-responsive">
                            <table class="table">
                                <thead>
                                    <tr>
                                        <th>Source</th>
                                        <th>Target</th>
                                        <th>Status</th>
                                        <th>Created</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for config in forwarding_configs %}
                                    <tr>
                                        <td>{{ config.source_guild_name }} > #{{ config.source_channel_name }}</td>
                                        <td>{{ config.target_guild_name }} > #{{ config.target_channel_name }}</td>
                                        <td>
                                            {% if config.is_active %}
                                            <span class="badge bg-success">Active</span>
                                            {% else %}
                                            <span class="badge bg-secondary">Inactive</span>
                                            {% endif %}
                                        </td>
                                        <td>{{ config.created_at }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <p class="text-muted">No forwarding configurations found.</p>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
        // Train management functions
        async function loadTrainSchedules() {
            try {
                const response = await fetch('/api/train/schedules');
                const data = await response.json();
                
                const schedulesDiv = document.getElementById('train-schedules');
                const scheduleSelect = document.getElementById('schedule-select');
                
                if (!data.schedules || data.schedules.length === 0) {
                    schedulesDiv.innerHTML = '<div class="text-muted">No train schedules found. Use !setupweekschedule to create them.</div>';
                    scheduleSelect.innerHTML = '<option value="">No schedules available</option>';
                    return;
                }
                
                // Populate schedules display
                let schedulesHTML = '';
                data.schedules.forEach(schedule => {
                    const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
                    const dayName = dayNames[schedule.day_of_week];
                    schedulesHTML += `
                        <div class="train-slot mb-2 p-2">
                            <strong>${schedule.name}</strong><br>
                            <small>${dayName} ${schedule.start_time} (${schedule.duration_minutes}min)</small><br>
                            <small class="text-success">${schedule.participant_count}/${schedule.max_participants || '∞'} participants</small>
                        </div>
                    `;
                });
                schedulesDiv.innerHTML = schedulesHTML;
                
                // Populate schedule selector
                let selectHTML = '<option value="">Select a train...</option>';
                data.schedules.forEach(schedule => {
                    const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
                    const dayName = dayNames[schedule.day_of_week];
                    selectHTML += `<option value="${schedule.id}">${schedule.name} (${dayName} ${schedule.start_time})</option>`;
                });
                scheduleSelect.innerHTML = selectHTML;
                
            } catch (error) {
                console.error('Error loading train schedules:', error);
                document.getElementById('train-schedules').innerHTML = '<div class="text-danger">Error loading schedules</div>';
            }
        }
        
        async function loadParticipants() {
            const scheduleId = document.getElementById('schedule-select').value;
            const participantsList = document.getElementById('participants-list');
            
            if (!scheduleId) {
                participantsList.innerHTML = '';
                return;
            }
            
            try {
                const response = await fetch(`/api/train/participants/${scheduleId}`);
                const participants = await response.json();
                
                if (participants.length === 0) {
                    participantsList.innerHTML = '<div class="text-muted">No participants yet</div>';
                    return;
                }
                
                let participantsHTML = '<h6>Current Participants:</h6>';
                participants.forEach(participant => {
                    participantsHTML += `
                        <div class="participant-item d-flex justify-content-between align-items-center">
                            <div>
                                <strong>${participant.display_name}</strong>
                                ${participant.is_host ? '<span class="badge bg-primary">Host</span>' : ''}
                                ${participant.twitch_username ? `<br><small>Twitch: ${participant.twitch_username}</small>` : ''}
                            </div>
                            <button class="btn btn-sm btn-outline-danger" onclick="removeParticipant(${participant.id})">
                                <i class="fas fa-times"></i>
                            </button>
                        </div>
                    `;
                });
                participantsList.innerHTML = participantsHTML;
                
            } catch (error) {
                console.error('Error loading participants:', error);
                participantsList.innerHTML = '<div class="text-danger">Error loading participants</div>';
            }
        }
        
        async function addParticipant() {
            const scheduleId = document.getElementById('schedule-select').value;
            const participantInput = document.getElementById('participant-input');
            const userInput = participantInput.value.trim();
            
            if (!scheduleId || !userInput) {
                alert('Please select a train and enter a Discord user ID or username');
                return;
            }
            
            try {
                const response = await fetch('/api/train/participants', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        schedule_id: scheduleId,
                        user_input: userInput
                    })
                });
                
                const result = await response.json();
                
                if (response.ok) {
                    participantInput.value = '';
                    loadParticipants(); // Refresh participants list
                    loadTrainSchedules(); // Refresh schedule counts
                    alert('Participant added successfully!');
                } else {
                    alert(`Error: ${result.error}`);
                }
                
            } catch (error) {
                console.error('Error adding participant:', error);
                alert('Error adding participant');
            }
        }
        
        async function removeParticipant(participantId) {
            if (!confirm('Are you sure you want to remove this participant?')) {
                return;
            }
            
            try {
                const response = await fetch(`/api/train/participants/${participantId}`, {
                    method: 'DELETE'
                });
                
                const result = await response.json();
                
                if (response.ok) {
                    loadParticipants(); // Refresh participants list
                    loadTrainSchedules(); // Refresh schedule counts
                    alert('Participant removed successfully!');
                } else {
                    alert(`Error: ${result.error}`);
                }
                
            } catch (error) {
                console.error('Error removing participant:', error);
                alert('Error removing participant');
            }
        }
        
        // Load train data when page loads
        document.addEventListener('DOMContentLoaded', function() {
            loadTrainSchedules();
        });
        
        // Auto-refresh every 30 seconds
        setTimeout(() => location.reload(), 30000);
    </script>
</body>
</html>'''

# Flask routes
@app.route('/')
def index():
    """Root endpoint for health checks."""
    uptime = datetime.utcnow() - bot_start_time
    uptime_str = str(uptime).split('.')[0]
    
    status_data = {
        'bot_name': os.getenv('BOT_NAME', 'Train Bot'),
        'status': 'healthy',
        'uptime': uptime_str,
        'server_time': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
        'service': 'discord-bot-server',
        'deployment_ready': True
    }
    
    accept_header = request.headers.get('Accept', '').lower()
    if 'application/json' in accept_header or 'application/json' in request.headers.get('Content-Type', ''):
        return jsonify(status_data)
    else:
        # Use modern tabbed dashboard
        bot_name = os.getenv('BOT_NAME', 'Game Lounge Train')
        return render_template('modern_dashboard.html', 
                             username='User',
                             bot_name=bot_name)

@app.route('/ping')
def ping():
    """Ping endpoint for monitoring."""
    global last_ping
    last_ping = datetime.utcnow()
    uptime = datetime.utcnow() - bot_start_time
    
    return jsonify({
        'status': 'ok',
        'uptime_seconds': int(uptime.total_seconds()),
        'timestamp': datetime.utcnow().isoformat(),
        'message': 'Bot is alive'
    })

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'uptime_seconds': int((datetime.utcnow() - bot_start_time).total_seconds())
    }), 200

@app.route('/api/debug')
def api_debug():
    """Debug endpoint to check bot_instance state."""
    global bot_instance
    return jsonify({
        'bot_instance_is_none': bot_instance is None,
        'bot_instance_type': str(type(bot_instance)),
        'has_user': hasattr(bot_instance, 'user') if bot_instance else False,
        'user_is_none': bot_instance.user is None if (bot_instance and hasattr(bot_instance, 'user')) else True,
        'has_guilds': hasattr(bot_instance, 'guilds') if bot_instance else False,
        'guilds_length': len(bot_instance.guilds) if (bot_instance and hasattr(bot_instance, 'guilds')) else 0,
        'is_ready': bot_instance.is_ready() if bot_instance else False
    })

@app.route('/api/bot/status')
def api_bot_status():
    """API endpoint to get bot status information."""
    global bot_instance
    try:
        uptime = datetime.utcnow() - bot_start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)
        
        if days > 0:
            uptime_str = f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            uptime_str = f"{hours}h {minutes}m"
        else:
            uptime_str = f"{minutes}m {seconds}s"
        
        # Get guild and user counts
        guild_count = 0
        user_count = 0
        latency = 0
        is_online = False
        bot_name = 'Bot'
        bot_id = 0
        bot_avatar = None
        
        if bot_instance:
            try:
                is_online = bot_instance.is_ready()
                if hasattr(bot_instance, 'guilds') and bot_instance.guilds:
                    guild_count = len(bot_instance.guilds)
                    user_count = sum(g.member_count or 0 for g in bot_instance.guilds)
                
                if hasattr(bot_instance, 'latency') and bot_instance.latency and not (bot_instance.latency != bot_instance.latency):
                    latency = round(bot_instance.latency * 1000)
                
                if bot_instance.user:
                    bot_name = bot_instance.user.name
                    bot_id = bot_instance.user.id
                    if bot_instance.user.avatar:
                        bot_avatar = str(bot_instance.user.avatar.url)
            except Exception as e:
                app.logger.error(f"Error accessing bot instance properties: {e}")
        
        return jsonify({
            'online': is_online,
            'guilds': guild_count,
            'users': user_count,
            'latency': latency,
            'commands_executed': 0,
            'uptime': uptime_str,
            'uptime_seconds': int(uptime.total_seconds()),
            'user': {
                'name': bot_name,
                'id': bot_id,
                'avatar': bot_avatar
            }
        })
    except Exception as e:
        app.logger.error(f"API error: {e}", exc_info=True)
        return jsonify({'error': str(e), 'online': False, 'guilds': 0, 'users': 0, 'latency': 0, 'uptime': '0s', 'uptime_seconds': 0, 'user': {'name': 'Bot', 'id': 0, 'avatar': None}}), 500

@app.route('/api/train/schedules')
@require_api_key
def api_train_schedules():
    """API endpoint to get train schedules, scoped to a guild via X-Guild-ID header.

    Callers must supply an X-Guild-ID header identifying the guild they are
    managing.  The bot verifies the guild is one it actually serves before
    returning any data.  Bot owners may omit the header only when authenticating
    with DASHBOARD_OWNER_KEY.
    """
    try:
        provided_key = request.headers.get('X-API-Key', '')
        key_type, _ = _classify_token(provided_key)
        is_owner = (key_type == "owner")

        guild_id_filter = request.args.get('guild_id') or request.headers.get('X-Guild-ID', '').strip()

        if not guild_id_filter and not is_owner:
            return jsonify({'error': 'X-Guild-ID header is required to scope schedule access'}), 400

        if guild_id_filter:
            err = _require_guild_access(guild_id_filter)
            if err:
                return err

        with DatabaseSession() as session:
            query = session.query(TrainSchedule).filter_by(is_active=True)
            if guild_id_filter:
                query = query.filter(TrainSchedule.guild_id == str(guild_id_filter))
            schedules = query.all()
            
            schedule_list = []
            for schedule in schedules:
                # Count participants
                participant_count = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule.id,
                    is_active=True
                ).count()
                
                # Get guild/server name
                guild_name = 'Unknown Server'
                if schedule.guild_id:
                    guild = session.query(Guild).filter_by(id=schedule.guild_id).first()
                    if guild:
                        guild_name = guild.name
                
                # Get day name for display
                day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                day_name = day_names[schedule.day_of_week] if 0 <= schedule.day_of_week < 7 else 'Unknown'
                
                # Format time for display — start_time stored as UK wall clock
                from datetime import datetime
                import pytz as _display_pytz
                _uk_tz = _display_pytz.timezone('Europe/London')
                _naive = datetime.combine(datetime.today(), schedule.start_time)
                _uk_dt = _uk_tz.localize(_naive)
                time_display = _uk_dt.strftime('%I:%M %p %Z').lstrip('0')
                
                schedule_list.append({
                    'id': schedule.id,
                    'name': schedule.name,
                    'guild_id': schedule.guild_id,
                    'guild_name': guild_name,
                    'day_of_week': schedule.day_of_week,
                    'day_name': day_name,
                    'start_time': schedule.start_time.strftime('%H:%M'),
                    'time_display': time_display,
                    'duration_minutes': schedule.duration_minutes,
                    'max_participants': schedule.max_participants,
                    'participant_count': participant_count,
                    'description': schedule.description,
                    'is_active': schedule.is_active
                })
                
            # Return format expected by frontend JavaScript
            return jsonify({
                'schedules': schedule_list,
                'count': len(schedule_list)
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/train/schedules/<int:schedule_id>', methods=['PUT'])
@require_api_key
def api_update_schedule(schedule_id):
    """API endpoint to update a train schedule."""
    try:
        data = request.get_json()
        with DatabaseSession() as session:
            schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
            if not schedule:
                return jsonify({'error': 'Schedule not found'}), 404

            if schedule.guild_id:
                err = _require_guild_access(str(schedule.guild_id))
                if err:
                    return err

            # Update fields
            if 'name' in data:
                schedule.name = data['name']
            if 'day_of_week' in data:
                schedule.day_of_week = int(data['day_of_week'])
            if 'start_time' in data:
                from datetime import datetime
                schedule.start_time = datetime.strptime(data['start_time'], '%H:%M').time()
            if 'duration_minutes' in data:
                schedule.duration_minutes = int(data['duration_minutes'])
            if 'max_participants' in data:
                schedule.max_participants = int(data['max_participants'])
            if 'description' in data:
                schedule.description = data['description']
            
            session.commit()
            return jsonify({'success': True, 'message': 'Schedule updated successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/train/schedules/<int:schedule_id>', methods=['DELETE'])
@require_api_key
def api_delete_schedule(schedule_id):
    """API endpoint to delete a train schedule."""
    try:
        with DatabaseSession() as session:
            schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
            if not schedule:
                return jsonify({'error': 'Schedule not found'}), 404

            if schedule.guild_id:
                err = _require_guild_access(str(schedule.guild_id))
                if err:
                    return err

            # Soft delete by setting is_active to False
            schedule.is_active = False
            session.commit()
            return jsonify({'success': True, 'message': 'Schedule deleted successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/train/participants/<int:schedule_id>')
@require_api_key
def api_train_participants(schedule_id):
    """API endpoint to get participants for a specific train schedule."""
    try:
        with DatabaseSession() as session:
            schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
            if not schedule:
                return jsonify({'error': 'Schedule not found'}), 404
            if schedule.guild_id:
                err = _require_guild_access(str(schedule.guild_id))
                if err:
                    return err

            participants = session.query(TrainParticipant).filter_by(
                schedule_id=schedule_id,
                is_active=True
            ).order_by(TrainParticipant.signed_up_at).all()
            
            participant_list = []
            for participant in participants:
                participant_list.append({
                    'id': participant.id,
                    'user_id': participant.user_id,
                    'username': participant.username,
                    'display_name': participant.display_name,
                    'twitch_username': participant.twitch_username,
                    'is_host': participant.is_host,
                    'signed_up_at': participant.signed_up_at.strftime('%Y-%m-%d %H:%M:%S'),
                    'notes': participant.notes
                })
                
            return jsonify(participant_list)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/train/participants', methods=['POST'])
@require_api_key
def api_add_train_participant():
    """API endpoint to add a participant to a train."""
    try:
        data = request.get_json()
        schedule_id = data.get('schedule_id')
        user_input = data.get('user_input')
        
        if not schedule_id or not user_input:
            return jsonify({'error': 'Missing schedule_id or user_input'}), 400
            
        with DatabaseSession() as session:
            # Check if schedule exists
            schedule = session.query(TrainSchedule).filter_by(
                id=schedule_id,
                is_active=True
            ).first()
            
            if not schedule:
                return jsonify({'error': 'Train schedule not found'}), 404

            if schedule.guild_id:
                err = _require_guild_access(str(schedule.guild_id))
                if err:
                    return err

            # Parse user input (could be user ID or username)
            user_id = None
            username = user_input.strip()
            display_name = username
            
            # If it's a numeric ID, use it directly
            if user_input.isdigit():
                user_id = int(user_input)
                username = f"User{user_id}"
                display_name = f"User{user_id}"
            else:
                # Remove @ if present
                if username.startswith('@'):
                    username = username[1:]
                user_id = 0  # Placeholder for manual additions
                display_name = username
            
            # Check if already signed up
            existing = session.query(TrainParticipant).filter_by(
                schedule_id=schedule_id,
                user_id=user_id,
                is_active=True
            ).first()
            
            if existing:
                return jsonify({'error': 'User already signed up for this train'}), 400
            
            # Check participant limit
            if schedule.max_participants:
                current_count = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    is_active=True
                ).count()
                
                if current_count >= schedule.max_participants:
                    return jsonify({'error': f'Train is full ({current_count}/{schedule.max_participants})'}), 400
            
            # Add participant
            participant = TrainParticipant(
                schedule_id=schedule_id,
                guild_id=schedule.guild_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                signed_up_at=datetime.utcnow(),
                is_active=True,
                notes="Added via dashboard"
            )
            
            session.add(participant)
            session.commit()

            # DM the added user if we have a real Discord ID
            if user_id and user_id != 0 and bot_instance:
                from types import SimpleNamespace
                sched_copy = SimpleNamespace(
                    id=schedule.id,
                    name=schedule.name,
                    duration_minutes=schedule.duration_minutes,
                    max_participants=schedule.max_participants,
                )
                _uid = user_id

                async def _send_dashboard_dm():
                    try:
                        discord_user = await bot_instance.fetch_user(_uid)
                        cog = bot_instance.cogs.get('TrainParticipantCommands')
                        if cog and discord_user:
                            await cog.send_admin_added_dm(
                                discord_user, sched_copy, bot_instance.user, None
                            )
                    except Exception as dm_err:
                        logger.error(f"Dashboard add DM failed for user {_uid}: {dm_err}")

                import asyncio as _asyncio
                _asyncio.run_coroutine_threadsafe(_send_dashboard_dm(), bot_instance.loop)

            return jsonify({'success': True, 'message': 'Participant added successfully'})
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/train/participants/<int:participant_id>', methods=['DELETE'])
@require_api_key
def api_remove_train_participant(participant_id):
    """API endpoint to remove a participant from a train."""
    try:
        with DatabaseSession() as session:
            participant = session.query(TrainParticipant).filter_by(
                id=participant_id,
                is_active=True
            ).first()
            
            if not participant:
                return jsonify({'error': 'Participant not found'}), 404

            if participant.guild_id:
                err = _require_guild_access(str(participant.guild_id))
                if err:
                    return err

            # Deactivate participant instead of deleting
            participant.is_active = False
            session.commit()
            
            return jsonify({'success': True, 'message': 'Participant removed successfully'})
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/dashboard')
@require_owner_key
def dashboard():
    """Main dashboard page."""
    try:
        return render_template_string(DASHBOARD_HTML, **get_dashboard_data())
    except Exception as e:
        return f"Dashboard Error: {str(e)}", 500

@app.route('/api/channels', methods=['GET'])
@require_owner_key
def api_get_channels():
    """Get all available channels for dropdown selection."""
    try:
        with DatabaseSession() as db_session:
            channels = db_session.query(Channel).all()
            
            channel_list = []
            for channel in channels:
                guild = db_session.query(Guild).filter(Guild.id == channel.guild_id).first()
                channel_list.append({
                    'id': channel.id,
                    'guild_id': channel.guild_id,
                    'guild_name': guild.name if guild else f"Guild {channel.guild_id}",
                    'name': channel.name,
                    'display_name': f"{guild.name if guild else 'Unknown'} > #{channel.name}"
                })
            
            # Return format expected by frontend JavaScript
            return jsonify({
                'status': 'success',
                'channels': channel_list,
                'count': len(channel_list)
            })
            
    except Exception as e:
        app.logger.error(f"Error fetching channels: {e}")
        return jsonify({'status': 'error', 'message': str(e), 'channels': []}), 500

@app.route('/api/guilds/<string:guild_id>/channels', methods=['GET'])
@require_api_key
def api_get_guild_channels(guild_id):
    """Get all text channels for a specific guild from Discord."""
    try:
        err = _require_guild_access(guild_id)
        if err:
            return err

        if not bot_instance or not bot_instance.is_ready():
            return jsonify({'status': 'error', 'message': 'Bot not ready', 'channels': []}), 503
        
        guild = bot_instance.get_guild(int(guild_id))
        if not guild:
            return jsonify({'status': 'error', 'message': 'Guild not found', 'channels': []}), 404
        
        channel_list = []
        for channel in guild.text_channels:
            channel_list.append({
                'id': str(channel.id),
                'name': channel.name,
                'category': channel.category.name if channel.category else 'No Category',
                'display_name': f"#{channel.name}"
            })
        
        # Sort by category then name
        channel_list.sort(key=lambda x: (x['category'], x['name']))
        
        return jsonify({
            'status': 'success',
            'guild_id': guild_id,
            'guild_name': guild.name,
            'channels': channel_list,
            'count': len(channel_list)
        })
        
    except Exception as e:
        app.logger.error(f"Error fetching guild channels: {e}")
        return jsonify({'status': 'error', 'message': str(e), 'channels': []}), 500

@app.route('/api/forwarding', methods=['GET'])
@require_api_key
def api_get_forwarding():
    """Get forwarding configurations, scoped to a server via server_id query parameter.

    Callers must supply a ?server_id= query parameter to scope results to a guild
    they are permitted to administer.  The bot verifies the guild is one it serves.
    Bot owners authenticating with DASHBOARD_OWNER_KEY may omit server_id to view
    all forwarding configurations.
    """
    try:
        server_id = request.args.get('server_id')

        provided_key = request.headers.get('X-API-Key', '')
        key_type, _ = _classify_token(provided_key)
        is_owner = (key_type == "owner")

        if not server_id and not is_owner:
            return jsonify({
                'status': 'error',
                'message': 'server_id query parameter is required to scope forwarding config access'
            }), 400

        if server_id:
            err = _require_guild_access(server_id)
            if err:
                return err

        with DatabaseSession() as db_session:
            query = db_session.query(ForwardingConfig).filter(
                ForwardingConfig.is_active == True
            )
            
            # Filter by server if specified (either source or target)
            if server_id:
                query = query.filter(
                    (ForwardingConfig.source_guild_id == server_id) |
                    (ForwardingConfig.target_guild_id == server_id)
                )
            
            configs = query.all()
            
            config_list = []
            for config in configs:
                # Try to resolve server and channel names from Discord
                source_guild_name = f"Server {config.source_guild_id}"
                source_channel_name = f"Channel {config.source_channel_id}"
                target_guild_name = f"Server {config.target_guild_id}"
                target_channel_name = f"Channel {config.target_channel_id}"
                
                if bot_instance and bot_instance.is_ready():
                    try:
                        # Get source guild and channel
                        source_guild = bot_instance.get_guild(int(config.source_guild_id))
                        if source_guild:
                            source_guild_name = source_guild.name
                            source_channel = source_guild.get_channel(int(config.source_channel_id))
                            if source_channel:
                                source_channel_name = source_channel.name
                        
                        # Get target guild and channel
                        target_guild = bot_instance.get_guild(int(config.target_guild_id))
                        if target_guild:
                            target_guild_name = target_guild.name
                            target_channel = target_guild.get_channel(int(config.target_channel_id))
                            if target_channel:
                                target_channel_name = target_channel.name
                    except Exception as e:
                        app.logger.debug(f"Could not resolve names for config {config.id}: {e}")
                
                config_list.append({
                    'id': config.id,
                    'source_guild_id': config.source_guild_id,
                    'source_guild_name': source_guild_name,
                    'source_channel_id': config.source_channel_id,
                    'source_channel_name': source_channel_name,
                    'target_guild_id': config.target_guild_id,
                    'target_guild_name': target_guild_name,
                    'target_channel_id': config.target_channel_id,
                    'target_channel_name': target_channel_name,
                    'is_active': config.is_active,
                    'created_at': config.created_at.strftime('%Y-%m-%d %H:%M:%S') if config.created_at else None,
                    'created_by': config.created_by
                })
            
            return jsonify({
                'active_count': len(config_list),
                'configs': config_list
            })
            
    except Exception as e:
        app.logger.error(f"Error fetching forwarding configs: {e}")
        return jsonify({'active_count': 0, 'configs': []}), 500

@app.route('/api/forwarding', methods=['POST'])
@require_api_key
def api_create_forwarding():
    """Create a new forwarding configuration from dashboard."""
    try:
        data = request.get_json()
        
        source_guild_id = data.get('source_guild_id')
        source_channel_id = data.get('source_channel_id')
        target_guild_id = data.get('target_guild_id')
        target_channel_id = data.get('target_channel_id')
        created_by = data.get('created_by', 'dashboard')
        
        if not all([source_guild_id, source_channel_id, target_guild_id, target_channel_id]):
            return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

        source_guild_id_str = str(source_guild_id)
        target_guild_id_str = str(target_guild_id)
        is_cross_guild = (source_guild_id_str != target_guild_id_str)

        if is_cross_guild:
            provided_key = request.headers.get('X-API-Key', '')
            key_type, _ = _classify_token(provided_key)
            if key_type != "owner":
                return jsonify({
                    'status': 'error',
                    'message': (
                        'Cross-guild forwarding (source guild differs from target guild) '
                        'requires owner-level authorization'
                    )
                }), 403

        err = _require_guild_access(source_guild_id_str)
        if err:
            return err

        if not is_cross_guild:
            err = _require_guild_access(target_guild_id_str)
            if err:
                return err

        with DatabaseSession() as db_session:
            # Check if forwarding already exists
            existing = db_session.query(ForwardingConfig).filter(
                ForwardingConfig.source_guild_id == str(source_guild_id),
                ForwardingConfig.source_channel_id == str(source_channel_id),
                ForwardingConfig.target_channel_id == str(target_channel_id),
                ForwardingConfig.is_active == True
            ).first()
            
            if existing:
                return jsonify({'status': 'error', 'message': 'Forwarding configuration already exists'}), 409
            
            # Create new config
            from datetime import datetime
            config = ForwardingConfig(
                source_guild_id=str(source_guild_id),
                source_channel_id=str(source_channel_id),
                target_guild_id=str(target_guild_id),
                target_channel_id=str(target_channel_id),
                created_by=str(created_by),
                is_active=True,
                created_at=datetime.utcnow()
            )
            
            db_session.add(config)
            db_session.commit()
            
            # Get resolved names for response
            source_guild_name = f"Server {source_guild_id}"
            source_channel_name = f"Channel {source_channel_id}"
            target_guild_name = f"Server {target_guild_id}"
            target_channel_name = f"Channel {target_channel_id}"
            
            if bot_instance and bot_instance.is_ready():
                try:
                    source_guild = bot_instance.get_guild(int(source_guild_id))
                    if source_guild:
                        source_guild_name = source_guild.name
                        source_channel = source_guild.get_channel(int(source_channel_id))
                        if source_channel:
                            source_channel_name = source_channel.name
                    
                    target_guild = bot_instance.get_guild(int(target_guild_id))
                    if target_guild:
                        target_guild_name = target_guild.name
                        target_channel = target_guild.get_channel(int(target_channel_id))
                        if target_channel:
                            target_channel_name = target_channel.name
                except:
                    pass
            
            return jsonify({
                'status': 'success',
                'message': 'Forwarding configuration created',
                'config': {
                    'id': config.id,
                    'source_guild_id': source_guild_id,
                    'source_guild_name': source_guild_name,
                    'source_channel_id': source_channel_id,
                    'source_channel_name': source_channel_name,
                    'target_guild_id': target_guild_id,
                    'target_guild_name': target_guild_name,
                    'target_channel_id': target_channel_id,
                    'target_channel_name': target_channel_name
                }
            })
            
    except Exception as e:
        app.logger.error(f"Error creating forwarding config: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/forwarding/<int:config_id>', methods=['PUT'])
@require_api_key
def api_update_forwarding(config_id):
    """Update a forwarding configuration."""
    try:
        data = request.get_json()
        
        with DatabaseSession() as db_session:
            config = db_session.query(ForwardingConfig).filter(ForwardingConfig.id == config_id).first()
            
            if not config:
                return jsonify({'status': 'error', 'message': 'Configuration not found'}), 404

            src = str(config.source_guild_id) if config.source_guild_id else None
            tgt = str(config.target_guild_id) if config.target_guild_id else None
            is_cross_guild = src and tgt and (src != tgt)

            if is_cross_guild:
                provided_key = request.headers.get('X-API-Key', '')
                key_type, _ = _classify_token(provided_key)
                if key_type != "owner":
                    return jsonify({
                        'status': 'error',
                        'message': 'Modifying a cross-guild forwarding config requires owner-level authorization'
                    }), 403
            elif src:
                err = _require_guild_access(src)
                if err:
                    return err

            if 'is_active' in data:
                config.is_active = data['is_active']
            
            db_session.commit()
            return jsonify({'status': 'success', 'message': 'Configuration updated'})
            
    except Exception as e:
        app.logger.error(f"Error updating forwarding config: {e}")
        return jsonify({'status': 'error', 'message': 'Database error occurred'}), 500

@app.route('/api/forwarding/<int:config_id>', methods=['DELETE'])
@require_api_key
def api_delete_forwarding(config_id):
    """Delete a forwarding configuration."""
    try:
        with DatabaseSession() as db_session:
            config = db_session.query(ForwardingConfig).filter(ForwardingConfig.id == config_id).first()
            
            if not config:
                return jsonify({'status': 'error', 'message': 'Configuration not found'}), 404

            src = str(config.source_guild_id) if config.source_guild_id else None
            tgt = str(config.target_guild_id) if config.target_guild_id else None
            is_cross_guild = src and tgt and (src != tgt)

            if is_cross_guild:
                provided_key = request.headers.get('X-API-Key', '')
                key_type, _ = _classify_token(provided_key)
                if key_type != "owner":
                    return jsonify({
                        'status': 'error',
                        'message': 'Deleting a cross-guild forwarding config requires owner-level authorization'
                    }), 403
            elif src:
                err = _require_guild_access(src)
                if err:
                    return err

            db_session.delete(config)
            db_session.commit()
            return jsonify({'status': 'success', 'message': 'Configuration deleted'})
            
    except Exception as e:
        app.logger.error(f"Error deleting forwarding config: {e}")
        return jsonify({'status': 'error', 'message': 'Database error occurred'}), 500

@app.route('/api/guilds/<string:guild_id>/token', methods=['GET'])
@require_owner_key
def api_get_guild_token(guild_id):
    """Owner-only: generate a guild-scoped API token for a specific guild.

    The returned token is cryptographically bound to this guild_id via HMAC.
    Distribute it to the admin of that guild; it will only grant access to
    endpoints that target that specific guild.
    """
    try:
        int(guild_id)
    except ValueError:
        return jsonify({'error': 'Invalid guild ID'}), 400

    if bot_instance and bot_instance.is_ready():
        if not bot_instance.get_guild(int(guild_id)):
            return jsonify({'error': 'Guild not found or bot is not a member'}), 404

    try:
        token = _derive_guild_token(guild_id)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 503

    return jsonify({
        'guild_id': guild_id,
        'token': token,
        'usage': 'Pass this value as the X-API-Key header for guild-scoped API requests'
    })


@app.route('/api/guilds', methods=['GET'])
@require_owner_key
def api_get_guilds():
    """Get all connected Discord guilds/servers."""
    try:
        if not bot_instance:
            app.logger.warning("bot_instance is None in /api/guilds")
            return jsonify({'guilds': [], 'count': 0, 'error': 'Bot not initialized'}), 503
        
        if not bot_instance.is_ready():
            app.logger.warning("bot_instance not ready in /api/guilds")
            return jsonify({'guilds': [], 'count': 0, 'error': 'Bot not ready'}), 503
        
        # Log guild count before iteration
        guild_count = len(bot_instance.guilds)
        app.logger.info(f"/api/guilds - bot_instance.guilds count: {guild_count}")
        
        with DatabaseSession() as db_session:
            guilds_list = []
            
            for guild in bot_instance.guilds:
                app.logger.debug(f"Processing guild: {guild.name} ({guild.id})")
                # Count active schedules for this guild
                active_schedules = db_session.query(TrainSchedule).filter(
                    TrainSchedule.guild_id == str(guild.id),
                    TrainSchedule.is_active == True
                ).count()
                
                # Check if live role manager is configured for this guild
                # For now, we'll set this as False and can enhance later
                live_tracking = hasattr(bot_instance, 'live_role_manager') and bot_instance.live_role_manager is not None
                
                guilds_list.append({
                    'id': str(guild.id),
                    'name': guild.name,
                    'icon': guild.icon.url if guild.icon else None,
                    'member_count': guild.member_count,
                    'active_schedules': active_schedules,
                    'live_tracking': live_tracking
                })
            
            return jsonify({
                'guilds': guilds_list,
                'count': len(guilds_list)
            })
            
    except Exception as e:
        app.logger.error(f"Error fetching guilds: {e}")
        return jsonify({'guilds': [], 'count': 0}), 500

@app.route('/api/trusted-users', methods=['GET'])
@require_owner_key
def api_get_trusted_users():
    """Get all trusted users."""
    try:
        with DatabaseSession() as db_session:
            trusted_users = db_session.query(TrustedUser).all()
            
            users_list = []
            for tu in trusted_users:
                # Try to get Discord user info if bot is ready
                discord_name = f"User {tu.user_id}"
                if bot_instance and bot_instance.is_ready():
                    try:
                        user = bot_instance.get_user(int(tu.user_id))
                        if user:
                            discord_name = f"{user.name}#{user.discriminator}" if user.discriminator != '0' else user.name
                    except:
                        pass
                
                users_list.append({
                    'id': tu.id,
                    'user_id': tu.user_id,
                    'discord_name': discord_name,
                    'granted_by': tu.granted_by,
                    'granted_at': tu.granted_at.strftime('%Y-%m-%d %H:%M:%S') if tu.granted_at else None
                })
            
            return jsonify({
                'users': users_list,
                'count': len(users_list)
            })
            
    except Exception as e:
        app.logger.error(f"Error fetching trusted users: {e}")
        return jsonify({'users': [], 'count': 0}), 500

@app.route('/api/trusted-roles', methods=['GET'])
@require_owner_key
def api_get_trusted_roles():
    """Get all trusted roles."""
    try:
        with DatabaseSession() as db_session:
            trusted_roles = db_session.query(TrustedRole).all()
            
            roles_list = []
            for tr in trusted_roles:
                # Use database values as defaults
                guild_name = f"Guild {tr.guild_id}"
                role_name = tr.role_name if tr.role_name else f"Role {tr.role_id}"
                
                # Try to get guild and role info if bot is ready
                if bot_instance and bot_instance.is_ready():
                    try:
                        guild = bot_instance.get_guild(int(tr.guild_id))
                        if guild:
                            guild_name = guild.name
                            role = guild.get_role(int(tr.role_id))
                            if role:
                                role_name = role.name
                    except:
                        pass
                
                roles_list.append({
                    'id': tr.id,
                    'guild_id': tr.guild_id,
                    'guild_name': guild_name,
                    'role_id': tr.role_id,
                    'role_name': role_name,
                    'created_at': tr.created_at.strftime('%Y-%m-%d %H:%M:%S') if tr.created_at else None
                })
            
            return jsonify({
                'roles': roles_list,
                'count': len(roles_list)
            })
            
    except Exception as e:
        app.logger.error(f"Error fetching trusted roles: {e}")
        return jsonify({'roles': [], 'count': 0}), 500

@app.route('/api/banned-users', methods=['GET'])
@require_owner_key
def api_get_banned_users():
    """Get all banned users."""
    try:
        with DatabaseSession() as db_session:
            banned_users = db_session.query(BannedUser).filter(
                BannedUser.is_active == True
            ).all()
            
            users_list = []
            for bu in banned_users:
                # Try to get Discord user info if bot is ready
                discord_name = f"User {bu.user_id}"
                if bot_instance and bot_instance.is_ready():
                    try:
                        user = bot_instance.get_user(int(bu.user_id))
                        if user:
                            discord_name = f"{user.name}#{user.discriminator}" if user.discriminator != '0' else user.name
                    except:
                        pass
                
                users_list.append({
                    'id': bu.id,
                    'user_id': bu.user_id,
                    'discord_name': discord_name,
                    'reason': bu.reason,
                    'banned_by': bu.banned_by,
                    'banned_at': bu.banned_at.strftime('%Y-%m-%d %H:%M:%S') if bu.banned_at else None
                })
            
            return jsonify({
                'users': users_list,
                'count': len(users_list)
            })
            
    except Exception as e:
        app.logger.error(f"Error fetching banned users: {e}")
        return jsonify({'users': [], 'count': 0}), 500

@app.route('/api/twitch/linked-accounts', methods=['GET'])
@require_owner_key
def api_get_twitch_linked_accounts():
    """Get all linked Twitch accounts from approved link requests."""
    try:
        with DatabaseSession() as db_session:
            # Use raw SQL to get all approved twitch link requests
            from sqlalchemy import text
            result = db_session.execute(text("""
                SELECT target_user_id, target_username, target_display_name, 
                       requested_twitch_username, created_at
                FROM twitch_link_requests 
                WHERE status = 'approved'
                ORDER BY created_at DESC
            """))
            
            accounts_list = []
            seen_users = set()
            
            for row in result:
                # Only show the most recent link per user
                if row.target_user_id in seen_users:
                    continue
                seen_users.add(row.target_user_id)
                
                # Try to get Discord user info if bot is ready
                discord_name = row.target_display_name or row.target_username or f"User {row.target_user_id}"
                if bot_instance and bot_instance.is_ready():
                    try:
                        discord_user = bot_instance.get_user(int(row.target_user_id))
                        if discord_user:
                            discord_name = f"{discord_user.name}#{discord_user.discriminator}" if discord_user.discriminator != '0' else discord_user.name
                    except:
                        pass
                
                accounts_list.append({
                    'id': row.target_user_id,
                    'discord_user_id': str(row.target_user_id),
                    'discord_name': discord_name,
                    'twitch_user_id': 'N/A',
                    'twitch_username': row.requested_twitch_username,
                    'linked_at': row.created_at.strftime('%Y-%m-%d %H:%M:%S') if row.created_at else None
                })
            
            return jsonify({
                'accounts': accounts_list,
                'count': len(accounts_list)
            })
            
    except Exception as e:
        app.logger.error(f"Error fetching linked Twitch accounts: {e}")
        return jsonify({'accounts': [], 'count': 0}), 500

@app.route('/api/guilds/<string:guild_id>/roles', methods=['GET'])
@require_api_key
def api_get_guild_roles(guild_id):
    """Get all roles for a specific guild."""
    try:
        err = _require_guild_access(guild_id)
        if err:
            return err

        if not bot_instance or not bot_instance.is_ready():
            return jsonify({'roles': [], 'error': 'Bot not ready'}), 503
        
        guild = bot_instance.get_guild(int(guild_id))
        if not guild:
            return jsonify({'roles': [], 'error': 'Guild not found'}), 404
        
        roles_list = []
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.name == '@everyone':
                continue
            roles_list.append({
                'id': str(role.id),
                'name': role.name,
                'color': str(role.color),
                'position': role.position,
                'mentionable': role.mentionable
            })
        
        return jsonify({'roles': roles_list, 'count': len(roles_list)})
    except Exception as e:
        app.logger.error(f"Error fetching guild roles: {e}")
        return jsonify({'roles': [], 'error': str(e)}), 500

@app.route('/api/settings/<string:guild_id>', methods=['GET'])
@require_api_key
def api_get_guild_settings(guild_id):
    """Get settings for a specific guild."""
    try:
        err = _require_guild_access(guild_id)
        if err:
            return err

        with DatabaseSession() as db_session:
            guild = db_session.query(Guild).filter_by(id=int(guild_id)).first()
            notification_settings = db_session.query(NotificationSettings).filter_by(guild_id=int(guild_id)).first()
            
            settings = {
                'server_description': guild.server_description if guild else '',
                'live_role_id': str(guild.live_role_id) if guild and guild.live_role_id else None,
                'live_tracking_enabled': guild.live_tracking_enabled if guild else False,
                'live_role_trains_only': guild.live_role_trains_only if guild else False,
                'notification_time': 60,
                'auto_ping_enabled': True,
                'backup_system_enabled': True,
                'twitch_chat_bot_enabled': True,
                'auto_announcements_enabled': True,
                'ten_minute_warnings_enabled': True,
                'one_hour_warnings_enabled': True,
                'comprehensive_reports_enabled': True,
                'attendance_interval_minutes': 25,
            }
            
            if notification_settings:
                settings.update({
                    'notification_time': notification_settings.ping_timeout_minutes or 60,
                    'auto_ping_enabled': notification_settings.auto_ping_enabled,
                    'backup_system_enabled': notification_settings.backup_system_enabled,
                    'twitch_chat_bot_enabled': notification_settings.twitch_chat_bot_enabled,
                    'auto_announcements_enabled': notification_settings.auto_announcements_enabled,
                    'ten_minute_warnings_enabled': notification_settings.ten_minute_warnings_enabled,
                    'one_hour_warnings_enabled': notification_settings.one_hour_warnings_enabled,
                    'comprehensive_reports_enabled': notification_settings.comprehensive_reports_enabled,
                    'attendance_interval_minutes': notification_settings.attendance_interval_minutes or 25,
                    'raid_notification_channel_id': str(notification_settings.raid_notification_channel_id) if notification_settings.raid_notification_channel_id else None,
                    'raid_ping_role_id': str(notification_settings.raid_ping_role_id) if notification_settings.raid_ping_role_id else None,
                    'backup_notification_channel_id': str(notification_settings.backup_notification_channel_id) if notification_settings.backup_notification_channel_id else None,
                    'backup_ping_role_id': str(notification_settings.backup_ping_role_id) if notification_settings.backup_ping_role_id else None,
                    'stream_notification_channel_id': str(notification_settings.stream_notification_channel_id) if notification_settings.stream_notification_channel_id else None,
                    'attendance_channel_id': str(notification_settings.attendance_channel_id) if notification_settings.attendance_channel_id else None,
                    'comprehensive_report_channel_id': str(notification_settings.comprehensive_report_channel_id) if notification_settings.comprehensive_report_channel_id else None,
                })
            
            return jsonify({'settings': settings, 'guild_id': guild_id})
    except Exception as e:
        app.logger.error(f"Error fetching guild settings: {e}")
        return jsonify({'settings': {}, 'error': str(e)}), 500

@app.route('/api/settings/<string:guild_id>/server-description', methods=['POST'])
@require_api_key
def api_update_server_description(guild_id):
    """Update server description for a guild."""
    try:
        err = _require_guild_access(guild_id)
        if err:
            return err

        data = request.get_json()
        description = data.get('description', '')
        
        with DatabaseSession() as db_session:
            guild = db_session.query(Guild).filter_by(id=int(guild_id)).first()
            if not guild:
                return jsonify({'error': 'Guild not found'}), 404
            
            guild.server_description = description
            db_session.commit()
            
            return jsonify({'status': 'success', 'description': description})
    except Exception as e:
        app.logger.error(f"Error updating server description: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/<string:guild_id>/notification-time', methods=['POST'])
@require_api_key
def api_update_notification_time(guild_id):
    """Update notification time for a guild."""
    try:
        err = _require_guild_access(guild_id)
        if err:
            return err

        data = request.get_json()
        minutes = data.get('minutes', 60)
        
        with DatabaseSession() as db_session:
            settings = db_session.query(NotificationSettings).filter_by(guild_id=int(guild_id)).first()
            if not settings:
                settings = NotificationSettings(guild_id=int(guild_id))
                db_session.add(settings)
            
            settings.ping_timeout_minutes = minutes
            db_session.commit()
            
            return jsonify({'status': 'success', 'minutes': minutes})
    except Exception as e:
        app.logger.error(f"Error updating notification time: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/<string:guild_id>/live-role', methods=['POST'])
@require_api_key
def api_update_live_role(guild_id):
    """Update live role settings for a guild."""
    try:
        err = _require_guild_access(guild_id)
        if err:
            return err

        data = request.get_json()
        
        with DatabaseSession() as db_session:
            guild = db_session.query(Guild).filter_by(id=int(guild_id)).first()
            if not guild:
                return jsonify({'error': 'Guild not found'}), 404
            
            if 'live_role_id' in data:
                guild.live_role_id = int(data['live_role_id']) if data['live_role_id'] else None
            if 'live_tracking_enabled' in data:
                guild.live_tracking_enabled = data['live_tracking_enabled']
            if 'live_role_trains_only' in data:
                guild.live_role_trains_only = data['live_role_trains_only']
            
            db_session.commit()
            
            return jsonify({
                'status': 'success',
                'live_role_id': str(guild.live_role_id) if guild.live_role_id else None,
                'live_tracking_enabled': guild.live_tracking_enabled,
                'live_role_trains_only': guild.live_role_trains_only
            })
    except Exception as e:
        app.logger.error(f"Error updating live role settings: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/<string:guild_id>/notifications', methods=['POST'])
@require_api_key
def api_update_notification_settings(guild_id):
    """Update notification settings for a guild."""
    try:
        err = _require_guild_access(guild_id)
        if err:
            return err

        data = request.get_json()
        
        with DatabaseSession() as db_session:
            settings = db_session.query(NotificationSettings).filter_by(guild_id=int(guild_id)).first()
            if not settings:
                settings = NotificationSettings(guild_id=int(guild_id))
                db_session.add(settings)
            
            updatable_fields = [
                'auto_ping_enabled', 'backup_system_enabled', 'twitch_chat_bot_enabled',
                'auto_announcements_enabled', 'ten_minute_warnings_enabled',
                'one_hour_warnings_enabled', 'comprehensive_reports_enabled',
                'attendance_interval_minutes'
            ]
            
            for field in updatable_fields:
                if field in data:
                    setattr(settings, field, data[field])
            
            channel_fields = [
                'raid_notification_channel_id', 'raid_ping_role_id',
                'backup_notification_channel_id', 'backup_ping_role_id',
                'stream_notification_channel_id', 'attendance_channel_id',
                'comprehensive_report_channel_id'
            ]
            
            for field in channel_fields:
                if field in data:
                    setattr(settings, field, int(data[field]) if data[field] else None)
            
            db_session.commit()
            
            return jsonify({'status': 'success'})
    except Exception as e:
        app.logger.error(f"Error updating notification settings: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/live-users/<string:guild_id>', methods=['GET'])
@require_api_key
def api_get_live_users(guild_id):
    """Get currently live users in a guild."""
    try:
        err = _require_guild_access(guild_id)
        if err:
            return err

        if not bot_instance or not bot_instance.is_ready():
            return jsonify({'live_users': [], 'count': 0}), 503
        
        guild = bot_instance.get_guild(int(guild_id))
        if not guild:
            return jsonify({'live_users': [], 'error': 'Guild not found'}), 404
        
        live_users = []
        with DatabaseSession() as db_session:
            users = db_session.query(User).filter(
                User.guild_id == int(guild_id),
                User.twitch_login.isnot(None)
            ).all()
            
            for user in users:
                member = guild.get_member(int(user.discord_id))
                if member:
                    is_streaming = any(
                        isinstance(a, discord.Streaming) for a in member.activities
                    )
                    if is_streaming:
                        streaming_activity = next(
                            (a for a in member.activities if isinstance(a, discord.Streaming)), None
                        )
                        live_users.append({
                            'discord_name': member.display_name,
                            'twitch_login': user.twitch_login,
                            'stream_title': streaming_activity.name if streaming_activity else 'Streaming',
                            'stream_url': streaming_activity.url if streaming_activity else f'https://twitch.tv/{user.twitch_login}',
                            'game': streaming_activity.game if streaming_activity else None
                        })
        
        return jsonify({'live_users': live_users, 'count': len(live_users)})
    except Exception as e:
        app.logger.error(f"Error fetching live users: {e}")
        return jsonify({'live_users': [], 'error': str(e)}), 500

@app.route('/api/maintenance', methods=['GET', 'POST'])
@require_api_key
def api_maintenance_mode():
    """Get or set maintenance mode."""
    import asyncio
    
    if request.method == 'GET':
        try:
            if not bot_instance:
                return jsonify({'enabled': False}), 503
            
            # Get maintenance mode using async method
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                is_maintenance = loop.run_until_complete(bot_instance.maintenance_manager.get())
            finally:
                loop.close()
            
            return jsonify({
                'enabled': is_maintenance,
                'message': 'Maintenance mode is active' if is_maintenance else 'Bot is operational'
            })
            
        except Exception as e:
            app.logger.error(f"Error getting maintenance mode: {e}")
            return jsonify({'enabled': False, 'error': str(e)}), 500
    
    elif request.method == 'POST':
        try:
            provided_key = request.headers.get('X-API-Key', '')
            key_type, _ = _classify_token(provided_key)
            if key_type != "owner":
                if not os.environ.get('DASHBOARD_OWNER_KEY', ''):
                    return jsonify({'error': 'Service unavailable: Owner key not configured'}), 503
                return jsonify({'error': 'Forbidden: Owner-level access required to change maintenance mode'}), 403

            data = request.get_json()
            enabled = data.get('enabled', False)
            
            if not bot_instance:
                return jsonify({'status': 'error', 'message': 'Bot not ready'}), 503
            
            # Set maintenance mode using async method
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success = loop.run_until_complete(bot_instance.maintenance_manager.set(enabled))
            finally:
                loop.close()
            
            if success:
                message = 'Maintenance mode enabled' if enabled else 'Maintenance mode disabled'
                return jsonify({
                    'status': 'success',
                    'message': message,
                    'enabled': enabled
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to update maintenance mode'
                }), 500
            
        except Exception as e:
            app.logger.error(f"Error setting maintenance mode: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


# ========================================
# TWITCH OAUTH ROUTES
# ========================================

from utils.oauth_tokens import create_oauth_binding_token, verify_oauth_binding_token


def _get_oauth_base_url():
    """Return the base URL for OAuth callbacks.
    Delegates to utils.bot_urls.get_oauth_base_url() so there is a single
    source of truth for URL detection logic."""
    from utils.bot_urls import get_oauth_base_url
    return get_oauth_base_url()

@app.route('/oauth/twitch/authorize')
def oauth_twitch_authorize():
    """Initiate Twitch OAuth flow.

    Expects a short-lived HMAC-signed ``token`` query parameter (created by
    :func:`utils.oauth_tokens.create_oauth_binding_token`) that encodes the
    Discord user ID.  Raw ``discord_user_id`` query parameters are no longer
    accepted so that callers cannot bind tokens to arbitrary Discord accounts.
    """
    try:
        from models import TwitchOAuthToken
        client_id = os.getenv('TWITCH_CLIENT_ID')
        redirect_uri = _get_oauth_base_url() + '/oauth/twitch/callback'
        
        scopes = 'chat:read chat:edit user:read:email moderator:read:chatters user:write:chat channel:manage:raids'
        
        binding_token = request.args.get('token', '')
        token_data = verify_oauth_binding_token(binding_token)
        if token_data is None:
            app.logger.warning("oauth_twitch_authorize: invalid or expired binding token")
            return "<h1>OAuth Error</h1><p>This authorization link is invalid or has expired. Please request a new one from the bot.</p>", 400

        discord_user_id, token_guild_id = token_data

        state = secrets.token_urlsafe(32)
        
        if not hasattr(app, 'oauth_states'):
            app.oauth_states = {}

        # Prune stale states on each new authorize request to prevent unbounded
        # in-memory growth from incomplete or abandoned OAuth flows.
        _now = datetime.now()
        _PRUNE_AGE_SECONDS = 600  # match binding token TTL
        stale_keys = [
            k for k, v in app.oauth_states.items()
            if (_now - v.get('created_at', _now)).total_seconds() > _PRUNE_AGE_SECONDS
        ]
        for k in stale_keys:
            app.oauth_states.pop(k, None)

        app.oauth_states[state] = {
            'created_at': _now,
            'discord_user_id': str(discord_user_id),
            'guild_id': str(token_guild_id) if token_guild_id else None,
        }
        
        auth_url = (
            f"https://id.twitch.tv/oauth2/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope={scopes}"
            f"&state={state}"
            f"&force_verify=true"
        )
        
        return redirect(auth_url)
        
    except Exception as e:
        app.logger.error(f"Error initiating Twitch OAuth: {e}")
        return f"<h1>OAuth Error</h1><p>{html.escape(str(e))}</p>", 500

@app.route('/oauth/twitch/callback')
def oauth_twitch_callback():
    """Handle Twitch OAuth callback."""
    try:
        from models import TwitchOAuthToken
        from database import DatabaseSession
        
        # Get authorization code and state
        code = request.args.get('code')
        state = request.args.get('state')
        error = request.args.get('error')
        
        if error:
            return f"<h1>OAuth Error</h1><p>Twitch returned an error: {html.escape(error)}</p>", 400
        
        if not code or not state:
            return "<h1>OAuth Error</h1><p>Missing code or state parameter</p>", 400
        
        # Verify state (CSRF protection)
        if not hasattr(app, 'oauth_states') or state not in app.oauth_states:
            return "<h1>OAuth Error</h1><p>Invalid state parameter</p>", 400

        # Enforce state expiry — states must be completed within 10 minutes of
        # creation, matching the binding token's own TTL.
        _OAUTH_STATE_MAX_AGE_SECONDS = 600
        state_data = app.oauth_states.pop(state)  # consume immediately to prevent replay
        state_created_at = state_data.get('created_at')
        if state_created_at and (datetime.now() - state_created_at).total_seconds() > _OAUTH_STATE_MAX_AGE_SECONDS:
            age_seconds = int((datetime.now() - state_created_at).total_seconds())
            discord_uid = state_data.get('discord_user_id', 'unknown')
            app.logger.warning(
                f"oauth_twitch_callback: expired state rejected for discord_user_id={discord_uid} "
                f"(age={age_seconds}s, limit={_OAUTH_STATE_MAX_AGE_SECONDS}s)"
            )
            return "<h1>OAuth Error</h1><p>This authorization link has expired. Please request a new one from the bot.</p>", 400

        # Exchange code for access token
        client_id = os.getenv('TWITCH_CLIENT_ID')
        client_secret = os.getenv('TWITCH_CLIENT_SECRET')
        redirect_uri = _get_oauth_base_url() + '/oauth/twitch/callback'
        
        token_response = requests.post(
            'https://id.twitch.tv/oauth2/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': redirect_uri
            }
        )
        
        if token_response.status_code != 200:
            return f"<h1>OAuth Error</h1><p>Failed to exchange code for token: {html.escape(token_response.text)}</p>", 500
        
        token_data = token_response.json()
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in')  # seconds
        
        # Get user info from Twitch
        user_response = requests.get(
            'https://api.twitch.tv/helix/users',
            headers={
                'Authorization': f'Bearer {access_token}',
                'Client-Id': client_id
            }
        )
        
        if user_response.status_code != 200:
            return f"<h1>OAuth Error</h1><p>Failed to get user info: {html.escape(user_response.text)}</p>", 500
        
        user_data = user_response.json()['data'][0]
        twitch_user_id = user_data['id']
        twitch_username = user_data['login']
        
        discord_user_id = int(state_data.get('discord_user_id', 0))
        _raw_guild_id = state_data.get('guild_id')
        state_guild_id = int(_raw_guild_id) if _raw_guild_id else None
        
        with DatabaseSession() as session:
            existing_token = session.query(TwitchOAuthToken).filter_by(
                twitch_user_id=twitch_user_id
            ).first()
            
            expires_at = datetime.now() + timedelta(seconds=expires_in) if expires_in else None
            
            if existing_token:
                existing_token.access_token = access_token
                existing_token.refresh_token = refresh_token
                existing_token.twitch_username = twitch_username
                existing_token.scopes = token_data.get('scope', [])
                existing_token.expires_at = expires_at
                existing_token.is_active = True
                existing_token.last_used_at = datetime.now()
                if discord_user_id:
                    existing_token.user_id = discord_user_id
                if state_guild_id and not existing_token.guild_id:
                    existing_token.guild_id = state_guild_id
            else:
                new_token = TwitchOAuthToken(
                    user_id=discord_user_id,
                    guild_id=state_guild_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    twitch_user_id=twitch_user_id,
                    twitch_username=twitch_username,
                    scopes=token_data.get('scope', []),
                    expires_at=expires_at,
                    is_active=True,
                    last_used_at=datetime.now()
                )
                session.add(new_token)

            # Update User.twitch_login with the OAuth-verified Twitch identity so that
            # the full account binding (attendance tracking, train records, etc.) is
            # completed through the verified flow rather than self-reported usernames.
            if discord_user_id:
                from models import User
                from utils.twitch_sync import TwitchLinkSyncService
                db_user = session.query(User).filter_by(id=discord_user_id).first()
                if db_user:
                    db_user.twitch_login = twitch_username
                    db_user.twitch_display_name = user_data.get('display_name', twitch_username)
                    db_user.twitch_linked_at = datetime.now()
                    db_user.twitch_source = 'twitch_oauth'
                    db_user.twitch_consent = True
                    guild_id = db_user.guild_id
                    TwitchLinkSyncService.sync_user_twitch(
                        session, discord_user_id, guild_id, twitch_username
                    )
                else:
                    app.logger.warning(
                        f"oauth_twitch_callback: no User row found for discord_user_id={discord_user_id}; "
                        f"TwitchOAuthToken saved but User.twitch_login could not be updated. "
                        f"User should run /linktwitch to trigger account creation."
                    )

            session.commit()

        # If the newly-saved token belongs to gl_stoney or the IRC fallback account,
        # clear the auth-failure cooldown so the bot reconnects immediately.
        try:
            if twitch_username.lower() in ('gl_stoney', 'unicornsp4rkl3s'):
                monitor = getattr(bot_instance, 'twitch_chat_monitor', None)
                if monitor and getattr(monitor, '_irc_auth_failed_until', None):
                    monitor._irc_auth_failed_until = None
                    app.logger.info(f"🔓 IRC auth cooldown cleared — new token registered for {twitch_username}")
        except Exception:
            pass

        # Notify admin about OAuth authorization
        try:
            owner_id = int(os.getenv('OWNER_ID_DISCORD', '887354716751810560'))
            
            async def notify_admin_oauth():
                try:
                    admin_user = bot_instance.get_user(owner_id)
                    if not admin_user:
                        admin_user = await bot_instance.fetch_user(owner_id)
                    if admin_user:
                        discord_user = None
                        if discord_user_id:
                            try:
                                discord_user = bot_instance.get_user(discord_user_id)
                                if not discord_user:
                                    discord_user = await bot_instance.fetch_user(discord_user_id)
                            except Exception:
                                pass
                        
                        display_name = discord_user.display_name if discord_user else f"User {discord_user_id}"
                        username = discord_user.name if discord_user else str(discord_user_id)
                        mention = discord_user.mention if discord_user else f"<@{discord_user_id}>"
                        
                        notify_embed = discord.Embed(
                            title="🎮 User Authorized Twitch OAuth (Auto-Raids)",
                            description=f"**{display_name}** (`{username}`) has authorized their Twitch account for auto-raids!",
                            color=0x9146ff,
                            timestamp=datetime.utcnow()
                        )
                        notify_embed.add_field(
                            name="🔗 Authorization Details",
                            value=f"**Discord User:** {mention}\n**Twitch Username:** `{twitch_username}`\n**Twitch ID:** `{twitch_user_id}`\n**Type:** OAuth Auto-Raid Authorization",
                            inline=False
                        )
                        if discord_user:
                            notify_embed.set_thumbnail(url=discord_user.display_avatar.url)
                        notify_embed.set_footer(text=f"Authorized via OAuth flow • {datetime.utcnow().strftime('%I:%M %p UTC')}")
                        dm_channel = await admin_user.create_dm()
                        await dm_channel.send(embed=notify_embed)
                except Exception as e:
                    app.logger.warning(f"Could not notify admin about OAuth: {e}")
            
            if bot_instance and bot_instance.is_ready():
                asyncio.run_coroutine_threadsafe(notify_admin_oauth(), bot_instance.loop)
        except Exception as notify_err:
            app.logger.warning(f"Error setting up admin OAuth notification: {notify_err}")
        
        return f"""
        <html>
        <head><title>OAuth Success</title></head>
        <body style="font-family: Arial; padding: 40px; text-align: center;">
            <h1 style="color: #6441a5;">✅ Twitch OAuth Successful!</h1>
            <p>Connected as: <strong>{html.escape(twitch_username)}</strong></p>
            <p>The bot can now monitor Twitch chat for attendance tracking.</p>
            <p>You can close this window and return to Discord.</p>
        </body>
        </html>
        """
        
    except Exception as e:
        app.logger.error(f"Error handling Twitch OAuth callback: {e}")
        app.logger.error(traceback.format_exc())
        return f"<h1>OAuth Error</h1><p>{html.escape(str(e))}</p>", 500

@app.route('/oauth/twitch/status')
@require_api_key
def oauth_twitch_status():
    """Show current Twitch OAuth status."""
    try:
        from models import TwitchOAuthToken
        from database import DatabaseSession
        
        with DatabaseSession() as session:
            active_token = session.query(TwitchOAuthToken).filter_by(is_active=True).first()
            
            if not active_token:
                return """
                <html>
                <head><title>Twitch OAuth Status</title></head>
                <body style="font-family: Arial; padding: 40px;">
                    <h1>&#9888;&#65039; No Active Twitch OAuth Token</h1>
                    <p>Chat monitoring is not available.</p>
                    <p>To authorize, use the <strong>/twitchoauth</strong> command in Discord to receive a personal authorization link.</p>
                </body>
                </html>
                """
            
            expires_in = (active_token.expires_at - datetime.now()).total_seconds() if active_token.expires_at else None
            expires_str = f"{int(expires_in / 3600)} hours" if expires_in else "Never"
            
            safe_username = html.escape(str(active_token.twitch_username or ''))
            safe_user_id = html.escape(str(active_token.twitch_user_id or ''))
            safe_expires = html.escape(expires_str)
            safe_last_used = html.escape(
                active_token.last_used_at.strftime('%Y-%m-%d %H:%M:%S')
                if active_token.last_used_at else 'Never'
            )

            return f"""
            <html>
            <head><title>Twitch OAuth Status</title></head>
            <body style="font-family: Arial; padding: 40px;">
                <h1 style="color: #6441a5;">&#9989; Twitch OAuth Active</h1>
                <p><strong>Username:</strong> {safe_username}</p>
                <p><strong>User ID:</strong> {safe_user_id}</p>
                <p><strong>Expires:</strong> {safe_expires}</p>
                <p><strong>Last Used:</strong> {safe_last_used}</p>
                <p>To re-authorize, use the <strong>/twitchoauth</strong> command in Discord.</p>
            </body>
            </html>
            """
            
    except Exception as e:
        app.logger.error(f"Error checking Twitch OAuth status: {e}")
        return f"<h1>Error</h1><p>{html.escape(str(e))}</p>", 500






@app.route('/webhooks/tiktok/<int:guild_id>', methods=['POST'])
def tiktok_webhook(guild_id):
    """
    Inbound webhook that accepts TikTok video URLs from Make / Zapier / IFTTT.
    Authenticates via ?token=<secret> query param.
    Body can be JSON {"url": "..."} or {"text": "..."} or plain text.
    """
    import re as _re
    import asyncio as _asyncio
    import aiohttp as _aiohttp

    try:
        from models import Guild as _Guild
        from database import DatabaseSession as _DB

        # --- 1. load guild record and validate token ---
        with _DB() as _sess:
            _guild = _sess.query(_Guild).filter_by(id=guild_id).first()
            if not _guild:
                return {'error': 'Unknown server'}, 404
            _expected_token = _guild.tiktok_webhook_token
            _feed_channel_id = _guild.tiktok_feed_channel_id

        if not _expected_token:
            return {'error': 'Webhook not configured — run !tiktokwebhookurl in Discord first'}, 403

        _provided = request.args.get('token') or request.headers.get('X-Webhook-Token', '')
        if not _provided or _provided != _expected_token:
            return {'error': 'Invalid token'}, 403

        if not _feed_channel_id:
            return {'error': 'No TikTok feed channel set — run !settiktokfeed in Discord first'}, 400

        # --- 2. extract TikTok URL from body ---
        _tiktok_pattern = _re.compile(
            r'https?://(?:www\.|vm\.|m\.|vt\.)?tiktok\.com/\S+',
            _re.IGNORECASE
        )
        _body_text = ''
        if request.is_json:
            _data = request.get_json(silent=True) or {}
            _body_text = _data.get('url') or _data.get('text') or _data.get('message') or ''
            if not _body_text:
                _body_text = ' '.join(str(v) for v in _data.values())
        else:
            _body_text = request.get_data(as_text=True) or ''

        _urls = _tiktok_pattern.findall(_body_text)
        if not _urls:
            return {'error': 'No TikTok URL found in request body'}, 400

        _tiktok_url = _urls[0].rstrip('.,)')

        # --- 3. post to Discord asynchronously ---
        async def _post_to_discord():
            _channel = bot_instance.get_channel(_feed_channel_id) if bot_instance else None
            if not _channel:
                return False

            _tt_data = {}
            try:
                async with _aiohttp.ClientSession() as _http:
                    async with _http.get(
                        'https://www.tiktok.com/oembed',
                        params={'url': _tiktok_url},
                        timeout=_aiohttp.ClientTimeout(total=8)
                    ) as _r:
                        if _r.status == 200:
                            _tt_data = await _r.json()
            except Exception:
                pass

            _title = _tt_data.get('title', '').strip() or 'New TikTok Video'
            _author = _tt_data.get('author_name', '').strip() or 'TikTok'
            _author_url = _tt_data.get('author_url', '') or _tiktok_url
            _thumb = _tt_data.get('thumbnail_url', '')

            _embed = discord.Embed(
                title=_title,
                url=_tiktok_url,
                color=0x010101
            )
            _embed.set_author(name=f'🎵 {_author}', url=_author_url)
            if _thumb:
                _embed.set_image(url=_thumb)
            _embed.set_footer(text='TikTok • Auto-posted')
            await _channel.send(embed=_embed)
            return True

        if bot_instance and bot_instance.loop and bot_instance.loop.is_running():
            _future = _asyncio.run_coroutine_threadsafe(_post_to_discord(), bot_instance.loop)
            _future.result(timeout=15)

        return {'status': 'ok', 'url': _tiktok_url}, 200

    except Exception as _e:
        logger.error(f'TikTok webhook error: {_e}', exc_info=True)
        return {'error': str(_e)}, 500


@app.route('/donate')
def donate_page():
    """Render the donation page with a Stripe-powered checkout form."""
    bot_name = os.getenv('BOT_NAME', 'Game Lounge Train')
    monthly_cost = os.getenv('BOT_MONTHLY_HOSTING_COST', '40')
    return render_template('donate.html', bot_name=bot_name, monthly_cost=monthly_cost)


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create a Stripe Checkout session for a one-time donation and redirect to it."""
    from stripe_client import get_stripe_client, StripeNotConnectedError
    from utils.bot_urls import get_oauth_base_url

    try:
        raw_amount = request.form.get('amount', '')
        amount_dollars = float(raw_amount)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid donation amount'}), 400

    if amount_dollars <= 0 or amount_dollars > 10000:
        return jsonify({'error': 'Donation amount must be between $0.01 and $10,000'}), 400

    amount_cents = int(round(amount_dollars * 100))
    bot_name = os.getenv('BOT_NAME', 'Game Lounge Train')
    base_url = get_oauth_base_url()

    try:
        client = get_stripe_client()
        session = client.checkout.sessions.create(
            params={
                'mode': 'payment',
                'payment_method_types': ['card'],
                'line_items': [{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f'Donation to {bot_name}',
                            'description': 'One-time donation to support 24/7 bot hosting costs',
                        },
                        'unit_amount': amount_cents,
                    },
                    'quantity': 1,
                }],
                'success_url': f'{base_url}/donate/success?session_id={{CHECKOUT_SESSION_ID}}',
                'cancel_url': f'{base_url}/donate/cancel',
            }
        )
    except StripeNotConnectedError as e:
        app.logger.error(f"Stripe not connected for checkout session: {e}")
        return jsonify({'error': 'Donations are temporarily unavailable. Please try again later.'}), 503
    except Exception as e:
        app.logger.error(f"Failed to create Stripe checkout session: {e}")
        return jsonify({'error': 'Could not start checkout. Please try again later.'}), 500

    return redirect(session.url, code=303)


@app.route('/donate/success')
def donate_success():
    """Landing page after a successful Stripe donation checkout."""
    bot_name = os.getenv('BOT_NAME', 'Game Lounge Train')
    return render_template('donate_success.html', bot_name=bot_name)


@app.route('/donate/cancel')
def donate_cancel():
    """Landing page when a user cancels the Stripe donation checkout."""
    bot_name = os.getenv('BOT_NAME', 'Game Lounge Train')
    return render_template('donate_cancel.html', bot_name=bot_name)


@app.route('/api/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events (currently: log completed donation checkouts).

    Verifies the signature using the webhook secret from the connected Stripe
    integration so unauthenticated callers can't forge payment events.
    """
    import stripe as _stripe
    from stripe_client import get_stripe_credentials, StripeNotConnectedError

    payload = request.data
    sig_header = request.headers.get('Stripe-Signature', '')

    try:
        _, webhook_secret = get_stripe_credentials()
    except StripeNotConnectedError as e:
        app.logger.error(f"Stripe webhook received but Stripe not connected: {e}")
        return jsonify({'error': 'Stripe not configured'}), 503

    if not webhook_secret:
        app.logger.warning("Stripe webhook received but no webhook secret configured — rejecting")
        return jsonify({'error': 'Webhook not configured'}), 503

    try:
        event = _stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except _stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    event_type = event.get('type', '')
    if event_type == 'checkout.session.completed':
        session_obj = event['data']['object']
        amount_total = session_obj.get('amount_total', 0)
        app.logger.info(
            f"✅ Stripe donation received: ${amount_total / 100:.2f} "
            f"(session {session_obj.get('id')})"
        )
    elif event_type in ('customer.subscription.created', 'customer.subscription.updated',
                         'customer.subscription.deleted', 'invoice.payment_failed'):
        app.logger.info(f"Stripe subscription event received: {event_type}")
    else:
        app.logger.debug(f"Unhandled Stripe webhook event type: {event_type}")

    return jsonify({'received': True}), 200


def get_dashboard_data():
    """Get data for dashboard template."""
    bot_status = get_bot_internal_status()
    db_stats = get_dashboard_database_stats()
    forwarding_configs = get_dashboard_forwarding_configs()
    
    uptime = datetime.utcnow() - bot_start_time
    uptime_str = f"{uptime.days}d {uptime.seconds//3600}h {(uptime.seconds%3600)//60}m"
    
    return {
        'uptime': uptime_str,
        'guilds': db_stats.get('guilds', 0),
        'users': db_stats.get('users', 0),
        'messages': db_stats.get('messages', 0),
        'forwarding': db_stats.get('active_forwarding', 0),
        'forwarding_configs': forwarding_configs
    }

def self_ping():
    """Self-ping the public health endpoint to keep autoscale instances warm.

    Autoscale deployments may spin down if there is no incoming HTTP traffic.
    Pinging the public URL every 4 minutes registers as real external traffic
    and prevents idle scale-to-zero while the Discord bot is running.
    """
    import time as _time
    # Give Flask time to start before the first ping
    _time.sleep(15)
    while True:
        try:
            from utils.bot_urls import get_oauth_base_url
            public_url = get_oauth_base_url()
            requests.get(f'{public_url}/health', timeout=10)
        except Exception:
            # Fall back to localhost ping on any error
            try:
                port = int(os.getenv('PORT', os.getenv('REPLIT_PORT', 5000)))
                requests.get(f'http://localhost:{port}/health', timeout=5)
            except Exception:
                pass
        _time.sleep(240)  # ping every 4 minutes

def start_keep_alive_server():
    """Start the keep-alive Flask server."""
    port = int(os.getenv('PORT', os.getenv('REPLIT_PORT', 5000)))
    
    def run_app():
        try:
            app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
        except Exception as e:
            logger = logging.getLogger('discord_bot')
            logger.error(f"Flask server failed to start on port {port}: {e}")
    
    # Start Flask server in thread
    flask_thread = threading.Thread(target=run_app, daemon=True)
    flask_thread.start()
    
    # Start self-ping thread
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()

# ========================================
# DISCORD BOT
# ========================================

class DiscordBot(commands.Bot):
    """Custom Discord bot class with comprehensive features."""
    
    def __init__(self):
        """Initialize the bot with proper intents and configuration."""
        intents = discord.Intents()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        intents.reactions = True  # Required for on_reaction_add/remove events
        intents.members = True  # Required to see role members
        
        super().__init__(
            command_prefix=BotConfig.COMMAND_PREFIX,
            intents=intents,
            help_command=None,
            case_insensitive=True
        )
        
        self.logger = logging.getLogger('discord_bot')
        self.start_time = datetime.utcnow()
        self.config = BotConfig()
        
        # Initialize database
        try:
            self.db_manager = get_db_manager()
            self.logger.info("Database connection established")
        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}")
            self.db_manager = None
        
        # Initialize server permissions manager
        try:
            from utils.server_permissions import ServerPermissionsManager
            if self.db_manager:
                self.permissions_manager = ServerPermissionsManager(self.db_manager.get_session())
                self.logger.info("✅ Server permissions manager initialized")
            else:
                self.permissions_manager = None
        except Exception as e:
            self.logger.error(f"Failed to initialize permissions manager: {e}")
            self.permissions_manager = None
        
        # Bot statistics
        self.commands_executed = 0
        self.messages_seen = 0
        
        # Thread-safe duplicate prevention system
        self._message_processing_lock = threading.Lock()
        self._processed_messages = set()
        self._message_processing_log = {}
        
        # Initialize stability manager and memory management
        self.stability_manager = None
        self.last_memory_check = datetime.now()
        self.memory_threshold = 95  # Percentage - use process-level memory, not system-wide
        self._last_cleanup = datetime.now()
        self.error_counts = {}
        self.circuit_breakers = {}
        
        # Dashboard management attributes
        self.trusted_users = set()
        self.monitored_streams = set()
        self.notification_settings = {}
        self.auto_ping_enabled = True
        self.pending_status_update = None
        
        # Message forwarding configuration
        self.bot_active = False
        self.source_channel_id = int(os.getenv('SOURCE_CHANNEL_ID', '1398094211320119358'))
        self.target_channel_id = int(os.getenv('TARGET_CHANNEL_ID', '1183143967622168668'))
        self.authorized_role_name = os.getenv('AUTHORIZED_ROLE_NAME', 'supervisor')
        
        # Store bot owner ID from config
        self.owner_id = BotConfig.OWNER_ID_DISCORD
        self.logger.info(f"Bot owner ID set to: {self.owner_id}")
        
        # Parse authorized user IDs
        authorized_users_str = os.getenv('AUTHORIZED_USER_IDS', str(BotConfig.OWNER_ID_DISCORD))
        self.authorized_user_ids = set()
        if authorized_users_str:
            try:
                self.authorized_user_ids = {int(uid.strip()) for uid in authorized_users_str.split(',') if uid.strip()}
            except ValueError:
                self.logger.warning("Invalid AUTHORIZED_USER_IDS format, using default")
                self.authorized_user_ids = {BotConfig.OWNER_ID_DISCORD}
        
        # Support knowledge base
        self.support_solutions = {
            'forwarding': {
                'keywords': ['forward', 'message', 'channel', 'send', 'copy', 'mirror'],
                'solutions': [
                    '• Use `!addforward #source-channel #target-channel` to create message forwarding',
                    '• Use `!forwards` to view all your forwarding configurations',
                    '• Use `!removeforward` to delete unwanted forwarding rules',
                    '• Make sure the bot has permissions in both channels',
                    '• Channels must be from different servers for cross-server forwarding'
                ]
            },
            'commands': {
                'keywords': ['command', 'help', 'usage', 'how to', 'syntax'],
                'solutions': [
                    '• Use `!help` to see all available commands',
                    '• Commands use the `!` prefix format',
                    '• Use `!dash` to access the bot dashboard with live statistics'
                ]
            },
            'dashboard': {
                'keywords': ['dashboard', 'web', 'interface', 'manage', 'monitor'],
                'solutions': [
                    '• Use `!dash` command to get instant dashboard with live statistics',
                    '• Shows real-time bot status, server info, and forwarding configs',
                    '• Dashboard displays authentic Discord server and channel names',
                    '• Updated automatically with current information each time you use `!dash`'
                ]
            }
        }
        
        # Maintenance mode support
        from utils.maintenance_manager import maintenance_manager
        self.maintenance_manager = maintenance_manager
        
        # Add global command check for maintenance mode and server permissions
        async def global_maintenance_check(ctx):
            """Global check that blocks commands during maintenance mode."""
            # Bot owner ALWAYS has full access
            if hasattr(ctx.bot, 'bot_owner_id') and ctx.author.id == ctx.bot.bot_owner_id:
                return True
            
            # Whitelisted commands that work even in maintenance mode
            whitelisted_commands = ['ping', 'maintenance', 'help']
            
            # Check if command is whitelisted
            if ctx.command and ctx.command.name in whitelisted_commands:
                return True
            
            # Check maintenance mode
            in_maintenance = await self.maintenance_manager.get()
            
            if in_maintenance:
                # Block command with maintenance message
                embed = discord.Embed(
                    title="🔧 Bot Under Maintenance",
                    description="The bot is currently undergoing maintenance. Most commands are temporarily disabled.",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="Available Commands",
                    value="`!ping` - Check bot status and toggle maintenance mode (admin only)",
                    inline=False
                )
                embed.set_footer(text="We'll be back soon!")
                
                try:
                    await ctx.send(embed=embed, delete_after=15)
                except:
                    pass
                
                return False
            
            return True
        
        # Register the global check
        self.add_check(global_maintenance_check)
        
        # Start Flask server for dashboard and health checks
        # Guard against double-start if main.py already started it
        import socket as _socket
        _s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        _port_free = _s.connect_ex(('127.0.0.1', int(os.getenv('PORT', os.getenv('REPLIT_PORT', 5000))))) != 0
        _s.close()
        if _port_free:
            self.logger.info("Starting Flask server for dashboard and health checks...")
            start_keep_alive_server()
            self.logger.info("✅ Flask server started successfully")
        else:
            self.logger.info("✅ Flask server already running (started by main.py)")
    
    async def has_trusted_role(self, user_id: int, guild_id: int) -> bool:
        """Check if a user has any trusted roles in a specific guild and trusted access is enabled."""
        if not self.db_manager:
            return False
            
        try:
            # Get the guild and user
            guild = self.get_guild(guild_id)
            if not guild:
                return False
                
            user = guild.get_member(user_id)
            if not user:
                return False
            
            # Get trusted roles for this guild
            with DatabaseSession() as session:
                # Trusted roles only apply when trusted access is enabled for this guild.
                server_perms = session.query(ServerFeaturePermissions).filter_by(
                    guild_id=guild_id
                ).first()
                if not server_perms or not server_perms.trusted_access_enabled:
                    return False

                trusted_roles = session.query(TrustedRole).filter(
                    TrustedRole.guild_id == guild_id,
                    TrustedRole.is_active == True
                ).all()
                
                if not trusted_roles:
                    return False
                
                # Check if user has any of these roles
                user_role_ids = {role.id for role in user.roles}
                trusted_role_ids = {trusted_role.role_id for trusted_role in trusted_roles}
                
                return bool(user_role_ids.intersection(trusted_role_ids))
                
        except Exception as e:
            self.logger.error(f"Error checking trusted role status: {e}")
            return False

    async def is_trusted_user(self, user_id: int, guild_id: int = None) -> bool:
        """Check if a user is trusted with owner-level permissions (individual user OR role-based)."""
        if not self.db_manager:
            return False
            
        try:
            with DatabaseSession() as session:
                # Check individual trusted user status
                trusted_user = session.query(TrustedUser).filter(
                    TrustedUser.user_id == user_id,
                    TrustedUser.is_active == True
                ).first()
                
                if trusted_user is not None:
                    # Global trusted-user records require a guild context and that
                    # the guild has trusted access enabled. Without a guild_id (e.g.
                    # a DM) there is no server to verify, so deny the check.
                    if guild_id is None:
                        return False
                    server_perms = session.query(ServerFeaturePermissions).filter_by(
                        guild_id=guild_id
                    ).first()
                    if not server_perms or not server_perms.trusted_access_enabled:
                        return False
                    return True
                
                # Check role-based trust if guild_id is provided
                if guild_id is not None:
                    return await self.has_trusted_role(user_id, guild_id)
                
                return False
        except Exception as e:
            self.logger.error(f"Error checking trusted user status: {e}")
            return False
    
    async def is_owner_or_trusted(self, user, guild_id: int = None) -> bool:
        """Check if user is the bot owner or a trusted user (individual or role-based)."""
        if await self.is_owner(user):
            return True
            
        # Try to get guild_id from user if not provided
        if guild_id is None and hasattr(user, 'guild') and user.guild:
            guild_id = user.guild.id
            
        return await self.is_trusted_user(user.id, guild_id)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Global check for all slash command interactions to enforce server permissions."""
        # Allow DM interactions
        if not interaction.guild:
            return True
        
        # Allow non-command interactions (buttons, selects, modals)
        if interaction.type != discord.InteractionType.application_command:
            return True
        
        # If no permissions manager, allow all
        if not hasattr(self, 'permissions_manager') or not self.permissions_manager:
            return True
        
        # Get command name
        command_name = interaction.command.name if interaction.command else None
        if not command_name:
            return True
        
        # Permission management and setup commands always allowed for authorized users
        if command_name in ['managefeatures', 'setserverfeatures', 'viewserverfeatures', 'resetserverfeatures', 'setuptrainpings', 'setattendancechannel']:
            return True
        
        # Check if command is allowed in this server
        if not await self.permissions_manager.is_command_allowed(interaction.guild.id, command_name):
            # Find which category this command belongs to
            from utils.server_permissions import FEATURE_CATEGORIES
            command_category = None
            for category, commands in FEATURE_CATEGORIES.items():
                if command_name.lower() in commands:
                    command_category = category
                    break
            
            category_name = command_category if command_category else "this feature"
            
            await interaction.response.send_message(
                f"❌ The `{category_name}` feature category is currently disabled in this server.\n"
                f"This command is not available here. Contact a server administrator for more information.",
                ephemeral=True
            )
            return False
        
        return True
    
    async def setup_hook(self):
        """Initial setup when bot starts."""
        self.logger.info("Setting up bot...")
        
        # Load maintenance mode state
        try:
            in_maintenance = await self.maintenance_manager.load()
            if in_maintenance:
                self.logger.warning("⚠️  MAINTENANCE MODE ACTIVE - Limited functionality enabled")
                self.logger.warning("Use /ping command to exit maintenance mode (admin only)")
            else:
                self.logger.info("✅ Normal operation mode - All commands available")
        except Exception as maint_error:
            self.logger.error(f"Failed to load maintenance mode: {maint_error}")
        
        # Initialize stability manager for bulletproof error handling
        try:
            from utils.stability_manager import get_stability_manager
            self.stability_manager = get_stability_manager(self)
            self.logger.info("✅ Stability manager initialized")
            
            # Start stability monitoring tasks only if not already running
            # Health monitor disabled - user preference
            if hasattr(self.stability_manager, 'memory_cleanup'):
                if not self.stability_manager.memory_cleanup.is_running():
                    self.stability_manager.memory_cleanup.start()
            
            # Start scheduled memory cleanup only if not already running
            if not self.scheduled_memory_cleanup.is_running():
                self.scheduled_memory_cleanup.start()
            if not self.stability_monitor.is_running():
                self.stability_monitor.start()
            
            self.logger.info("✅ All stability tasks started")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize stability manager: {e}")
            # Continue without stability manager if it fails
        
        # Load cogs
        try:
            await self.load_extension('cogs.basic_commands')
            self.logger.info("Loaded basic_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load basic_commands cog: {e}")
            
        try:
            await self.load_extension('cogs.database_commands')
            self.logger.info("Loaded database_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load database_commands cog: {e}")
            
        try:
            await self.load_extension('cogs.admin_commands')
            self.logger.info("Loaded admin_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load admin_commands cog: {e}")
            
        try:
            await self.load_extension('cogs.forwarding_commands')
            self.logger.info("Loaded forwarding_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load forwarding_commands cog: {e}")
            
        try:
            await self.load_extension('cogs.ban_commands')
            self.logger.info("Loaded ban_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load ban_commands cog: {e}")
            
        try:
            await self.load_extension('cogs.permission_commands')
            self.logger.info("Loaded permission_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load permission_commands cog: {e}")
            
        try:
            await self.load_extension('cogs.trusted_user_commands')
            self.logger.info("Loaded trusted_user_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load trusted_user_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.trusted_role_commands')
            self.logger.info("Loaded trusted_role_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load trusted_role_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.twitchcord_commands')
            self.logger.info("Loaded twitchcord_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load twitchcord_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.notification_commands')
            self.logger.info("Loaded notification_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load notification_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.train_participant_commands')
            self.logger.info("Loaded train_participant_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load train_participant_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.schedule_setup_commands')
            self.logger.info("Loaded schedule_setup_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load schedule_setup_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.interactive_schedule_commands')
            await self.load_extension('cogs.manual_notification_commands')
            self.logger.info("Loaded interactive_schedule_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load interactive_schedule_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.weekly_scheduler')
            self.logger.info("Loaded weekly_scheduler cog")
        except Exception as e:
            self.logger.error(f"Failed to load weekly_scheduler cog: {e}")
        
        try:
            await self.load_extension('cogs.crash_setup_commands')
            self.logger.info("Loaded crash_setup_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load crash_setup_commands cog: {e}")
            
        try:
            await self.load_extension('cogs.twitch_test_commands')
            self.logger.info("Loaded twitch_test_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load twitch_test_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.chatbot_settings_commands')
            self.logger.info("Loaded chatbot_settings_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load chatbot_settings_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.chatbot_test_commands')
            self.logger.info("Loaded chatbot_test_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load chatbot_test_commands cog: {e}")

        try:
            await self.load_extension('cogs.gl_train_setup')
            self.logger.info("Loaded gl_train_setup cog")
        except Exception as e:
            self.logger.error(f"Failed to load gl_train_setup cog: {e}")
        
        try:
            await self.load_extension('cogs.oauth_commands')
            self.logger.info("Loaded oauth_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load oauth_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.analytics_commands')
            self.logger.info("Loaded analytics_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load analytics_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.live_role_commands')
            self.logger.info("Loaded live_role_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load live_role_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.server_permissions_commands')
            self.logger.info("Loaded server_permissions_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load server_permissions_commands cog: {e}")
        
        try:
            await self.load_extension('cogs.cleanup_trusted_command')
            self.logger.info("Loaded cleanup_trusted_command cog")
        except Exception as e:
            self.logger.error(f"Failed to load cleanup_trusted_command cog: {e}")
        
        # TRUSTED ACCESS CONTROL - Bot owner controls per-server trusted access
        try:
            await self.load_extension('cogs.trusted_access_control')
            self.logger.info("Loaded trusted_access_control cog")
        except Exception as e:
            self.logger.error(f"Failed to load trusted_access_control cog: {e}")
        
        # PRIVACY & DATA MANAGEMENT - TOS compliance commands
        try:
            await self.load_extension('cogs.privacy_commands')
            self.logger.info("Loaded privacy_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load privacy_commands cog: {e}")
        
        # SPAM PROTECTION - Monitor new member joins and detect suspicious accounts
        try:
            await self.load_extension('cogs.spam_protection')
            self.logger.info("Loaded spam_protection cog")
        except Exception as e:
            self.logger.error(f"Failed to load spam_protection cog: {e}")
        
        # AFK COMMAND - Auto-reply when mentioned while AFK
        try:
            await self.load_extension('cogs.afk_command')
            self.logger.info("Loaded afk_command cog")
        except Exception as e:
            self.logger.error(f"Failed to load afk_command cog: {e}")
        
        # MULTI-PLATFORM STREAMING - YouTube, Kick, Twitch clips/VODs
        try:
            await self.load_extension('cogs.stream_settings_commands')
            self.logger.info("Loaded stream_settings_commands cog")
            
            # Load Live Chat Announcement Commands
            await self.load_extension('cogs.live_chat_announcement_commands')
            await self.load_extension('cogs.google_sheets_commands')
            self.logger.info("Loaded live_chat_announcement_commands cog")
            
            # Load Auto-Shoutout Commands
            await self.load_extension('cogs.auto_shoutout_commands')
            self.logger.info("Loaded auto_shoutout_commands cog")
            
            # Load Tutorial Command
            await self.load_extension('cogs.tutorial_command')
            self.logger.info("Loaded tutorial_command cog")
        except Exception as e:
            self.logger.error(f"Failed to load stream_settings_commands cog: {e}")

        # TikTok account monitor
        try:
            await self.load_extension('cogs.tiktok_monitor_commands')
            self.logger.info("Loaded tiktok_monitor_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load tiktok_monitor_commands cog: {e}")

        # Subscription/monetization system (disabled by default until owner enables it)
        try:
            await self.load_extension('cogs.subscription_commands')
            self.logger.info("Loaded subscription_commands cog")
        except Exception as e:
            self.logger.error(f"Failed to load subscription_commands cog: {e}")
        
        # Start status updater
        self.status_updater.start()
        # Start Discord heartbeat (periodic status message to configured channel)
        self.discord_heartbeat.start()
        # Start weekly Monday signup announcement scheduler
        self.signup_announcement_scheduler.start()
        # Start scheduled ping checker
        # self.schedule_ping_checker.start()  # DISABLED: Conflicts with main notification system
        
        # Initialize and start Twitch sync task
        try:
            from utils.twitch_sync_task import TwitchSyncTask
            self.twitch_sync_task = TwitchSyncTask(self)
            self.twitch_sync_task.start()
            self.logger.info("Started Twitch sync task")
        except Exception as e:
            self.logger.error(f"Failed to start Twitch sync task: {e}")
            
        # Initialize Twitch chat monitor
        try:
            from utils.twitch_chat_monitor import TwitchChatMonitor
            
            self.twitch_chat_monitor = TwitchChatMonitor(self)
            self.logger.info("✅ Twitch chat monitor initialized")
            
            # Auto-connect to Twitch IRC on startup
            async def connect_twitch_monitor():
                await self.wait_until_ready()
                try:
                    await self.twitch_chat_monitor.connect_to_twitch_irc()
                    self.logger.info("✅ Twitch chat monitor connected and ready")
                except Exception as e:
                    self.logger.error(f"Failed to auto-connect Twitch monitor: {e}")
                # Health-check stored Twitch tokens and DM the owner if any need re-linking
                try:
                    await self.twitch_chat_monitor.check_token_health(reason="startup")
                except Exception as e:
                    self.logger.error(f"Token health check failed: {e}")
            
            self.loop.create_task(connect_twitch_monitor())
            self.logger.info("✅ Twitch monitor auto-connect task created")
        except Exception as e:
            self.logger.error(f"Failed to initialize Twitch chat monitor: {e}")
        
        # Initialize Live Role Manager
        try:
            from utils.live_role_manager import LiveRoleManager
            self.live_role_manager = LiveRoleManager(self)
            self.logger.info("✅ Live role manager initialized")
            
            # Start live role update task
            self.live_role_updater.start()
            self.logger.info("✅ Live role updater task started")
            
            # Start dedicated auto-shoutout watcher (runs independently of live roles)
            async def start_shoutout_watcher():
                await self.wait_until_ready()
                try:
                    await self.live_role_manager.start_auto_shoutout_watcher()
                except Exception as e:
                    self.logger.error(f"Auto-shoutout watcher failed: {e}")
            
            self.loop.create_task(start_shoutout_watcher())
            self.logger.info("✅ Auto-shoutout watcher task started")
        except Exception as e:
            self.logger.error(f"Failed to initialize live role manager: {e}")
        
        # Initialize Multi-Platform Stream Manager
        try:
            from utils.multi_platform_stream_manager import MultiPlatformStreamManager
            self.multi_platform_manager = MultiPlatformStreamManager(self)
            self.loop.create_task(self.multi_platform_manager.start_checker())
            self.logger.info("✅ Multi-platform stream manager initialized")
        except Exception as e:
            self.logger.error(f"Failed to initialize multi-platform stream manager: {e}")
        
        # Initialize Comprehensive Report Manager (day-after attendance summaries)
        try:
            from utils.comprehensive_report_manager import ComprehensiveReportManager
            self.comprehensive_report_manager = ComprehensiveReportManager(self)
            self.comprehensive_report_checker.start()
            self.logger.info("✅ Comprehensive report manager initialized and checker started")
        except Exception as e:
            self.comprehensive_report_manager = None
            self.logger.error(f"Failed to initialize comprehensive report manager: {e}")
        
        # Start 1-hour warning checker task
        try:
            self.one_hour_warning_checker.start()
            self.logger.info("✅ 1-hour warning checker task started")
        except Exception as e:
            self.logger.error(f"Failed to start 1-hour warning checker: {e}")
        
        # Start 10-minute warning checker task
        try:
            self.ten_minute_warning_checker.start()
            self.logger.info("✅ 10-minute warning checker task started")
        except Exception as e:
            self.logger.error(f"Failed to start 10-minute warning checker: {e}")
        
        
        # Twitch outreach checker disabled - was sending unexpected DMs to participants
        # Uncomment below to re-enable automatic Twitch link outreach DMs (every 6 hours)
        # try:
        #     self.twitch_outreach_checker.start()
        #     self.logger.info("✅ Twitch outreach checker task started (every 6 hours)")
        # except Exception as e:
        #     self.logger.error(f"Failed to start Twitch outreach checker: {e}")

        # Start trial expiry checker (no-ops unless subscriptions are enabled)
        try:
            self.trial_expiry_checker.start()
            self.logger.info("✅ Trial expiry checker task started (every 24 hours)")
        except Exception as e:
            self.logger.error(f"Failed to start trial expiry checker: {e}")
        
        self.logger.info("Started background tasks")
        
        # Register restart-safe waitlist views (Task #38: panel buttons + DM offer buttons)
        try:
            from cogs.train_participant_commands import WaitlistPanelView, LeaveTrainButton
            from cogs.notification_commands import WaitlistOfferButton
            self.add_view(WaitlistPanelView(self))
            self.add_dynamic_items(WaitlistOfferButton)
            self.add_dynamic_items(LeaveTrainButton)
            self.logger.info("✅ Registered waitlist persistent views")
        except Exception as e:
            self.logger.error(f"Failed to register waitlist persistent views: {e}")
        
        # CRITICAL: Sync slash commands in setup_hook since on_ready may not trigger
        try:
            self.logger.info("🔄 SYNCING SLASH COMMANDS IN SETUP_HOOK...")
            # Note: tree.sync() needs to be called after the bot connects
            # We'll create a task to sync after connection
            async def sync_after_ready():
                await self.wait_until_ready()
                try:
                    synced = await self.tree.sync()
                    self.logger.info(f"✅ SETUP SYNC SUCCESS: {len(synced)} slash commands synced globally")
                    # Log each command for verification
                    for cmd in synced:
                        self.logger.info(f"   📋 Command registered: /{cmd.name}")
                except Exception as e:
                    self.logger.error(f"❌ SETUP SYNC ERROR: {e}")
                
                # One-time cleanup of stale guild-specific command registrations that used
                # to cause duplicates. This used to run on every restart (2 Discord API
                # calls per guild), which doesn't scale once the bot joins many servers
                # (e.g. after verification lifts the 100-guild cap). Now it only runs once
                # and is tracked via a SystemSettings flag so future restarts skip it.
                try:
                    from models import SystemSettings
                    from database import DatabaseSession
                    dedup_done = False
                    with DatabaseSession() as session:
                        row = session.query(SystemSettings).filter_by(
                            setting_key='guild_command_dedup_done'
                        ).first()
                        dedup_done = bool(row and row.is_enabled)

                    if not dedup_done:
                        for guild in self.guilds:
                            guild_obj = discord.Object(id=guild.id)
                            self.tree.clear_commands(guild=guild_obj)
                            await self.tree.sync(guild=guild_obj)
                            self.logger.info(f"✅ Cleared guild-specific commands for {guild.name} (dedup)")

                        with DatabaseSession() as session:
                            row = session.query(SystemSettings).filter_by(
                                setting_key='guild_command_dedup_done'
                            ).first()
                            if row:
                                row.is_enabled = True
                            else:
                                session.add(SystemSettings(
                                    setting_key='guild_command_dedup_done',
                                    is_enabled=True
                                ))
                            session.commit()
                        self.logger.info("✅ Guild command dedup complete — will be skipped on future restarts")
                    else:
                        self.logger.info("ℹ️ Guild command dedup already completed previously — skipping")
                except Exception as e:
                    self.logger.warning(f"Guild command dedup error (non-critical): {e}")
            
            # Create and start the sync task
            self.loop.create_task(sync_after_ready())
            self.logger.info("✅ Slash command sync task created")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to create slash command sync task: {e}")
    
    async def _on_ready_core(self):
        """Core startup logic called from on_ready."""
        self.start_time = datetime.utcnow()
        if self.user:
            self.logger.info(f'Bot is ready! Logged in as {self.user.name} (ID: {self.user.id})')
        self.logger.info(f'Connected to {len(self.guilds)} guilds')
        
        # Check for maintenance mode before proceeding
        try:
            from utils.maintenance_manager import maintenance_manager
            in_maintenance = await maintenance_manager.get()
                
            if in_maintenance:
                    self.logger.warning("🔧 MAINTENANCE MODE ENABLED - Bot entering dormant state")
                    self.logger.warning("Bot will only respond to !ping command from authorized users")
                    self.logger.warning("Use !ping to exit maintenance mode and go online")
                    
                    # Set maintenance status
                    await self.change_presence(
                        activity=discord.Activity(
                            type=discord.ActivityType.custom,
                            name="🔧 Maintenance Mode | Use !ping to restore"
                        ),
                        status=discord.Status.idle
                    )
                    
                    # Skip all normal initialization and return
                    return
        except Exception as e:
            self.logger.error(f"Error checking maintenance mode: {e}")
            # Continue with normal startup if maintenance check fails
        
        # Update database with current guilds
        if self.db_manager:
            await self._sync_guilds_to_database()

        # Set initial status
        try:
            # Check for custom status first
            custom_status = None
            activity_type_str = None
            try:
                with DatabaseSession() as status_session:
                    custom_setting = status_session.query(SystemSettings).filter_by(
                        setting_key='custom_status',
                        is_enabled=True
                    ).first()
                    if custom_setting and hasattr(custom_setting, 'setting_value'):
                        custom_status = getattr(custom_setting, 'setting_value', None)
                    
                    # Also load activity type
                    type_setting = status_session.query(SystemSettings).filter_by(
                        setting_key='activity_type',
                        is_enabled=True
                    ).first()
                    if type_setting and hasattr(type_setting, 'setting_value'):
                        activity_type_str = getattr(type_setting, 'setting_value', None)
            except Exception as e:
                self.logger.debug(f"Could not load custom status: {e}")
            
            if custom_status:
                # Use custom status with proper activity type
                activity_type_map = {
                    'playing': discord.ActivityType.playing,
                    'watching': discord.ActivityType.watching,
                    'listening': discord.ActivityType.listening,
                    'streaming': discord.ActivityType.streaming
                }
                
                activity_type = activity_type_map.get(activity_type_str, discord.ActivityType.watching)
                
                await self.change_presence(
                    activity=discord.Activity(
                        type=activity_type,
                        name=custom_status
                    ),
                    status=discord.Status.online
                )
                self.logger.info(f"Bot status set to ONLINE with custom activity: {activity_type_str} {custom_status}")
            else:
                # Fallback to member count
                guild_count = len(self.guilds) if self.guilds else 0
                member_count = sum(len(guild.members) for guild in self.guilds) if self.guilds else 0
                
                await self.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.watching,
                        name=f"{member_count:,} members | {self.config.COMMAND_PREFIX}help"
                    ),
                    status=discord.Status.online
                )
                self.logger.info(f"Bot status set to ONLINE with activity: watching {member_count:,} members across {guild_count} servers")
        except Exception as e:
            self.logger.error(f"Failed to set initial status: {e}")
        
        # Start automated database backups
        try:
            from utils.database_backup import get_backup_system
            backup_system = get_backup_system()
            await backup_system.start_scheduled_backups(interval_hours=24)
            self.logger.info("✅ Automated daily backups started")
        except Exception as e:
            self.logger.error(f"Failed to start automated backups: {e}")
        
        # Notification scheduler is started by the NotificationCommands cog's own on_ready listener

        # Backfill any missing Twitch user IDs (runs once on startup, silently no-ops if all present)
        try:
            if hasattr(self, 'live_role_manager') and self.live_role_manager:
                asyncio.ensure_future(self.live_role_manager.backfill_missing_twitch_ids())
        except Exception as e:
            self.logger.error(f"Failed to start Twitch ID backfill: {e}")

        # Fire an initial heartbeat 3 minutes after startup so admins see it
        # immediately rather than waiting up to 4 hours for the first loop tick.
        async def _initial_heartbeat():
            await asyncio.sleep(180)
            try:
                await self.discord_heartbeat()
            except Exception as e:
                self.logger.warning(f"Initial heartbeat failed: {e}")
        asyncio.ensure_future(_initial_heartbeat())

        # Send startup notification
        try:
            from utils.crash_notifications import send_restart_success, get_notifier
            notifier = get_notifier()
            if notifier.enabled:
                guild_count = len(self.guilds)
                restart_count = getattr(self, '_startup_count', 0)
                self._startup_count = restart_count + 1
                
                send_restart_success(restart_count)
                self.logger.info("✅ Startup notification sent")
            else:
                self.logger.debug("Startup notifications not configured (no webhook URL)")
        except Exception as e:
            self.logger.error(f"❌ Failed to send startup notification: {e}")

        # One-time: clear GL schedules, turn off maintenance, post signup announcement
        async def _one_time_gl_reset():
            DONE_KEY = 'gl_reset_20260718'
            try:
                from models import TrainSchedule
                from utils.maintenance_manager import maintenance_manager
                GL_GUILD_ID      = 1183084958110191616
                GL_GENERAL_ID    = 1183143967622168668
                GL_SIGNUPS_ID    = 1183135069896966154

                with DatabaseSession() as session:
                    # Skip if already done
                    done = session.query(SystemSettings).filter_by(setting_key=DONE_KEY).first()
                    if done:
                        return

                    # 1. Delete all active schedules for Game Lounge
                    deleted = session.query(TrainSchedule).filter_by(
                        guild_id=GL_GUILD_ID, is_active=True
                    ).all()
                    for s in deleted:
                        s.is_active = False
                    self.logger.info(f"🗑️ One-time reset: deactivated {len(deleted)} GL schedule(s)")

                    # 2. Turn off maintenance mode
                    maint = session.query(SystemSettings).filter_by(
                        setting_key='maintenance_mode'
                    ).first()
                    if maint:
                        maint.is_enabled = False
                        maint.setting_value = 'false'
                    session.commit()
                    self.logger.info("🟢 One-time reset: maintenance mode disabled")

                # Update in-memory maintenance cache
                await maintenance_manager.set(False)

                # 3. Send signup announcement to #general
                guild = self.get_guild(GL_GUILD_ID)
                if guild:
                    general   = guild.get_channel(GL_GENERAL_ID)
                    signups   = guild.get_channel(GL_SIGNUPS_ID)
                    sig_mention = signups.mention if signups else "#raid-train-sign-ups"
                    if general:
                        announcement = (
                            "🎮 **RAID TRAIN TIME SLOTS AVAILABLE!** 🎮\n\n"
                            f"📍 **Post your preferred time slot in:** {sig_mention}\n\n"
                            "**How to join:**\n"
                            "1️⃣ Share your available time slot in the channel\n"
                            "2️⃣ A staff member will get you set up on the schedule\n"
                            "3️⃣ Link your Twitch account with `/linktwitch` (if not already linked)\n"
                            "4️⃣ **Adding `rstone203` as a mod on your Twitch channel is recommended** "
                            "(for announcements & auto-shoutouts, not required for attendance)\n\n"
                            "**📊 How attendance tracking works:**\n"
                            "✅ **Automatic tracking** - We monitor your Twitch chat during your time slot\n"
                            "✅ **No sign-ups needed** - Just stream at your scheduled time\n"
                            "✅ **Live participation counted** - Active chatters are tracked automatically\n"
                            "✅ **Reports sent daily** - Full attendance reports posted after each train\n\n"
                            "🚂 Let's ride the train together! All aboard!"
                        )
                        embed = discord.Embed(
                            title="🎯 Raid Train Time Slots Available!",
                            description=announcement,
                            color=0x9146FF,
                            timestamp=datetime.utcnow()
                        )
                        await general.send(content="@everyone", embed=embed)
                        self.logger.info("📢 One-time reset: signup announcement posted to #general")

                # Mark done
                with DatabaseSession() as session:
                    session.add(SystemSettings(setting_key=DONE_KEY, setting_value='done', is_enabled=True))
                    session.commit()

            except Exception as err:
                self.logger.error(f"❌ One-time GL reset failed: {err}", exc_info=True)

        asyncio.ensure_future(_one_time_gl_reset())

    async def on_message(self, message):
        """Handle incoming messages and message forwarding."""
        if message.author == self.user:
            return

        if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
            try:
                from utils.auto_link_helper import handle_twitch_username_reply
                handled = await handle_twitch_username_reply(self, message)
                if handled:
                    return
                await message.channel.send(
                    "This bot can not reply to messages. Please contact **rstone203** if you need help. Thank you!"
                )
            except discord.Forbidden:
                pass
            return
            
        self.messages_seen += 1
        
        # Thread-safe duplicate prevention system
        message_id = message.id
        current_time = datetime.utcnow().isoformat()
        
        # Use atomic lock to prevent race conditions in duplicate checking
        with self._message_processing_lock:
            # Log this event for debugging
            if message_id in self._message_processing_log:
                self._message_processing_log[message_id].append(current_time)
                self.logger.error(f"🚨 DUPLICATE EVENT DETECTED: Message ID {message_id} from {message.author} - Event #{len(self._message_processing_log[message_id])} at {current_time}")
            else:
                self._message_processing_log[message_id] = [current_time]
            
            # Atomic check-and-add to prevent race conditions
            if message_id in self._processed_messages:
                self.logger.warning(f"🔄 DUPLICATE PREVENTED: Message ID {message_id} from {message.author} already processed - blocking repeat processing")
                return
                
            # Add to processed set atomically
            self._processed_messages.add(message_id)
            self.logger.debug(f"✅ Message ID {message_id} added to processed set (size: {len(self._processed_messages)})")
            
            # Memory management (still within lock to prevent race conditions)
            if len(self._processed_messages) > 1000:
                old_size = len(self._processed_messages)
                sorted_messages = sorted(list(self._processed_messages))
                self._processed_messages = set(sorted_messages[-500:])
                self.logger.info(f"🧹 Cleaned processed messages cache: {old_size} → {len(self._processed_messages)}")
        
        # At this point, we have atomically verified this message hasn't been processed yet
        
        # Debug logging for command detection
        if message.content.startswith('!'):
            self.logger.info(f"Received potential command: '{message.content}' from {message.author} in {message.guild} (ID: {message_id})")
        
        content = message.content.lower()

        # Handle START/STOP commands for message forwarding
        if content in ['!start', '!stop']:
            has_manage_messages = message.author.guild_permissions.manage_messages if hasattr(message.author, 'guild_permissions') else False
            is_authorized_user = message.author.id in self.authorized_user_ids
            
            if not has_manage_messages and not is_authorized_user:
                await message.channel.send('You do not have access to this command, only staff members are able to access this. If you believe this is a mistake, please contact higher authority or rstone203 (if he\'s in your server or a server you are in)')
                return
            
            if content == '!start':
                await message.channel.send('ℹ️ **Legacy command disabled!** Use `!addforward` to create forwarding configurations or access the web dashboard with `!dashboard` to manage forwarding.')
                self.logger.info(f"Legacy !start command disabled, redirected {message.author} to modern system")
                return
            
            if content == '!stop':
                await message.channel.send('ℹ️ **Legacy command disabled!** Use `!removeforward` to delete forwarding configurations or access the web dashboard with `!dashboard` to manage forwarding.')
                self.logger.info(f"Legacy !stop command disabled, redirected {message.author} to modern system")
                return
        
        # Check for cross-server forwarding configurations
        # Skip forwarding check if this is a bot command (starts with command prefix)
        if message.content.startswith(self.command_prefix):
            # This is a command - skip forwarding and process commands only
            await self.process_commands(message)
            return
        
        if self.db_manager and message.guild:
            try:
                with DatabaseSession() as session:
                    configs = session.query(ForwardingConfig).filter(
                        ForwardingConfig.source_guild_id == str(message.guild.id),
                        ForwardingConfig.source_channel_id == str(message.channel.id),
                        ForwardingConfig.is_active == True
                    ).all()
                    
                    if configs:
                        self.logger.debug(f"Found {len(configs)} active forwarding configs for {message.guild.name}#{message.channel.name}")
                    
                    forwarded_targets = set()
                    
                    for config in configs:
                        try:
                            channel_id_value = getattr(config, 'target_channel_id')
                            target_channel_id = int(channel_id_value)
                            
                            if target_channel_id in forwarded_targets:
                                continue
                            
                            target_channel = self.get_channel(target_channel_id)
                            
                            if not target_channel:
                                try:
                                    target_channel = await self.fetch_channel(target_channel_id)
                                except (discord.NotFound, discord.Forbidden):
                                    self.logger.warning(f"⚠️ Forwarding config {config.id}: target channel {target_channel_id} not found or no access - disabling config")
                                    config.is_active = False
                                    session.commit()
                                    continue
                                except discord.HTTPException:
                                    self.logger.warning(f"⚠️ Could not fetch target channel {target_channel_id} - skipping this time")
                                    continue
                        except (ValueError, TypeError, AttributeError):
                            self.logger.error(f"Invalid target_channel_id in config {config.id}")
                            continue
                            
                        if target_channel and isinstance(target_channel, (discord.TextChannel, discord.Thread, discord.DMChannel)):
                            try:
                                forwarded_targets.add(target_channel_id)

                                message_content = message.content or ""
                                author = message.author

                                # ── Build the main forwarded-message embed ──────────────────
                                fw_embed = discord.Embed(
                                    color=author.color if hasattr(author, 'color') and author.color != discord.Color.default() else discord.Color(0x5865F2),
                                    timestamp=message.created_at
                                )

                                # Author line — avatar + display name + jump link
                                author_avatar = author.display_avatar.url if hasattr(author, 'display_avatar') else None
                                fw_embed.set_author(
                                    name=author.display_name,
                                    icon_url=author_avatar,
                                    url=message.jump_url
                                )

                                # Message content (handles empty content gracefully)
                                if message_content:
                                    fw_embed.description = message_content
                                elif not message.attachments and not message.embeds and message.stickers:
                                    fw_embed.description = f"*[Sticker: {message.stickers[0].name}]*"
                                elif not message_content and not message.attachments and not message.embeds:
                                    fw_embed.description = "*[No text content]*"

                                # If there's exactly one image attachment, show it inline
                                image_set = False
                                files = []
                                extra_attachment_links = []
                                for attachment in message.attachments:
                                    is_image = attachment.content_type and attachment.content_type.startswith("image/")
                                    if is_image and not image_set:
                                        fw_embed.set_image(url=attachment.url)
                                        image_set = True
                                    elif attachment.size <= 8388608:
                                        try:
                                            import io
                                            file_data = await attachment.read()
                                            files.append(discord.File(io.BytesIO(file_data), filename=attachment.filename))
                                        except Exception:
                                            extra_attachment_links.append(f"[{attachment.filename}]({attachment.url})")
                                    else:
                                        extra_attachment_links.append(f"[{attachment.filename}]({attachment.url}) *(too large to upload)*")

                                if extra_attachment_links:
                                    fw_embed.add_field(name="📎 Attachments", value="\n".join(extra_attachment_links), inline=False)

                                # Source footer
                                channel_mention = f"#{message.channel.name}"
                                fw_embed.set_footer(text=f"📤 {message.guild.name} · {channel_mention}")

                                # ── Clone only intentional (rich) embeds from the source ────
                                # Skip Discord's auto-generated link/video/article previews —
                                # those are type "link", "video", "article", "image", "gifv".
                                # Only type "rich" means a bot/webhook intentionally built it.
                                extra_embeds = []
                                for orig in message.embeds:
                                    if orig.type != "rich":
                                        continue
                                    cloned = discord.Embed(
                                        title=orig.title,
                                        description=orig.description,
                                        color=orig.color,
                                        url=orig.url,
                                        timestamp=orig.timestamp
                                    )
                                    if orig.author:
                                        cloned.set_author(name=orig.author.name, url=orig.author.url, icon_url=orig.author.icon_url)
                                    if orig.thumbnail:
                                        cloned.set_thumbnail(url=orig.thumbnail.url)
                                    if orig.image:
                                        cloned.set_image(url=orig.image.url)
                                    for field in orig.fields:
                                        cloned.add_field(name=field.name, value=field.value, inline=field.inline)
                                    if orig.footer:
                                        cloned.set_footer(text=orig.footer.text, icon_url=orig.footer.icon_url)
                                    extra_embeds.append(cloned)

                                # Discord allows max 10 embeds per message
                                all_embeds = [fw_embed] + extra_embeds[:9]

                                allowed_mentions = discord.AllowedMentions.none()

                                send_success = False
                                max_retries = 3
                                for attempt in range(max_retries + 1):
                                    try:
                                        await target_channel.send(
                                            embeds=all_embeds,
                                            files=files if attempt == 0 else [],
                                            allowed_mentions=allowed_mentions
                                        )
                                        send_success = True
                                        break
                                    except discord.Forbidden:
                                        self.logger.warning(f"⚠️ No permission to forward to {target_channel.name} (config {config.id}) - disabling")
                                        config.is_active = False
                                        session.commit()
                                        break
                                    except discord.NotFound:
                                        self.logger.warning(f"⚠️ Target channel {target_channel_id} deleted - disabling config {config.id}")
                                        config.is_active = False
                                        session.commit()
                                        break
                                    except discord.HTTPException as e:
                                        if attempt < max_retries:
                                            self.logger.warning(f"⚠️ Retry {attempt+1}/{max_retries} forwarding to {target_channel.name}: {e}")
                                            await asyncio.sleep(1.5 * (attempt + 1))
                                        else:
                                            self.logger.error(f"❌ Failed to forward after {max_retries+1} attempts to {target_channel.name}: {e}")

                                if send_success:
                                    self.logger.info(f"✅ Forwarded: {message.author} ({message.guild.name}) → {target_channel.guild.name}#{target_channel.name}")
                                    await self._log_message_to_database(message, forwarded=True, target_channel_id=target_channel_id)

                            except Exception as e:
                                self.logger.error(f"Error forwarding message to {target_channel.id}: {e}", exc_info=True)
                
            except Exception as e:
                self.logger.error(f"Error checking forwarding configs: {e}")
        
        # TikTok video auto-repost
        if message.guild and not message.author.bot:
            tiktok_pattern = re.compile(
                r'https?://(?:www\.|vm\.|m\.|vt\.)?tiktok\.com/\S+',
                re.IGNORECASE
            )
            tiktok_urls = tiktok_pattern.findall(message.content)
            if tiktok_urls:
                try:
                    with DatabaseSession() as _tt_sess:
                        _tt_guild = _tt_sess.query(Guild).filter_by(id=message.guild.id).first()
                        _tt_channel_id = _tt_guild.tiktok_feed_channel_id if _tt_guild else None
                    if _tt_channel_id:
                        _tt_feed_ch = message.guild.get_channel(_tt_channel_id)
                        if _tt_feed_ch and _tt_feed_ch.id != message.channel.id:
                            import aiohttp as _aiohttp
                            for _tt_url in tiktok_urls[:3]:  # cap at 3 per message
                                try:
                                    async with _aiohttp.ClientSession() as _tt_http:
                                        async with _tt_http.get(
                                            'https://www.tiktok.com/oembed',
                                            params={'url': _tt_url},
                                            timeout=_aiohttp.ClientTimeout(total=8)
                                        ) as _tt_resp:
                                            if _tt_resp.status == 200:
                                                _tt_data = await _tt_resp.json()
                                            else:
                                                _tt_data = {}
                                except Exception:
                                    _tt_data = {}

                                _tt_title = _tt_data.get('title', '').strip() or 'TikTok Video'
                                _tt_author = _tt_data.get('author_name', '').strip() or message.author.display_name
                                _tt_author_url = _tt_data.get('author_url', '')
                                _tt_thumb = _tt_data.get('thumbnail_url', '')

                                _tt_embed = discord.Embed(
                                    title=_tt_title,
                                    url=_tt_url,
                                    description=f"Shared by {message.author.mention} in {message.channel.mention}",
                                    color=0x010101
                                )
                                _tt_embed.set_author(
                                    name=f"🎵 {_tt_author}",
                                    url=_tt_author_url or _tt_url
                                )
                                if _tt_thumb:
                                    _tt_embed.set_image(url=_tt_thumb)
                                _tt_embed.set_footer(text="TikTok")
                                await _tt_feed_ch.send(embed=_tt_embed)
                                self.logger.info(f"📱 TikTok reposted from {message.author} → #{_tt_feed_ch.name}")
                except Exception as _tt_err:
                    self.logger.error(f"TikTok feed error: {_tt_err}")

        # Log regular message to database
        if self.db_manager:
            await self._log_message_to_database(message, forwarded=False)
        
        # Process commands
        await self.process_commands(message)
    
    async def on_command(self, ctx):
        """Called when a command is invoked."""
        self.commands_executed += 1
        self.logger.info(f"Command '{ctx.command}' executed by {ctx.author} in {ctx.guild}")
        
        if self.db_manager:
            await self._log_command_to_database(ctx)
    
    async def on_command_error(self, ctx, error):
        """Handle command errors."""
        if isinstance(error, commands.CommandNotFound):
            return
            
        # Skip CheckFailure errors - let command-specific error handlers deal with permissions
        if isinstance(error, commands.CheckFailure):
            self.logger.debug(f"Permission check failed for {ctx.command} by {ctx.author} - handled by command decorator")
            return
        
        # Check if this is a hybrid command interaction that's already been handled
        if hasattr(ctx, 'interaction') and ctx.interaction:
            # For slash commands, check if already responded
            if ctx.interaction.response.is_done():
                self.logger.error(f"Command error (already responded): {error}")
                return
        
        # Handle specific errors with responses
        try:
            if isinstance(error, commands.MissingRequiredArgument):
                await ctx.send(f"❌ Missing required argument: `{error.param.name}`")
            elif isinstance(error, commands.BadArgument):
                await ctx.send(f"❌ Invalid argument provided")
            elif isinstance(error, commands.CommandOnCooldown):
                await ctx.send(f"⏰ Command on cooldown. Try again in {error.retry_after:.1f} seconds")
            elif isinstance(error, commands.MissingPermissions):
                await ctx.send("❌ You don't have permission to use this command")
            else:
                self.logger.error(f"Unhandled command error: {error}")
                await ctx.send("❌ An unexpected error occurred")
        except discord.errors.HTTPException as e:
            # Log HTTP errors but don't try to send another response
            self.logger.error(f"HTTP error in error handler: {e}")
        except Exception as e:
            # Log any other errors in the error handler
            self.logger.error(f"Error in error handler: {e}")
    
    @tasks.loop(minutes=5)
    async def status_updater(self):
        """Update bot status periodically."""
        try:
            from utils.maintenance_manager import maintenance_manager
            if await maintenance_manager.get():
                await self.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.custom, 
                        name="🔧 Maintenance Mode | Use !ping to restore"
                    ), 
                    status=discord.Status.idle
                )
                return
            
            # Check for custom status first (run DB query off the event loop)
            custom_status = None
            try:
                def _fetch_custom_status():
                    with DatabaseSession() as status_session:
                        setting = status_session.query(SystemSettings).filter_by(
                            setting_key='custom_status',
                            is_enabled=True
                        ).first()
                        return getattr(setting, 'setting_value', None) if setting else None
                custom_status = await asyncio.to_thread(_fetch_custom_status)
            except Exception as e:
                self.logger.debug(f"Could not load custom status in updater: {e}")
            
            if custom_status:
                # Use custom status
                activity = discord.Activity(
                    type=discord.ActivityType.watching,
                    name=custom_status
                )
            else:
                # Fallback to member count
                guild_count = len(self.guilds)
                member_count = sum(len(guild.members) for guild in self.guilds) if self.guilds else 0
                activity = discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"{member_count:,} members | {self.config.COMMAND_PREFIX}help"
                )
            
            await self.change_presence(activity=activity, status=discord.Status.online)
            
        except Exception as e:
            self.logger.error(f"❌ Error updating status: {e}", exc_info=True)
    
    @status_updater.error
    async def status_updater_error(self, error):
        """Handle errors in status_updater and restart it."""
        self.logger.error(f"⚠️ status_updater crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting status_updater in 60 seconds...")
        await asyncio.sleep(60)
        self.status_updater.restart()
    
    @status_updater.before_loop
    async def before_status_updater(self):
        """Wait until bot is ready before starting status updates."""
        await self.wait_until_ready()
    
    # DISABLED: This conflicts with the main notification system in cogs/notification_commands.py
    # @tasks.loop(minutes=5)
    async def schedule_ping_checker_disabled(self):
        """DISABLED: Check for upcoming train schedules and ping participants 60 minutes before."""
        try:
            from datetime import datetime, timezone, timedelta
            from models import TrainSchedule
            
            if not self.db_manager:
                return
                
            # Get current time in UTC
            now = datetime.now(timezone.utc)
            # Check for schedules starting in the next 60-65 minutes
            start_time = now + timedelta(minutes=60)
            end_time = now + timedelta(minutes=65)
            
            with self.db_manager.get_session() as db_session:
                # Get schedules that should be pinged now
                schedules = db_session.query(TrainSchedule).filter(
                    TrainSchedule.is_active == True,
                    TrainSchedule.participant_ids.isnot(None)
                ).all()
                
                for schedule in schedules:
                    if not schedule.participant_ids:
                        continue
                        
                    # Check if this schedule should be pinged now
                    should_ping = False
                    ping_time = None
                    
                    if schedule.schedule_type == 'one-time' and schedule.specific_date:
                        # One-time event - check specific date and time
                        schedule_datetime = datetime.combine(
                            schedule.specific_date, 
                            schedule.start_time
                        ).replace(tzinfo=timezone.utc)
                        
                        if start_time <= schedule_datetime <= end_time:
                            should_ping = True
                            ping_time = schedule_datetime
                    
                    elif schedule.schedule_type == 'recurring':
                        # Recurring event - check if today matches the day of week
                        today_weekday = now.weekday()  # 0=Monday, 6=Sunday
                        if today_weekday == schedule.day_of_week:
                            # Create datetime for today with the schedule time
                            today_schedule_time = datetime.combine(
                                now.date(),
                                schedule.start_time
                            ).replace(tzinfo=timezone.utc)
                            
                            if start_time <= today_schedule_time <= end_time:
                                should_ping = True
                                ping_time = today_schedule_time
                    
                    if should_ping and ping_time:
                        await self._send_schedule_ping(schedule, ping_time)
                        
        except Exception as e:
            self.logger.error(f"Error in schedule ping checker: {e}")
    
    async def _send_schedule_ping(self, schedule, ping_time, target_channel_id=None):
        """Send comprehensive ping notifications with backup system."""
        try:
            guild = self.get_guild(int(schedule.guild_id))
            if not guild:
                return
                
            # Use target channel if specified, otherwise find a suitable channel
            channel = None
            if target_channel_id:
                channel = guild.get_channel(int(target_channel_id))
            
            if not channel:
                # Find a suitable channel to send the ping (first text channel)
                for text_channel in guild.text_channels:
                    if text_channel.permissions_for(guild.me).send_messages:
                        channel = text_channel
                        break
            
            if not channel:
                self.logger.warning(f"No suitable channel found in guild {guild.name} for schedule ping")
                return
            
            # Send comprehensive multi-stage ping system
            await self._send_primary_ping(channel, schedule, ping_time)
            
            # Schedule backup ping check for 15 minutes later
            import asyncio
            asyncio.create_task(self._schedule_backup_check(channel, schedule, ping_time))
            
        except Exception as e:
            self.logger.error(f"Error sending schedule ping: {e}")
    
    async def _send_primary_ping(self, channel, schedule, ping_time):
        """Send the primary ping to all participants."""
        try:
            # Create ping message for primary participants
            user_pings = []
            for user_id in schedule.participant_ids:
                user_pings.append(f"<@{user_id}>")
            
            ping_text = " ".join(user_pings)
            
            # Primary notification embed
            embed = discord.Embed(
                title="🚂 Train Schedule Reminder",
                description=f"**{schedule.name}** starts in **60 minutes**!",
                color=0xFFD700,  # Gold color
                timestamp=ping_time
            )
            
            embed.add_field(
                name="⏰ Start Time",
                value=f"<t:{int(ping_time.timestamp())}:t> (<t:{int(ping_time.timestamp())}:R>)",
                inline=True
            )
            
            embed.add_field(
                name="⏱️ Duration",
                value=f"{schedule.duration_minutes} minutes",
                inline=True
            )
            
            if schedule.description:
                embed.add_field(
                    name="📝 Description",
                    value=schedule.description,
                    inline=False
                )
            
            embed.add_field(
                name="📋 Primary Notification",
                value="✅ React with ✅ if you're ready!\n❌ React with ❌ if you can't make it\n⏰ **Backup notification in 15 minutes if no response**",
                inline=False
            )
            
            embed.set_footer(text="React to confirm! Backup ping in 15 min if no response 🎮")
            
            # Send the primary ping message
            message = await channel.send(content=f"🎯 **PRIMARY NOTIFICATION**\n{ping_text}", embed=embed)
            
            # Add reaction buttons for responses
            await message.add_reaction("✅")
            await message.add_reaction("❌")
            
            # Store message ID for backup checking
            self._primary_messages = getattr(self, '_primary_messages', {})
            self._primary_messages[f"{schedule.guild_id}_{schedule.id}"] = message.id
            
            self.logger.info(f"Sent primary ping for '{schedule.name}' to {len(schedule.participant_ids)} participants")
            
        except Exception as e:
            self.logger.error(f"Error sending primary ping: {e}")
    
    async def _schedule_backup_check(self, channel, schedule, ping_time):
        """Schedule backup notification check after 15 minutes."""
        try:
            import asyncio
            from datetime import timedelta
            
            # Wait 15 minutes before checking for responses
            await asyncio.sleep(15 * 60)  # 15 minutes
            
            # Check if anyone responded to the primary message
            message_key = f"{schedule.guild_id}_{schedule.id}"
            primary_messages = getattr(self, '_primary_messages', {})
            
            if message_key in primary_messages:
                try:
                    message_id = primary_messages[message_key]
                    primary_message = await channel.fetch_message(message_id)
                    
                    # Check reactions
                    confirmed_users = set()
                    declined_users = set()
                    
                    for reaction in primary_message.reactions:
                        if str(reaction.emoji) == "✅":
                            async for user in reaction.users():
                                if not user.bot:
                                    confirmed_users.add(user.id)
                        elif str(reaction.emoji) == "❌":
                            async for user in reaction.users():
                                if not user.bot:
                                    declined_users.add(user.id)
                    
                    # Send backup notification based on responses
                    await self._send_backup_notification(channel, schedule, ping_time, confirmed_users, declined_users)
                    
                except Exception as e:
                    # If we can't fetch the message, send backup anyway
                    self.logger.warning(f"Could not check primary message reactions: {e}")
                    await self._send_backup_notification(channel, schedule, ping_time, set(), set())
            else:
                # Send backup notification
                await self._send_backup_notification(channel, schedule, ping_time, set(), set())
                
        except Exception as e:
            self.logger.error(f"Error in backup check: {e}")
    
    async def _send_backup_notification(self, channel, schedule, ping_time, confirmed_users, declined_users):
        """Send backup notification based on response status."""
        try:
            from datetime import datetime, timezone
            
            # Calculate time remaining (should be ~45 minutes now)
            now = datetime.now(timezone.utc)
            time_remaining = ping_time - now
            minutes_left = int(time_remaining.total_seconds() / 60)
            
            # Determine who needs backup pings
            all_participant_ids = [int(uid) for uid in schedule.participant_ids]
            no_response_ids = []
            
            for uid in all_participant_ids:
                if uid not in confirmed_users and uid not in declined_users:
                    no_response_ids.append(uid)
            
            # Create backup notification
            if confirmed_users:
                # Some people confirmed - send status update + backup ping
                embed = discord.Embed(
                    title="📊 Train Status Update + Backup Ping",
                    description=f"**{schedule.name}** starts in **{minutes_left} minutes**!",
                    color=0xFF6B35,  # Orange color
                    timestamp=ping_time
                )
                
                confirmed_text = f"✅ **Confirmed ({len(confirmed_users)})**: " + " ".join([f"<@{uid}>" for uid in confirmed_users])
                embed.add_field(name="Responses Received", value=confirmed_text, inline=False)
                
                if declined_users:
                    declined_text = f"❌ **Can't Make It ({len(declined_users)})**: " + " ".join([f"<@{uid}>" for uid in declined_users])
                    embed.add_field(name="Declined", value=declined_text, inline=False)
                
                if no_response_ids:
                    backup_pings = " ".join([f"<@{uid}>" for uid in no_response_ids])
                    embed.add_field(
                        name="⚠️ Backup Ping - No Response Yet",
                        value=f"**Still need response from:** {backup_pings}\n**Please confirm if you're coming!**",
                        inline=False
                    )
                    await channel.send(content=f"🔄 **BACKUP PING** - {backup_pings}", embed=embed)
                else:
                    embed.add_field(name="🎉 Status", value="All participants have responded!", inline=False)
                    await channel.send(embed=embed)
            
            else:
                # Nobody confirmed - send urgent backup ping
                all_pings = " ".join([f"<@{uid}>" for uid in all_participant_ids])
                
                embed = discord.Embed(
                    title="🚨 URGENT: No Response to Train Schedule",
                    description=f"**{schedule.name}** starts in **{minutes_left} minutes**!",
                    color=0xFF0000,  # Red color
                    timestamp=ping_time
                )
                
                embed.add_field(
                    name="⚠️ No Responses Received",
                    value="Nobody has responded to the initial ping!\n**Please confirm if you're still coming!**",
                    inline=False
                )
                
                embed.add_field(
                    name="⏰ Action Needed",
                    value=f"✅ React with ✅ if you're ready!\n❌ React with ❌ if you can't make it\n🆘 **URGENT - Only {minutes_left} minutes left!**",
                    inline=False
                )
                
                embed.set_footer(text="BACKUP PING - Please respond immediately! 🚨")
                
                backup_message = await channel.send(content=f"🚨 **BACKUP PING - NO RESPONSES**\n{all_pings}", embed=embed)
                await backup_message.add_reaction("✅")
                await backup_message.add_reaction("❌")
            
            self.logger.info(f"Sent backup notification for '{schedule.name}' - {len(confirmed_users)} confirmed, {len(no_response_ids)} no response")
            
        except Exception as e:
            self.logger.error(f"Error sending backup notification: {e}")
    
    # @schedule_ping_checker.before_loop  # DISABLED: Conflicts with main notification system
    async def before_schedule_ping_checker_disabled(self):
        """DISABLED: Wait until the bot is ready before starting schedule ping checks."""
        await self.wait_until_ready()
    
    async def send_test_ping(self, channel_id, test_user_ids=None):
        """Send a test ping to demonstrate the system."""
        try:
            from datetime import datetime, timezone, timedelta
            import discord
            
            # Find the channel directly
            channel = None
            for guild in self.guilds:
                found_channel = guild.get_channel(int(channel_id))
                if found_channel:
                    channel = found_channel
                    break
            
            if not channel:
                self.logger.error(f"Could not find channel {channel_id}")
                return False
            
            # Create test ping time (in 60 minutes from now)  
            test_ping_time = datetime.now(timezone.utc) + timedelta(minutes=60)
            
            # Create mock user pings
            mock_user_ids = test_user_ids or ["1234567890123456789", "9876543210987654321"]
            user_pings = [f"<@{user_id}>" for user_id in mock_user_ids]
            ping_text = " ".join(user_pings)
            
            # Create test embed
            embed = discord.Embed(
                title="🧪 **TEST PING** - Train Schedule Reminder",
                description="**⚠️ This is a TEST notification - NOT a real train!**\n**🧪 Test Train Schedule** starts in **60 minutes**!",
                color=0xFFD700,  # Gold color
                timestamp=test_ping_time
            )
            
            embed.add_field(
                name="⏰ Start Time",
                value=f"<t:{int(test_ping_time.timestamp())}:t> (<t:{int(test_ping_time.timestamp())}:R>)",
                inline=True
            )
            
            embed.add_field(
                name="⏱️ Duration", 
                value="60 minutes",
                inline=True
            )
            
            embed.add_field(
                name="📝 Description",
                value="This is a test ping to demonstrate the automated reminder system!",
                inline=False
            )
            
            embed.set_footer(text="Get ready for the train! 🎮")
            
            # Send the test ping with clear TEST indicator
            test_content = f"🧪 **TEST PING** 🧪\n{ping_text}"
            await channel.send(content=test_content, embed=embed)
            
            self.logger.info(f"Successfully sent test ping to channel {channel_id} ({channel.name})")
            return True
            
        except Exception as e:
            self.logger.error(f"Error sending test ping: {e}")
            return False
    
    # Database helper methods
    async def _sync_guilds_to_database(self):
        """Sync current guilds to database."""
        try:
            with DatabaseSession() as session:
                for guild in self.guilds:
                    existing_guild = session.query(Guild).filter(Guild.id == guild.id).first()
                    if not existing_guild:
                        db_guild = Guild(
                            id=guild.id,
                            name=guild.name,
                            owner_id=guild.owner_id,
                            member_count=guild.member_count or 0,
                            is_active=True
                        )
                        session.add(db_guild)
                    else:
                        session.query(Guild).filter(Guild.id == guild.id).update({
                            'name': guild.name,
                            'member_count': guild.member_count or 0,
                            'is_active': True
                        })
                session.commit()
                self.logger.info(f"Synced {len(self.guilds)} guilds to database")
                
                await self.sync_channels_to_database()
        except Exception as e:
            self.logger.error(f"Failed to sync guilds to database: {e}")
    
    async def sync_channels_to_database(self):
        """Sync channel information to database."""
        if not self.db_manager:
            return
            
        try:
            with DatabaseSession() as session:
                channel_count = 0
                for guild in self.guilds:
                    for channel in guild.text_channels:
                        existing_channel = session.query(Channel).filter(Channel.id == channel.id).first()
                        
                        if existing_channel:
                            session.query(Channel).filter(Channel.id == channel.id).update({
                                'name': channel.name,
                                'position': channel.position,
                                'last_updated': datetime.utcnow()
                            })
                        else:
                            db_channel = Channel(
                                id=channel.id,
                                guild_id=guild.id,
                                name=channel.name,
                                type='text',
                                position=channel.position,
                                last_updated=datetime.utcnow()
                            )
                            session.add(db_channel)
                        
                        channel_count += 1
                
                session.commit()
                self.logger.info(f"Synced {channel_count} channels to database")
        except Exception as e:
            self.logger.error(f"Failed to sync channels to database: {e}")
    
    async def _log_message_to_database(self, message, forwarded=False, target_channel_id=None):
        """Log message to database."""
        if not self.db_manager:
            return
            
        try:
            with DatabaseSession() as session:
                # Ensure the user exists in the database (create-or-get pattern)
                existing_user = session.query(User).filter(User.id == message.author.id).first()
                if not existing_user:
                    # Create the user if they don't exist
                    db_user = User(
                        id=message.author.id,
                        username=message.author.name,
                        display_name=message.author.display_name,
                        guild_id=message.guild.id if message.guild else None,
                        first_seen=datetime.utcnow(),
                        last_seen=datetime.utcnow(),
                        message_count=1,
                        is_bot=message.author.bot
                    )
                    session.add(db_user)
                else:
                    # Update last seen and increment message count
                    existing_user.last_seen = datetime.utcnow()
                    existing_user.message_count = (existing_user.message_count or 0) + 1
                    
                    # Update display name if it's changed
                    if existing_user.display_name != message.author.display_name:
                        existing_user.display_name = message.author.display_name
                
                # Check if message already exists (prevent duplicate key errors)
                existing_message = session.query(Message).filter(
                    Message.discord_message_id == message.id
                ).first()
                
                if not existing_message:
                    # Create new message
                    db_message = Message(
                        discord_message_id=message.id,
                        user_id=message.author.id,
                        guild_id=message.guild.id if message.guild else None,
                        channel_id=message.channel.id,
                        content=message.content[:2000] if message.content else None,
                        timestamp=message.created_at,
                        was_forwarded=forwarded,
                        forwarded_to_channel=target_channel_id if forwarded else None,
                        forwarded_at=datetime.utcnow() if forwarded else None
                    )
                    session.add(db_message)
                    session.commit()
                elif forwarded:
                    # Update existing message to mark it as forwarded
                    existing_message.was_forwarded = True
                    existing_message.forwarded_to_channel = target_channel_id
                    existing_message.forwarded_at = datetime.utcnow()
                    session.commit()
        except Exception as e:
            self.logger.error(f"Failed to log message to database: {e}")
    
    async def _log_command_to_database(self, ctx):
        """Log command execution to database."""
        if not self.db_manager:
            return
            
        try:
            with DatabaseSession() as session:
                command_log = CommandLog(
                    user_id=ctx.author.id,
                    guild_id=ctx.guild.id if ctx.guild else None,
                    channel_id=ctx.channel.id,
                    command_name=ctx.command.name,
                    arguments=' '.join(str(arg) for arg in ctx.args[2:]) if len(ctx.args) > 2 else None,
                    timestamp=datetime.utcnow(),
                    success=True
                )
                session.add(command_log)
                session.commit()
        except Exception as e:
            self.logger.error(f"Failed to log command to database: {e}")

    # ========================================
    # COMMAND IMPLEMENTATIONS
    # ========================================
    
    # All command implementations moved to cogs - no duplicates
    
    # All command implementations moved to cogs - no conflicts
    
    # All commands handled by cogs to prevent conflicts and double responses
    
    # FORWARDING COMMANDS
    async def is_owner_or_trusted_check(self, ctx):
        """Check if user is owner or trusted."""
        return await self.is_owner_or_trusted(ctx.author)
    
    # ========================================
    # STABILITY AND MEMORY MANAGEMENT METHODS
    # ========================================
    
    async def perform_memory_cleanup(self, force: bool = False):
        """Perform memory cleanup to prevent crashes."""
        try:
            current_time = datetime.now()
            
            # Only perform cleanup if enough time has passed or forced
            if not force and (current_time - self._last_cleanup).total_seconds() < 300:  # 5 minutes
                return
            
            self.logger.info("🧹 Performing memory cleanup...")
            
            # Clear processed message cache periodically (keep last 500 to prevent duplicates)
            if len(self._processed_messages) > 1000:
                old_size = len(self._processed_messages)
                # Filter to only keep integers (message IDs) and sort them
                valid_messages = [msg for msg in self._processed_messages if isinstance(msg, int)]
                sorted_messages = sorted(valid_messages)
                self._processed_messages = set(sorted_messages[-500:])
                self.logger.info(f"Cleaned processed messages cache: {old_size} → {len(self._processed_messages)}")
            
            # Clear old message processing logs
            cutoff_time = current_time - timedelta(hours=1)
            old_entries = [
                msg_id for msg_id, timestamp in self._message_processing_log.items()
                if isinstance(timestamp, datetime) and timestamp < cutoff_time
            ]
            for msg_id in old_entries:
                del self._message_processing_log[msg_id]
            
            if old_entries:
                self.logger.info(f"Cleared {len(old_entries)} old processing log entries")
            
            # Force garbage collection
            collected = gc.collect()
            if collected > 0:
                self.logger.info(f"Garbage collected {collected} objects")
            
            # Clear Discord cache if memory usage is high
            memory_percent = psutil.virtual_memory().percent
            if memory_percent > self.memory_threshold:
                self.logger.warning(f"High memory usage: {memory_percent}%")
                await self.clear_discord_cache()
            
            self._last_cleanup = current_time
            self.logger.info("✅ Memory cleanup completed")
            
        except Exception as e:
            self.logger.error(f"❌ Memory cleanup failed: {e}")
    
    async def clear_discord_cache(self):
        """Clear Discord internal caches to free memory."""
        try:
            self.logger.info("🗑️ Clearing Discord caches...")
            
            # NOTE: Never clear guild cache - it causes the bot to lose track of servers
            
            # Clear message cache
            if hasattr(self, '_connection') and hasattr(self._connection, '_messages'):
                message_count = len(self._connection._messages) if self._connection._messages else 0
                if message_count > 100:
                    self._connection._messages.clear()
                    self.logger.info(f"Cleared {message_count} messages from cache")
            
            self.logger.info("✅ Discord cache cleared")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to clear Discord cache: {e}")
    
    def check_memory_usage(self) -> Dict[str, Any]:
        """Check current memory usage and return status."""
        try:
            memory = psutil.virtual_memory()
            return {
                'percent': memory.percent,
                'available_mb': memory.available / (1024 * 1024),
                'used_mb': memory.used / (1024 * 1024),
                'threshold_exceeded': memory.percent > self.memory_threshold
            }
        except Exception as e:
            self.logger.error(f"Failed to check memory usage: {e}")
            return {'error': str(e)}
    
    async def handle_critical_error(self, operation: str, error: Exception, context: Dict[str, Any] = None):
        """Handle critical errors with proper logging and recovery."""
        try:
            error_info = {
                'operation': operation,
                'error': str(error),
                'type': type(error).__name__,
                'time': datetime.now().isoformat(),
                'context': context or {}
            }
            
            self.logger.error(f"🚨 CRITICAL ERROR in {operation}: {error}")
            self.logger.debug(f"Error details: {error_info}")
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            
            # Increment error count for circuit breaker pattern
            self.error_counts[operation] = self.error_counts.get(operation, 0) + 1
            
            # If too many errors, activate circuit breaker
            if self.error_counts[operation] >= 5:
                self.circuit_breakers[operation] = True
                self.logger.warning(f"🔴 Circuit breaker activated for {operation}")
            
            # Send crash notification if available
            try:
                from utils.crash_notifications import send_error_notification
                await send_error_notification(f"Critical error in {operation}", str(error))
            except:
                pass  # Don't crash on notification failure
            
            # Attempt memory cleanup on critical errors
            await self.perform_memory_cleanup(force=True)
            
        except Exception as cleanup_error:
            self.logger.error(f"Failed to handle critical error: {cleanup_error}")
    
    def is_operation_circuit_broken(self, operation: str) -> bool:
        """Check if an operation's circuit breaker is active."""
        return self.circuit_breakers.get(operation, False)
    
    def reset_circuit_breaker(self, operation: str):
        """Reset circuit breaker for an operation."""
        if operation in self.circuit_breakers:
            del self.circuit_breakers[operation]
        if operation in self.error_counts:
            self.error_counts[operation] = 0
        self.logger.info(f"🟢 Circuit breaker reset for {operation}")
    
    @tasks.loop(minutes=15)
    async def scheduled_memory_cleanup(self):
        """Scheduled memory cleanup task with resilient error handling."""
        try:
            await self.perform_memory_cleanup(force=False)
        except Exception as e:
            self.logger.error(f"❌ Scheduled memory cleanup failed: {e}", exc_info=True)
    
    @scheduled_memory_cleanup.error
    async def scheduled_memory_cleanup_error(self, error):
        """Handle errors in scheduled_memory_cleanup and restart it."""
        self.logger.error(f"⚠️ scheduled_memory_cleanup crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting scheduled_memory_cleanup in 60 seconds...")
        await asyncio.sleep(60)
        self.scheduled_memory_cleanup.restart()
    
    @tasks.loop(hours=4)
    async def discord_heartbeat(self):
        """Periodically post a brief status message to a configured channel.

        Keeps the bot visibly active in Discord during long train sessions and
        gives admins a simple way to confirm both Discord and Twitch IRC are up.
        Channel is set with !setstatuschannel; silently skips if not configured.
        """
        try:
            from utils.bot_monitor import update_heartbeat
            update_heartbeat()

            with DatabaseSession() as session:
                setting = session.query(SystemSettings).filter_by(
                    setting_key='heartbeat_channel_id', is_enabled=True
                ).first()
                if not setting or not setting.setting_value:
                    return
                channel_id = int(setting.setting_value)

            channel = self.get_channel(channel_id)
            if not channel:
                return

            uptime = datetime.utcnow() - bot_start_time
            hours, rem = divmod(int(uptime.total_seconds()), 3600)
            mins = rem // 60

            irc_ok = getattr(getattr(self, 'twitch_chat_monitor', None), 'is_connected', False)
            irc_status = "✅ Connected" if irc_ok else "⚠️ Disconnected"

            # Next upcoming train (soonest active schedule from now)
            uk_tz = pytz.timezone('Europe/London')
            now_uk = datetime.now(uk_tz)
            next_train_str = "None scheduled"
            try:
                with DatabaseSession() as session:
                    from models import TrainSchedule
                    schedules = session.query(TrainSchedule).filter_by(is_active=True).all()
                    soonest = None
                    soonest_delta = None
                    for s in schedules:
                        sd = getattr(s, 'specific_date', None)
                        if sd:
                            # One-time schedule: use its actual date (with <5am rollover) and
                            # never roll a past occurrence forward to next week.
                            if isinstance(sd, str):
                                sd = datetime.strptime(sd, '%Y-%m-%d').date()
                            cand_date = sd + timedelta(days=1) if s.start_time.hour < 5 else sd
                            candidate = uk_tz.localize(
                                datetime.combine(cand_date, s.start_time)
                            )
                            if candidate <= now_uk:
                                continue
                        else:
                            days_ahead = (s.day_of_week - now_uk.weekday()) % 7
                            candidate = now_uk.replace(
                                hour=s.start_time.hour, minute=s.start_time.minute,
                                second=0, microsecond=0
                            ) + timedelta(days=days_ahead)
                            if candidate <= now_uk:
                                candidate += timedelta(days=7)
                        delta = candidate - now_uk
                        if soonest_delta is None or delta < soonest_delta:
                            soonest = s
                            soonest_delta = delta
                    if soonest and soonest_delta:
                        td_h = int(soonest_delta.total_seconds() // 3600)
                        td_m = int((soonest_delta.total_seconds() % 3600) // 60)
                        next_train_str = f"{soonest.name} (in {td_h}h {td_m}m)"
            except Exception:
                pass

            embed = discord.Embed(
                title="🤖 Bot Heartbeat",
                color=0x00cc66 if irc_ok else 0xffaa00,
                timestamp=datetime.utcnow()
            )
            # Beta-schema health: reports column presence (not enablement) so a
            # failed republish that left the 8 beta columns un-migrated is visible
            # in Discord instead of only in deployment logs.
            try:
                missing_cols = get_db_manager().check_additive_columns()
                if missing_cols:
                    beta_status = "⚠️ MISSING " + ", ".join(missing_cols)
                else:
                    beta_status = "✅ OK"
            except Exception:
                beta_status = "❓ Unknown (check failed)"
            beta_ok = beta_status.startswith("✅")

            embed.add_field(name="Uptime", value=f"{hours}h {mins}m", inline=True)
            embed.add_field(name="Twitch IRC", value=irc_status, inline=True)
            embed.add_field(name="Next Train", value=next_train_str, inline=False)
            embed.add_field(name="Beta Schema", value=beta_status, inline=False)
            if not beta_ok:
                embed.color = 0xff3333
            embed.set_footer(text="Auto-heartbeat • use !setstatuschannel off to disable")

            await channel.send(embed=embed)
            self.logger.info(f"💓 Heartbeat sent to #{channel.name}")

        except Exception as e:
            self.logger.error(f"❌ discord_heartbeat error: {e}", exc_info=True)

    @discord_heartbeat.error
    async def discord_heartbeat_error(self, error):
        self.logger.error(f"⚠️ discord_heartbeat crashed: {error}", exc_info=True)
        await asyncio.sleep(60)
        self.discord_heartbeat.restart()

    @discord_heartbeat.before_loop
    async def before_discord_heartbeat(self):
        await self.wait_until_ready()

    # ── Weekly Monday signup announcement ──────────────────────────────────
    @tasks.loop(minutes=30)
    async def signup_announcement_scheduler(self):
        """Post the weekly Monday noon EST signup announcement to Game Lounge #general."""
        try:
            import pytz
            est = pytz.timezone('US/Eastern')
            now_est = datetime.now(est)

            # Only fire on Monday (weekday 0) between 12:00 and 12:30 PM EST/EDT
            if not (now_est.weekday() == 0 and now_est.hour == 12 and now_est.minute < 30):
                return

            with DatabaseSession() as session:
                enabled_setting = session.query(SystemSettings).filter_by(
                    setting_key='signup_announce_enabled'
                ).first()
                if not enabled_setting or not enabled_setting.is_enabled:
                    return

                today_str = now_est.strftime('%Y-%m-%d')
                last_sent = session.query(SystemSettings).filter_by(
                    setting_key='signup_announce_last_date'
                ).first()
                if last_sent and last_sent.setting_value == today_str:
                    return

            GL_GUILD_ID = 1183084958110191616
            GL_GENERAL_CHANNEL_ID = 1183143967622168668
            GL_SIGNUPS_CHANNEL_ID = 1183135069896966154

            guild = self.get_guild(GL_GUILD_ID)
            if not guild:
                self.logger.warning("signup_announcement_scheduler: Game Lounge guild not found")
                return

            general_channel = guild.get_channel(GL_GENERAL_CHANNEL_ID)
            signups_channel = guild.get_channel(GL_SIGNUPS_CHANNEL_ID)
            if not general_channel:
                self.logger.warning("signup_announcement_scheduler: #general channel not found")
                return

            signups_mention = signups_channel.mention if signups_channel else "#raid-train-sign-ups"

            announcement = (
                "🎮 **RAID TRAIN TIME SLOTS AVAILABLE!** 🎮\n\n"
                f"📍 **Post your preferred time slot in:** {signups_mention}\n\n"
                "**How to join:**\n"
                "1️⃣ Share your available time slot in the channel\n"
                "2️⃣ A staff member will get you set up on the schedule\n"
                "3️⃣ Link your Twitch account with `/linktwitch` (if not already linked)\n"
                "4️⃣ **Adding `rstone203` as a mod on your Twitch channel is recommended** "
                "(for announcements & auto-shoutouts, not required for attendance)\n\n"
                "**📊 How attendance tracking works:**\n"
                "✅ **Automatic tracking** - We monitor your Twitch chat during your time slot\n"
                "✅ **No sign-ups needed** - Just stream at your scheduled time\n"
                "✅ **Live participation counted** - Active chatters are tracked automatically\n"
                "✅ **Reports sent daily** - Full attendance reports posted after each train\n\n"
                "🚂 Let's ride the train together! All aboard!"
            )

            embed = discord.Embed(
                title="🎯 Raid Train Time Slots Available!",
                description=announcement,
                color=0x9146FF,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="Every Monday at noon • use !setannounce off to disable")

            await general_channel.send(content="@everyone", embed=embed)
            self.logger.info(f"📢 Weekly signup announcement posted to #{general_channel.name}")

            with DatabaseSession() as session:
                last_sent = session.query(SystemSettings).filter_by(
                    setting_key='signup_announce_last_date'
                ).first()
                if last_sent:
                    last_sent.setting_value = today_str
                else:
                    session.add(SystemSettings(
                        setting_key='signup_announce_last_date',
                        setting_value=today_str,
                        is_enabled=True,
                    ))
                session.commit()

        except Exception as e:
            self.logger.error(f"❌ signup_announcement_scheduler error: {e}", exc_info=True)

    @signup_announcement_scheduler.error
    async def signup_announcement_scheduler_error(self, error):
        self.logger.error(f"⚠️ signup_announcement_scheduler crashed: {error}", exc_info=True)
        await asyncio.sleep(60)
        self.signup_announcement_scheduler.restart()

    @signup_announcement_scheduler.before_loop
    async def before_signup_announcement_scheduler(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=5)
    async def stability_monitor(self):
        """Monitor stability and take corrective action."""
        try:
            # Check memory usage
            memory_status = self.check_memory_usage()
            if memory_status.get('threshold_exceeded', False):
                self.logger.warning("⚠️ Memory threshold exceeded - triggering cleanup")
                await self.perform_memory_cleanup(force=True)
            
            # Check for too many circuit breakers
            if len(self.circuit_breakers) > 3:
                self.logger.warning(f"🔴 {len(self.circuit_breakers)} circuit breakers active - entering degraded mode")
                await self.degraded_mode_operation()
            
            # Check for connection issues
            if not self.is_ready():
                self.logger.warning("🔌 Bot not ready - connection may be unstable")
                
        except Exception as e:
            self.logger.error(f"❌ Stability monitor failed: {e}", exc_info=True)
    
    @stability_monitor.error
    async def stability_monitor_error(self, error):
        """Handle errors in stability_monitor and restart it."""
        self.logger.error(f"⚠️ stability_monitor crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting stability_monitor in 60 seconds...")
        await asyncio.sleep(60)
        self.stability_monitor.restart()
    
    @tasks.loop(minutes=3)
    async def live_role_updater(self):
        """Update live roles for users streaming on Twitch."""
        try:
            if hasattr(self, 'live_role_manager'):
                await self.live_role_manager.update_all_guilds()
        except Exception as e:
            self.logger.error(f"❌ Live role updater failed: {e}", exc_info=True)
    
    @live_role_updater.before_loop
    async def before_live_role_updater(self):
        """Wait for bot to be ready before starting live role updates."""
        await self.wait_until_ready()
    
    @live_role_updater.error
    async def live_role_updater_error(self, error):
        """Handle errors in live_role_updater and restart it."""
        self.logger.error(f"⚠️ live_role_updater crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting live_role_updater in 60 seconds...")
        await asyncio.sleep(60)
        self.live_role_updater.restart()
    
    async def _check_if_streamer_live(self, twitch_user_id: str) -> bool:
        """
        Check if a Twitch streamer is currently live.
        
        Args:
            twitch_user_id: Twitch user ID to check
            
        Returns:
            True if streamer is live, False otherwise
        """
        try:
            import aiohttp
            from models import TwitchOAuthToken
            from database import DatabaseSession
            
            # Get OAuth token
            with DatabaseSession() as session:
                token_record = session.query(TwitchOAuthToken).filter_by(is_active=True).first()
                if not token_record:
                    return False
                token = token_record.access_token
            
            # Check Twitch API
            url = f"https://api.twitch.tv/helix/streams?user_id={twitch_user_id}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Client-Id": os.getenv('TWITCH_CLIENT_ID')
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        # If data array is not empty, streamer is live
                        return len(data.get('data', [])) > 0
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking if streamer {twitch_user_id} is live: {e}")
            return False
    
    @tasks.loop(minutes=10)
    async def one_hour_warning_checker(self):
        """Check for schedules starting in 1 hour and send warnings with live status to chat."""
        try:
            if not hasattr(self, 'twitch_chat_bot'):
                return
            
            from datetime import datetime, timedelta
            import pytz
            from database import DatabaseSession
            from models import TrainSchedule, User, Guild, NotificationSettings
            
            eastern_tz = pytz.timezone('Europe/London')
            now = datetime.now(eastern_tz)
            today_date = now.date()
            current_day = now.weekday()
            
            with DatabaseSession() as session:
                # Get all active schedules
                schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.is_active == True
                ).all()
                
                for schedule in schedules:
                    # Check if 1-hour warnings are enabled for this server
                    settings = session.query(NotificationSettings).filter_by(guild_id=schedule.guild_id).first()
                    if not settings or not settings.twitch_chat_bot_enabled or not settings.one_hour_warnings_enabled:
                        continue
                    
                    # Calculate schedule time. One-time (specific_date) schedules must use
                    # their stored date so a past train never re-fires on the same weekday.
                    specific_date = getattr(schedule, 'specific_date', None)
                    if specific_date:
                        if isinstance(specific_date, str):
                            specific_date = datetime.strptime(specific_date, '%Y-%m-%d').date()
                        target_date = specific_date
                        if schedule.start_time.hour < 5:
                            target_date = target_date + timedelta(days=1)
                    else:
                        schedule_day = schedule.day_of_week
                        days_until_schedule = (schedule_day - current_day) % 7
                        target_date = today_date + timedelta(days=days_until_schedule)

                    naive = datetime.combine(target_date, schedule.start_time)
                    schedule_datetime_eastern = eastern_tz.localize(naive)
                    minutes_until_start = (schedule_datetime_eastern - now).total_seconds() / 60
                    
                    # Check if schedule is starting in approximately 1 hour (55-65 minute window)
                    if 55 <= minutes_until_start <= 65:
                        # Get the current broadcaster (host)
                        host = session.query(User).filter_by(id=schedule.user_id).first()
                        if not host or not host.twitch_id or not host.twitch_login:
                            continue
                        
                        # Get next rider
                        next_rider = await self.twitch_chat_bot.get_next_rider(schedule.guild_id, host.twitch_login)
                        if not next_rider:
                            continue
                        
                        # Check if next rider is already live
                        is_live = await self._check_if_streamer_live(next_rider['twitch_id'])
                        
                        # Send message to current broadcaster's Twitch chat
                        if is_live:
                            message = f"hey @{host.twitch_login}, just a heads up @{next_rider['twitch_login']} has started their stream! 🔴"
                        else:
                            message = f"hey @{host.twitch_login}, just a heads up @{next_rider['twitch_login']} goes live in 1 hour! ⏰"
                        
                        success = await self.twitch_chat_bot.send_chat_message(host.twitch_id, message)
                        if success:
                            self.logger.info(f"⏰ Sent 1-hour warning to {host.twitch_login} for {next_rider['twitch_login']} (live: {is_live})")
                
        except Exception as e:
            self.logger.error(f"❌ 1-hour warning checker failed: {e}", exc_info=True)
    
    @one_hour_warning_checker.before_loop
    async def before_one_hour_warning_checker(self):
        """Wait for bot to be ready before checking warnings."""
        await self.wait_until_ready()
    
    @one_hour_warning_checker.error
    async def one_hour_warning_checker_error(self, error):
        """Handle errors in one_hour_warning_checker and restart it."""
        self.logger.error(f"⚠️ one_hour_warning_checker crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting one_hour_warning_checker in 60 seconds...")
        await asyncio.sleep(60)
        self.one_hour_warning_checker.restart()
    
    @tasks.loop(hours=6)
    async def twitch_outreach_checker(self):
        """Automatically DM train participants who haven't linked their Twitch account."""
        try:
            from utils.auto_link_helper import check_and_outreach_unlinked_participants
            contacted = await check_and_outreach_unlinked_participants(self)
            if contacted > 0:
                self.logger.info(f"✅ Auto-outreach: sent {contacted} Twitch link DMs")
        except Exception as e:
            self.logger.error(f"Error in twitch outreach checker: {e}", exc_info=True)

    @twitch_outreach_checker.before_loop
    async def before_twitch_outreach_checker(self):
        await self.wait_until_ready()
        import asyncio
        await asyncio.sleep(120)

    @tasks.loop(hours=24)
    async def trial_expiry_checker(self):
        """Notify users whose free trial has expired and mark expired trials inactive.

        Only runs meaningful checks when the subscription system is enabled
        (SubscriptionSettings.subscription_required). No-ops otherwise so this
        task is safe to always start.
        """
        try:
            from database import DatabaseSession
            from models import SubscriptionSettings, UserSubscription

            with DatabaseSession() as session:
                settings = session.query(SubscriptionSettings).first()
                if not settings or not settings.subscription_required:
                    return

                expired_trials = session.query(UserSubscription).filter(
                    UserSubscription.is_trial == True,
                    UserSubscription.trial_end != None,
                    UserSubscription.trial_end <= datetime.utcnow(),
                    UserSubscription.trial_expiry_notification_sent == False,
                    UserSubscription.is_active == False
                ).all()

                user_ids = [(sub.id, sub.user_id) for sub in expired_trials]

            for sub_id, user_id in user_ids:
                try:
                    user = self.get_user(user_id) or await self.fetch_user(user_id)
                    if user:
                        await user.send(
                            "⌛ Your free trial for organizer access has ended. "
                            "Use `/subscribe` in the server to see how to continue, "
                            "or reach out to the bot owner with questions."
                        )
                except Exception as dm_error:
                    self.logger.debug(f"Could not DM user {user_id} about trial expiry: {dm_error}")

                try:
                    with DatabaseSession() as session:
                        sub = session.query(UserSubscription).filter_by(id=sub_id).first()
                        if sub:
                            sub.trial_expiry_notification_sent = True
                            session.commit()
                except Exception as db_error:
                    self.logger.error(f"Error marking trial expiry notified for user {user_id}: {db_error}")

            if user_ids:
                self.logger.info(f"Trial expiry checker processed {len(user_ids)} expired trial(s)")
        except Exception as e:
            self.logger.error(f"Error in trial expiry checker: {e}", exc_info=True)

    @trial_expiry_checker.before_loop
    async def before_trial_expiry_checker(self):
        await self.wait_until_ready()
        import asyncio
        await asyncio.sleep(150)

    @tasks.loop(minutes=5)
    async def ten_minute_warning_checker(self):
        """Check for schedules starting in 10 minutes and send warnings to chat."""
        try:
            if not hasattr(self, 'twitch_chat_bot'):
                return
            
            from datetime import datetime, timedelta
            import pytz
            from database import DatabaseSession
            from models import TrainSchedule, User, Guild, NotificationSettings
            
            eastern_tz = pytz.timezone('Europe/London')
            now = datetime.now(eastern_tz)
            today_date = now.date()
            current_day = now.weekday()
            
            with DatabaseSession() as session:
                # Get all active schedules
                schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.is_active == True
                ).all()
                
                for schedule in schedules:
                    # Check if 10-minute warnings are enabled for this server
                    settings = session.query(NotificationSettings).filter_by(guild_id=schedule.guild_id).first()
                    if not settings or not settings.twitch_chat_bot_enabled or not settings.ten_minute_warnings_enabled:
                        continue
                    
                    # Calculate schedule time. One-time (specific_date) schedules must use
                    # their stored date so a past train never re-fires on the same weekday.
                    specific_date = getattr(schedule, 'specific_date', None)
                    if specific_date:
                        if isinstance(specific_date, str):
                            specific_date = datetime.strptime(specific_date, '%Y-%m-%d').date()
                        schedule_date = specific_date
                        if schedule.start_time.hour < 5:
                            schedule_date = schedule_date + timedelta(days=1)
                    else:
                        schedule_day = schedule.day_of_week
                        days_until_schedule = (schedule_day - current_day) % 7
                        schedule_date = today_date + timedelta(days=days_until_schedule)

                        # Apply week offset if present (recurring schedules only)
                        import re
                        week_offset = 0
                        if schedule.description:
                            match = re.search(r'\[WEEK_OFFSET:(\d+)\]', schedule.description)
                            if match:
                                week_offset = int(match.group(1))

                        if week_offset > 0:
                            schedule_date = schedule_date + timedelta(weeks=week_offset)
                    
                    # start_time stored as UK wall clock — localize directly
                    naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
                    start_datetime = eastern_tz.localize(naive_uk_datetime)

                    # Check if it's 10 minutes before start (±1 minute tolerance)
                    time_until_start = (start_datetime - now).total_seconds()
                    
                    # Send warning if between 9-11 minutes before start
                    if 540 <= time_until_start <= 660:  # 9-11 minutes
                        host = session.query(User).filter_by(id=schedule.host_user_id).first()
                        if host and host.twitch_id and host.twitch_login:
                            # Get next rider
                            next_rider = await self.twitch_chat_bot.get_next_rider(schedule.guild_id, host.twitch_login)
                            
                            if next_rider:
                                message = f"⏰ 10-MINUTE WARNING! Next rider {next_rider['twitch_login']} goes live in 10 minutes! Get ready to raid: twitch.tv/{next_rider['twitch_login']}"
                                await self.twitch_chat_bot.send_chat_message(host.twitch_id, message)
                                self.logger.info(f"⏰ Sent 10-min warning to {host.twitch_login} for next rider {next_rider['twitch_login']}")
                
        except Exception as e:
            self.logger.error(f"❌ 10-minute warning checker failed: {e}", exc_info=True)
    
    @ten_minute_warning_checker.before_loop
    async def before_ten_minute_warning_checker(self):
        """Wait for bot to be ready before checking warnings."""
        await self.wait_until_ready()
    
    @ten_minute_warning_checker.error
    async def ten_minute_warning_checker_error(self, error):
        """Handle errors in ten_minute_warning_checker and restart it."""
        self.logger.error(f"⚠️ ten_minute_warning_checker crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting ten_minute_warning_checker in 60 seconds...")
        await asyncio.sleep(60)
        self.ten_minute_warning_checker.restart()
    
    @tasks.loop(minutes=30)
    async def comprehensive_report_checker(self):
        """Generate day-after attendance summaries and send any that are due (~noon UTC)."""
        try:
            manager = getattr(self, 'comprehensive_report_manager', None)
            if manager is None:
                return
            await manager.generate_and_schedule_reports()
            await manager.send_pending_reports()
        except Exception as e:
            self.logger.error(f"❌ comprehensive_report_checker failed: {e}", exc_info=True)
    
    @comprehensive_report_checker.before_loop
    async def before_comprehensive_report_checker(self):
        """Wait for the bot to be ready before generating/sending reports."""
        await self.wait_until_ready()
    
    @comprehensive_report_checker.error
    async def comprehensive_report_checker_error(self, error):
        """Handle errors in comprehensive_report_checker and restart it."""
        self.logger.error(f"⚠️ comprehensive_report_checker crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting comprehensive_report_checker in 60 seconds...")
        await asyncio.sleep(60)
        self.comprehensive_report_checker.restart()
    
    # ========================================
    # DISCORD CONNECTION RESILIENCE METHODS
    # ========================================
    
    async def on_disconnect(self):
        """Handle Discord disconnection with proper logging."""
        self.logger.warning("🔌 Discord connection lost - attempting reconnection...")
        
        # Log connection metrics if available
        if hasattr(self, 'latency'):
            self.logger.info(f"Last known latency: {self.latency * 1000:.2f}ms")
        
        # Perform emergency memory cleanup
        await self.perform_memory_cleanup(force=True)
        
        # Send disconnection notification only if bot was previously connected
        # Skip during startup to avoid false alarms
        try:
            from datetime import datetime
            stability_mgr = self.stability_manager if hasattr(self, 'stability_manager') else None
            if stability_mgr and hasattr(stability_mgr, 'was_connected') and stability_mgr.was_connected:
                # Check if we're past the startup grace period (60 seconds)
                startup_grace_period = (datetime.now() - stability_mgr.startup_time).total_seconds() < 60
                if not startup_grace_period:
                    from utils.crash_notifications import send_error_notification
                    await send_error_notification("Discord Connection Lost", "Bot disconnected from Discord gateway")
        except:
            pass  # Don't crash on notification failure
    
    async def on_resumed(self):
        """Handle Discord reconnection."""
        self.logger.info("🔌 Discord connection resumed successfully")
        
        # Reset connection-related circuit breakers
        connection_operations = [op for op in self.circuit_breakers.keys() if 'connection' in op.lower()]
        for operation in connection_operations:
            self.reset_circuit_breaker(operation)
        
        # Send reconnection notification only if this was a real reconnection (not initial startup)
        try:
            from datetime import datetime
            stability_mgr = self.stability_manager if hasattr(self, 'stability_manager') else None
            if stability_mgr and hasattr(stability_mgr, 'was_connected') and stability_mgr.was_connected:
                # Check if we're past the startup grace period (60 seconds)
                startup_grace_period = (datetime.now() - stability_mgr.startup_time).total_seconds() < 60
                if not startup_grace_period:
                    from utils.crash_notifications import send_error_notification
                    await send_error_notification("Discord Connection Restored", "Bot reconnected successfully")
        except:
            pass
    
    async def on_ready(self):
        """Enhanced on_ready with stability features."""
        self.logger.info(f"🟢 Bot connected as {self.user} (ID: {self.user.id})")
        
        # Start stability manager if available (health monitor disabled - user preference)
        if self.stability_manager:
            try:
                if not self.stability_manager.memory_cleanup.is_running():
                    self.stability_manager.memory_cleanup.start()
                self.logger.info("✅ Stability manager tasks started")
            except Exception as e:
                self.logger.error(f"❌ Failed to start stability manager: {e}")
        
        # Check memory usage on startup
        memory_status = self.check_memory_usage()
        if not memory_status.get('error'):
            self.logger.info(f"📊 Memory usage: {memory_status['percent']:.1f}%")
            if memory_status['threshold_exceeded']:
                self.logger.warning("⚠️ High memory usage detected on startup")
                await self.perform_memory_cleanup(force=True)
        
        # Reset all circuit breakers on successful connection
        if self.circuit_breakers:
            self.logger.info("🟢 Resetting all circuit breakers on connection")
            for operation in list(self.circuit_breakers.keys()):
                self.reset_circuit_breaker(operation)

        # Run core startup logic (guild sync, status, slash commands, backups, etc.)
        await self._on_ready_core()

    async def stable_send_message(self, channel, content=None, **kwargs):
        """Send message with automatic retry and error handling."""
        operation = "send_message"
        
        # Check circuit breaker
        if self.is_operation_circuit_broken(operation):
            self.logger.warning(f"Circuit breaker active for {operation} - skipping")
            return None
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if isinstance(channel, int):
                    channel = self.get_channel(channel)
                    if not channel:
                        raise ValueError(f"Channel {channel} not found")
                
                return await channel.send(content, **kwargs)
                
            except discord.errors.RateLimited as e:
                wait_time = e.retry_after
                self.logger.warning(f"Rate limited sending message, waiting {wait_time}s")
                await asyncio.sleep(wait_time)
                
            except discord.errors.Forbidden:
                self.logger.error(f"No permission to send message to channel {channel}")
                break  # Don't retry permission errors
                
            except discord.errors.HTTPException as e:
                if e.status == 429:  # Rate limit
                    await asyncio.sleep(1)
                    continue
                else:
                    self.logger.error(f"HTTP error sending message: {e}")
                    if attempt == max_retries - 1:
                        await self.handle_critical_error(operation, e)
                    
            except Exception as e:
                self.logger.error(f"Error sending message (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    await self.handle_critical_error(operation, e)
                await asyncio.sleep(1 * (attempt + 1))
        
        return None
    
    async def stable_edit_message(self, message, content=None, **kwargs):
        """Edit message with automatic retry and error handling."""
        operation = "edit_message"
        
        # Check circuit breaker
        if self.is_operation_circuit_broken(operation):
            self.logger.warning(f"Circuit breaker active for {operation} - skipping")
            return None
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await message.edit(content=content, **kwargs)
                
            except discord.errors.RateLimited as e:
                wait_time = e.retry_after
                self.logger.warning(f"Rate limited editing message, waiting {wait_time}s")
                await asyncio.sleep(wait_time)
                
            except discord.errors.NotFound:
                self.logger.warning("Message not found for editing (may have been deleted)")
                break  # Don't retry if message doesn't exist
                
            except discord.errors.Forbidden:
                self.logger.error("No permission to edit message")
                break  # Don't retry permission errors
                
            except Exception as e:
                self.logger.error(f"Error editing message (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    await self.handle_critical_error(operation, e)
                await asyncio.sleep(1 * (attempt + 1))
        
        return None
    
    async def stable_add_reaction(self, message, emoji):
        """Add reaction with automatic retry and error handling."""
        operation = "add_reaction"
        
        # Check circuit breaker
        if self.is_operation_circuit_broken(operation):
            self.logger.warning(f"Circuit breaker active for {operation} - skipping")
            return False
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await message.add_reaction(emoji)
                return True
                
            except discord.errors.RateLimited as e:
                wait_time = e.retry_after
                self.logger.warning(f"Rate limited adding reaction, waiting {wait_time}s")
                await asyncio.sleep(wait_time)
                
            except discord.errors.NotFound:
                self.logger.warning("Message not found for adding reaction")
                break
                
            except discord.errors.Forbidden:
                self.logger.warning("No permission to add reaction")
                break
                
            except Exception as e:
                self.logger.error(f"Error adding reaction (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    await self.handle_critical_error(operation, e)
                await asyncio.sleep(1 * (attempt + 1))
        
        return False
    
    # ========================================
    # GRACEFUL DEGRADATION METHODS
    # ========================================
    
    async def safe_operation_wrapper(self, operation_name: str, operation_func, fallback_func=None, **kwargs):
        """Wrapper for operations with graceful degradation."""
        try:
            # Check circuit breaker first
            if self.is_operation_circuit_broken(operation_name):
                self.logger.warning(f"Circuit breaker active for {operation_name} - using fallback")
                if fallback_func:
                    return await fallback_func(**kwargs) if asyncio.iscoroutinefunction(fallback_func) else fallback_func(**kwargs)
                return None
            
            # Execute the operation
            result = await operation_func(**kwargs) if asyncio.iscoroutinefunction(operation_func) else operation_func(**kwargs)
            
            # Reset error count on success
            if operation_name in self.error_counts:
                self.error_counts[operation_name] = 0
                
            return result
            
        except Exception as e:
            self.logger.error(f"Operation {operation_name} failed: {e}")
            await self.handle_critical_error(operation_name, e, kwargs)
            
            # Try fallback if available
            if fallback_func:
                try:
                    self.logger.info(f"Attempting fallback for {operation_name}")
                    return await fallback_func(**kwargs) if asyncio.iscoroutinefunction(fallback_func) else fallback_func(**kwargs)
                except Exception as fallback_error:
                    self.logger.error(f"Fallback for {operation_name} also failed: {fallback_error}")
            
            return None
    
    async def safe_database_operation(self, operation_func, fallback_value=None, **kwargs):
        """Execute database operation with graceful degradation."""
        operation_name = "database_operation"
        
        try:
            # Check if database is available
            if not self.db_manager:
                self.logger.warning("Database manager not available - using fallback")
                return fallback_value
            
            # Execute database operation with retries
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    result = await operation_func(**kwargs) if asyncio.iscoroutinefunction(operation_func) else operation_func(**kwargs)
                    return result
                    
                except Exception as e:
                    if "connection" in str(e).lower() or "timeout" in str(e).lower():
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1 * (attempt + 1))
                            continue
                    raise
            
        except Exception as e:
            self.logger.error(f"Database operation failed: {e}")
            await self.handle_critical_error(operation_name, e)
            
            # Use fallback value
            self.logger.info(f"Using fallback value for database operation: {fallback_value}")
            return fallback_value
    
    async def safe_notification_send(self, channel_id: int, message: str, fallback_log: bool = True):
        """Send notification with graceful degradation."""
        try:
            channel = self.get_channel(channel_id)
            if not channel:
                raise ValueError(f"Channel {channel_id} not found")
            
            result = await self.stable_send_message(channel, message)
            if result:
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to send notification to channel {channel_id}: {e}")
        
        # Fallback: Log the message
        if fallback_log:
            self.logger.info(f"NOTIFICATION FALLBACK: {message}")
        
        return False
    
    async def safe_reaction_add(self, message, emoji, fallback_log: bool = True):
        """Add reaction with graceful degradation."""
        try:
            result = await self.stable_add_reaction(message, emoji)
            if result:
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to add reaction {emoji}: {e}")
        
        # Fallback: Log the reaction attempt
        if fallback_log:
            self.logger.info(f"REACTION FALLBACK: Would add {emoji} to message {message.id}")
        
        return False
    
    async def safe_user_permission_check(self, user_id: int, guild_id: int = None, fallback_result: bool = False):
        """Check user permissions with graceful degradation."""
        try:
            return await self.safe_database_operation(
                lambda: self.is_trusted_user(user_id, guild_id),
                fallback_value=fallback_result
            )
        except Exception as e:
            self.logger.error(f"Permission check failed for user {user_id}: {e}")
            return fallback_result
    
    async def degraded_mode_operation(self, essential_only: bool = True):
        """Switch to degraded mode operation."""
        self.logger.warning("🔴 Entering degraded mode operation")
        
        try:
            # Stop non-essential background tasks
            if essential_only:
                self.logger.info("Stopping non-essential background tasks")
                
                # Keep only critical tasks running
                essential_tasks = ['notification_scheduler', 'status_updater', 'persistent_updater']
                
                # This is a placeholder - actual implementation would depend on how tasks are tracked
                self.logger.info(f"Keeping essential tasks: {essential_tasks}")
            
            # Increase error thresholds temporarily
            original_thresholds = {}
            for operation in self.error_counts:
                original_thresholds[operation] = 5  # Store original threshold
            
            # Double the error thresholds in degraded mode
            self.logger.info("Increased error thresholds for degraded mode")
            
            # Trigger aggressive memory cleanup
            await self.perform_memory_cleanup(force=True)
            
            # Send degraded mode notification
            try:
                from utils.crash_notifications import send_error_notification
                await send_error_notification("Degraded Mode Active", "Bot operating in degraded mode to maintain stability")
            except:
                pass
            
            self.logger.warning("🟡 Degraded mode active - reduced functionality for stability")
            
        except Exception as e:
            self.logger.error(f"Failed to enter degraded mode: {e}")
    
    async def recover_from_degraded_mode(self):
        """Attempt to recover from degraded mode."""
        self.logger.info("🟢 Attempting recovery from degraded mode")
        
        try:
            # Reset circuit breakers
            circuit_breaker_count = len(self.circuit_breakers)
            for operation in list(self.circuit_breakers.keys()):
                self.reset_circuit_breaker(operation)
            
            # Perform health check
            if hasattr(self, 'check_memory_usage'):
                memory_status = self.check_memory_usage()
                if memory_status.get('threshold_exceeded', False):
                    self.logger.warning("Memory still high - delaying full recovery")
                    await self.perform_memory_cleanup(force=True)
                    return False
            
            # Send recovery notification
            try:
                from utils.crash_notifications import send_error_notification
                await send_error_notification("Recovery Successful", f"Bot recovered from degraded mode. Reset {circuit_breaker_count} circuit breakers.")
            except:
                pass
            
            self.logger.info("✅ Successfully recovered from degraded mode")
            return True
            
        except Exception as e:
            self.logger.error(f"Recovery from degraded mode failed: {e}")
            return False
    
    # addforward command now handled by cogs/forwarding_commands.py
    # Removed to prevent duplicates:
    async def add_forwarding_disabled(self, ctx, source_guild_id: int, source_channel_id: int, target_channel_id: int = 0):
        """Add a channel forwarding configuration (Owner only)."""
        if not await self.is_owner_or_trusted_check(ctx):
            await ctx.send("❌ This command requires owner or trusted user permissions.")
            return
        
        if target_channel_id == 0:
            target_channel_id = ctx.channel.id
        
        source_guild = self.get_guild(source_guild_id)
        if source_guild is None:
            await ctx.send(f"❌ Bot is not in server with ID: {source_guild_id}")
            return
        
        source_channel = source_guild.get_channel(source_channel_id)
        if source_channel is None:
            await ctx.send(f"❌ Channel with ID {source_channel_id} not found in {source_guild.name}")
            return
        
        target_channel = self.get_channel(target_channel_id)
        if target_channel is None:
            await ctx.send(f"❌ Channel with ID {target_channel_id} not found")
            return
        
        if not source_channel.permissions_for(source_guild.me).read_messages:
            await ctx.send(f"❌ Bot lacks read permissions in {source_channel.mention} ({source_guild.name})")
            return
        
        if not target_channel.permissions_for(target_channel.guild.me).send_messages:
            await ctx.send(f"❌ Bot lacks send permissions in {target_channel.mention}")
            return
        
        try:
            with DatabaseSession() as session:
                existing = session.query(ForwardingConfig).filter(
                    ForwardingConfig.source_guild_id == str(source_guild_id),
                    ForwardingConfig.source_channel_id == str(source_channel_id),
                    ForwardingConfig.target_channel_id == str(target_channel_id),
                    ForwardingConfig.is_active == True
                ).first()
                
                if existing:
                    await ctx.send(f"⚠️ Messages from {source_channel.mention} ({source_guild.name}) are already being forwarded to {target_channel.mention}")
                    return
                
                config = ForwardingConfig(
                    source_guild_id=str(source_guild_id),
                    source_channel_id=str(source_channel_id),
                    target_guild_id=str(target_channel.guild.id),
                    target_channel_id=str(target_channel_id),
                    created_by=str(ctx.author.id),
                    is_active=True,
                    created_at=datetime.utcnow()
                )
                
                session.add(config)
                session.commit()
            
            embed = discord.Embed(
                title="✅ Forwarding Added",
                description="Cross-server message forwarding configured successfully!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📤 Source",
                value=f"**{source_guild.name}**\n{source_channel.mention}",
                inline=True
            )
            
            embed.add_field(
                name="📥 Target",
                value=f"**{target_channel.guild.name}**\n{target_channel.mention}",
                inline=True
            )
            
            embed.add_field(
                name="⚙️ Configuration",
                value=f"**Created by:** {ctx.author.mention}\n**Status:** Active\n**Type:** Cross-server",
                inline=False
            )
            
            embed.set_footer(text="Messages will now be forwarded automatically")
            
            await ctx.send(embed=embed)
            self.logger.info(f"Forwarding added: {source_guild.name}#{source_channel.name} → {target_channel.guild.name}#{target_channel.name} by {ctx.author}")
            
        except Exception as e:
            self.logger.error(f"Error adding forwarding config: {e}")
            await ctx.send(f"❌ Failed to add forwarding configuration: {str(e)}")
    
    # forwards command now handled by cogs/forwarding_commands.py
    # Removed to prevent duplicates:
    async def list_forwarding_disabled(self, ctx):
        """List all forwarding configurations."""
        if not self.db_manager:
            await ctx.send("❌ Database is not connected")
            return
            
        try:
            with DatabaseSession() as session:
                configs = session.query(ForwardingConfig).all()
                
                if not configs:
                    embed = discord.Embed(
                        title="📋 Forwarding Configurations",
                        description="No forwarding configurations found.",
                        color=0x667eea
                    )
                    await ctx.send(embed=embed)
                    return
                
                embed = discord.Embed(
                    title="📋 Forwarding Configurations",
                    description=f"Found {len(configs)} forwarding configurations:",
                    color=0x667eea,
                    timestamp=datetime.utcnow()
                )
                
                active_count = 0
                for config in configs[:10]:  # Limit to 10 to avoid embed limits
                    source_guild = self.get_guild(int(config.source_guild_id))
                    target_guild = self.get_guild(int(config.target_guild_id))
                    source_channel = self.get_channel(int(config.source_channel_id)) if source_guild else None
                    target_channel = self.get_channel(int(config.target_channel_id))
                    
                    status = "🟢 Active" if config.is_active else "🔴 Inactive"
                    if config.is_active:
                        active_count += 1
                    
                    source_name = f"{source_guild.name if source_guild else 'Unknown'} > #{source_channel.name if source_channel else 'unknown'}"
                    target_name = f"{target_guild.name if target_guild else 'Unknown'} > #{target_channel.name if target_channel else 'unknown'}"
                    
                    embed.add_field(
                        name=f"Config #{config.id} {status}",
                        value=f"**From:** {source_name}\n**To:** {target_name}\n**Created:** {config.created_at.strftime('%Y-%m-%d') if config.created_at else 'Unknown'}",
                        inline=True
                    )
                
                if len(configs) > 10:
                    embed.add_field(
                        name="📝 Note",
                        value=f"Showing first 10 of {len(configs)} configurations",
                        inline=False
                    )
                
                embed.add_field(
                    name="📊 Summary",
                    value=f"**Total:** {len(configs)}\n**Active:** {active_count}\n**Inactive:** {len(configs) - active_count}",
                    inline=False
                )
                
                embed.set_footer(text="Use !dashboard for web management interface")
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error listing forwarding configs: {e}")
            await ctx.send(f"❌ Failed to retrieve forwarding configurations: {str(e)}")
    
    # removeforward command now handled by cogs/forwarding_commands.py
    # Removed to prevent duplicates:
    async def remove_forwarding_disabled(self, ctx, config_id: int):
        """Remove a forwarding configuration (Owner only)."""
        if not await self.is_owner_or_trusted_check(ctx):
            await ctx.send("❌ This command requires owner or trusted user permissions.")
            return
        
        if not self.db_manager:
            await ctx.send("❌ Database is not connected")
            return
            
        try:
            with DatabaseSession() as session:
                config = session.query(ForwardingConfig).filter(ForwardingConfig.id == config_id).first()
                
                if not config:
                    await ctx.send(f"❌ Forwarding configuration with ID {config_id} not found")
                    return
                
                # Get channel info for confirmation
                source_guild = self.get_guild(int(config.source_guild_id))
                target_guild = self.get_guild(int(config.target_guild_id))
                source_channel = self.get_channel(int(config.source_channel_id)) if source_guild else None
                target_channel = self.get_channel(int(config.target_channel_id))
                
                source_name = f"{source_guild.name if source_guild else 'Unknown'} > #{source_channel.name if source_channel else 'unknown'}"
                target_name = f"{target_guild.name if target_guild else 'Unknown'} > #{target_channel.name if target_channel else 'unknown'}"
                
                session.delete(config)
                session.commit()
            
            embed = discord.Embed(
                title="✅ Forwarding Removed",
                description=f"Forwarding configuration #{config_id} has been deleted.",
                color=0xff0000,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🗑️ Deleted Configuration",
                value=f"**From:** {source_name}\n**To:** {target_name}",
                inline=False
            )
            
            embed.set_footer(text="Messages will no longer be forwarded")
            
            await ctx.send(embed=embed)
            self.logger.info(f"Forwarding config #{config_id} removed by {ctx.author}")
            
        except Exception as e:
            self.logger.error(f"Error removing forwarding config: {e}")
            await ctx.send(f"❌ Failed to remove forwarding configuration: {str(e)}")
    
    # DATABASE COMMANDS
    # dbstats command now handled by cogs/database_commands.py
    # Removed to prevent duplicates:
    async def database_stats(self, ctx):
        """Show database statistics."""
        if not self.db_manager:
            await ctx.send("❌ Database is not connected")
            return
            
        try:
            with DatabaseSession() as session:
                guild_count = session.query(Guild).filter(Guild.is_active == True).count()
                user_count = session.query(User).count()
                message_count = session.query(Message).count()
                forwarded_count = session.query(Message).filter(Message.was_forwarded == True).count()
                command_count = session.query(CommandLog).count()
                
                yesterday = datetime.utcnow() - timedelta(days=1)
                recent_messages = session.query(Message).filter(Message.timestamp >= yesterday).count()
                recent_commands = session.query(CommandLog).filter(CommandLog.timestamp >= yesterday).count()
                recent_forwards = session.query(Message).filter(
                    Message.timestamp >= yesterday,
                    Message.was_forwarded == True
                ).count()
                
                embed = discord.Embed(
                    title="📊 Database Statistics",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📈 Total Counts",
                    value=f"**Guilds:** {guild_count:,}\n"
                          f"**Users:** {user_count:,}\n"
                          f"**Messages:** {message_count:,}\n"
                          f"**Forwarded:** {forwarded_count:,}\n"
                          f"**Commands:** {command_count:,}",
                    inline=True
                )
                
                embed.add_field(
                    name="⚡ Last 24 Hours",
                    value=f"**Messages:** {recent_messages:,}\n"
                          f"**Commands:** {recent_commands:,}\n"
                          f"**Forwarded:** {recent_forwards:,}",
                    inline=True
                )
                
                embed.add_field(
                    name="💾 Database Status",
                    value="✅ Connected\n🟢 Operational",
                    inline=True
                )
                
                embed.set_footer(text="Database statistics updated in real-time")
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error retrieving database statistics: {e}")
            await ctx.send(f"❌ Failed to retrieve database statistics: {str(e)}")
    
    # ADMIN COMMANDS
    # shutdown command now handled by cogs/admin_commands.py
    # Removed to prevent duplicates:
    async def shutdown_bot(self, ctx):
        """Shutdown the bot (Owner only)."""
        if not await self.is_owner_or_trusted_check(ctx):
            await ctx.send("❌ This command requires owner permissions.")
            return
        
        embed = discord.Embed(
            title="🔴 Shutting Down",
            description="Bot is shutting down...",
            color=0xff0000,
            timestamp=datetime.utcnow()
        )
        await ctx.send(embed=embed)
        self.logger.info(f"Bot shutdown initiated by {ctx.author}")
        await self.close()
    
    # restart command now handled by cogs/admin_commands.py
    # Removed to prevent duplicates:
    async def restart_bot(self, ctx):
        """Restart the bot (Owner only)."""
        if not await self.is_owner_or_trusted_check(ctx):
            await ctx.send("❌ This command requires owner permissions.")
            return
        
        embed = discord.Embed(
            title="🔄 Restarting",
            description="Bot is restarting... Please wait.",
            color=0xffa500,
            timestamp=datetime.utcnow()
        )
        await ctx.send(embed=embed)
        self.logger.info(f"Bot restart initiated by {ctx.author}")
        await self.close()
    
    # trustuser command now handled by cogs/trusted_user_commands.py
    # Removed to prevent duplicates:
    async def trust_user(self, ctx, user: discord.User, *, notes: str = ""):
        """Add a user to the trusted users list (Owner only)."""
        if not await self.is_owner(ctx.author):
            await ctx.send("❌ This command requires bot owner permissions.")
            return
        
        if not self.db_manager:
            await ctx.send("❌ Database is not connected")
            return
            
        try:
            with DatabaseSession() as session:
                existing = session.query(TrustedUser).filter(
                    TrustedUser.user_id == user.id,
                    TrustedUser.is_active == True
                ).first()
                
                if existing:
                    await ctx.send(f"❌ {user.display_name} is already a trusted user.")
                    return
                
                trusted_user = TrustedUser(
                    user_id=user.id,
                    username=user.name,
                    display_name=user.display_name or user.name,
                    granted_by=ctx.author.id,
                    granted_at=datetime.utcnow(),
                    is_active=True,
                    notes=notes if notes else f"Trusted by {ctx.author} via command"
                )
                
                session.add(trusted_user)
                session.commit()
            
            embed = discord.Embed(
                title="✅ User Trusted",
                description=f"{user.mention} has been granted trusted status with owner-level permissions.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="👤 User",
                value=f"**Name:** {user.display_name}\n**ID:** {user.id}",
                inline=True
            )
            
            embed.add_field(
                name="🛡️ Permissions",
                value="• All owner commands\n• Message forwarding management\n• Bot administration",
                inline=True
            )
            
            if notes:
                embed.add_field(
                    name="📝 Notes",
                    value=notes,
                    inline=False
                )
            
            embed.set_footer(text="Use !untrustuser to revoke trusted status")
            
            await ctx.send(embed=embed)
            self.logger.info(f"User {user} granted trusted status by {ctx.author}")
            
        except Exception as e:
            self.logger.error(f"Error adding trusted user: {e}")
            await ctx.send(f"❌ Failed to add trusted user: {str(e)}")
    
    # Dashboard command removed - handled by cogs/basic_commands.py to prevent duplicates
    # async def dashboard_access(self, interaction: discord.Interaction):
    #     """Dashboard access moved to basic_commands cog."""
    #     pass

# ========================================
# MAIN FUNCTION
# ========================================

def main():
    """Main function to start the bot and keep-alive server."""
    # Setup logging
    logger = setup_logger()
    
    logger.info("Starting Consolidated Train Bot in deployment-ready mode...")
    logger.info("All-in-one Discord bot with comprehensive features")
    
    # Port configuration
    port = int(os.getenv('PORT', os.getenv('REPLIT_PORT', 5000)))
    logger.info(f"Target deployment port: {port}")
    
    # Set environment variables for Flask server
    os.environ['FLASK_RUN_PORT'] = str(port)
    os.environ['FLASK_RUN_HOST'] = '0.0.0.0'
    
    # Start keep-alive server
    logger.info("Starting Flask health server for deployment...")
    keep_alive_thread = threading.Thread(target=start_keep_alive_server, daemon=True)
    keep_alive_thread.start()
    
    # Give the health server time to start
    time.sleep(3)
    
    # Verify Flask server
    logger.info("Verifying deployment health endpoints...")
    
    # Check for bot token
    bot_token = os.getenv('DISCORD_BOT_TOKEN')
    if not bot_token:
        logger.error("DISCORD_BOT_TOKEN environment variable not found!")
        logger.error("Please set your Discord bot token in the Secrets tab.")
        logger.warning("Health server is running for deployment - bot functionality disabled")
        
        try:
            while True:
                time.sleep(60)
                logger.debug("Health server maintenance mode")
        except KeyboardInterrupt:
            logger.info("Application stopped")
            sys.exit(0)
        return
    
    bot_token = bot_token.strip()
    
    if not bot_token.startswith(('Bot ', 'MTM', 'MTE', 'MTV', 'MTA')):
        logger.warning("Token format may be incorrect. Discord bot tokens typically start with 'MTx...'")
    
    logger.info("Bot token loaded successfully")
    
    # Verify keep-alive server
    health_check_passed = False
    for attempt in range(3):
        try:
            response = requests.get(f'http://localhost:{port}/health', timeout=10)
            if response.status_code == 200:
                logger.info(f"✅ Deployment health check passed (attempt {attempt + 1})")
                health_check_passed = True
                break
            else:
                logger.warning(f"Health check returned status: {response.status_code} (attempt {attempt + 1})")
        except Exception as e:
            logger.warning(f"Health check attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(2)
    
    if health_check_passed:
        logger.info("🚀 Deployment ready - Flask server responding to health checks")
    else:
        logger.warning("⚠️ Health check verification failed, but continuing startup")
    
    # Create and run the Discord bot
    logger.info("Initializing Discord bot...")
    bot = DiscordBot()
    
    # Start background monitoring tasks
    asyncio.create_task(memory_logger())
    asyncio.create_task(cleanup_stale_raids())
    
    # Connect bot instance to keep-alive server
    set_bot_instance(bot)
    logger.info("Bot instance connected to keep-alive server")
    
    try:
        logger.info("Starting Discord bot...")
        bot.run(bot_token)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        logger.error("Bot startup failed, but maintaining health server for deployment")
        
        logger.info("Keep-alive server will continue running for health checks")
        
        try:
            response = requests.get('http://localhost:5000/health', timeout=5)
            if response.status_code == 200:
                logger.info("Health server confirmed accessible for deployment")
            else:
                logger.warning(f"Health server status: {response.status_code}")
        except Exception as health_error:
            logger.warning(f"Health server check failed: {health_error}")
        
        try:
            logger.info("Entering deployment keep-alive mode...")
            while True:
                time.sleep(60)
                logger.debug("Main thread alive for deployment health checks")
        except KeyboardInterrupt:
            logger.info("Application stopped")
            sys.exit(0)

if __name__ == "__main__":
    main()