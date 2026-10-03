"""
Permission decorators for slash commands.
Provides proper async database checks for bot owner, trusted users, and trusted roles.
"""

import discord
from discord import app_commands
from database import run_db
from models import TrustedUser, TrustedRole, ServerFeaturePermissions
import logging

logger = logging.getLogger('discord_bot.slash_permissions')


async def is_bot_owner(interaction: discord.Interaction) -> bool:
    """Check if the user is the bot owner."""
    return interaction.user.id == interaction.client.owner_id


def _is_trusted_access_enabled_for_guild_sync(session, guild_id: int) -> bool:
    """Return True only if the bot owner has enabled trusted access for this guild."""
    try:
        perms = session.query(ServerFeaturePermissions).filter_by(
            guild_id=guild_id
        ).first()
        return perms is not None and perms.trusted_access_enabled
    except Exception as e:
        logger.error(f"Error checking trusted_access_enabled for guild {guild_id}: {e}")
        return False


async def is_trusted_user_check(interaction: discord.Interaction) -> bool:
    """Check if user is in the trusted users table AND trusted access is enabled for this guild."""
    # Trusted access is always guild-scoped; deny all DM invocations.
    if not interaction.guild:
        return False

    user_id = interaction.user.id
    guild_id = interaction.guild.id

    def _work(session):
        trusted = session.query(TrustedUser).filter_by(
            user_id=user_id,
            is_active=True
        ).first()
        if trusted is None:
            return False
        # Trusted user records are global, but they only apply when the guild
        # has trusted access enabled.
        if not _is_trusted_access_enabled_for_guild_sync(session, guild_id):
            return False
        return True

    try:
        # Runs the whole DB check (connect/query/commit/close, incl. any
        # retries) in a background thread so a slow/sleeping DB endpoint
        # can never block the Discord gateway heartbeat.
        return await run_db(_work)
    except Exception as e:
        logger.error(f"Error checking trusted user status: {e}")
        return False


async def has_trusted_role_check(interaction: discord.Interaction) -> bool:
    """Check if user has a trusted role in the current guild and trusted access is enabled."""
    if not interaction.guild:
        return False

    guild_id = interaction.guild.id
    user_role_ids = {role.id for role in interaction.user.roles}

    def _work(session):
        # Trusted roles only apply when trusted access is enabled for this guild.
        if not _is_trusted_access_enabled_for_guild_sync(session, guild_id):
            return False

        # Get all trusted roles for this guild
        trusted_roles = session.query(TrustedRole).filter_by(
            guild_id=guild_id,
            is_active=True
        ).all()

        if not trusted_roles:
            return False

        # Check if user has any of the trusted roles
        trusted_role_ids = {tr.role_id for tr in trusted_roles}

        return bool(user_role_ids & trusted_role_ids)

    try:
        return await run_db(_work)
    except Exception as e:
        logger.error(f"Error checking trusted role status: {e}")
        return False


async def has_manage_guild_permission(interaction: discord.Interaction) -> bool:
    """Check if user has Manage Server or Administrator permission."""
    if not interaction.guild:
        return False
    return (interaction.user.guild_permissions.manage_guild or 
            interaction.user.guild_permissions.administrator)


# ============================================================
# Permission Check Functions (for use with @app_commands.check)
# ============================================================

async def check_owner_only(interaction: discord.Interaction) -> bool:
    """
    Owner-only permission check.
    Returns True if user is the bot owner.
    """
    is_owner = await is_bot_owner(interaction)
    
    if not is_owner:
        await interaction.response.send_message(
            "❌ This command is restricted to the bot owner only.",
            ephemeral=True
        )
    
    return is_owner


async def check_owner_or_trusted(interaction: discord.Interaction) -> bool:
    """
    Owner or trusted user permission check.
    Returns True if user is:
    - Bot owner, OR
    - In the trusted users database, OR
    - Has a trusted role in this server
    """
    # Check owner first (fastest)
    if await is_bot_owner(interaction):
        return True
    
    # Check trusted user
    if await is_trusted_user_check(interaction):
        return True
    
    # Check trusted role
    if await has_trusted_role_check(interaction):
        return True
    
    # User doesn't have permission
    await interaction.response.send_message(
        "❌ You don't have permission to use this command.\n\n"
        "**Required:** Bot owner, trusted user, or trusted role member.",
        ephemeral=True
    )
    return False


async def check_admin_or_trusted(interaction: discord.Interaction) -> bool:
    """
    Admin or trusted permission check.
    Returns True if user is:
    - Bot owner, OR
    - In the trusted users database, OR
    - Has a trusted role in this server, OR
    - Has Manage Server permission
    """
    # Check owner first (fastest)
    if await is_bot_owner(interaction):
        return True
    
    # Check manage guild permission
    if await has_manage_guild_permission(interaction):
        return True
    
    # Check trusted user
    if await is_trusted_user_check(interaction):
        return True
    
    # Check trusted role
    if await has_trusted_role_check(interaction):
        return True
    
    # User doesn't have permission
    await interaction.response.send_message(
        "❌ You don't have permission to use this command.\n\n"
        "**Required:** Server admin, bot owner, trusted user, or trusted role member.",
        ephemeral=True
    )
    return False


# ============================================================
# Convenience Decorators
# ============================================================

def owner_only():
    """
    Decorator for owner-only slash commands.
    
    Usage:
        @app_commands.command(name="mycommand", description="My command")
        @owner_only()
        async def my_command(self, interaction: discord.Interaction):
            ...
    """
    return app_commands.check(check_owner_only)


def owner_or_trusted():
    """
    Decorator for commands accessible to owner or trusted users/roles.
    
    Usage:
        @app_commands.command(name="mycommand", description="My command")
        @owner_or_trusted()
        async def my_command(self, interaction: discord.Interaction):
            ...
    """
    return app_commands.check(check_owner_or_trusted)


def admin_or_trusted():
    """
    Decorator for commands accessible to server admins, owner, or trusted users/roles.
    
    Usage:
        @app_commands.command(name="mycommand", description="My command")
        @admin_or_trusted()
        async def my_command(self, interaction: discord.Interaction):
            ...
    """
    return app_commands.check(check_admin_or_trusted)


async def check_server_admin_only(interaction: discord.Interaction) -> bool:
    """
    Server admin or bot owner permission check.
    Returns True if user is:
    - Bot owner, OR
    - Has Manage Server (manage_guild) permission
    Trusted users and trusted roles are NOT granted access.
    """
    if await is_bot_owner(interaction):
        return True

    if await has_manage_guild_permission(interaction):
        return True

    await interaction.response.send_message(
        "❌ You don't have permission to use this command.\n\n"
        "**Required:** Server admin (Manage Server permission) or bot owner.",
        ephemeral=True
    )
    return False


def server_admin_only():
    """
    Decorator for commands that require a real server admin or bot owner.
    Trusted users and trusted roles are NOT sufficient.

    Usage:
        @app_commands.command(name="mycommand", description="My command")
        @server_admin_only()
        async def my_command(self, interaction: discord.Interaction):
            ...
    """
    return app_commands.check(check_server_admin_only)


# ============================================================
# Helper Functions
# ============================================================

async def can_user_access_command(interaction: discord.Interaction, permission_level: str = "admin") -> bool:
    """
    Helper function to check if a user can access a command.
    
    Args:
        interaction: The Discord interaction
        permission_level: "owner", "trusted", or "admin"
    
    Returns:
        bool: True if user has access, False otherwise
    """
    if permission_level == "owner":
        return await is_bot_owner(interaction)
    elif permission_level == "trusted":
        return await check_owner_or_trusted(interaction)
    elif permission_level == "admin":
        return await check_admin_or_trusted(interaction)
    else:
        logger.error(f"Invalid permission level: {permission_level}")
        return False


def get_permission_error_message(permission_level: str = "admin") -> str:
    """Get a user-friendly error message for permission denied."""
    if permission_level == "owner":
        return "❌ This command is restricted to the bot owner only."
    elif permission_level == "trusted":
        return ("❌ You don't have permission to use this command.\n\n"
                "**Required:** Bot owner, trusted user, or trusted role member.")
    elif permission_level == "admin":
        return ("❌ You don't have permission to use this command.\n\n"
                "**Required:** Server admin, bot owner, trusted user, or trusted role member.")
    else:
        return "❌ You don't have permission to use this command."
