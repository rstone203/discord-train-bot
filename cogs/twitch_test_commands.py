"""
Test commands for Twitch chat monitoring functionality.
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
from datetime import datetime, timedelta
from database import DatabaseSession
from models import TrainSchedule, TrainParticipant, User, TrustedUser

logger = logging.getLogger(__name__)

def is_admin_or_trusted():
    """Check if user has manage_guild permission OR is a trusted user."""
    async def predicate(ctx):
        if hasattr(ctx, 'guild') and ctx.guild:
            # Check if user has manage guild permission
            if ctx.author.guild_permissions.manage_guild:
                return True
                
            # Check if user is a trusted user in this guild
            try:
                with DatabaseSession() as session:
                    trusted_user = session.query(TrustedUser).filter_by(
                        guild_id=ctx.guild.id,
                        user_id=ctx.author.id,
                        is_active=True
                    ).first()
                    return trusted_user is not None
            except Exception as e:
                logger.error(f"Error checking trusted user status: {e}")
                # Fallback to permission check only if database fails
                return ctx.author.guild_permissions.manage_guild
        else:
            # Allow in DMs only if user has manage_guild in any shared guild
            return False
    return commands.check(predicate)

class TwitchTestCommands(commands.Cog):
    """Test commands for Twitch functionality."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    async def respond(self, interaction, *args, **kwargs):
        """Helper method to safely respond to an interaction."""
        if interaction.response.is_done():
            if 'view' in kwargs and kwargs['view'] is None:
                del kwargs['view']
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)

    @app_commands.command(name='testchatmonitor', description='Test Twitch chat monitoring for a specific schedule')
    async def test_chat_monitor(self, interaction: discord.Interaction, schedule_id: int):
        """Test Twitch chat monitoring for a specific train schedule."""
        try:
            # Check if Twitch chat monitor is available
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await self.respond(interaction, "❌ Twitch chat monitor is not initialized", ephemeral=True)
                return
            
            with DatabaseSession() as session:
                # Get schedule info
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id,
                    guild_id=interaction.guild.id,
                    is_active=True
                ).first()
                
                if not schedule:
                    await self.respond(interaction, f"❌ Train schedule #{schedule_id} not found.", ephemeral=True)
                    return
                
                # Get participants with linked Twitch accounts
                participants = session.query(TrainParticipant, User).join(
                    User, TrainParticipant.user_id == User.id
                ).filter(
                    TrainParticipant.schedule_id == schedule_id,
                    TrainParticipant.is_active == True,
                    User.twitch_login.isnot(None)
                ).all()
                
                embed = discord.Embed(
                    title="🧪 Testing Twitch Chat Monitor",
                    description=f"Starting test monitoring for **{schedule.name}**",
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📋 Schedule Info",
                    value=f"**ID:** {schedule_id}\n**Name:** {schedule.name}\n**Duration:** {schedule.duration_minutes} min",
                    inline=True
                )
                
                if participants:
                    twitch_channels = [user.twitch_login for _, user in participants]
                    embed.add_field(
                        name="🎮 Channels to Monitor",
                        value=f"**Count:** {len(twitch_channels)}\n**Channels:** {', '.join(twitch_channels[:5])}{'...' if len(twitch_channels) > 5 else ''}",
                        inline=True
                    )
                else:
                    embed.add_field(
                        name="⚠️ Warning",
                        value="No participants with linked Twitch accounts found",
                        inline=True
                    )
                
                await self.respond(interaction, embed=embed)
                
                # Start the monitoring
                if participants:
                    self.logger.info(f"🧪 Starting test chat monitoring for schedule {schedule_id}")
                    await self.bot.twitch_chat_monitor.start_train_monitoring(schedule_id)
                    
                    # Send follow-up message
                    await interaction.followup.send(
                        f"✅ **Test monitoring started!**\n"
                        f"Monitoring {len(participants)} Twitch channels for {schedule.duration_minutes} minutes.\n"
                        f"Use `/sessionsummary {schedule_id}` after the test to see results."
                    )
                
        except Exception as e:
            self.logger.error(f"Error testing chat monitor: {e}")
            await self.respond(interaction, f"❌ Error starting test: {str(e)}", ephemeral=True)

    @app_commands.command(name='chatmonitorstatus', description='Check status of Twitch chat monitoring')
    async def chat_monitor_status(self, interaction: discord.Interaction):
        """Check the status of Twitch chat monitoring."""
        try:
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await self.respond(interaction, "❌ Twitch chat monitor is not initialized", ephemeral=True)
                return
            
            monitor = self.bot.twitch_chat_monitor
            
            embed = discord.Embed(
                title="📊 Twitch Chat Monitor Status",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )
            
            # Connection status
            connection_status = "🟢 Connected" if monitor.is_connected else "🔴 Disconnected"
            embed.add_field(
                name="🔗 Connection Status",
                value=connection_status,
                inline=True
            )
            
            # Active monitors
            active_count = len(monitor.active_monitors)
            embed.add_field(
                name="🎯 Active Monitors",
                value=f"{active_count} train sessions",
                inline=True
            )
            
            # Chat participants
            total_channels = len(monitor.chat_participants)
            total_users = sum(len(users) for users in monitor.chat_participants.values())
            embed.add_field(
                name="👥 Chat Activity",
                value=f"{total_channels} channels\n{total_users} active users",
                inline=True
            )
            
            # API credentials
            has_credentials = bool(monitor.client_id and monitor.client_secret)
            credentials_status = "✅ Configured" if has_credentials else "❌ Missing"
            embed.add_field(
                name="🔑 API Credentials",
                value=credentials_status,
                inline=True
            )
            
            # OAuth Token Health Check
            token_status = "❌ Not obtained"
            token_details = ""
            try:
                from models import TwitchOAuthToken
                with DatabaseSession() as session:
                    active_token = session.query(TwitchOAuthToken).filter_by(is_active=True).first()
                    if active_token:
                        if active_token.expires_at and active_token.expires_at < datetime.utcnow():
                            token_status = "🔴 EXPIRED"
                            token_details = f"\n⚠️ Expired: {active_token.expires_at.strftime('%Y-%m-%d %H:%M')}\n📋 Run `/twitchoauth` to refresh"
                        else:
                            token_status = "🟢 Valid"
                            if active_token.expires_at:
                                expires_in = active_token.expires_at - datetime.utcnow()
                                days_left = expires_in.days
                                token_details = f"\n✅ Expires in {days_left} days"
                    else:
                        token_details = "\n📋 Run `/twitchoauth` to setup"
            except Exception as e:
                token_details = f"\n⚠️ Check failed: {str(e)[:50]}"
            
            embed.add_field(
                name="🎫 OAuth Token",
                value=token_status + token_details,
                inline=True
            )
            
            # Recent activity
            if monitor.chat_participants:
                recent_activity = []
                for channel, users in list(monitor.chat_participants.items())[:3]:
                    recent_activity.append(f"#{channel}: {len(users)} users")
                
                embed.add_field(
                    name="📈 Recent Activity",
                    value="\n".join(recent_activity) if recent_activity else "No recent activity",
                    inline=False
                )
            
            await self.respond(interaction, embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error checking chat monitor status: {e}")
            await self.respond(interaction, f"❌ Error checking status: {str(e)}", ephemeral=True)

    @app_commands.command(name='stopchatmonitor', description='Stop chat monitoring for a specific schedule')
    async def stop_chat_monitor(self, interaction: discord.Interaction, schedule_id: int):
        """Stop chat monitoring for a specific schedule."""
        try:
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await self.respond(interaction, "❌ Twitch chat monitor is not initialized", ephemeral=True)
                return
            
            monitor = self.bot.twitch_chat_monitor
            task_key = f"train_{schedule_id}"
            
            if task_key in monitor.active_monitors:
                await monitor.stop_train_monitoring(schedule_id)
                await self.respond(interaction, 
                    f"✅ **Stopped monitoring for schedule #{schedule_id}**\n"
                    f"Use `/sessionsummary {schedule_id}` to view the results."
                )
            else:
                await self.respond(interaction, 
                    f"⚠️ No active monitoring found for schedule #{schedule_id}",
                    ephemeral=True
                )
                
        except Exception as e:
            self.logger.error(f"Error stopping chat monitor: {e}")
            await self.respond(interaction, f"❌ Error stopping monitor: {str(e)}", ephemeral=True)

    @app_commands.command(name='starttest', description='Start automated train monitoring (trusted users only)')
    @is_admin_or_trusted()
    async def start_automated_test(self, interaction: discord.Interaction, schedule_id: int = None):
        """Start automated train monitoring - runs until manually stopped."""
        try:
            # Import the automated test system
            from automated_test_attendance import start_automated_attendance_test
            from models import NotificationSettings
            
            # Check if schedule exists and get notification settings
            with DatabaseSession() as session:
                # If no schedule_id provided, auto-detect first active schedule
                if schedule_id is None:
                    schedule = session.query(TrainSchedule).filter_by(
                        guild_id=interaction.guild.id,
                        is_active=True
                    ).first()
                    
                    if not schedule:
                        await self.respond(interaction, 
                            "❌ No active schedules found in this server. Create one first with `/createtrain`.",
                            ephemeral=True
                        )
                        return
                    
                    schedule_id = schedule.id
                else:
                    schedule = session.query(TrainSchedule).filter_by(
                        id=schedule_id,
                        guild_id=interaction.guild.id,
                        is_active=True
                    ).first()
                    
                    if not schedule:
                        await self.respond(interaction, 
                            f"❌ Schedule ID {schedule_id} not found or inactive in this server.",
                            ephemeral=True
                        )
                        return
                
                # Check if notification settings exist
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=interaction.guild.id
                ).first()
                
                if not settings or not settings.attendance_channel_id:
                    await self.respond(interaction, 
                        "❌ No attendance channel configured. Please run `/setattendancechannel` first.",
                        ephemeral=True
                    )
                    return
                
                # Extract values we need before session closes
                schedule_name = schedule.name
                notification_channel_id = settings.attendance_channel_id
            
            # Start the automated test (outside session context) - runs indefinitely
            success = await start_automated_attendance_test(
                self.bot, 
                interaction.guild.id, 
                schedule_id, 
                notification_channel_id,
                0  # 0 = run indefinitely until manually stopped
            )
            
            if not success:
                await self.respond(interaction, 
                    "❌ Failed to start automated test. Check if another test is already running.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="🧪 Automated Attendance Test Started",
                description=f"Started automated testing for **{schedule_name}** (ID: {schedule_id})",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📋 Test Details",
                value=(
                    f"• **Schedule:** {schedule_name}\n"
                    f"• **Interval:** Every 15 minutes\n"
                    f"• **Duration:** Until manually stopped\n"
                    f"• **Reports:** Unlimited"
                ),
                inline=False
            )
            
            embed.add_field(
                name="🎮 What Gets Tracked",
                value=(
                    "✅ **Twitch Chat Presence** - Everyone in your Twitch chat\n"
                    "✅ **Real-Time Monitoring** - Updates every 3 minutes\n"
                    "✅ **Attendance Reports** - Clean reports every 15 minutes\n"
                    "✅ **Database Persistence** - All data saved automatically"
                ),
                inline=False
            )
            
            embed.add_field(
                name="🛠️ Control Commands",
                value=(
                    "• `/sendattendancenow` - Send report immediately\n"
                    "• `/chatmonitorstatus` - Check monitoring status\n"
                    "• `/stoptest` - Stop tracking"
                ),
                inline=False
            )
            
            embed.set_footer(text="Attendance reports will be sent every 15 minutes!")
            
            await self.respond(interaction, embed=embed)
            self.logger.info(f"Automated attendance test started by {interaction.user} for schedule {schedule_id}")
            
        except Exception as e:
            self.logger.error(f"Error starting automated test: {e}")
            await self.respond(interaction, 
                f"❌ Error starting automated test: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name='testtwitchchat', description='Test Twitch chat monitoring for any channel (no schedule needed)')
    async def test_twitch_chat(self, interaction: discord.Interaction, twitch_username: str, duration_minutes: int = 3):
        """Test Twitch chat monitoring for a single channel without needing a schedule."""
        try:
            # Acknowledge interaction immediately to prevent timeout
            await interaction.response.defer()
            
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await interaction.followup.send("❌ Twitch chat monitor is not initialized", ephemeral=True)
                return
            
            monitor = self.bot.twitch_chat_monitor
            
            # Auto-connect if not connected
            if not monitor.is_connected:
                await interaction.followup.send("🔄 Connecting to Twitch IRC...")
                try:
                    await monitor.connect_to_twitch_irc()
                    await asyncio.sleep(2)
                    if not monitor.is_connected:
                        await interaction.followup.send("❌ Failed to connect to Twitch IRC. Please try again.", ephemeral=True)
                        return
                except Exception as e:
                    await interaction.followup.send(f"❌ Connection error: {str(e)}", ephemeral=True)
                    return
            
            if duration_minutes < 1 or duration_minutes > 15:
                await interaction.followup.send("❌ Duration must be between 1 and 15 minutes.", ephemeral=True)
                return
            
            twitch_username = twitch_username.lower().strip()
            
            embed = discord.Embed(
                title="🧪 Testing Twitch Chat Monitor",
                description=f"Starting standalone test monitoring for **{twitch_username}**",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🎮 Channel",
                value=f"twitch.tv/{twitch_username}",
                inline=True
            )
            
            embed.add_field(
                name="⏱️ Duration",
                value=f"{duration_minutes} minute{'s' if duration_minutes != 1 else ''}",
                inline=True
            )
            
            embed.add_field(
                name="📊 What This Tests",
                value="• Connects to Twitch chat\n• Tracks who's active in chat\n• Shows real-time presence data",
                inline=False
            )
            
            await interaction.followup.send(embed=embed)
            
            with DatabaseSession() as session:
                linked_users = session.query(User).filter(
                    User.twitch_login.isnot(None),
                    User.guild_id == interaction.guild.id
                ).all()
                
                discord_user_map = {user.twitch_login.lower(): user.username for user in linked_users}
            
            test_key = f"test_{twitch_username}_{interaction.id}"
            
            if twitch_username not in monitor.chat_participants:
                monitor.chat_participants[twitch_username] = set()
            
            await monitor.join_channel(twitch_username)
            
            await interaction.followup.send(
                f"✅ **Monitoring started!**\n"
                f"Now tracking chat activity in **{twitch_username}**'s channel.\n"
                f"The test will run for **{duration_minutes} minute{'s' if duration_minutes != 1 else ''}**."
            )
            
            async def check_results():
                await asyncio.sleep(duration_minutes * 60)
                
                participants = monitor.chat_participants.get(twitch_username, set())
                
                result_embed = discord.Embed(
                    title="📊 Chat Monitoring Test Results",
                    description=f"Test completed for **{twitch_username}**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                result_embed.add_field(
                    name="⏱️ Duration",
                    value=f"{duration_minutes} minute{'s' if duration_minutes != 1 else ''}",
                    inline=True
                )
                
                result_embed.add_field(
                    name="👥 Users Detected",
                    value=f"{len(participants)} active in chat",
                    inline=True
                )
                
                if participants:
                    linked = []
                    unlinked = []
                    
                    for username in sorted(participants):
                        if username in discord_user_map:
                            linked.append(f"✅ {username} ({discord_user_map[username]})")
                        else:
                            unlinked.append(f"❌ {username}")
                    
                    if linked:
                        result_embed.add_field(
                            name="🔗 Linked Users Found",
                            value="\n".join(linked[:10]) + ("\n..." if len(linked) > 10 else ""),
                            inline=False
                        )
                    
                    if unlinked:
                        result_embed.add_field(
                            name="👤 Other Chat Users",
                            value="\n".join(unlinked[:10]) + ("\n..." if len(unlinked) > 10 else ""),
                            inline=False
                        )
                else:
                    result_embed.add_field(
                        name="⚠️ No Activity",
                        value="No chat activity detected during the test period.",
                        inline=False
                    )
                
                result_embed.set_footer(text=f"Test ID: {interaction.id}")
                
                await interaction.followup.send(embed=result_embed)
                
                await monitor.leave_channel(twitch_username)
            
            asyncio.create_task(check_results())
            
        except Exception as e:
            self.logger.error(f"Error testing Twitch chat: {e}", exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ Error starting test: {str(e)}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ Error starting test: {str(e)}", ephemeral=True)
            except:
                pass

    @app_commands.command(name='sendattendancenow', description='Manually send attendance report for active monitoring sessions')
    @is_admin_or_trusted()
    async def send_attendance_now(self, interaction: discord.Interaction):
        """Manually trigger attendance report for all currently active trains and quick tests."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await interaction.followup.send("❌ Twitch chat monitor not available", ephemeral=True)
                return
            
            monitor = self.bot.twitch_chat_monitor
            
            # Get all active monitors (trains and quick tests)
            active_trains = []
            active_quicktests = []
            
            for key in list(monitor.active_monitors.keys()):
                if key.startswith('train_'):
                    schedule_id = int(key.replace('train_', ''))
                    active_trains.append(schedule_id)
                elif key.startswith('quicktest_'):
                    twitch_username = key.replace('quicktest_', '')
                    active_quicktests.append(twitch_username)
            
            if not active_trains and not active_quicktests:
                await interaction.followup.send(
                    "❌ No active monitoring sessions found.\n"
                    "Start a quick test with `/quicktest` or wait for a train to begin.",
                    ephemeral=True
                )
                return
            
            # Generate reports
            reports_sent = 0
            quicktest_reports = 0
            
            # Generate attendance report for each active train
            for schedule_id in active_trains:
                try:
                    await monitor._generate_chat_attendance_report(schedule_id)
                    reports_sent += 1
                except Exception as e:
                    self.logger.error(f"Error generating report for schedule {schedule_id}: {e}")
            
            # Generate quick test reports directly to the user
            for twitch_username in active_quicktests:
                try:
                    test_key = f"quicktest_{twitch_username}"
                    
                    # Get chatters for this test
                    all_chatters = set()
                    if twitch_username in monitor.chat_participants:
                        all_chatters = monitor.chat_participants[twitch_username]
                    
                    # Get viewer count
                    viewer_count = await monitor.get_stream_viewer_count(twitch_username)
                    
                    # Create quick report embed
                    embed = discord.Embed(
                        title=f"🧪 Quick Test Attendance Report",
                        description=f"**Twitch Channel:** {twitch_username}",
                        color=0x9146ff,
                        timestamp=datetime.utcnow()
                    )
                    
                    # Chatters list
                    if all_chatters:
                        chatters_list = ', '.join(sorted(all_chatters, key=str.lower))
                        if len(chatters_list) > 1000:
                            chatters_list = chatters_list[:1000] + '...'
                        embed.add_field(
                            name=f"👁️ Chat Viewers ({len(all_chatters)})",
                            value=chatters_list or "None detected",
                            inline=False
                        )
                    else:
                        embed.add_field(
                            name="👁️ Chat Viewers",
                            value="No viewers with chat open detected yet",
                            inline=False
                        )
                    
                    # Viewer stats
                    if viewer_count is not None and viewer_count > 0:
                        engagement_pct = (len(all_chatters) / viewer_count * 100) if viewer_count > 0 else 0
                        embed.add_field(
                            name="📊 Stream Stats",
                            value=f"**Total Viewers:** {viewer_count}\n**Chat Engagement:** {engagement_pct:.1f}%",
                            inline=False
                        )
                    
                    embed.set_footer(text=f"Quick Test • Channel: {twitch_username}")
                    
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    quicktest_reports += 1
                    
                except Exception as e:
                    self.logger.error(f"Error generating quick test report for {twitch_username}: {e}")
            
            # Summary message
            summary_parts = []
            if reports_sent > 0:
                summary_parts.append(f"✅ {reports_sent} train report(s) sent to attendance channels")
            if quicktest_reports > 0:
                summary_parts.append(f"🧪 {quicktest_reports} quick test report(s) shown above")
            
            if summary_parts and quicktest_reports == 0:
                await interaction.followup.send("\n".join(summary_parts), ephemeral=True)
            
        except Exception as e:
            self.logger.error(f"Error generating attendance report: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    @app_commands.command(name='stoptest', description='Stop automated train monitoring (trusted users only)')
    @is_admin_or_trusted()
    async def stop_automated_test(self, interaction: discord.Interaction, schedule_id: int = 3):
        """Stop automated train monitoring."""
        try:
            if not hasattr(self.bot, 'active_tests') or schedule_id not in self.bot.active_tests:
                await self.respond(interaction, 
                    f"❌ No active test found for schedule ID {schedule_id}.",
                    ephemeral=True
                )
                return
            
            # Stop the test
            test_instance = self.bot.active_tests[schedule_id]
            test_instance.stop_test()
            del self.bot.active_tests[schedule_id]
            
            embed = discord.Embed(
                title="🛑 Automated Test Stopped",
                description=f"Stopped automated testing for schedule ID {schedule_id}",
                color=0xff9900,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 Test Complete",
                value="The automated attendance test has been stopped. Use `/sessionsummary` to view final results.",
                inline=False
            )
            
            await self.respond(interaction, embed=embed)
            self.logger.info(f"Automated test stopped by {interaction.user} for schedule {schedule_id}")
            
        except Exception as e:
            self.logger.error(f"Error stopping automated test: {e}")
            await self.respond(interaction, 
                f"❌ Error stopping automated test: {str(e)}",
                ephemeral=True
            )

    @app_commands.command(name='quicktest', description='Quick test attendance tracking for any Twitch channel (no schedule needed)')
    @app_commands.describe(
        twitch_username='Twitch username to monitor (e.g., rstone203)',
        duration='Test duration in minutes (default: 5, max: 120)'
    )
    async def quick_test(self, interaction: discord.Interaction, twitch_username: str, duration: int = 5):
        """Quick test of attendance tracking without creating a schedule."""
        # Validate first — before touching the interaction at all
        if duration < 1 or duration > 120:
            await interaction.response.send_message("❌ Duration must be between 1-120 minutes.", ephemeral=True)
            return

        if not hasattr(self.bot, 'twitch_chat_monitor'):
            await interaction.response.send_message("❌ Twitch chat monitor is not initialized.", ephemeral=True)
            return

        monitor = self.bot.twitch_chat_monitor
        test_key = f"quicktest_{twitch_username.lower()}"

        if test_key in monitor.active_monitors:
            await interaction.response.send_message(
                f"❌ Already running a quick test for **{twitch_username}**. Stop it first with `/stopquicktest`.",
                ephemeral=True
            )
            return

        try:
            guild_id = interaction.guild_id
            command_channel_id = interaction.channel_id

            # Respond instantly — no defer() needed
            confirm_embed = discord.Embed(
                title="🧪 Quick Attendance Test Started",
                description=f"Now monitoring **[{twitch_username}](https://twitch.tv/{twitch_username})**",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )
            confirm_embed.add_field(name="⏱️ Duration", value=f"{duration} minutes", inline=True)
            confirm_embed.add_field(name="🔄 Poll Rate", value="Every 2 minutes", inline=True)
            confirm_embed.add_field(
                name="📋 What happens",
                value=(
                    f"• Polls chat every 2 min to count viewers\n"
                    f"• Sends a full attendance report at the end\n"
                    f"• Stop early with `/stopquicktest {twitch_username}`"
                ),
                inline=False
            )
            confirm_embed.set_footer(text=f"Started by {interaction.user}")
            await interaction.response.send_message(embed=confirm_embed, ephemeral=True)

            async def _polling_loop():
                channel_name = twitch_username.lower()
                try:
                    # Make sure we are in the IRC channel
                    await monitor.join_channel(channel_name)

                    # Reset tracking for this channel
                    monitor.chat_participants[channel_name] = set()
                    monitor.current_live_channels = [channel_name]

                    elapsed = 0
                    max_duration = duration * 60
                    poll_interval = 120  # poll every 2 minutes

                    while elapsed < max_duration:
                        wait_time = min(poll_interval, max_duration - elapsed)
                        await asyncio.sleep(wait_time)
                        elapsed += wait_time

                        chatters = await monitor.get_chatters_from_api(channel_name, guild_id=guild_id)
                        if chatters:
                            monitor.chat_participants[channel_name].update(chatters)
                            monitor.current_cycle_chatters[channel_name] = chatters.copy()
                            self.logger.info(f"🧪 Quicktest poll: {len(chatters)} chatters in #{channel_name} (total: {len(monitor.chat_participants[channel_name])})")

                    # Generate final report
                    all_chatters = monitor.chat_participants.get(channel_name, set())
                    viewer_count = await monitor.get_stream_viewer_count(channel_name)

                    embed = discord.Embed(
                        title="🏁 Quick Test — Final Attendance Report",
                        description=f"**Channel:** [{channel_name}](https://twitch.tv/{channel_name})\n**Duration:** {duration} minutes",
                        color=0x9146ff,
                        timestamp=datetime.utcnow()
                    )

                    if viewer_count and viewer_count > 0:
                        engagement = (len(all_chatters) / viewer_count * 100) if viewer_count > 0 else 0
                        embed.add_field(
                            name="📊 Stats",
                            value=f"**Total Viewers:** {viewer_count:,}\n**In Chat:** {len(all_chatters)}\n**Engagement:** {engagement:.1f}%",
                            inline=True
                        )
                    else:
                        embed.add_field(
                            name="📊 Stats",
                            value=f"**Unique Chatters:** {len(all_chatters)}",
                            inline=True
                        )

                    if all_chatters:
                        chatter_list = sorted(all_chatters, key=str.lower)
                        display = ', '.join(f'`{c}`' for c in chatter_list[:30])
                        if len(chatter_list) > 30:
                            display += f'\n_...and {len(chatter_list) - 30} more_'
                        embed.add_field(name=f"👥 Chatters ({len(all_chatters)})", value=display, inline=False)
                    else:
                        embed.add_field(name="👥 Chatters", value="None detected — stream may not have been live", inline=False)

                    embed.set_footer(text=f"Quick test complete")

                    # Try to send to attendance channel first, fall back to command channel
                    sent = False
                    try:
                        from database import DatabaseSession
                        from models import NotificationSettings
                        with DatabaseSession() as session:
                            settings = session.query(NotificationSettings).filter_by(guild_id=guild_id).first()
                            if settings and settings.attendance_channel_id:
                                guild = self.bot.get_guild(guild_id)
                                att_channel = guild.get_channel(settings.attendance_channel_id) if guild else None
                                if att_channel:
                                    await att_channel.send(embed=embed)
                                    sent = True
                    except Exception as e:
                        self.logger.warning(f"Could not send to attendance channel: {e}")

                    if not sent:
                        try:
                            guild = self.bot.get_guild(guild_id)
                            ch = guild.get_channel(command_channel_id) if guild else None
                            if ch:
                                await ch.send(embed=embed)
                        except Exception as e:
                            self.logger.error(f"Could not send quicktest report anywhere: {e}")

                    self.logger.info(f"✅ Quick test complete for #{channel_name} — {len(all_chatters)} unique chatters")

                except asyncio.CancelledError:
                    self.logger.info(f"⏹️ Quick test for #{channel_name} cancelled early")
                except Exception as e:
                    self.logger.error(f"Error in quicktest polling loop for {channel_name}: {e}", exc_info=True)
                finally:
                    if test_key in monitor.active_monitors:
                        del monitor.active_monitors[test_key]

            task = asyncio.create_task(_polling_loop())
            monitor.active_monitors[test_key] = task

            self.logger.info(f"Quick test started by {interaction.user} for {twitch_username} ({duration}m)")

        except Exception as e:
            self.logger.error(f"Error in quick test: {e}", exc_info=True)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ Error starting quick test: {str(e)}", ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name='stopquicktest', description='Stop a running quick test')
    @app_commands.describe(twitch_username='Twitch username to stop monitoring')
    async def stop_quick_test(self, interaction: discord.Interaction, twitch_username: str):
        """Stop a running quick test early."""
        try:
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await interaction.response.send_message("❌ Twitch chat monitor is not initialized.", ephemeral=True)
                return

            monitor = self.bot.twitch_chat_monitor
            test_key = f"quicktest_{twitch_username.lower()}"

            if test_key not in monitor.active_monitors:
                await interaction.response.send_message(
                    f"❌ No active quick test found for **{twitch_username}**.",
                    ephemeral=True
                )
                return

            monitor.active_monitors[test_key].cancel()

            embed = discord.Embed(
                title="🛑 Quick Test Stopped",
                description=f"Stopped monitoring **{twitch_username}**",
                color=0xff9900,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text=f"Stopped by {interaction.user}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.info(f"Quick test stopped by {interaction.user} for {twitch_username}")

        except Exception as e:
            self.logger.error(f"Error stopping quick test: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ Error stopping quick test: {str(e)}", ephemeral=True)
            except Exception:
                pass

async def setup(bot):
    await bot.add_cog(TwitchTestCommands(bot))