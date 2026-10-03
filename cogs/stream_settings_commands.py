"""
Stream Settings Commands - Configure multi-platform streaming features
"""

import discord
from discord import app_commands
from discord.ext import commands
from database import get_db_session
from models import StreamPlatform, User
from utils.slash_permissions import admin_or_trusted
import logging

logger = logging.getLogger('discord_bot.stream_settings')

class StreamSettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    stream_group = app_commands.Group(name="stream", description="Multi-platform streaming settings")

    @stream_group.command(name="link", description="Link a YouTube or Kick account for notifications")
    @app_commands.describe(
        platform="Platform to link (youtube or kick)",
        platform_username="Your username on the platform",
        platform_user_id="Your user/channel ID (YouTube channel ID required)",
        notification_channel="Channel for notifications"
    )
    @admin_or_trusted()
    async def link_platform(
        self,
        interaction: discord.Interaction,
        platform: str,
        platform_username: str,
        platform_user_id: str,
        notification_channel: discord.TextChannel
    ):
        await interaction.response.defer(ephemeral=True)
        
        if platform.lower() not in ['youtube', 'kick', 'twitch']:
            await interaction.followup.send("❌ Platform must be: youtube, kick, or twitch", ephemeral=True)
            return
        
        session = get_db_session()
        try:
            user = session.query(User).filter_by(
                id=interaction.user.id,
                guild_id=interaction.guild_id
            ).first()
            
            if not user:
                user = User(
                    id=interaction.user.id,
                    username=str(interaction.user),
                    guild_id=interaction.guild_id
                )
                session.add(user)
                session.commit()
            
            existing = session.query(StreamPlatform).filter_by(
                user_id=user.id,
                guild_id=interaction.guild_id,
                platform=platform.lower()
            ).first()
            
            if existing:
                existing.platform_username = platform_username
                existing.platform_user_id = platform_user_id
                existing.notification_channel_id = notification_channel.id
                await interaction.followup.send(
                    f"✅ Updated {platform.capitalize()} link!\n"
                    f"📺 Username: {platform_username}\n"
                    f"📢 Notifications: {notification_channel.mention}",
                    ephemeral=True
                )
            else:
                platform_entry = StreamPlatform(
                    user_id=user.id,
                    guild_id=interaction.guild_id,
                    platform=platform.lower(),
                    platform_user_id=platform_user_id,
                    platform_username=platform_username,
                    notification_channel_id=notification_channel.id,
                    notify_live=True
                )
                session.add(platform_entry)
                await interaction.followup.send(
                    f"✅ Linked {platform.capitalize()} account!\n"
                    f"📺 Username: {platform_username}\n"
                    f"📢 Notifications: {notification_channel.mention}\n"
                    f"💡 Use `/stream settings` to configure clips/VODs",
                    ephemeral=True
                )
            
            session.commit()
            logger.info(f"Linked {platform} for {interaction.user.name} in {interaction.guild.name}")
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error linking platform: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
        finally:
            session.close()

    @stream_group.command(name="settings", description="Configure notification settings for a platform")
    @app_commands.describe(
        platform="Platform to configure",
        notify_live="Send notifications when going live",
        notify_clips="Send notifications for new clips (Twitch only)",
        notify_vods="Send notifications for new VODs (Twitch only)"
    )
    @admin_or_trusted()
    async def configure_settings(
        self,
        interaction: discord.Interaction,
        platform: str,
        notify_live: bool = True,
        notify_clips: bool = False,
        notify_vods: bool = False
    ):
        await interaction.response.defer(ephemeral=True)
        
        session = get_db_session()
        try:
            platform_entry = session.query(StreamPlatform).filter_by(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                platform=platform.lower()
            ).first()
            
            if not platform_entry:
                await interaction.followup.send(
                    f"❌ You haven't linked a {platform} account yet. Use `/stream link` first!",
                    ephemeral=True
                )
                return
            
            platform_entry.notify_live = notify_live
            platform_entry.notify_clips = notify_clips if platform.lower() == 'twitch' else False
            platform_entry.notify_vods = notify_vods if platform.lower() == 'twitch' else False
            
            session.commit()
            
            settings_msg = f"✅ **{platform.capitalize()} Notification Settings Updated**\n"
            settings_msg += f"🔴 Live Notifications: {'✅ Enabled' if notify_live else '❌ Disabled'}\n"
            
            if platform.lower() == 'twitch':
                settings_msg += f"🎬 Clip Notifications: {'✅ Enabled' if notify_clips else '❌ Disabled'}\n"
                settings_msg += f"📹 VOD Notifications: {'✅ Enabled' if notify_vods else '❌ Disabled'}"
            
            await interaction.followup.send(settings_msg, ephemeral=True)
            logger.info(f"Updated {platform} settings for {interaction.user.name}")
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating settings: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
        finally:
            session.close()

    @stream_group.command(name="list", description="List all linked streaming platforms")
    @admin_or_trusted()
    async def list_platforms(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        session = get_db_session()
        try:
            platforms = session.query(StreamPlatform).filter_by(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id
            ).all()
            
            if not platforms:
                await interaction.followup.send(
                    "📭 You haven't linked any platforms yet.\n"
                    "💡 Use `/stream link` to get started!",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="📺 Your Linked Streaming Platforms",
                color=discord.Color.blue()
            )
            
            for platform in platforms:
                channel = self.bot.get_channel(platform.notification_channel_id)
                channel_name = channel.mention if channel else "Unknown"
                
                status_icon = "🟢" if platform.notify_live else "🔴"
                clips_icon = "🎬" if platform.notify_clips else "❌"
                vods_icon = "📹" if platform.notify_vods else "❌"
                
                value = f"**Username:** {platform.platform_username}\n"
                value += f"**Notifications:** {channel_name}\n"
                value += f"{status_icon} Live | {clips_icon} Clips | {vods_icon} VODs"
                
                embed.add_field(
                    name=f"{platform.platform.capitalize()}",
                    value=value,
                    inline=False
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error listing platforms: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
        finally:
            session.close()

    @stream_group.command(name="unlink", description="Unlink a streaming platform")
    @app_commands.describe(platform="Platform to unlink (youtube, kick, or twitch)")
    @admin_or_trusted()
    async def unlink_platform(self, interaction: discord.Interaction, platform: str):
        await interaction.response.defer(ephemeral=True)
        
        session = get_db_session()
        try:
            platform_entry = session.query(StreamPlatform).filter_by(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                platform=platform.lower()
            ).first()
            
            if not platform_entry:
                await interaction.followup.send(
                    f"❌ You don't have a linked {platform} account.",
                    ephemeral=True
                )
                return
            
            session.delete(platform_entry)
            session.commit()
            
            await interaction.followup.send(
                f"✅ Unlinked {platform.capitalize()} account!",
                ephemeral=True
            )
            logger.info(f"Unlinked {platform} for {interaction.user.name}")
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error unlinking platform: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
        finally:
            session.close()

    @stream_group.command(name="schedule", description="View Twitch stream schedule for a user")
    @app_commands.describe(user="Discord user to check schedule for (defaults to you)")
    async def view_schedule(self, interaction: discord.Interaction, user: discord.User = None):
        await interaction.response.defer()
        
        target_user = user or interaction.user
        
        session = get_db_session()
        try:
            user_db = session.query(User).filter_by(
                id=target_user.id,
                guild_id=interaction.guild_id
            ).first()
            
            if not user_db or not user_db.twitch_id:
                await interaction.followup.send(
                    f"❌ {target_user.mention} doesn't have a linked Twitch account.",
                    ephemeral=True
                )
                return
            
            if hasattr(self.bot, 'multi_platform_manager'):
                schedule = await self.bot.multi_platform_manager.get_twitch_schedule(user_db.twitch_id)
                
                if not schedule:
                    await interaction.followup.send(
                        f"📅 {target_user.mention} has no scheduled streams on Twitch.",
                        ephemeral=True
                    )
                    return
                
                embed = discord.Embed(
                    title=f"📅 {target_user.display_name}'s Twitch Schedule",
                    color=discord.Color.purple()
                )
                
                for segment in schedule[:5]:
                    start_time = segment.get('start_time', 'Unknown')
                    title = segment.get('title', 'Untitled Stream')
                    category = segment.get('category', {}).get('name', 'No Category')
                    
                    embed.add_field(
                        name=title,
                        value=f"🕐 {start_time}\n📂 {category}",
                        inline=False
                    )
                
                await interaction.followup.send(embed=embed)
                
            else:
                await interaction.followup.send(
                    "❌ Stream schedule feature is not available.",
                    ephemeral=True
                )
            
        except Exception as e:
            logger.error(f"Error viewing schedule: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
        finally:
            session.close()

async def setup(bot):
    await bot.add_cog(StreamSettingsCog(bot))
    logger.info("Stream settings commands loaded")
