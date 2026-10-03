#!/usr/bin/env python3
"""
Trusted Role Management Commands (Slash Commands)
Allows server administrators to designate roles as "trusted" for organizer-level permissions.
Members with these roles get organizer-level permissions in that specific server.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime
from database import DatabaseSession
from models import TrustedRole

logger = logging.getLogger('discord_bot.trusted_role_commands')


class TrustedRoleCommands(commands.Cog):
    """Slash commands for managing server-specific trusted roles."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.trusted_role_commands')
    
    @app_commands.command(name='addtrustedrole', description='[Admin] Grant a role trusted organizer permissions')
    @app_commands.describe(
        role="The role to grant trusted permissions",
        notes="Optional notes about why this role is trusted"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def add_trusted_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        notes: str = None
    ):
        """Add a role to the trusted roles list - members get organizer permissions in this server."""
        
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        try:
            with DatabaseSession() as session:
                # Check if role is already trusted
                existing = session.query(TrustedRole).filter(
                    TrustedRole.guild_id == interaction.guild.id,
                    TrustedRole.role_id == role.id,
                    TrustedRole.is_active == True
                ).first()
                
                if existing:
                    await interaction.followup.send(
                        f"❌ {role.mention} is already a trusted role in this server.",
                        ephemeral=True
                    )
                    return
                
                # Add to trusted roles
                trusted_role = TrustedRole(
                    guild_id=interaction.guild.id,
                    role_id=role.id,
                    role_name=role.name,
                    granted_by=interaction.user.id,
                    granted_at=datetime.utcnow(),
                    is_active=True,
                    notes=notes if notes else f"Trusted by {interaction.user.name} via /addtrustedrole"
                )
                
                session.add(trusted_role)
                session.commit()
            
            # Build success embed
            embed = discord.Embed(
                title="✅ Trusted Role Added",
                description=f"Role {role.mention} has been granted trusted organizer permissions!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🎯 Role",
                value=f"{role.mention}\n**Name:** {role.name}\n**ID:** {role.id}",
                inline=True
            )
            
            embed.add_field(
                name="👥 Member Count",
                value=f"{len(role.members)} members",
                inline=True
            )
            
            embed.add_field(
                name="🔓 Permissions Granted",
                value=(
                    "✅ Create & manage trains\n"
                    "✅ Access organizer features\n"
                    "✅ Works only in this server"
                ),
                inline=False
            )
            
            if notes:
                embed.add_field(
                    name="📝 Notes",
                    value=notes,
                    inline=False
                )
            
            embed.add_field(
                name="ℹ️ About Trusted Roles",
                value=(
                    "Members with this role can organize trains "
                    "in **this server only**. This is perfect for your staff, moderators, "
                    "or designated event organizers."
                ),
                inline=False
            )
            
            embed.set_footer(text=f"Added by {interaction.user.display_name}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            self.logger.info(
                f"Trusted role added: {role.name} ({role.id}) in {interaction.guild.name} "
                f"by {interaction.user.name}"
            )
            
        except Exception as e:
            self.logger.error(f"Error adding trusted role: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error adding trusted role: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(name='removetrustedrole', description='[Admin] Remove trusted permissions from a role')
    @app_commands.describe(
        role="The role to remove trusted permissions from"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_trusted_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role
    ):
        """Remove a role from the trusted roles list."""
        
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        try:
            with DatabaseSession() as session:
                trusted_role = session.query(TrustedRole).filter(
                    TrustedRole.guild_id == interaction.guild.id,
                    TrustedRole.role_id == role.id,
                    TrustedRole.is_active == True
                ).first()
                
                if not trusted_role:
                    await interaction.followup.send(
                        f"❌ {role.mention} is not a trusted role in this server.",
                        ephemeral=True
                    )
                    return
                
                # Deactivate the trusted role
                trusted_role.is_active = False
                trusted_role.updated_at = datetime.utcnow()
                session.commit()
            
            # Build success embed
            embed = discord.Embed(
                title="🚫 Trusted Role Removed",
                description=f"Trusted permissions have been revoked for {role.mention}",
                color=0xff0000,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🎯 Role",
                value=f"{role.mention}\n**Name:** {role.name}\n**ID:** {role.id}",
                inline=True
            )
            
            embed.add_field(
                name="👥 Affected Members",
                value=f"{len(role.members)} members",
                inline=True
            )
            
            embed.add_field(
                name="⚠️ Access Removed",
                value=(
                    "Members of this role will no longer have organizer permissions."
                ),
                inline=False
            )
            
            embed.set_footer(text=f"Removed by {interaction.user.display_name}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            self.logger.info(
                f"Trusted role removed: {role.name} ({role.id}) in {interaction.guild.name} "
                f"by {interaction.user.name}"
            )
            
        except Exception as e:
            self.logger.error(f"Error removing trusted role: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error removing trusted role: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(name='listtrustedroles', description='View all trusted roles in this server')
    async def list_trusted_roles(self, interaction: discord.Interaction):
        """List all trusted roles in the current server."""
        
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        try:
            with DatabaseSession() as session:
                trusted_roles = session.query(TrustedRole).filter(
                    TrustedRole.guild_id == interaction.guild.id,
                    TrustedRole.is_active == True
                ).order_by(TrustedRole.granted_at.desc()).all()
                
                # Extract data while in session to avoid detached instance errors
                role_data = []
                for trusted_role in trusted_roles:
                    # Try to get current role object
                    role_obj = interaction.guild.get_role(trusted_role.role_id)
                    
                    role_data.append({
                        'role_id': trusted_role.role_id,
                        'role_name': trusted_role.role_name,
                        'exists': role_obj is not None,
                        'member_count': len(role_obj.members) if role_obj else 0,
                        'granted_at': trusted_role.granted_at.strftime('%Y-%m-%d'),
                        'notes': trusted_role.notes
                    })
            
            if not role_data:
                embed = discord.Embed(
                    title="👥 Trusted Roles",
                    description=(
                        "No trusted roles configured in this server.\n\n"
                        "**What are trusted roles?**\n"
                        "Server administrators can designate roles as 'trusted' to give those members "
                        "organizer access for train management.\n\n"
                        "Use `/addtrustedrole @RoleName` to add one!"
                    ),
                    color=0xffa500
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Build embed with role list
            embed = discord.Embed(
                title="👥 Trusted Roles in This Server",
                description=(
                    f"Found **{len(role_data)}** trusted role(s) with organizer permissions.\n"
                    "Members of these roles can create and manage trains."
                ),
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            # Add each role as a field
            for i, data in enumerate(role_data, 1):
                role_obj = interaction.guild.get_role(data['role_id'])
                role_mention = role_obj.mention if role_obj else f"@{data['role_name']} (deleted)"
                
                status = "✅ Active" if data['exists'] else "⚠️ Role Deleted"
                
                field_value = [
                    f"**Status:** {status}",
                    f"**Members:** {data['member_count']}",
                    f"**Added:** {data['granted_at']}"
                ]
                
                if data['notes']:
                    field_value.append(f"**Notes:** {data['notes']}")
                
                embed.add_field(
                    name=f"{i}. {role_mention}",
                    value="\n".join(field_value),
                    inline=False
                )
            
            embed.add_field(
                name="🛡️ Server Administrators",
                value=(
                    "Users with the **Administrator** permission can add or remove "
                    "trusted roles using `/addtrustedrole` and `/removetrustedrole`"
                ),
                inline=False
            )
            
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            self.logger.error(f"Error listing trusted roles: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error listing trusted roles: {str(e)}",
                ephemeral=True
            )
    
    @add_trusted_role.error
    async def add_trusted_role_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle errors for add_trusted_role command."""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission to add trusted roles.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ An error occurred: {str(error)}",
                ephemeral=True
            )
    
    @remove_trusted_role.error
    async def remove_trusted_role_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle errors for remove_trusted_role command."""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission to remove trusted roles.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ An error occurred: {str(error)}",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(TrustedRoleCommands(bot))
