#!/usr/bin/env python3
"""
Trusted Access Control Commands (Bot Owner Only)
Allows bot owner to enable/disable trusted user/role access per server.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from database import DatabaseSession
from models import ServerFeaturePermissions, get_est_time
from utils.slash_permissions import owner_only


class TrustedAccessControl(commands.Cog):
    """Bot owner commands to control trusted access per server."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.trusted_access_control')
    
    @app_commands.command(
        name='enabletrustedaccess',
        description='[Owner] Enable trusted user/role access for a specific server'
    )
    @app_commands.describe(
        server_id="The Discord server ID to enable trusted access for"
    )
    @owner_only()
    async def enable_trusted_access(
        self,
        interaction: discord.Interaction,
        server_id: str
    ):
        """Enable trusted access for a specific server (bot owner only)."""
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            guild_id = int(server_id)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid server ID. Please provide a numeric server ID.",
                ephemeral=True
            )
            return
        
        # Check if bot is in this server
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await interaction.followup.send(
                f"❌ Bot is not in server with ID `{guild_id}`.\n"
                f"Make sure the server ID is correct and the bot is a member.",
                ephemeral=True
            )
            return
        
        # Enable trusted access for this server
        try:
            with DatabaseSession() as session:
                server_perms = session.query(ServerFeaturePermissions).filter_by(
                    guild_id=guild_id
                ).first()
                
                if not server_perms:
                    # Create new permissions record
                    server_perms = ServerFeaturePermissions(
                        guild_id=guild_id,
                        trusted_access_enabled=True,
                        configured_by=interaction.user.id
                    )
                    session.add(server_perms)
                else:
                    # Update existing record
                    server_perms.trusted_access_enabled = True
                    server_perms.configured_by = interaction.user.id
                    server_perms.updated_at = get_est_time()
                
                session.commit()
                
                self.logger.info(f"✅ Enabled trusted access for server {guild.name} ({guild_id})")
                
                await interaction.followup.send(
                    f"✅ **Trusted Access ENABLED**\n\n"
                    f"**Server:** {guild.name}\n"
                    f"**Server ID:** `{guild_id}`\n\n"
                    f"🛡️ Trusted users and roles in this server can now use organizer features for FREE.\n"
                    f"Server admins can manage trusted users/roles with:\n"
                    f"• `/addtrustedrole @RoleName`\n"
                    f"• `/addtrusteduser @Username`",
                    ephemeral=True
                )
                
        except Exception as e:
            self.logger.error(f"Error enabling trusted access: {e}")
            await interaction.followup.send(
                f"❌ Error enabling trusted access: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(
        name='disabletrustedaccess',
        description='[Owner] Disable trusted user/role access for a specific server'
    )
    @app_commands.describe(
        server_id="The Discord server ID to disable trusted access for"
    )
    @owner_only()
    async def disable_trusted_access(
        self,
        interaction: discord.Interaction,
        server_id: str
    ):
        """Disable trusted access for a specific server (bot owner only)."""
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            guild_id = int(server_id)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid server ID. Please provide a numeric server ID.",
                ephemeral=True
            )
            return
        
        # Check if bot is in this server
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await interaction.followup.send(
                f"⚠️ Bot is not in server with ID `{guild_id}`, but I'll disable it anyway.",
                ephemeral=True
            )
        
        # Disable trusted access for this server
        try:
            with DatabaseSession() as session:
                server_perms = session.query(ServerFeaturePermissions).filter_by(
                    guild_id=guild_id
                ).first()
                
                if not server_perms:
                    # Create new permissions record with trusted access disabled
                    server_perms = ServerFeaturePermissions(
                        guild_id=guild_id,
                        trusted_access_enabled=False,
                        configured_by=interaction.user.id
                    )
                    session.add(server_perms)
                else:
                    # Update existing record
                    server_perms.trusted_access_enabled = False
                    server_perms.configured_by = interaction.user.id
                    server_perms.updated_at = get_est_time()
                
                session.commit()
                
                server_name = guild.name if guild else f"Server {guild_id}"
                self.logger.info(f"✅ Disabled trusted access for {server_name} ({guild_id})")
                
                await interaction.followup.send(
                    f"✅ **Trusted Access DISABLED**\n\n"
                    f"**Server:** {server_name}\n"
                    f"**Server ID:** `{guild_id}`\n\n"
                    f"🔒 Trusted users and roles in this server will NO LONGER have free access.\n"
                    f"They will no longer have free organizer access.\n\n"
                    f"**Note:** Trusted user/role records are preserved and can be re-enabled.",
                    ephemeral=True
                )
                
        except Exception as e:
            self.logger.error(f"Error disabling trusted access: {e}")
            await interaction.followup.send(
                f"❌ Error disabling trusted access: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(
        name='listtrustedservers',
        description='[Owner] List all servers with trusted access enabled'
    )
    @owner_only()
    async def list_trusted_servers(self, interaction: discord.Interaction):
        """List all servers with trusted access enabled (bot owner only)."""
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            with DatabaseSession() as session:
                enabled_servers = session.query(ServerFeaturePermissions).filter_by(
                    trusted_access_enabled=True
                ).all()
                
                if not enabled_servers:
                    await interaction.followup.send(
                        "📋 **No servers have trusted access enabled.**\n\n"
                        "Use `/enabletrustedaccess <server_id>` to enable it for a server.",
                        ephemeral=True
                    )
                    return
                
                embed = discord.Embed(
                    title="🛡️ Servers with Trusted Access Enabled",
                    description=f"**{len(enabled_servers)}** server(s) allow free trusted access",
                    color=0x5865F2,
                    timestamp=discord.utils.utcnow()
                )
                
                for server_perm in enabled_servers[:25]:  # Discord embed limit
                    guild = self.bot.get_guild(server_perm.guild_id)
                    guild_name = guild.name if guild else f"Unknown ({server_perm.guild_id})"
                    
                    embed.add_field(
                        name=f"🏠 {guild_name}",
                        value=f"ID: `{server_perm.guild_id}`",
                        inline=False
                    )
                
                if len(enabled_servers) > 25:
                    embed.set_footer(text=f"Showing first 25 of {len(enabled_servers)} servers")
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
        except Exception as e:
            self.logger.error(f"Error listing trusted servers: {e}")
            await interaction.followup.send(
                f"❌ Error listing servers: {str(e)}",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(TrustedAccessControl(bot))
