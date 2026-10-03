#!/usr/bin/env python3
"""
Server Feature Permissions Manager
Controls which features are enabled/disabled per server.
"""

import logging
from typing import Optional, Dict
from functools import wraps
import discord
from discord.ext import commands
from models import ServerFeaturePermissions, get_est_time
from sqlalchemy.orm import Session

logger = logging.getLogger('server_permissions')

# Feature category mappings - which commands belong to which category
FEATURE_CATEGORIES = {
    'trains': [
        'addslot', 'removeslot', 'jointrain', 'leavetrain', 'mytrains',
        'jointraindropdown', 'leavetraindropdown', 'trainroster', 'timeslots',
        'setuppersistenttimeslots', 'removepersistenttimeslots', 'nexttrain',
        'setuptrainpings', 'setattendancechannel', 'refreshpersistent',
        'markcomplete', 'sendattendancenow', 'sessionsummary'
    ],
    'twitch': [
        'twitchcord', 'linktwitch', 'twitchhelp', 'twitchstatus', 'streams',
        'poll', 'leaderboard', 'twitchsettings', 'topsupporters', 'raidstats',
        'testchatmonitor', 'chatmonitorstatus', 'stopchatmonitor', 'starttest',
        'testtwitchchat', 'stoptest', 'twitchoauth', 'oauthstatus',
        'setliverole', 'removeliverole', 'liverolestatus', 'setlivemode'
    ],
    'forwarding': [
        'addforward', 'listforwards', 'removeforward'
    ],
    'admin': [
        'banuser', 'unbanuser', 'listbans', 'backups', 'viewbackups',
        'guildinfo', 'dbstats', 'topusers', 'maintenance', 'endmaintenance'
    ],
    'analytics': [
        'analytics', 'sheetsync', 'sheetsyncsaturday', 'sheetsunsync',
        'sheetstatus', 'sheetsyncnow'
    ],
    'general': [
        'ping', 'info', 'botinvite', 'help', 'dash', 'dashboard', 'uptime',
        'invite', 'status', 'servers', 'updates', 'donate', 'support'
    ]
}

# Beta feature categories - these default to DISABLED everywhere until a server
# admin/owner explicitly enables them via /managefeatures. Unlike the legacy
# categories above, the absence of a permissions row means OFF for these.
BETA_FEATURES = {'reminders', 'waitlist', 'stats', 'self_signup'}


class ServerPermissionsManager:
    """Manages feature permissions for servers."""
    
    def __init__(self, db_session: Session = None):
        # db_session parameter kept for backwards compatibility but not used
        self._permissions_cache: Dict[int, ServerFeaturePermissions] = {}
    
    def clear_cache(self, guild_id: Optional[int] = None):
        """Clear the permissions cache for a specific guild or all guilds."""
        if guild_id is not None:
            self._permissions_cache.pop(guild_id, None)
            logger.info(f"Cleared permissions cache for guild {guild_id}")
        else:
            self._permissions_cache.clear()
            logger.info("Cleared all permissions cache")
    
    async def get_server_permissions(self, guild_id: int) -> Optional[ServerFeaturePermissions]:
        """Get feature permissions for a server (with caching)."""
        from database import run_db

        # Check cache first
        if guild_id in self._permissions_cache:
            return self._permissions_cache[guild_id]

        # Run the query (connect/query/commit/close) entirely in a background
        # thread via run_db - this is the only way to guarantee a slow or
        # sleeping DB endpoint never blocks the Discord gateway heartbeat.
        def _work(session):
            permissions = session.query(ServerFeaturePermissions).filter_by(
                guild_id=guild_id
            ).first()

            # Make attributes concrete before detaching (forces lazy-loaded attrs to load)
            if permissions:
                # Access all attributes to ensure they're loaded
                _ = (permissions.trains_enabled, permissions.twitch_enabled,
                     permissions.forwarding_enabled, permissions.admin_enabled,
                     permissions.analytics_enabled, permissions.general_enabled,
                     getattr(permissions, 'reminders_enabled', False),
                     getattr(permissions, 'waitlist_enabled', False),
                     getattr(permissions, 'stats_enabled', False),
                     getattr(permissions, 'self_signup_enabled', False),
                     permissions.configured_by, permissions.updated_at, permissions.notes)
                session.expunge(permissions)
            return permissions

        permissions = await run_db(_work)
        if permissions:
            self._permissions_cache[guild_id] = permissions

        return permissions
    
    async def is_feature_enabled(self, guild_id: int, feature_category: str) -> bool:
        """Check if a feature category is enabled for a server.

        Legacy categories default to ENABLED when no permissions row exists.
        Beta categories (see BETA_FEATURES) default to DISABLED everywhere until
        explicitly enabled per-server.
        """
        permissions = await self.get_server_permissions(guild_id)
        
        # If no permissions set: legacy features on, beta features off.
        if not permissions:
            return feature_category not in BETA_FEATURES
        
        # Check the specific feature category. Use getattr with safe defaults so
        # the code tolerates the beta columns being absent until the prod schema
        # migration is applied at publish time.
        feature_map = {
            'trains': permissions.trains_enabled,
            'twitch': permissions.twitch_enabled,
            'forwarding': permissions.forwarding_enabled,
            'admin': permissions.admin_enabled,
            'analytics': permissions.analytics_enabled,
            'general': permissions.general_enabled,
            'reminders': getattr(permissions, 'reminders_enabled', False),
            'waitlist': getattr(permissions, 'waitlist_enabled', False),
            'stats': getattr(permissions, 'stats_enabled', False),
            'self_signup': getattr(permissions, 'self_signup_enabled', False),
        }
        
        default = feature_category not in BETA_FEATURES
        value = feature_map.get(feature_category, default)
        return bool(value) if value is not None else default
    
    async def is_command_allowed(self, guild_id: int, command_name: str) -> bool:
        """Check if a specific command is allowed in a server."""
        # Find which category this command belongs to
        command_category = None
        for category, commands in FEATURE_CATEGORIES.items():
            if command_name.lower() in commands:
                command_category = category
                break
        
        # If command not in any category, allow it
        if not command_category:
            return True
        
        return await self.is_feature_enabled(guild_id, command_category)
    
    async def set_server_permissions(
        self,
        guild_id: int,
        configured_by: int,
        trains: bool = True,
        twitch: bool = True,
        forwarding: bool = True,
        admin: bool = True,
        analytics: bool = True,
        general: bool = True,
        reminders: bool = False,
        waitlist: bool = False,
        stats: bool = False,
        self_signup: bool = False,
        notes: Optional[str] = None
    ) -> ServerFeaturePermissions:
        """Set feature permissions for a server."""
        from database import run_db

        def _work(session):
            # Get existing permissions
            permissions = session.query(ServerFeaturePermissions).filter_by(
                guild_id=guild_id
            ).first()
            
            if permissions:
                # Update existing permissions
                permissions.trains_enabled = trains
                permissions.twitch_enabled = twitch
                permissions.forwarding_enabled = forwarding
                permissions.admin_enabled = admin
                permissions.analytics_enabled = analytics
                permissions.general_enabled = general
                permissions.reminders_enabled = reminders
                permissions.waitlist_enabled = waitlist
                permissions.stats_enabled = stats
                permissions.self_signup_enabled = self_signup
                permissions.configured_by = configured_by
                permissions.updated_at = get_est_time()
                if notes:
                    permissions.notes = notes
            else:
                # Create new permissions
                permissions = ServerFeaturePermissions(
                    guild_id=guild_id,
                    trains_enabled=trains,
                    twitch_enabled=twitch,
                    forwarding_enabled=forwarding,
                    admin_enabled=admin,
                    analytics_enabled=analytics,
                    general_enabled=general,
                    reminders_enabled=reminders,
                    waitlist_enabled=waitlist,
                    stats_enabled=stats,
                    self_signup_enabled=self_signup,
                    configured_by=configured_by,
                    notes=notes
                )
                session.add(permissions)

            session.flush()

            # Access all attributes to ensure they're loaded before detaching
            _ = (permissions.trains_enabled, permissions.twitch_enabled,
                 permissions.forwarding_enabled, permissions.admin_enabled,
                 permissions.analytics_enabled, permissions.general_enabled,
                 getattr(permissions, 'reminders_enabled', False),
                 getattr(permissions, 'waitlist_enabled', False),
                 getattr(permissions, 'stats_enabled', False),
                 getattr(permissions, 'self_signup_enabled', False),
                 permissions.configured_by, permissions.updated_at, permissions.notes)

            # Detach from session for caching (session.commit() happens in run_db
            # after this function returns, so expunge here keeps attrs loaded)
            session.expunge(permissions)
            return permissions

        permissions = await run_db(_work)

        # Clear old cache and update with new object
        self.clear_cache(guild_id)
        self._permissions_cache[guild_id] = permissions

        logger.info(f"Updated server permissions for guild {guild_id}")
        return permissions
    
    async def reset_server_permissions(self, guild_id: int):
        """Reset server permissions to default (all enabled)."""
        from database import run_db

        def _work(session):
            permissions = session.query(ServerFeaturePermissions).filter_by(
                guild_id=guild_id
            ).first()

            if permissions:
                session.delete(permissions)
                return True
            return False

        deleted = await run_db(_work)
        if deleted:
            self.clear_cache(guild_id)
            logger.info(f"Reset server permissions for guild {guild_id}")


def requires_feature(feature_category: str):
    """Decorator to check if a feature is enabled before executing a command."""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, ctx: commands.Context, *args, **kwargs):
            # Get guild ID
            if not ctx.guild:
                # DM commands always allowed
                return await func(self, ctx, *args, **kwargs)
            
            guild_id = ctx.guild.id
            
            # Get bot instance and permissions manager
            bot = self.bot if hasattr(self, 'bot') else ctx.bot
            if not hasattr(bot, 'permissions_manager'):
                # No permissions manager, allow command
                return await func(self, ctx, *args, **kwargs)
            
            permissions_manager: ServerPermissionsManager = bot.permissions_manager
            
            # Check if feature is enabled
            if not await permissions_manager.is_feature_enabled(guild_id, feature_category):
                await ctx.send(
                    f"❌ This feature category (`{feature_category}`) is currently disabled in this server.\n"
                    f"Contact a server administrator for more information.",
                    ephemeral=True
                )
                return
            
            # Feature is enabled, execute command
            return await func(self, ctx, *args, **kwargs)
        
        return wrapper
    return decorator
