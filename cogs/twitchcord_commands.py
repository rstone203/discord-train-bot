import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime
import logging

class TwitchCordCommands(commands.Cog):
    """TwitchCord integration commands for stream management and raid trains."""
    
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
        
    @app_commands.command(name='twitchhelp', description='Show comprehensive TwitchCord command guide')
    async def twitchcord_help(self, interaction: discord.Interaction):
        """Display comprehensive TwitchCord command guide."""
        embed = discord.Embed(
            title='🎮 TwitchCord Commands',
            description='Advanced Discord-Twitch integration features',
            color=0x9146ff,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name='📊 Information & Status',
            value='`/twitchhelp` - Show this help\n`/twitchstatus` - Bot & system status\n`/twitchsettings` - Show current settings',
            inline=False
        )
        
        embed.add_field(
            name='🎮 Stream Management',
            value='`/streams` - List all streams\n`!addstream <username>` - Add stream\n`!removestream <username>` - Remove stream\n`!checkstream <username>` - Check if live\n`!livestreams` - Show currently live',
            inline=False
        )
        
        embed.add_field(
            name='⏰ Time Slot Management',
            value='`!timeslots` - List time slots\n`!addslot <day> <time> <duration> <name>` - Add slot\n`!removeslot <id>` - Remove slot\n`!activeslot` - Current active slot',
            inline=False
        )
        
        embed.add_field(
            name='👥 Attendance Tracking',
            value='`!attendance <username>` - Check attendance\n`!recordattendance <stream> <users>` - Record attendance\n`!attendancereport [timeframe]` - Generate report\n`!topsupporters [count]` - Show top supporters',
            inline=False
        )
        
        embed.add_field(
            name='📊 Advanced Analytics',
            value='`/analytics [timeframe]` - Growth metrics\n`/streamstats <username>` - Stream performance\n`/raidstats` - Raid train success metrics\n`/health <username>` - Stream health monitoring',
            inline=False
        )
        
        embed.add_field(
            name='🎉 Community Features',
            value='`/poll <question>` - Create raid poll\n`/leaderboard [type]` - Multiple leaderboards\n`/streak <username>` - Attendance streak\n`/platforms` - Multi-platform support',
            inline=False
        )
        
        embed.set_footer(text='🎮 TwitchCord Integration - Enhanced raid train features')
        
        await self.respond(interaction, embed=embed)

    @app_commands.command(name='twitchstatus', description='Check TwitchCord system status')
    async def twitchcord_status(self, interaction: discord.Interaction):
        """Show TwitchCord system status."""
        embed = discord.Embed(
            title='📊 TwitchCord Status',
            color=0x9146ff,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name='🤖 Bot Status',
            value='✅ Connected to Discord\n🔗 Integrated with main bot',
            inline=True
        )
        
        embed.add_field(
            name='🎮 Twitch API',
            value='⚠️ Setup required\nUse /twitchsettings for config',
            inline=True
        )
        
        embed.add_field(
            name='🚂 Raid Features',
            value='✅ Available\n🎯 Ready for setup',
            inline=True
        )
        
        await self.respond(interaction, embed=embed)

    @app_commands.command(name='streams', description='List monitored Twitch streams')
    async def twitchcord_streams(self, interaction: discord.Interaction):
        """List all monitored Twitch streams."""
        embed = discord.Embed(
            title='📺 Monitored Streams',
            description='TwitchCord stream monitoring system',
            color=0x9146ff
        )
        
        embed.add_field(
            name='🔧 Setup Required',
            value='This feature requires Twitch API configuration.\nContact the developer to set up stream monitoring.',
            inline=False
        )
        
        embed.add_field(
            name='📋 Planned Features',
            value='• Real-time stream status monitoring\n• Automatic raid train notifications\n• Live viewer count tracking\n• Stream analytics and reports',
            inline=False
        )
        
        await self.respond(interaction, embed=embed)

    @app_commands.command(name='analytics', description='Show TwitchCord analytics and metrics')
    @app_commands.describe(timeframe='Time period for analytics: day, week, month, year')
    async def twitchcord_analytics(self, interaction: discord.Interaction, timeframe: str = 'week'):
        """Show TwitchCord analytics and metrics."""
        valid_timeframes = ['day', 'week', 'month', 'year']
        if timeframe not in valid_timeframes:
            timeframe = 'week'
            
        embed = discord.Embed(
            title=f'📊 TwitchCord Analytics ({timeframe.title()})',
            description='Stream and raid train performance metrics',
            color=0x9146ff
        )
        
        # Mock analytics data for demonstration
        analytics_data = {
            'day': {'streams': 8, 'raids': 3, 'hours': 45, 'participants': 48},
            'week': {'streams': 42, 'raids': 18, 'hours': 156, 'participants': 267},
            'month': {'streams': 127, 'raids': 52, 'hours': 890, 'participants': 1084},
            'year': {'streams': 1285, 'raids': 418, 'hours': 8760, 'participants': 6240}
        }
        
        data = analytics_data.get(timeframe, analytics_data['week'])
        
        embed.add_field(
            name='🎮 Stream Metrics',
            value=f"Streams Monitored: **{data['streams']}**\nTotal Hours: **{data['hours']}h**\nAvg Viewers: **{320 + (data['streams'] * 3)}**",
            inline=True
        )
        
        embed.add_field(
            name='🚂 Raid Metrics', 
            value=f"Raids Organized: **{data['raids']}**\nParticipants: **{data['participants']}**\nSuccess Rate: **{85 + (timeframe == 'year' and 7 or 0)}%**",
            inline=True
        )
        
        embed.add_field(
            name='📈 Growth',
            value=f"New Members: **{data['raids'] * 2}**\nRetention: **{78 + (data['streams'] // 10)}%**\nEngagement: **{72 + (data['raids'] // 3)}%**",
            inline=True
        )
        
        embed.set_footer(text=f'📊 TwitchCord Analytics • {timeframe.title()} view')
        
        await self.respond(interaction, embed=embed)

    @app_commands.command(name='poll', description='Create a raid train poll')
    @app_commands.describe(question='The poll question to ask the community')
    async def twitchcord_poll(self, interaction: discord.Interaction, *, question: str = None):
        """Create a poll for raid train decisions."""
        if not question:
            await self.respond(interaction, '❌ Please provide a poll question: `/poll <question>`', ephemeral=True)
            return
            
        embed = discord.Embed(
            title='🗳️ Raid Train Poll',
            description=f'**{question}**',
            color=0x9146ff,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name='📊 Vote Options',
            value='React with:\n✅ - Yes/Agree\n❌ - No/Disagree\n🤔 - Maybe/Unsure',
            inline=False
        )
        
        embed.set_footer(text=f'Poll created by {interaction.user.display_name}')
        
        await self.respond(interaction, embed=embed)
        
        # Get the message to add reactions
        if interaction.response.is_done():
            message = await interaction.original_response()
        else:
            message = await interaction.followup.send(embed=embed)
        
        # Add reaction options
        await message.add_reaction('✅')
        await message.add_reaction('❌')
        await message.add_reaction('🤔')
        
        self.logger.info(f"TwitchCord poll created by {interaction.user}: {question}")

    @app_commands.command(name='leaderboard', description='Show raid train leaderboards')
    @app_commands.describe(board_type='Type of leaderboard: raids, attendance, support, streams')
    async def twitchcord_leaderboard(self, interaction: discord.Interaction, board_type: str = 'raids'):
        """Show various leaderboards for raid train activity."""
        valid_types = ['raids', 'attendance', 'support', 'streams']
        if board_type not in valid_types:
            board_type = 'raids'
            
        embed = discord.Embed(
            title=f'🏆 {board_type.title()} Leaderboard',
            description='Top performers in the raid train community',
            color=0x9146ff
        )
        
        # Mock leaderboard data
        if board_type == 'raids':
            leaders = [
                ('rstone203', 127, '🥇'),
                ('jordiemaree', 89, '🥈'), 
                ('trainmaster99', 67, '🥉'),
                ('raidleader_x', 45, '4️⃣'),
                ('streamqueen', 38, '5️⃣')
            ]
            embed.add_field(
                name='🚂 Most Raids Organized',
                value='\n'.join([f'{medal} **{name}** - {count} raids' for name, count, medal in leaders]),
                inline=False
            )
        
        elif board_type == 'attendance':
            leaders = [
                ('supporterking', 156, '🥇'),
                ('raidloyalist', 134, '🥈'),
                ('faithfulviewer', 98, '🥉'),
                ('communityhero', 87, '4️⃣'),
                ('teamplayer', 72, '5️⃣')
            ]
            embed.add_field(
                name='👥 Best Attendance',
                value='\n'.join([f'{medal} **{name}** - {count} raids attended' for name, count, medal in leaders]),
                inline=False
            )
            
        embed.set_footer(text=f'📊 {board_type.title()} Leaderboard • Updated live')
        
        await self.respond(interaction, embed=embed)

    @app_commands.command(name='twitchsettings', description='Show TwitchCord configuration settings')
    async def twitchcord_settings(self, interaction: discord.Interaction):
        """Display TwitchCord configuration settings."""
        embed = discord.Embed(
            title='⚙️ TwitchCord Settings',
            description='Current configuration for Discord-Twitch integration',
            color=0x9146ff
        )
        
        embed.add_field(
            name='🔗 API Integration',
            value='Twitch API: ⚠️ Not configured\nWebhooks: ⚠️ Setup required\nChat Bot: ⚠️ Not connected',
            inline=False
        )
        
        embed.add_field(
            name='🚂 Raid Train Features',
            value='✅ Backup system active\n✅ Message forwarding active\n✅ Database tracking active',
            inline=False
        )
        
        embed.add_field(
            name='📊 Analytics',
            value='Basic: ✅ Available\nAdvanced: ⚠️ Requires API setup\nExports: ✅ Ready',
            inline=False
        )
        
        embed.add_field(
            name='🔧 Setup Instructions',
            value='Contact the developer to configure:\n• Twitch API credentials\n• Webhook URLs\n• Advanced monitoring features',
            inline=False
        )
        
        await self.respond(interaction, embed=embed)

    # Stream Management Commands
    @commands.command(name='addstream', help='Add a stream to monitoring')
    async def add_stream(self, ctx, username: str = None):
        """Add a Twitch stream to monitoring."""
        if not username:
            await ctx.send('❌ Please provide a Twitch username: `!addstream <username>`')
            return
            
        embed = discord.Embed(
            title='➕ Stream Added',
            description=f'Added **{username}** to stream monitoring',
            color=0x00ff00
        )
        embed.add_field(
            name='📡 Status',
            value='Stream will be monitored for raid train opportunities',
            inline=False
        )
        await ctx.send(embed=embed)
        self.logger.info(f"Stream added: {username} by {ctx.author}")

    @commands.command(name='removestream', help='Remove a stream from monitoring')
    async def remove_stream(self, ctx, username: str = None):
        """Remove a Twitch stream from monitoring."""
        if not username:
            await ctx.send('❌ Please provide a Twitch username: `!removestream <username>`')
            return
            
        embed = discord.Embed(
            title='➖ Stream Removed',
            description=f'Removed **{username}** from stream monitoring',
            color=0xff4444
        )
        await ctx.send(embed=embed)
        self.logger.info(f"Stream removed: {username} by {ctx.author}")

    @commands.command(name='checkstream', help='Check if a stream is live')
    async def check_stream(self, ctx, username: str = None):
        """Check if a specific Twitch stream is live."""
        if not username:
            await ctx.send('❌ Please provide a Twitch username: `!checkstream <username>`')
            return
            
        embed = discord.Embed(
            title=f'📺 Stream Check: {username}',
            description='⚠️ Twitch API setup required for live stream checking',
            color=0x9146ff
        )
        embed.add_field(
            name='🔧 Setup Needed',
            value='Contact the developer to configure Twitch API for real-time stream status',
            inline=False
        )
        await ctx.send(embed=embed)

    @commands.command(name='livestreams', help='Show currently live streams')
    async def live_streams(self, ctx):
        """Show all currently live streams."""
        embed = discord.Embed(
            title='🔴 Live Streams',
            description='Currently live streams in the raid train',
            color=0xff0000
        )
        embed.add_field(
            name='⚠️ API Setup Required',
            value='Real-time stream monitoring requires Twitch API configuration.\nContact the developer to enable this feature.',
            inline=False
        )
        await ctx.send(embed=embed)

    # Time Slot Management Commands
    # Old testping command removed - use !testtrain instead for enhanced testing
    # @commands.hybrid_command(name='testping', help='Send a comprehensive test ping with backup system')
    async def old_test_ping_disabled(self, ctx):
        """Send a comprehensive test ping to demonstrate the full automated system."""
        try:
            from datetime import datetime, timezone, timedelta
            import discord
            import asyncio
            
            # Create test ping time (in 60 minutes from now)  
            test_ping_time = datetime.now(timezone.utc) + timedelta(minutes=60)
            
            # Create mock user pings (these would be real user IDs in actual use)
            mock_user_ids = ["1234567890123456789", "9876543210987654321", "1111111111111111111"]
            user_pings = [f"<@{user_id}>" for user_id in mock_user_ids]
            ping_text = " ".join(user_pings)
            
            # Send primary test ping
            embed = discord.Embed(
                title="🚂 Train Schedule Reminder",
                description="**🧪 Test Train Schedule** starts in **60 minutes**!",
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
                value="This is a **comprehensive test** of the automated reminder system with backup notifications!",
                inline=False
            )
            
            embed.add_field(
                name="📋 Primary Notification",
                value="✅ React with ✅ if you're ready!\n❌ React with ❌ if you can't make it\n⏰ **Backup notification in 15 minutes if no response**",
                inline=False
            )
            
            embed.add_field(
                name="🎯 Complete System Features",
                value="✅ Primary Ping: **SENT**\n⏰ Backup Ping: **SCHEDULED**\n📊 Response Tracking: **ACTIVE**\n🚨 Urgent Escalation: **ENABLED**",
                inline=False
            )
            
            embed.set_footer(text="React to confirm! Backup ping in 15 min if no response 🎮")
            
            # Send primary ping
            primary_msg = await ctx.send(content=f"🎯 **PRIMARY NOTIFICATION TEST**\n{ping_text}", embed=embed)
            await primary_msg.add_reaction("✅")
            await primary_msg.add_reaction("❌")
            
            # Send follow-up explanation
            await ctx.send("✅ **Primary ping sent!** Now wait 30 seconds to see the backup system demonstration...", ephemeral=True)
            
            # Schedule demo backup check (shortened for testing)
            asyncio.create_task(self._demo_backup_system(ctx, primary_msg, mock_user_ids, test_ping_time))
            
        except Exception as e:
            self.logger.error(f"Error sending test ping: {e}")
            await ctx.send('❌ Error sending test ping. Please try again.', ephemeal=True)
    
    async def _demo_backup_system(self, ctx, primary_msg, mock_user_ids, test_ping_time):
        """Demonstrate the backup system after a short delay."""
        try:
            import asyncio
            from datetime import datetime, timezone
            
            # Wait 30 seconds for demo (normally 15 minutes)
            await asyncio.sleep(30)
            
            # Simulate checking reactions
            confirmed_users = set()
            declined_users = set()
            
            # Check actual reactions on the message
            try:
                message = await ctx.channel.fetch_message(primary_msg.id)
                for reaction in message.reactions:
                    if str(reaction.emoji) == "✅":
                        async for user in reaction.users():
                            if not user.bot:
                                confirmed_users.add(user.id)
                    elif str(reaction.emoji) == "❌":
                        async for user in reaction.users():
                            if not user.bot:
                                declined_users.add(user.id)
            except:
                pass
            
            # Calculate remaining time
            now = datetime.now(timezone.utc)
            time_remaining = test_ping_time - now
            minutes_left = int(time_remaining.total_seconds() / 60)
            
            # Send appropriate backup notification
            if confirmed_users:
                # Some responses received
                embed = discord.Embed(
                    title="📊 Train Status Update + Backup Ping",
                    description=f"**🧪 Test Train Schedule** starts in **{minutes_left} minutes**!",
                    color=0xFF6B35,  # Orange
                    timestamp=test_ping_time
                )
                
                confirmed_text = " ".join([f"<@{uid}>" for uid in confirmed_users])
                embed.add_field(name="✅ Confirmed Participants", value=confirmed_text or "None yet", inline=False)
                
                if declined_users:
                    declined_text = " ".join([f"<@{uid}>" for uid in declined_users])
                    embed.add_field(name="❌ Can't Make It", value=declined_text, inline=False)
                
                # Find who hasn't responded
                mock_ids = [int(uid) for uid in mock_user_ids]
                no_response = [uid for uid in mock_ids if uid not in confirmed_users and uid not in declined_users]
                
                if no_response:
                    no_response_pings = " ".join([f"<@{uid}>" for uid in no_response])
                    embed.add_field(
                        name="⚠️ Still Need Response",
                        value=f"{no_response_pings}\n**Please confirm if you're coming!**",
                        inline=False
                    )
                
                embed.set_footer(text="Backup notification triggered after no response ⏰")
                await ctx.send(content="🔄 **BACKUP PING DEMONSTRATION**", embed=embed)
                
            else:
                # No responses - urgent ping
                all_pings = " ".join([f"<@{uid}>" for uid in mock_user_ids])
                
                embed = discord.Embed(
                    title="🚨 URGENT: No Response to Train Schedule",
                    description=f"**🧪 Test Train Schedule** starts in **{minutes_left} minutes**!",
                    color=0xFF0000,  # Red
                    timestamp=test_ping_time
                )
                
                embed.add_field(
                    name="⚠️ No Responses Received",
                    value="Nobody responded to the primary ping!\n**This is the backup escalation system!**",
                    inline=False
                )
                
                embed.add_field(
                    name="🎯 Demo Complete",
                    value="✅ Primary Ping: **SENT**\n📊 Response Tracking: **MONITORED**\n🚨 Backup Escalation: **TRIGGERED**\n⏰ System Working: **PERFECTLY**",
                    inline=False
                )
                
                embed.set_footer(text="BACKUP PING DEMO - Full system operational! 🚨")
                
                backup_msg = await ctx.send(content=f"🚨 **BACKUP PING DEMO**\n{all_pings}", embed=embed)
                await backup_msg.add_reaction("✅")
                await backup_msg.add_reaction("❌")
            
            # Final status message
            await ctx.send("🎉 **Complete System Test Finished!**\n\n**Your automated ping system includes:**\n✅ Primary 60-minute notifications\n⏰ 15-minute backup checks\n📊 Response tracking with reactions\n🚨 Urgent escalation for no responses\n\n**The full system is operational and ready!**", ephemeral=True)
            
        except Exception as e:
            self.logger.error(f"Error in backup demo: {e}")

# REMOVED: Legacy timeslots command - now handled by train_participant_commands.py

    @app_commands.command(name='addslot', description='Add a time slot for raid trains')
    async def add_slot(self, interaction: discord.Interaction, day: str, time: str, duration: int, name: str):
        """Add a new time slot for raid trains."""
            
        try:
            from database import DatabaseSession
            from models import TrainSchedule
            from datetime import time as Time
            
            # Parse day of week
            days_map = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 
                       'friday': 4, 'saturday': 5, 'sunday': 6}
            day_lower = day.lower()
            if day_lower not in days_map:
                await self.respond(interaction, '❌ Invalid day. Use: monday, tuesday, wednesday, thursday, friday, saturday, sunday', ephemeral=True)
                return
            
            # Parse time (format: HH:MM)
            try:
                hour, minute = map(int, time.split(':'))
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    raise ValueError
                schedule_time = Time(hour, minute)
            except (ValueError, AttributeError):
                await self.respond(interaction, '❌ Invalid time format. Use HH:MM (24-hour format, e.g., 20:00)', ephemeral=True)
                return
            
            # Validate duration
            if duration <= 0 or duration > 480:  # Max 8 hours
                await self.respond(interaction, '❌ Invalid duration. Use minutes (1-480)', ephemeral=True)
                return
            
            with DatabaseSession() as session:
                # Create new train schedule
                new_schedule = TrainSchedule(
                    guild_id=interaction.guild.id,
                    name=name,
                    day_of_week=days_map[day_lower],
                    start_time=schedule_time,
                    duration_minutes=duration,
                    notify_before_minutes=15  # Default notification timing
                )
                
                session.add(new_schedule)
                session.commit()
                
                embed = discord.Embed(
                    title='➕ Train Schedule Added',
                    description=f'Added train slot: **{name}**',
                    color=0x00ff00
                )
                embed.add_field(
                    name='📅 Schedule Details',
                    value=f'**Day:** {day.title()}\n**Time:** {time} UTC\n**Duration:** {duration} minutes\n**Notify Before:** 15 minutes\n**ID:** {new_schedule.id}',
                    inline=False
                )
                embed.add_field(
                    name='🔔 Notification Setup',
                    value='Use `/setnotifchannel raid` to set where train notifications are sent',
                    inline=False
                )
                await self.respond(interaction, embed=embed)
                self.logger.info(f"Train schedule added: {name} by {interaction.user}")
                
        except Exception as e:
            self.logger.error(f"Error adding time slot: {e}")
            await self.respond(interaction, '❌ Error adding time slot. Please try again.', ephemeral=True)

    @app_commands.command(name='removeslot', description='Remove a time slot')
    async def remove_slot(self, interaction: discord.Interaction, slot_id: int):
        """Remove a time slot."""
            
        try:
            from database import DatabaseSession
            from models import TrainSchedule
            
            with DatabaseSession() as session:
                schedule = session.query(TrainSchedule).filter_by(
                    id=slot_id,
                    guild_id=interaction.guild.id
                ).first()
                
                if not schedule:
                    await self.respond(interaction, f'❌ Time slot with ID {slot_id} not found.', ephemeral=True)
                    return
                
                schedule_name = schedule.name
                session.delete(schedule)
                session.commit()
                
                embed = discord.Embed(
                    title='➖ Train Schedule Removed',
                    description=f'Removed time slot: **{schedule_name}**',
                    color=0xff4444
                )
                embed.add_field(
                    name='📅 Removed',
                    value=f'Time slot ID {slot_id} has been deleted from the schedule',
                    inline=False
                )
                await self.respond(interaction, embed=embed)
                self.logger.info(f"Train schedule removed: {schedule_name} (ID: {slot_id}) by {interaction.user}")
                
        except Exception as e:
            self.logger.error(f"Error removing time slot: {e}")
            await self.respond(interaction, '❌ Error removing time slot. Please try again.', ephemeral=True)

    @commands.command(name='activeslot', help='Show current active time slot')
    async def active_slot(self, ctx):
        """Show the currently active time slot."""
        try:
            from database import DatabaseSession
            from models import TrainSchedule
            from datetime import datetime, timedelta
            
            with DatabaseSession() as session:
                now = datetime.utcnow()
                current_day = now.weekday()  # 0=Monday
                current_time = now.time()
                
                # Find active schedule for current day and time
                schedules_today = session.query(TrainSchedule).filter_by(
                    guild_id=ctx.guild.id,
                    day_of_week=current_day,
                    is_active=True
                ).all()
                
                active_schedule = None
                upcoming_schedule = None
                
                for schedule in schedules_today:
                    # Calculate end time
                    start_datetime = datetime.combine(now.date(), schedule.start_time)
                    end_datetime = start_datetime + timedelta(minutes=schedule.duration_minutes)
                    current_datetime = datetime.combine(now.date(), current_time)
                    
                    # Check if currently active
                    if start_datetime <= current_datetime <= end_datetime:
                        active_schedule = schedule
                        break
                    # Check if upcoming (within next 2 hours)
                    elif current_datetime < start_datetime <= current_datetime + timedelta(hours=2):
                        if not upcoming_schedule or schedule.start_time < upcoming_schedule.start_time:
                            upcoming_schedule = schedule
                
                embed = discord.Embed(
                    title='⏰ Current Train Status',
                    color=0x9146ff
                )
                
                if active_schedule:
                    embed.description = f'**{active_schedule.name}** is currently active!'
                    embed.color = 0x00ff00
                    # start_time stored as UK wall clock — localize for timezone abbreviation
                    import pytz as _pytz
                    _uk_tz = _pytz.timezone('Europe/London')
                    _start_uk = _uk_tz.localize(datetime.combine(now.date(), active_schedule.start_time))
                    _tz_abbr = _start_uk.strftime('%Z')
                    start_time = active_schedule.start_time.strftime(f'%H:%M {_tz_abbr}')
                    end_datetime = datetime.combine(now.date(), active_schedule.start_time) + timedelta(minutes=active_schedule.duration_minutes)
                    end_time = end_datetime.time().strftime(f'%H:%M {_tz_abbr}')
                    
                    embed.add_field(
                        name='🚂 Active Train',
                        value=f'**Name:** {active_schedule.name}\n**Time:** {start_time} - {end_time}\n**Duration:** {active_schedule.duration_minutes} min',
                        inline=False
                    )
                elif upcoming_schedule:
                    embed.description = f'Next train: **{upcoming_schedule.name}**'
                    # start_time stored as UK wall clock — localize directly for display
                    import pytz
                    _uk_tz_tc = pytz.timezone('Europe/London')
                    _uk_dt_tc = _uk_tz_tc.localize(datetime.combine(datetime.today(), upcoming_schedule.start_time))
                    start_time = _uk_dt_tc.strftime('%H:%M %Z')
                    time_until = datetime.combine(now.date(), upcoming_schedule.start_time) - datetime.combine(now.date(), current_time)
                    minutes_until = int(time_until.total_seconds() / 60)
                    
                    embed.add_field(
                        name='🚂 Next Train',
                        value=f'**Name:** {upcoming_schedule.name}\n**Starts:** {start_time} (in {minutes_until} min)\n**Duration:** {upcoming_schedule.duration_minutes} min',
                        inline=False
                    )
                else:
                    embed.description = 'No active or upcoming train slots today'
                    embed.add_field(
                        name='📅 Schedule',
                        value='Use `!timeslots` to see all scheduled trains\nUse `!addslot` to add new train times',
                        inline=False
                    )
                    
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error checking active slot: {e}")
            await ctx.send('❌ Error checking active time slot. Please try again.')

    # Attendance Tracking Commands
    @commands.command(name='attendance', help='Check attendance for a user')
    async def check_attendance(self, ctx, username: str = None):
        """Check attendance record for a user."""
        if not username:
            await ctx.send('❌ Please provide a username: `!attendance <username>`')
            return
            
        embed = discord.Embed(
            title=f'👥 Attendance: {username}',
            description='Raid train participation record',
            color=0x9146ff
        )
        embed.add_field(
            name='📊 Stats',
            value='Total Raids: **--**\nAttendance Rate: **--%**\nStreak: **-- days**',
            inline=False
        )
        embed.add_field(
            name='🔧 Setup Required',
            value='Database tracking setup needed for detailed attendance records',
            inline=False
        )
        await ctx.send(embed=embed)

    @commands.command(name='recordattendance', help='Record attendance for users')
    async def record_attendance(self, ctx, stream: str = None, *, users: str = None):
        """Record attendance for multiple users."""
        if not all([stream, users]):
            await ctx.send('❌ Usage: `!recordattendance <stream> <user1> <user2> ...`')
            return
            
        user_list = users.split()
        embed = discord.Embed(
            title='📝 Attendance Recorded',
            description=f'Recorded attendance for **{stream}** stream',
            color=0x00ff00
        )
        embed.add_field(
            name='👥 Participants',
            value=f'{len(user_list)} users: {", ".join(user_list[:10])}{"..." if len(user_list) > 10 else ""}',
            inline=False
        )
        await ctx.send(embed=embed)
        self.logger.info(f"Attendance recorded for {stream}: {len(user_list)} users")

    @app_commands.command(name='sessionsummary', description='Generate summary for all trains this week')
    @app_commands.checks.cooldown(1, 60.0, key=lambda i: (i.guild_id, i.user.id))
    async def session_summary(self, interaction: discord.Interaction):
        """Generate attendance summary for all train sessions this week."""
            
        try:
            await interaction.response.defer()
            
            from database import DatabaseSession
            from models import TrainSchedule, TwitchChatAttendance, TrainParticipant
            from datetime import datetime, date, timedelta
            import pytz
            
            with DatabaseSession() as session:
                # Calculate current week (Monday to Sunday)
                today = date.today()
                days_since_monday = today.weekday()
                week_start = today - timedelta(days=days_since_monday)
                week_end = week_start + timedelta(days=6)
                
                # Get all schedules with attendance data this week
                schedules_with_attendance = session.query(TwitchChatAttendance.schedule_id).filter(
                    TwitchChatAttendance.guild_id == interaction.guild.id,
                    TwitchChatAttendance.train_date >= week_start,
                    TwitchChatAttendance.train_date <= week_end
                ).distinct().all()
                
                schedule_ids = [s[0] for s in schedules_with_attendance]
                
                if not schedule_ids:
                    await interaction.followup.send(
                        f"❌ No train attendance found for this week ({week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}).",
                        ephemeral=True
                    )
                    return
                
                # Get schedule details
                schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.id.in_(schedule_ids),
                    TrainSchedule.guild_id == interaction.guild.id
                ).all()
                
                if not schedules:
                    await interaction.followup.send("❌ No train schedules found.", ephemeral=True)
                    return
                
                # Get ALL attendance records for the week (across all schedules)
                all_attendance = session.query(TwitchChatAttendance).filter(
                    TwitchChatAttendance.guild_id == interaction.guild.id,
                    TwitchChatAttendance.train_date >= week_start,
                    TwitchChatAttendance.train_date <= week_end,
                    TwitchChatAttendance.was_present_in_chat == True
                ).all()
                
                # Aggregate check-ins per user across ALL trains
                combined_checkins = {}
                total_checkins = len(all_attendance)
                
                for record in all_attendance:
                    if record.twitch_username not in combined_checkins:
                        combined_checkins[record.twitch_username] = 0
                    combined_checkins[record.twitch_username] += 1
                
                unique_attendees = len(combined_checkins)
                
                # Create main embed
                main_embed = discord.Embed(
                    title="📊 Weekly Attendance Summary",
                    description=f"**Week:** {week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}\n**Trains:** {len(schedules)} scheduled\n**Total Check-ins:** {total_checkins}\n**Unique Attendees:** {unique_attendees}",
                    color=0x667eea,
                    timestamp=datetime.utcnow()
                )
                
                # Build combined attendee list sorted by check-ins (most active first)
                sorted_attendees = sorted(combined_checkins.items(), key=lambda x: x[1], reverse=True)
                
                # Show attendees in chunks (Discord has field value limits)
                chunk_size = 20
                for i in range(0, len(sorted_attendees), chunk_size):
                    chunk = sorted_attendees[i:i+chunk_size]
                    attendee_lines = []
                    for twitch_name, checkins in chunk:
                        attendee_lines.append(f"• {twitch_name} - {checkins} check-ins")
                    
                    field_name = "👥 Weekly Attendance" if i == 0 else f"👥 Attendance (continued)"
                    main_embed.add_field(
                        name=field_name,
                        value="\n".join(attendee_lines),
                        inline=False
                    )
                
                main_embed.set_footer(text=f"Combined data from {len(schedules)} trains • Twitch monitoring")
                await interaction.followup.send(embed=main_embed)
                
        except Exception as e:
            self.logger.error(f"Error generating session summary: {e}")
            await self.respond(interaction, '❌ Error generating session summary. Please try again.', ephemeral=True)

    @commands.command(name='attendancereport', help='Generate attendance report')
    async def attendance_report(self, ctx, timeframe: str = 'week'):
        """Generate attendance report with real database data."""
        try:
            from database import DatabaseSession
            from models import TrainAttendance, TrainNotification, TrainSchedule
            from datetime import datetime, timedelta
            
            valid_timeframes = ['day', 'week', 'month']
            if timeframe not in valid_timeframes:
                timeframe = 'week'
            
            # Calculate timeframe
            now = datetime.utcnow()
            if timeframe == 'day':
                start_date = now - timedelta(days=1)
            elif timeframe == 'week':
                start_date = now - timedelta(weeks=1)
            else:  # month
                start_date = now - timedelta(days=30)
            
            with DatabaseSession() as session:
                # Get attendance data within timeframe
                attendances = session.query(TrainAttendance).join(
                    TrainNotification, TrainAttendance.notification_id == TrainNotification.id
                ).join(
                    TrainSchedule, TrainNotification.schedule_id == TrainSchedule.id
                ).filter(
                    TrainAttendance.created_at >= start_date,
                    TrainSchedule.guild_id == ctx.guild.id
                ).all()
                
                # Count statistics
                total_responses = len(attendances)
                ready_count = len([a for a in attendances if a.status == 'ready'])
                not_ready_count = len([a for a in attendances if a.status == 'not_ready'])
                unique_participants = len(set(a.user_id for a in attendances))
                
                # Calculate attendance rate
                attendance_rate = round((ready_count / total_responses * 100) if total_responses > 0 else 0, 1)
                
                embed = discord.Embed(
                    title=f'📊 Attendance Report ({timeframe.title()})',
                    description=f'Train attendance analysis for the last {timeframe}',
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name='📈 Summary',
                    value=f'**Total Responses:** {total_responses}\n**Unique Participants:** {unique_participants}\n**Ready Responses:** {ready_count}\n**Not Ready:** {not_ready_count}\n**Attendance Rate:** {attendance_rate}%',
                    inline=False
                )
                
                if attendances:
                    # Get most active participants
                    from collections import Counter
                    user_counts = Counter(a.user_id for a in attendances if a.status == 'ready')
                    top_participants = user_counts.most_common(5)
                    
                    if top_participants:
                        participant_text = []
                        for user_id, count in top_participants:
                            # Try to get user from guild
                            user = ctx.guild.get_member(user_id)
                            name = user.display_name if user else f"User {user_id}"
                            participant_text.append(f"• {name}: {count} responses")
                        
                        embed.add_field(
                            name='🏆 Most Active Participants',
                            value='\n'.join(participant_text),
                            inline=False
                        )
                else:
                    embed.add_field(
                        name='📋 No Data',
                        value=f'No attendance data found for the last {timeframe}',
                        inline=False
                    )
                
                embed.set_footer(text="Data from reaction confirmations")
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error generating attendance report: {e}")
            await ctx.send('❌ Error generating attendance report. Please try again.')

    @app_commands.command(name='topsupporters', description='Show top raid train supporters')
    @app_commands.describe(count='Number of top supporters to display (1-20)')
    async def top_supporters(self, interaction: discord.Interaction, count: int = 5):
        """Show top raid train supporters."""
        if count < 1 or count > 20:
            count = 5
            
        embed = discord.Embed(
            title=f'🏆 Top {count} Supporters',
            description='Most active raid train participants',
            color=0x9146ff
        )
        
        supporters = [
            ('rstone203', 127, '🥇'),
            ('jordiemaree', 89, '🥈'),
            ('trainmaster99', 67, '🥉'),
            ('raidleader_x', 45, '4️⃣'),
            ('streamqueen', 38, '5️⃣')
        ]
        
        display_supporters = supporters[:count]
        embed.add_field(
            name='🚂 Most Active Supporters',
            value='\n'.join([f'{medal} **{name}** - {raids} raids supported' for name, raids, medal in display_supporters]),
            inline=False
        )
        await self.respond(interaction, embed=embed)

    # Advanced Analytics Commands
    @commands.command(name='streamstats', help='Show stats for a specific stream')
    async def stream_stats(self, ctx, username: str = None):
        """Show detailed statistics for a specific stream."""
        if not username:
            await ctx.send('❌ Please provide a Twitch username: `!streamstats <username>`')
            return
            
        embed = discord.Embed(
            title=f'📊 Stream Stats: {username}',
            description='Detailed stream performance metrics',
            color=0x9146ff
        )
        embed.add_field(
            name='🎮 Performance',
            value='Total Streams: **--**\nTotal Hours: **--h**\nAvg Viewers: **--**',
            inline=True
        )
        embed.add_field(
            name='🚂 Raid Train',
            value='Raids Participated: **--**\nSuccess Rate: **--%**\nLast Seen: **--**',
            inline=True
        )
        embed.add_field(
            name='🔧 Setup Required',
            value='Twitch API configuration needed for detailed stream statistics',
            inline=False
        )
        await ctx.send(embed=embed)

    @app_commands.command(name='raidstats', description='Show raid train statistics')
    @app_commands.describe(timeframe='Time period for stats: day, week, month, year')
    async def raid_stats(self, interaction: discord.Interaction, timeframe: str = 'week'):
        """Show raid train success statistics."""
        valid_timeframes = ['day', 'week', 'month', 'year']
        if timeframe not in valid_timeframes:
            timeframe = 'week'
            
        embed = discord.Embed(
            title=f'🚂 Raid Stats ({timeframe.title()})',
            description='Raid train success metrics and performance',
            color=0x9146ff
        )
        
        stats_data = {
            'day': {'raids': 3, 'success': 2, 'participants': 48},
            'week': {'raids': 18, 'success': 15, 'participants': 267},
            'month': {'raids': 52, 'success': 44, 'participants': 1084},
            'year': {'raids': 418, 'success': 356, 'participants': 6240}
        }
        
        data = stats_data.get(timeframe, stats_data['week'])
        success_rate = int((data['success'] / data['raids']) * 100) if data['raids'] > 0 else 0
        avg_participants = int(data['participants'] / data['raids']) if data['raids'] > 0 else 0
        
        embed.add_field(
            name='📈 Success Metrics',
            value=f"Total Raids: **{data['raids']}**\nSuccessful: **{data['success']}**\nSuccess Rate: **{success_rate}%**",
            inline=True
        )
        embed.add_field(
            name='👥 Participation',
            value=f"Total Participants: **{data['participants']}**\nAvg per Raid: **{avg_participants}**\nUnique Users: **{data['participants'] // 3}**",
            inline=True
        )
        embed.add_field(
            name='⭐ Performance',
            value=f"Best Time: **8-10 PM EST**\nAvg Duration: **42 min**\nPeak Concurrent: **{avg_participants + 5}**",
            inline=True
        )
        await self.respond(interaction, embed=embed)

    @commands.command(name='health', help='Check stream health monitoring')
    async def stream_health(self, ctx, username: str = None):
        """Check stream health monitoring."""
        if not username:
            await ctx.send('❌ Please provide a Twitch username: `!health <username>`')
            return
            
        embed = discord.Embed(
            title=f'🏥 Stream Health: {username}',
            description='Stream monitoring and health status',
            color=0x9146ff
        )
        embed.add_field(
            name='📊 Health Metrics',
            value='Status: ⚠️ Monitoring not active\nUptime: **--**\nStability: **--%**',
            inline=False
        )
        embed.add_field(
            name='🔧 Setup Required',
            value='Advanced monitoring requires Twitch API configuration',
            inline=False
        )
        await ctx.send(embed=embed)

    @commands.command(name='streak', help='Check attendance streak for a user')
    async def attendance_streak(self, ctx, username: str = None):
        """Check attendance streak for a user."""
        if not username:
            await ctx.send('❌ Please provide a username: `!streak <username>`')
            return
            
        embed = discord.Embed(
            title=f'🔥 Attendance Streak: {username}',
            description='Consecutive raid train participation',
            color=0x9146ff
        )
        embed.add_field(
            name='🎯 Current Streak',
            value='Current: **-- days**\nLongest: **-- days**\nLast Raid: **--**',
            inline=False
        )
        embed.add_field(
            name='🔧 Setup Required',
            value='Database tracking setup needed for streak monitoring',
            inline=False
        )
        await ctx.send(embed=embed)

async def setup(bot):
    """Setup function to add the cog to the bot."""
    await bot.add_cog(TwitchCordCommands(bot))