"""
Weekly update scheduler for posting bot update summaries.
This cog manages automated weekly posting of bot updates to configured channels.
"""

import discord
from discord.ext import commands, tasks
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from database import DatabaseSession
from models import Guild, BotUpdate, UpdateChannelConfig, get_est_time
from utils.update_manager import UpdateManager

class WeeklyScheduler(commands.Cog):
    """Automated weekly update posting scheduler."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.weekly_scheduler')
        self.update_manager = UpdateManager()
        
        # Global scheduler enabled flag
        self.posting_enabled = True
        
        # Retry configuration
        self.max_retries = 3
        self.retry_delay = 300  # 5 minutes between retries
        
        self.logger.info("Weekly scheduler initialized")
    
    async def cog_load(self):
        """Start background tasks when the cog loads."""
        self.weekly_update_scheduler.start()
        self.logger.info("Weekly update scheduler started")
    
    async def cog_unload(self):
        """Stop background tasks when the cog unloads."""
        self.weekly_update_scheduler.cancel()
        self.logger.info("Weekly update scheduler stopped")
    
    @tasks.loop(minutes=30)  # Check every 30 minutes for precise timing
    async def weekly_update_scheduler(self):
        """Background task to post weekly update summaries."""
        try:
            if not self.posting_enabled:
                return
            
            # Check for maintenance mode first
            try:
                from models import SystemSettings
                with DatabaseSession() as session:
                    maintenance_setting = session.query(SystemSettings).filter_by(
                        setting_key='maintenance_mode'
                    ).first()
                    
                    if maintenance_setting and maintenance_setting.is_enabled:
                        # Skip all scheduler processing in maintenance mode
                        return
            except Exception as e:
                self.logger.debug(f"Maintenance mode check failed, proceeding normally: {e}")
            
            current_time = get_est_time()
            
            # Process per-guild schedules instead of global posting time
            await self.process_scheduled_guilds(current_time)
                
        except Exception as e:
            self.logger.error(f"❌ Error in weekly update scheduler: {e}", exc_info=True)
    
    @weekly_update_scheduler.error
    async def weekly_update_scheduler_error(self, error):
        """Handle errors in weekly_update_scheduler and restart it."""
        self.logger.error(f"⚠️ weekly_update_scheduler crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting weekly_update_scheduler in 60 seconds...")
        await asyncio.sleep(60)
        self.weekly_update_scheduler.restart()
    
    @weekly_update_scheduler.before_loop
    async def before_weekly_scheduler(self):
        """Wait for bot to be ready before starting scheduler."""
        await self.bot.wait_until_ready()
        self.logger.info("Weekly scheduler ready - waiting for next posting time")
    
    def should_post_for_config(self, current_time: datetime, config: UpdateChannelConfig) -> bool:
        """
        Check if it's time to post weekly updates for a specific guild configuration.
        
        Args:
            current_time (datetime): Current time in UK
            config (UpdateChannelConfig): Guild-specific update configuration
            
        Returns:
            bool: True if it's posting time for this guild, False otherwise
        """
        # Check if it's the right day of week for this guild
        if current_time.weekday() != config.posting_day:
            return False
        
        # Check if it's the right hour (within 30-minute window due to loop frequency)
        if current_time.hour != config.posting_hour:
            return False
        
        # Check if we're in the right minute range (to avoid duplicate posts)
        if current_time.minute < 0 or current_time.minute >= 30:
            return False
        
        # Check for duplicate posts - has this guild already been posted to today?
        if self.was_posted_today(config, current_time):
            return False
        
        return True
    
    def was_posted_today(self, config: UpdateChannelConfig, current_time: datetime) -> bool:
        """
        Check if updates were already posted today for this configuration.
        
        Args:
            config (UpdateChannelConfig): Guild configuration
            current_time (datetime): Current time in UK
            
        Returns:
            bool: True if already posted today, False otherwise
        """
        if not config.last_posted_at:
            return False
        
        # Check if last post was today
        today = current_time.date()
        last_post_date = config.last_posted_at.date()
        
        return today == last_post_date
    
    async def process_scheduled_guilds(self, current_time: datetime):
        """Process per-guild schedules and post updates for guilds that are due."""
        self.logger.info("Checking per-guild update schedules")
        
        try:
            # Check for Sunday updates notifications first
            if self.is_sunday_update_time(current_time):
                await self.send_sunday_updates_notifications(current_time)
            
            # Get all enabled update configurations
            configs = await self.get_enabled_update_configs()
            
            posted_guilds = []
            
            for config in configs:
                try:
                    # Check if it's time to post for this specific guild
                    if self.should_post_for_config(current_time, config):
                        self.logger.info(f"Processing scheduled updates for guild {config.guild_id}")
                        success = await self.post_guild_weekly_updates(config.guild_id, config)
                        if success:
                            posted_guilds.append(config.guild_id)
                            # Update last posted time
                            await self.update_last_posted_time(config.id, current_time)
                except Exception as e:
                    self.logger.error(f"Failed to process updates for guild {config.guild_id}: {e}")
                    # Continue with other guilds even if one fails
                    continue
            
            # Also post global updates to guilds that include them and are scheduled now
            if posted_guilds:
                await self.post_global_weekly_updates_to_scheduled(posted_guilds, current_time)
            
            if posted_guilds:
                self.logger.info(f"Completed weekly update processing for {len(posted_guilds)} guilds")
            
        except Exception as e:
            self.logger.error(f"Error processing scheduled guilds: {e}")
    
    def is_sunday_update_time(self, current_time: datetime) -> bool:
        """
        Check if it's Sunday at 6 PM UK - time to send updates notifications.
        
        Args:
            current_time (datetime): Current time in UK
            
        Returns:
            bool: True if it's Sunday at 6 PM UK
        """
        # Sunday is day 6 in Python's weekday (Monday=0)
        if current_time.weekday() != 6:  # Not Sunday
            return False
        
        # Check if it's 6 PM UK (18:00)
        if current_time.hour != 18:
            return False
        
        # Check if it's within the first 30 minutes of 6 PM (since we check every 30 minutes)
        if current_time.minute >= 30:
            return False
            
        return True
    
    async def send_sunday_updates_notifications(self, current_time: datetime):
        """Send updates notifications to all guilds with configured updates notification channels."""
        self.logger.info("Processing Sunday updates notifications")
        
        try:
            from models import NotificationSettings, TrainSchedule, ForwardingConfig
            
            with DatabaseSession() as session:
                # Get all notification settings with updates channels configured
                settings_list = session.query(NotificationSettings).filter(
                    NotificationSettings.updates_notification_channel_id.isnot(None)
                ).all()
                
                if not settings_list:
                    self.logger.info("No guilds have updates notification channels configured")
                    return
                
                sent_count = 0
                today = current_time.strftime("%B %d, %Y")
                
                for settings in settings_list:
                    try:
                        # Get the guild and channel
                        guild = self.bot.get_guild(settings.guild_id)
                        if not guild:
                            self.logger.warning(f"Guild {settings.guild_id} not found")
                            continue
                        
                        channel = guild.get_channel(settings.updates_notification_channel_id)
                        if not channel:
                            self.logger.warning(f"Updates channel {settings.updates_notification_channel_id} not found in guild {guild.name}")
                            continue
                        
                        # Get system statistics
                        active_schedules = session.query(TrainSchedule).filter_by(is_active=True).count()
                        forwarding_configs = session.query(ForwardingConfig).filter_by(is_active=True).count()
                        
                        # Create the updates embed
                        embed = discord.Embed(
                            title=f"🚂 {guild.name} Bot Updates - Weekly Summary",
                            description=f"**Weekly Update** - Recent improvements and system status (as of {today})",
                            color=0x667eea
                        )
                        
                        # Add recent improvements info
                        embed.add_field(
                            name="🔧 Recent System Improvements",
                            value=(
                                "• **Fixed critical notification system** - Train notifications now work reliably\n"
                                "• **Enhanced message forwarding** - Added source server/channel information\n"
                                "• **Improved error handling** - Better stability and crash prevention\n"
                                "• **Database optimization** - Enhanced duplicate prevention and logging\n"
                                "• **Updates notifications** - Added configurable update notifications ✨\n"
                                "• **Automatic weekly updates** - Sends updates every Sunday automatically"
                            ),
                            inline=False
                        )
                        
                        # Enhanced current system status
                        uptime = getattr(self.bot, 'start_time', None)
                        uptime_str = ""
                        if uptime:
                            delta = current_time - uptime
                            days = delta.days
                            hours = delta.seconds // 3600
                            uptime_str = f"\n• **Uptime:** {days}d {hours}h"
                        
                        status_info = (
                            f"• **Servers:** {len(self.bot.guilds)} Discord servers connected\n"
                            f"• **Commands:** {len([cmd for cmd in self.bot.tree.get_commands()])} slash commands available\n"
                            f"• **Train Schedules:** {active_schedules} active training schedules\n"
                            f"• **Message Forwarding:** {forwarding_configs} active forwarding rules\n"
                            f"• **Notification System:** ✅ Fixed and running reliably{uptime_str}"
                        )
                        embed.add_field(
                            name="📈 Current System Status",
                            value=status_info,
                            inline=False
                        )
                        
                        # Add feature highlights
                        features_info = (
                            "• **Train Management** - Schedule and manage train sessions with notifications\n"
                            "• **Message Forwarding** - Cross-server message forwarding with source info\n"
                            "• **Web Dashboard** - Live UI for monitoring and management\n"
                            "• **Database Integration** - PostgreSQL with comprehensive logging\n"
                            "• **Admin Tools** - Permission system and trusted user management"
                        )
                        embed.add_field(
                            name="🚀 Key Features",
                            value=features_info,
                            inline=False
                        )
                        
                        # Update footer
                        embed.set_footer(text="📅 Weekly Update • Use !dashboard or /dashboard to access the web interface")
                        
                        # Get role to ping if configured
                        ping_role = guild.get_role(settings.updates_ping_role_id) if settings.updates_ping_role_id else None
                        
                        # Build message content
                        message_content = ""
                        if ping_role and ping_role.mentionable:
                            message_content = f"📢 **Weekly Bot Update:** {ping_role.mention}"
                        
                        # Send the notification
                        try:
                            if message_content:
                                await channel.send(content=message_content, embed=embed)
                            else:
                                await channel.send(embed=embed)
                            
                            sent_count += 1
                            self.logger.info(f"Sent Sunday updates notification to {guild.name} (#{channel.name})")
                            
                        except discord.errors.Forbidden:
                            self.logger.error(f"No permission to send updates to {guild.name} (#{channel.name})")
                        except discord.errors.NotFound:
                            self.logger.error(f"Channel {channel.name} no longer exists in {guild.name}")
                        except Exception as send_error:
                            self.logger.error(f"Error sending updates to {guild.name}: {send_error}")
                    
                    except Exception as guild_error:
                        self.logger.error(f"Error processing updates for guild {settings.guild_id}: {guild_error}")
                        continue
                
                self.logger.info(f"Completed Sunday updates notifications - sent to {sent_count}/{len(settings_list)} guilds")
                
        except Exception as e:
            self.logger.error(f"Error sending Sunday updates notifications: {e}")
    
    async def get_enabled_update_configs(self) -> List[UpdateChannelConfig]:
        """Get all enabled update configurations from the database."""
        try:
            with DatabaseSession() as session:
                configs = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.is_enabled == True
                ).all()
                return configs
        except Exception as e:
            self.logger.error(f"Error getting enabled update configs: {e}")
            return []
    
    async def update_last_posted_time(self, config_id: int, posted_time: datetime):
        """Update the last_posted_at time for a specific configuration."""
        try:
            with DatabaseSession() as session:
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.id == config_id
                ).first()
                
                if config:
                    config.last_posted_at = posted_time
                    config.updated_at = get_est_time()
                    session.commit()
                    self.logger.debug(f"Updated last_posted_at for config {config_id}")
                else:
                    self.logger.warning(f"Config {config_id} not found for update")
                    
        except Exception as e:
            self.logger.error(f"Error updating last posted time for config {config_id}: {e}")
    
    async def post_global_weekly_updates_to_scheduled(self, scheduled_guild_ids: List[int], current_time: datetime):
        """Post global weekly updates only to guilds that are scheduled now and include global updates."""
        try:
            # Check if there are global updates
            has_updates = await self.update_manager.has_new_updates_this_week(guild_id=None)
            
            if not has_updates:
                self.logger.info("No new global updates")
                return
            
            # Get global updates
            updates = await self.update_manager.get_updates_for_weekly_post(guild_id=None)
            
            if not updates:
                self.logger.info("No global updates to post")
                return
            
            # Format the weekly summary
            embed = await self.update_manager.format_weekly_summary_embed(updates)
            
            # Get configurations for scheduled guilds that include global updates
            try:
                with DatabaseSession() as session:
                    configs = session.query(UpdateChannelConfig).filter(
                        UpdateChannelConfig.guild_id.in_(scheduled_guild_ids),
                        UpdateChannelConfig.include_global_updates == True,
                        UpdateChannelConfig.is_enabled == True
                    ).all()
            except Exception as e:
                self.logger.error(f"Error getting configs for global updates: {e}")
                return
            
            success_count = 0
            total_configs = len(configs)
            
            for config in configs:
                try:
                    channel = self.bot.get_channel(config.update_channel_id)
                    if channel:
                        # Add role mention if configured
                        content = None
                        if config.notify_role_id:
                            role = channel.guild.get_role(config.notify_role_id)
                            if role and role.mentionable:
                                content = role.mention
                        
                        success = await self.post_summary_with_retry(channel, embed, content=content, config_id=config.id)
                        if success:
                            success_count += 1
                            # Update last global posted time for this config
                            await self.update_last_global_posted_time(config.id, current_time)
                        else:
                            self.logger.error(f"Failed to post global updates to guild {config.guild_id}")
                    else:
                        self.logger.warning(f"Channel {config.update_channel_id} not found for guild {config.guild_id}")
                except Exception as e:
                    self.logger.error(f"Error posting global updates to guild {config.guild_id}: {e}")
            
            # Note: We don't mark global updates as globally posted here since we track per-destination
            # Each successful destination already updated its last_global_posted_at timestamp
            # This prevents duplicate posting while allowing retries for failed destinations
            
            if success_count > 0:
                self.logger.info(f"Successfully posted global updates to {success_count}/{total_configs} scheduled guilds")
                
                # Optional: Only mark as globally posted if ALL destinations succeeded
                if success_count == total_configs:
                    update_ids = [int(update.id) for update in updates]
                    await self.update_manager.mark_updates_as_weekly_posted(update_ids)
                    self.logger.info("Marked global updates as posted (all destinations succeeded)")
                else:
                    self.logger.info(f"Global updates partially delivered ({success_count}/{total_configs}). Will retry failed destinations next cycle.")
            else:
                self.logger.error("Failed to post global updates to any scheduled guilds")
                
        except Exception as e:
            self.logger.error(f"Error posting global weekly updates to scheduled guilds: {e}")
    
    async def update_last_global_posted_time(self, config_id: int, posted_time: datetime):
        """Update the last_global_posted_at time for a specific configuration."""
        try:
            with DatabaseSession() as session:
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.id == config_id
                ).first()
                
                if config:
                    config.last_global_posted_at = posted_time
                    config.updated_at = get_est_time()
                    session.commit()
                    self.logger.debug(f"Updated last_global_posted_at for config {config_id}")
                else:
                    self.logger.warning(f"Config {config_id} not found for global update")
                    
        except Exception as e:
            self.logger.error(f"Error updating last global posted time for config {config_id}: {e}")
    
    async def post_guild_weekly_updates(self, guild_id: int, config: Optional[UpdateChannelConfig] = None) -> bool:
        """
        Post weekly updates for a specific guild.
        
        Args:
            guild_id (int): Discord guild ID
            config (Optional[UpdateChannelConfig]): Guild configuration (if available)
            
        Returns:
            bool: True if posting was successful, False otherwise
        """
        try:
            # Check if there are new updates for this guild
            has_updates = await self.update_manager.has_new_updates_this_week(guild_id=guild_id)
            
            if not has_updates:
                self.logger.info(f"No new updates for guild {guild_id}")
                return True  # No updates to post is still success
            
            # Use provided config or get update channel for this guild
            if config:
                update_channel = self.bot.get_channel(config.update_channel_id)
                config_id = config.id
            else:
                update_channel = await self.get_update_channel_for_guild(guild_id)
                config_id = None
            
            if not update_channel:
                self.logger.warning(f"No update channel configured for guild {guild_id}")
                return False  # Unable to post
            
            # Get updates to post
            updates = await self.update_manager.get_updates_for_weekly_post(guild_id=guild_id)
            
            if not updates:
                self.logger.info(f"No updates to post for guild {guild_id}")
                return True  # No updates to post is still success
            
            # Format the weekly summary
            embed = await self.update_manager.format_weekly_summary_embed(updates)
            
            # Add role mention if configured
            content = None
            if config and config.notify_role_id:
                role = update_channel.guild.get_role(config.notify_role_id)
                if role and role.mentionable:
                    content = role.mention
            
            # Post the summary
            success = await self.post_summary_with_retry(update_channel, embed, content=content, config_id=config_id)
            
            if success:
                # Mark updates as posted
                update_ids = [int(update.id) for update in updates]
                await self.update_manager.mark_updates_as_weekly_posted(update_ids)
                
                self.logger.info(f"Successfully posted {len(updates)} updates to guild {guild_id}")
                return True
            else:
                self.logger.error(f"Failed to post updates to guild {guild_id} after retries")
                return False
                
        except Exception as e:
            self.logger.error(f"Error posting guild weekly updates for {guild_id}: {e}")
            return False
    
    async def post_global_weekly_updates(self):
        """Post global weekly updates to all configured channels that include global updates."""
        try:
            # Check if there are global updates
            has_updates = await self.update_manager.has_new_updates_this_week(guild_id=None)
            
            if not has_updates:
                self.logger.info("No new global updates")
                return
            
            # Get global updates
            updates = await self.update_manager.get_updates_for_weekly_post(guild_id=None)
            
            if not updates:
                self.logger.info("No global updates to post")
                return
            
            # Format the weekly summary
            embed = await self.update_manager.format_weekly_summary_embed(updates)
            
            # Get all configurations that include global updates
            configured_channels = await self.get_global_update_channels()
            
            success_count = 0
            total_channels = len(configured_channels)
            
            for channel_config in configured_channels:
                try:
                    channel = self.bot.get_channel(channel_config['channel_id'])
                    if channel:
                        # Add role mention if configured
                        content = None
                        if channel_config['role_id']:
                            role = channel.guild.get_role(channel_config['role_id'])
                            if role and role.mentionable:
                                content = role.mention
                        
                        success = await self.post_summary_with_retry(channel, embed, content=content)
                        if success:
                            success_count += 1
                        else:
                            self.logger.error(f"Failed to post global updates to channel {channel_config['channel_id']}")
                    else:
                        self.logger.warning(f"Channel {channel_config['channel_id']} not found")
                except Exception as e:
                    self.logger.error(f"Error posting to channel {channel_config['channel_id']}: {e}")
            
            # Legacy method: Mark as posted only if ALL destinations succeed
            if success_count > 0:
                if success_count == total_channels:
                    # Only mark as posted if ALL destinations succeeded
                    update_ids = [int(update.id) for update in updates]
                    await self.update_manager.mark_updates_as_weekly_posted(update_ids)
                    self.logger.info(f"Successfully posted global updates to all {total_channels} channels - marked as posted")
                else:
                    self.logger.warning(f"Global updates only delivered to {success_count}/{total_channels} channels. NOT marking as posted to allow retries.")
            else:
                self.logger.error("Failed to post global updates to any channels")
                
        except Exception as e:
            self.logger.error(f"Error posting global weekly updates: {e}")
    
    async def post_summary_with_retry(self, channel: discord.TextChannel, embed: discord.Embed, content: Optional[str] = None, config_id: Optional[int] = None) -> bool:
        """
        Post summary to channel with enhanced retry logic and error handling.
        
        Args:
            channel (discord.TextChannel): Channel to post to
            embed (discord.Embed): Embed to post
            content (str, optional): Additional content to post (e.g., role mention)
            config_id (int, optional): Configuration ID for auto-disabling on permanent failures
            
        Returns:
            bool: True if successful, False otherwise
        """
        for attempt in range(self.max_retries):
            try:
                await channel.send(content=content, embed=embed)
                self.logger.debug(f"Successfully posted to channel {channel.id} in guild {channel.guild.id}")
                return True
                
            except discord.Forbidden as e:
                self.logger.error(f"No permission to post in channel {channel.id} (guild: {channel.guild.id}): {e}")
                # Auto-disable configuration for permanent permission issues
                if config_id:
                    await self.auto_disable_config(config_id, f"Permission denied: {e}")
                return False
                
            except discord.NotFound as e:
                self.logger.error(f"Channel {channel.id} not found (likely deleted): {e}")
                # Auto-disable configuration for deleted channels
                if config_id:
                    await self.auto_disable_config(config_id, f"Channel not found: {e}")
                return False
                
            except discord.HTTPException as e:
                # Handle rate limits with exponential backoff
                if e.status == 429:  # Rate limited
                    retry_after = getattr(e, 'retry_after', self.retry_delay)
                    self.logger.warning(f"Rate limited posting to channel {channel.id}, retrying after {retry_after}s (attempt {attempt + 1})")
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(retry_after)
                elif e.status >= 500:  # Server errors - retry with exponential backoff
                    backoff_delay = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    self.logger.warning(f"Server error ({e.status}) posting to channel {channel.id}, retrying after {backoff_delay}s (attempt {attempt + 1}): {e}")
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(backoff_delay)
                else:
                    # Client errors (4xx) - don't retry
                    self.logger.error(f"Client error ({e.status}) posting to channel {channel.id}: {e}")
                    if config_id and e.status in [400, 403, 404]:
                        await self.auto_disable_config(config_id, f"Client error {e.status}: {e}")
                    return False
                    
            except Exception as e:
                self.logger.error(f"Unexpected error posting to channel {channel.id} (attempt {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    backoff_delay = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    await asyncio.sleep(backoff_delay)
        
        # All retries exhausted
        self.logger.error(f"Failed to post to channel {channel.id} after {self.max_retries} attempts")
        return False
    
    async def auto_disable_config(self, config_id: int, reason: str):
        """Auto-disable a configuration due to permanent failures."""
        try:
            with DatabaseSession() as session:
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.id == config_id
                ).first()
                
                if config:
                    config.is_enabled = False
                    config.updated_at = get_est_time()
                    session.commit()
                    
                    self.logger.warning(f"Auto-disabled update config {config_id} for guild {config.guild_id}: {reason}")
                    
                    # Optionally, could notify admins here about the auto-disable
                    
                else:
                    self.logger.warning(f"Config {config_id} not found for auto-disable")
                    
        except Exception as e:
            self.logger.error(f"Error auto-disabling config {config_id}: {e}")
    
    async def get_active_guilds(self) -> List[Guild]:
        """Get all active guilds from the database."""
        try:
            with DatabaseSession() as session:
                guilds = session.query(Guild).filter(Guild.is_active == True).all()
                return guilds
        except Exception as e:
            self.logger.error(f"Error getting active guilds: {e}")
            return []
    
    async def get_update_channel_for_guild(self, guild_id: int) -> Optional[discord.TextChannel]:
        """
        Get the update channel for a specific guild from database configuration.
        
        Args:
            guild_id (int): Discord guild ID
            
        Returns:
            Optional[discord.TextChannel]: Update channel or None if not configured
        """
        try:
            with DatabaseSession() as session:
                config = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.guild_id == guild_id,
                    UpdateChannelConfig.is_enabled == True
                ).first()
                
                if not config:
                    self.logger.info(f"No update channel configuration found for guild {guild_id}")
                    return None
                
                # Get the channel object
                channel = self.bot.get_channel(config.update_channel_id)
                
                if not channel:
                    self.logger.warning(f"Configured update channel {config.update_channel_id} not found for guild {guild_id}")
                    return None
                
                return channel
            
        except Exception as e:
            self.logger.error(f"Error getting update channel for guild {guild_id}: {e}")
            return None
    
    async def get_configured_channel_count(self) -> int:
        """
        Get the total number of configured update channels.
        
        Returns:
            int: Number of configured channels
        """
        try:
            with DatabaseSession() as session:
                count = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.is_enabled == True
                ).count()
                return count
        except Exception as e:
            self.logger.error(f"Error getting configured channel count: {e}")
            return 0
    
    async def get_global_update_channels(self) -> List[Dict[str, Any]]:
        """
        Get all channels configured to receive global updates.
        
        Returns:
            List[Dict[str, Any]]: List of channel configurations with channel_id and role_id
        """
        try:
            with DatabaseSession() as session:
                configs = session.query(UpdateChannelConfig).filter(
                    UpdateChannelConfig.is_enabled == True,
                    UpdateChannelConfig.include_global_updates == True
                ).all()
                
                channels = []
                for config in configs:
                    channels.append({
                        'guild_id': config.guild_id,
                        'channel_id': config.update_channel_id,
                        'role_id': config.notify_role_id
                    })
                
                return channels
                
        except Exception as e:
            self.logger.error(f"Error getting global update channels: {e}")
            return []
    
    def is_owner_or_trusted():
        """Check if user is the bot owner or a trusted user."""
        async def predicate(ctx):
            return await ctx.bot.is_owner_or_trusted(ctx.author)
        return commands.check(predicate)
    
    # Admin Commands for Manual Control
    
    @commands.command(name='forceweeklypost')
    @is_owner_or_trusted()
    async def force_weekly_post(self, ctx, dry_run: bool = False):
        """
        Manually trigger weekly update posting (Admin only).
        
        Args:
            dry_run (bool): If True, show what would be posted without actually posting
        """
        embed = discord.Embed(
            title="🔄 Manual Weekly Update Trigger",
            description="Processing weekly updates...",
            color=0x9146ff,
            timestamp=datetime.utcnow()
        )
        
        status_msg = await ctx.send(embed=embed)
        
        try:
            if dry_run:
                # Dry run mode - show what would be posted
                await self.run_dry_run(ctx)
            else:
                # Actually post updates
                await self.process_weekly_updates()
                
                embed.description = "✅ Weekly updates processed successfully!"
                embed.color = 0x00ff00
            
        except Exception as e:
            embed.description = f"❌ Error processing weekly updates: {str(e)}"
            embed.color = 0xff0000
            self.logger.error(f"Manual weekly post failed: {e}")
        
        await status_msg.edit(embed=embed)
    
    async def run_dry_run(self, ctx):
        """
        Run a dry run to show what updates would be posted.
        
        Args:
            ctx: Discord command context
        """
        try:
            guilds = await self.get_active_guilds()
            
            embed = discord.Embed(
                title="📋 Weekly Update Dry Run",
                description="Here's what would be posted:",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )
            
            total_updates = 0
            
            # Check each guild
            for guild in guilds[:5]:  # Limit to first 5 guilds to avoid embed limits
                guild_updates = await self.update_manager.get_updates_for_weekly_post(guild_id=int(guild.id))
                if guild_updates:
                    total_updates += len(guild_updates)
                    embed.add_field(
                        name=f"🏠 {guild.name}",
                        value=f"{len(guild_updates)} updates pending",
                        inline=True
                    )
            
            # Check global updates
            global_updates = await self.update_manager.get_updates_for_weekly_post(guild_id=None)
            if global_updates:
                total_updates += len(global_updates)
                embed.add_field(
                    name="🌐 Global Updates",
                    value=f"{len(global_updates)} updates pending",
                    inline=True
                )
            
            embed.add_field(
                name="📊 Total",
                value=f"{total_updates} updates would be posted",
                inline=False
            )
            
            embed.set_footer(text="This was a dry run - no updates were actually posted")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Dry run failed: {str(e)}")
    
    @commands.command(name='schedulerstatus')
    @is_owner_or_trusted()
    async def scheduler_status(self, ctx):
        """Check the status of the weekly scheduler (Admin only)."""
        try:
            current_time = get_est_time()
            next_post_time = self.get_next_posting_time(current_time)
            
            embed = discord.Embed(
                title="📅 Weekly Scheduler Status",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="Status",
                value="🟢 Running" if self.weekly_update_scheduler.is_running() else "🔴 Stopped",
                inline=True
            )
            
            embed.add_field(
                name="Posting Enabled",
                value="✅ Yes" if self.posting_enabled else "❌ No",
                inline=True
            )
            
            embed.add_field(
                name="Current Time (UK)",
                value=current_time.strftime("%Y-%m-%d %H:%M:%S"),
                inline=True
            )
            
            embed.add_field(
                name="Next Posting Time",
                value=next_post_time.strftime("%Y-%m-%d %H:%M:%S UK"),
                inline=True
            )
            
            embed.add_field(
                name="Posting Schedule",
                value=f"Sundays at {self.posting_hour:02d}:{self.posting_minute:02d} UK",
                inline=True
            )
            
            # Count configured channels from database
            channel_count = await self.get_configured_channel_count()
            embed.add_field(
                name="Configured Channels",
                value=f"{channel_count} channels",
                inline=True
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error getting scheduler status: {str(e)}")
    
    def get_next_posting_time(self, current_time: datetime) -> datetime:
        """
        Calculate the next posting time based on current time.
        
        Args:
            current_time (datetime): Current time in UK
            
        Returns:
            datetime: Next posting time
        """
        # Calculate days until next Sunday
        days_ahead = self.posting_day - current_time.weekday()
        if days_ahead <= 0:  # Target day already happened this week
            days_ahead += 7
        
        next_post = current_time.replace(
            hour=self.posting_hour,
            minute=self.posting_minute,
            second=0,
            microsecond=0
        ) + timedelta(days=days_ahead)
        
        return next_post
    
    @commands.command(name='togglescheduler')
    @is_owner_or_trusted()
    async def toggle_scheduler(self, ctx, enabled: Optional[bool] = None):
        """
        Enable or disable the weekly scheduler (Admin only).
        
        Args:
            enabled (bool): True to enable, False to disable, None to toggle
        """
        if enabled is None:
            self.posting_enabled = not self.posting_enabled
        else:
            self.posting_enabled = enabled
        
        status = "enabled" if self.posting_enabled else "disabled"
        color = 0x00ff00 if self.posting_enabled else 0xff0000
        
        embed = discord.Embed(
            title="⚙️ Scheduler Toggle",
            description=f"Weekly update posting is now **{status}**",
            color=color,
            timestamp=datetime.utcnow()
        )
        
        await ctx.send(embed=embed)
        self.logger.info(f"Weekly scheduler {status} by {ctx.author}")

async def setup(bot):
    await bot.add_cog(WeeklyScheduler(bot))