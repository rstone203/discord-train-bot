"""Chat bot settings commands for managing Twitch chat bot features per server."""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from database import DatabaseSession
from models import NotificationSettings
from utils.slash_permissions import admin_or_trusted

logger = logging.getLogger('discord_bot.chatbot_settings_commands')

class ChatBotSettingsCommands(commands.Cog):
    """Commands to manage Twitch chat bot settings."""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="chatbotsettings", description="Configure Twitch chat bot features for this server")
    @app_commands.describe(
        enabled="Enable or disable all Twitch chat bot features",
        auto_announcements="Send announcements when next rider goes live",
        one_hour_warnings="Send 1-hour warnings with live status check",
        ten_minute_warnings="Send 10-minute warnings before next rider"
    )
    @admin_or_trusted()
    async def chatbot_settings(
        self,
        interaction: discord.Interaction,
        enabled: bool = None,
        auto_announcements: bool = None,
        one_hour_warnings: bool = None,
        ten_minute_warnings: bool = None
    ):
        """Configure Twitch chat bot settings for this server."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            guild_id = interaction.guild.id
            
            with DatabaseSession() as session:
                # Get or create notification settings
                settings = session.query(NotificationSettings).filter_by(guild_id=guild_id).first()
                if not settings:
                    settings = NotificationSettings(guild_id=guild_id)
                    session.add(settings)
                
                # Apply updates
                changes = []
                if enabled is not None:
                    settings.twitch_chat_bot_enabled = enabled
                    changes.append(f"Chat bot {'enabled' if enabled else 'disabled'}")
                
                if auto_announcements is not None:
                    settings.auto_announcements_enabled = auto_announcements
                    changes.append(f"Auto-announcements {'enabled' if auto_announcements else 'disabled'}")
                
                if one_hour_warnings is not None:
                    settings.one_hour_warnings_enabled = one_hour_warnings
                    changes.append(f"1-hour warnings {'enabled' if one_hour_warnings else 'disabled'}")
                
                if ten_minute_warnings is not None:
                    settings.ten_minute_warnings_enabled = ten_minute_warnings
                    changes.append(f"10-minute warnings {'enabled' if ten_minute_warnings else 'disabled'}")
                
                session.commit()
                
                # Create response embed
                embed = discord.Embed(
                    title="🤖 Twitch Chat Bot Settings",
                    color=discord.Color.blue()
                )
                
                if changes:
                    embed.description = "✅ Settings updated successfully!"
                    embed.add_field(
                        name="Changes Made",
                        value="\n".join(f"• {change}" for change in changes),
                        inline=False
                    )
                
                # Show current settings
                embed.add_field(
                    name="Current Configuration",
                    value=(
                        f"**Chat Bot:** {'✅ Enabled' if settings.twitch_chat_bot_enabled else '❌ Disabled'}\n"
                        f"**Auto-Announcements:** {'✅ Enabled' if settings.auto_announcements_enabled else '❌ Disabled'}\n"
                        f"**1-Hour Warnings:** {'✅ Enabled' if settings.one_hour_warnings_enabled else '❌ Disabled'}\n"
                        f"**10-Min Warnings:** {'✅ Enabled' if settings.ten_minute_warnings_enabled else '❌ Disabled'}"
                    ),
                    inline=False
                )
                
                embed.add_field(
                    name="Features",
                    value=(
                        "🔔 **Auto-Announcements**: Notifies current riders in Twitch chat when the next person goes live\n"
                        "⏰ **1-Hour Warnings**: Sends 1-hour warning with live status (mentions both current and next rider)\n"
                        "⏰ **10-Min Warnings**: Sends reminder to current riders 10 minutes before next rider's start time\n"
                        "🎯 **!raidnext Command**: Moderators can type `!raidnext` in Twitch chat to raid the next person (moderator-only)"
                    ),
                    inline=False
                )
                
                embed.set_footer(text="These settings apply server-wide. The !raidnext command is always available when chat bot is enabled (moderators only).")
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                logger.info(f"Chat bot settings updated for guild {guild_id}: {changes}")
                
        except Exception as e:
            logger.error(f"Error updating chat bot settings: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Failed to update chat bot settings. Please try again.",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(ChatBotSettingsCommands(bot))
