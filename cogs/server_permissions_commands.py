#!/usr/bin/env python3
"""
Server Feature Permissions Commands
Allows bot owner to configure which features are enabled per server.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Optional
from utils.slash_permissions import owner_only

logger = logging.getLogger('discord_bot.server_permissions_commands')


class FeatureToggleView(discord.ui.View):
    """Interactive view for toggling server features."""
    
    def __init__(self, bot, guild_id: int, user_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.logger = logging.getLogger('discord_bot.feature_toggle_view')

    async def async_init(self):
        """Populate the toggle buttons. Must be awaited before the view is sent."""
        await self.update_buttons()
        return self

    async def get_current_permissions(self):
        """Get current permissions from database."""
        permissions_manager = self.bot.permissions_manager
        permissions = await permissions_manager.get_server_permissions(self.guild_id)
        
        if not permissions:
            # No permissions set: legacy features on, beta features off by default
            return {
                'trains': True,
                'twitch': True,
                'forwarding': True,
                'admin': True,
                'analytics': True,
                'general': True,
                'reminders': False,
                'waitlist': False,
                'stats': False,
                'self_signup': False
            }
        
        return {
            'trains': permissions.trains_enabled,
            'twitch': permissions.twitch_enabled,
            'forwarding': permissions.forwarding_enabled,
            'admin': permissions.admin_enabled,
            'analytics': permissions.analytics_enabled,
            'general': permissions.general_enabled,
            'reminders': bool(getattr(permissions, 'reminders_enabled', False)),
            'waitlist': bool(getattr(permissions, 'waitlist_enabled', False)),
            'stats': bool(getattr(permissions, 'stats_enabled', False)),
            'self_signup': bool(getattr(permissions, 'self_signup_enabled', False))
        }
    
    async def update_buttons(self):
        """Update button styles based on current state."""
        self.clear_items()
        current = await self.get_current_permissions()
        
        # Create buttons for each feature
        self.add_item(self.create_toggle_button('trains', '🚂 Trains', current['trains']))
        self.add_item(self.create_toggle_button('twitch', '📺 Twitch', current['twitch']))
        self.add_item(self.create_toggle_button('forwarding', '↪️ Forwarding', current['forwarding']))
        self.add_item(self.create_toggle_button('analytics', '📊 Analytics', current['analytics']))
        self.add_item(self.create_toggle_button('admin', '⚙️ Admin', current['admin']))
        self.add_item(self.create_toggle_button('general', '🌐 General', current['general']))
        # Beta features (default OFF)
        self.add_item(self.create_toggle_button('reminders', '⏰ Reminders', current['reminders']))
        self.add_item(self.create_toggle_button('waitlist', '📝 Waitlist', current['waitlist']))
        self.add_item(self.create_toggle_button('stats', '🏅 Stats', current['stats']))
        self.add_item(self.create_toggle_button('self_signup', '🙋 Self Signup', current['self_signup']))
    
    def create_toggle_button(self, feature: str, label: str, is_enabled: bool):
        """Create a toggle button for a feature."""
        button = discord.ui.Button(
            label=label,
            custom_id=f"toggle_{feature}",
            style=discord.ButtonStyle.success if is_enabled else discord.ButtonStyle.secondary,
            emoji="✅" if is_enabled else "❌"
        )
        
        async def button_callback(interaction: discord.Interaction):
            # Check if user is owner
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "❌ Only the bot owner can modify server features.",
                    ephemeral=True
                )
                return
            
            # Toggle the feature
            await self.toggle_feature(interaction, feature)
        
        button.callback = button_callback
        return button
    
    async def toggle_feature(self, interaction: discord.Interaction, feature: str):
        """Toggle a specific feature on/off."""
        try:
            # Get current state
            current = await self.get_current_permissions()
            
            # Toggle the feature
            current[feature] = not current[feature]
            
            # Update in database
            permissions_manager = self.bot.permissions_manager
            await permissions_manager.set_server_permissions(
                guild_id=self.guild_id,
                configured_by=interaction.user.id,
                trains=current['trains'],
                twitch=current['twitch'],
                forwarding=current['forwarding'],
                admin=current['admin'],
                analytics=current['analytics'],
                general=current['general'],
                reminders=current['reminders'],
                waitlist=current['waitlist'],
                stats=current['stats'],
                self_signup=current['self_signup']
            )
            
            # Update buttons
            await self.update_buttons()
            
            # Update the message with new button states
            embed = await self.create_embed(interaction.guild)
            await interaction.response.edit_message(embed=embed, view=self)
            
            feature_names = {
                'trains': 'Trains & Attendance',
                'twitch': 'Twitch Integration',
                'forwarding': 'Message Forwarding',
                'analytics': 'Analytics & Sheets',
                'admin': 'Admin Commands',
                'general': 'General Commands',
                'reminders': 'Train Reminders (beta)',
                'waitlist': 'Signup Waitlist (beta)',
                'stats': 'Stats & Leaderboard (beta)',
                'self_signup': 'Self Signup /jointrain (beta)'
            }
            
            status = "enabled" if current[feature] else "disabled"
            self.logger.info(
                f"{feature_names[feature]} {status} for guild {self.guild_id} "
                f"by {interaction.user.name}"
            )
            
        except Exception as e:
            self.logger.error(f"Error toggling feature {feature}: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Error toggling feature: {str(e)}",
                ephemeral=True
            )
    
    async def create_embed(self, guild):
        """Create status embed showing current permissions."""
        current = await self.get_current_permissions()
        
        embed = discord.Embed(
            title="⚙️ Server Feature Management",
            description=f"Click buttons below to enable/disable features for **{guild.name}**\n\n"
                       f"✅ = Enabled | ❌ = Disabled",
            color=discord.Color.blue()
        )
        
        features = {
            "🚂 Trains & Attendance": current['trains'],
            "📺 Twitch Integration": current['twitch'],
            "↪️ Message Forwarding": current['forwarding'],
            "📊 Analytics & Sheets": current['analytics'],
            "⚙️ Admin Commands": current['admin'],
            "🌐 General Commands": current['general']
        }
        
        status_list = []
        for feature, enabled in features.items():
            status = "✅ Enabled" if enabled else "❌ Disabled"
            status_list.append(f"{feature}: **{status}**")
        
        embed.add_field(
            name="Current Status",
            value="\n".join(status_list),
            inline=False
        )
        
        beta_features = {
            "⏰ Train Reminders": current['reminders'],
            "📝 Signup Waitlist": current['waitlist'],
            "🏅 Stats & Leaderboard": current['stats'],
            "🙋 Self Signup (/jointrain)": current['self_signup']
        }
        
        beta_list = []
        for feature, enabled in beta_features.items():
            status = "✅ Enabled" if enabled else "❌ Disabled"
            beta_list.append(f"{feature}: **{status}**")
        
        embed.add_field(
            name="🧪 Beta Features (off by default)",
            value="\n".join(beta_list),
            inline=False
        )
        
        embed.set_footer(text="Changes take effect immediately • Buttons expire after 5 minutes")
        
        return embed


class ServerPermissionsCommands(commands.Cog):
    """Commands for managing server feature permissions."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.server_permissions_commands')
    
    @app_commands.command(name="managefeatures", description="[Owner] Interactive feature management with toggle buttons")
    @owner_only()
    async def managefeatures(self, interaction: discord.Interaction):
        """Open interactive feature management interface with toggle buttons."""
        
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Create interactive view
            view = await FeatureToggleView(self.bot, interaction.guild.id, interaction.user.id).async_init()
            embed = await view.create_embed(interaction.guild)
            
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True
            )
            
            self.logger.info(
                f"Feature management opened for {interaction.guild.name} ({interaction.guild.id}) "
                f"by {interaction.user.name}"
            )
            
        except Exception as e:
            self.logger.error(f"Error opening feature management: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error opening feature management: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(name="setserverfeatures", description="[Owner] Configure which features are enabled in this server")
    @app_commands.describe(
        trains="Enable train scheduling and attendance features",
        twitch="Enable Twitch integration and live role features",
        forwarding="Enable message forwarding features",
        admin="Enable admin and configuration commands",
        analytics="Enable analytics and Google Sheets sync",
        general="Enable general commands (ping, info, help, dashboard)",
        reminders="[Beta] Enable per-user opt-in train reminder DMs",
        waitlist="[Beta] Enable signup waitlist with DM seat offers",
        stats="[Beta] Enable participant stats / leaderboard",
        notes="Optional notes about why these settings were applied"
    )
    @owner_only()
    async def setserverfeatures(
        self,
        interaction: discord.Interaction,
        trains: bool = True,
        twitch: bool = True,
        forwarding: bool = True,
        admin: bool = True,
        analytics: bool = True,
        general: bool = True,
        reminders: bool = False,
        waitlist: bool = False,
        stats: bool = False,
        notes: Optional[str] = None
    ):
        """Configure which feature categories are enabled in this server."""
        
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get permissions manager
            permissions_manager = self.bot.permissions_manager
            
            # Set permissions
            permissions = await permissions_manager.set_server_permissions(
                guild_id=interaction.guild.id,
                configured_by=interaction.user.id,
                trains=trains,
                twitch=twitch,
                forwarding=forwarding,
                admin=admin,
                analytics=analytics,
                general=general,
                reminders=reminders,
                waitlist=waitlist,
                stats=stats,
                notes=notes
            )
            
            # Build response
            embed = discord.Embed(
                title="✅ Server Features Configured",
                description=f"Feature permissions updated for **{interaction.guild.name}**",
                color=discord.Color.green()
            )
            
            # Add feature status
            features = {
                "🚂 Trains & Attendance": trains,
                "📺 Twitch Integration": twitch,
                "↪️ Message Forwarding": forwarding,
                "⚙️ Admin Commands": admin,
                "📊 Analytics & Sheets": analytics,
                "🌐 General Commands": general,
                "⏰ Train Reminders (beta)": reminders,
                "📝 Signup Waitlist (beta)": waitlist,
                "🏅 Stats & Leaderboard (beta)": stats
            }
            
            enabled = []
            disabled = []
            for feature, status in features.items():
                if status:
                    enabled.append(feature)
                else:
                    disabled.append(feature)
            
            if enabled:
                embed.add_field(
                    name="✅ Enabled Features",
                    value="\n".join(enabled),
                    inline=False
                )
            
            if disabled:
                embed.add_field(
                    name="❌ Disabled Features",
                    value="\n".join(disabled),
                    inline=False
                )
            
            if notes:
                embed.add_field(name="📝 Notes", value=notes, inline=False)
            
            embed.set_footer(text=f"Configured by {interaction.user.name}")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            self.logger.info(
                f"Server features configured for {interaction.guild.name} ({interaction.guild.id}) "
                f"by {interaction.user.name}"
            )
            
        except Exception as e:
            self.logger.error(f"Error setting server features: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error configuring server features: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(name="viewserverfeatures", description="View which features are enabled in this server")
    async def viewserverfeatures(self, interaction: discord.Interaction):
        """View feature permissions for this server."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        try:
            permissions_manager = self.bot.permissions_manager
            permissions = await permissions_manager.get_server_permissions(interaction.guild.id)
            
            embed = discord.Embed(
                title="📋 Server Feature Status",
                description=f"Feature permissions for **{interaction.guild.name}**",
                color=discord.Color.blue()
            )
            
            if not permissions:
                embed.add_field(
                    name="Status",
                    value="✅ All features enabled (no restrictions set)",
                    inline=False
                )
            else:
                # Add feature status
                features = {
                    "🚂 Trains & Attendance": permissions.trains_enabled,
                    "📺 Twitch Integration": permissions.twitch_enabled,
                    "↪️ Message Forwarding": permissions.forwarding_enabled,
                    "⚙️ Admin Commands": permissions.admin_enabled,
                    "📊 Analytics & Sheets": permissions.analytics_enabled,
                    "🌐 General Commands": permissions.general_enabled
                }
                
                status_list = []
                for feature, enabled in features.items():
                    status = "✅ Enabled" if enabled else "❌ Disabled"
                    status_list.append(f"{feature}: {status}")
                
                embed.add_field(
                    name="Feature Status",
                    value="\n".join(status_list),
                    inline=False
                )
                
                beta_features = {
                    "⏰ Train Reminders": bool(getattr(permissions, 'reminders_enabled', False)),
                    "📝 Signup Waitlist": bool(getattr(permissions, 'waitlist_enabled', False)),
                    "🏅 Stats & Leaderboard": bool(getattr(permissions, 'stats_enabled', False)),
                    "🙋 Self Signup (/jointrain)": bool(getattr(permissions, 'self_signup_enabled', False))
                }
                
                beta_list = []
                for feature, enabled in beta_features.items():
                    status = "✅ Enabled" if enabled else "❌ Disabled"
                    beta_list.append(f"{feature}: {status}")
                
                embed.add_field(
                    name="🧪 Beta Features (off by default)",
                    value="\n".join(beta_list),
                    inline=False
                )
                
                if permissions.notes:
                    embed.add_field(name="📝 Notes", value=permissions.notes, inline=False)
                
                embed.set_footer(
                    text=f"Last updated: {permissions.updated_at.strftime('%Y-%m-%d %H:%M')} UK"
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            self.logger.error(f"Error viewing server features: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error viewing server features: {str(e)}",
                ephemeral=True
            )
    
    @app_commands.command(name="resetserverfeatures", description="[Owner] Reset server to default (all features enabled)")
    @owner_only()
    async def resetserverfeatures(self, interaction: discord.Interaction):
        """Reset server permissions to default (all features enabled)."""
        
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True
            )
            return
        
        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        try:
            permissions_manager = self.bot.permissions_manager
            await permissions_manager.reset_server_permissions(interaction.guild.id)
            
            embed = discord.Embed(
                title="✅ Server Features Reset",
                description=f"All features have been enabled for **{interaction.guild.name}**",
                color=discord.Color.green()
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            self.logger.info(
                f"Server features reset for {interaction.guild.name} ({interaction.guild.id}) "
                f"by {interaction.user.name}"
            )
            
        except Exception as e:
            self.logger.error(f"Error resetting server features: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Error resetting server features: {str(e)}",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(ServerPermissionsCommands(bot))
