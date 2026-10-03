"""
Commands for setting up the complete train schedule system.
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, time, timedelta
import pytz
from database import DatabaseSession
from models import TrainSchedule, NotificationSettings
from utils.schedule_cache import invalidate_schedule_cache

UK_TZ = pytz.timezone("Europe/London")

class ScheduleSetupCommands(commands.Cog):
    """Commands for setting up train schedules."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    def is_admin_or_trusted():
        """Check if user has manage_guild permission OR is a trusted user."""
        async def predicate(ctx):
            # Check manage_guild permission first
            if ctx.author.guild_permissions.manage_guild:
                return True
            # Check if user is trusted, scoped to the current guild so that
            # the guild-level trusted_access_enabled flag is enforced.
            guild_id = ctx.guild.id if ctx.guild else None
            return await ctx.bot.is_trusted_user(ctx.author.id, guild_id)
        return commands.check(predicate)

    def get_next_occurrence_date(self, day_of_week):
        """
        Calculate the next occurrence date for a given day of the week.
        This ensures timezone conversions use the correct DST offset.
        
        Args:
            day_of_week (int): 0=Monday, 1=Tuesday, ..., 6=Sunday
            
        Returns:
            datetime.date: The next occurrence date
        """
        from datetime import date, timedelta
        today = date.today()
        today_weekday = today.weekday()  # 0=Monday, 6=Sunday
        
        # Calculate days until next occurrence
        days_ahead = day_of_week - today_weekday
        if days_ahead <= 0:  # Target day already happened this week
            days_ahead += 7
        
        next_occurrence = today + timedelta(days=days_ahead)
        return next_occurrence

    def parse_time_input(self, time_str, day_of_week=None):
        """
        Parse various time input formats and convert to UTC time object.
        
        Args:
            time_str: Time string to parse (e.g., "3pm", "15:00", "10:30am")
            day_of_week: Optional day of week (0-6) for DST-aware conversion
                        If provided, uses next occurrence of that day for conversion
                        If None, uses today's date (legacy behavior)
        """
        time_str = time_str.replace(' ', '').lower()
        
        # Handle am/pm formats
        if 'am' in time_str or 'pm' in time_str:
            is_pm = 'pm' in time_str
            time_str = time_str.replace('am', '').replace('pm', '')
            
            # Handle formats like "10:30" or "10"
            if ':' in time_str:
                parts = time_str.split(':')
                if len(parts) != 2:
                    return None
                try:
                    hour = int(parts[0])
                    minute = int(parts[1])
                except ValueError:
                    return None
            else:
                try:
                    hour = int(time_str)
                    minute = 0
                except ValueError:
                    return None
            
            # Convert to 24-hour format
            if is_pm and hour != 12:
                hour += 12
            elif not is_pm and hour == 12:
                hour = 0
                
        else:
            # 24-hour format
            if ':' in time_str:
                parts = time_str.split(':')
                if len(parts) != 2:
                    return None
                try:
                    hour = int(parts[0])
                    minute = int(parts[1])
                except ValueError:
                    return None
            else:
                try:
                    hour = int(time_str)
                    minute = 0
                except ValueError:
                    return None
        
        # Validate time
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
            
        # Return the time as UK wall clock time (no UTC conversion).
        # Times are stored as-entered (UK local), and localized against the actual
        # schedule date at notification time so DST is handled automatically.
        return time(hour, minute)

    @is_admin_or_trusted()
    @commands.guild_only()
    @commands.command(name='setuptrainschedule', help='Set up custom train schedule: !setuptrainschedule <day> [slots] [duration] [start_time] [weeks_ahead]')
    async def setup_train_schedule(self, ctx, day_name: str = "", num_slots: int = 9, duration_minutes: int = 90, start_time: str = "10:00am", weeks_ahead: int = 0):
        """Set up a customizable train schedule for a specific day.
        
        Args:
            day_name: Day of the week (monday, tuesday, etc.)
            num_slots: Number of train slots to create (default: 9)
            duration_minutes: Duration of each slot in minutes (default: 90)
            start_time: Start time in UK time (e.g., '10:00am', '2:30pm') (default: '10:00am')
            weeks_ahead: Create schedules X weeks in the future (0=this week, 1=next week, etc.) (default: 0)
        """
        if not day_name:
            await ctx.send("❌ **Usage:** `!setuptrainschedule <day> [slots] [duration] [start_time] [weeks_ahead]`\n\n**Examples:**\n• `!setuptrainschedule monday` - 9 slots, 90min each, starting 10am THIS week\n• `!setuptrainschedule saturday 9 90 10:00am 1` - 9 slots starting 10am NEXT week\n• `!setuptrainschedule saturday 12 60 9:00am` - 12 slots, 60min each, starting 9am THIS week\n• `!setuptrainschedule sunday 6 120 2:00pm 2` - 6 slots starting 2pm in 2 weeks\n\n**Available days:** monday, tuesday, wednesday, thursday, friday, saturday, sunday")
            return
            
        # Convert day name to day_of_week number
        days_map = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        
        day_name_lower = day_name.lower()
        if day_name_lower not in days_map:
            await ctx.send("❌ Invalid day. Use: monday, tuesday, wednesday, thursday, friday, saturday, sunday")
            return
            
        day_of_week = days_map[day_name_lower]
        
        # Validate parameters
        if num_slots < 1 or num_slots > 24:
            await ctx.send("❌ Number of slots must be between 1 and 24")
            return
            
        if duration_minutes < 15 or duration_minutes > 480:
            await ctx.send("❌ Duration must be between 15 minutes and 8 hours (480 minutes)")
            return
        
        if weeks_ahead < 0 or weeks_ahead > 4:
            await ctx.send("❌ weeks_ahead must be between 0 and 4 (up to 4 weeks in the future)")
            return
            
        # Parse start time (DST-aware using next occurrence of this day)
        parsed_start_time = self.parse_time_input(start_time, day_of_week)
        if not parsed_start_time:
            await ctx.send("❌ Invalid start time format. Examples: `10:00am`, `2:30pm`, `14:30`, `7pm`")
            return
        
        try:
            with DatabaseSession() as session:
                # Remove existing schedules for this day
                existing = session.query(TrainSchedule).filter_by(
                    guild_id=ctx.guild.id,
                    day_of_week=day_of_week
                ).all()
                
                for schedule in existing:
                    setattr(schedule, 'is_active', False)
                
                # Use default 1-hour notification timing for backup system
                notify_before = 60
                
                # Create customizable train schedule slots
                created_schedules = []
                current_time = parsed_start_time
                
                for i in range(num_slots):
                    # Generate slot name
                    slot_name = f"Train Slot {i+1}"
                    if i == 0:
                        slot_name = "Opening Train"
                    elif i == num_slots - 1:
                        slot_name = "Final Train"
                    elif num_slots > 6:  # More descriptive names for longer schedules
                        if i < num_slots // 3:
                            slot_name = f"Morning Train {i+1}"
                        elif i < (2 * num_slots) // 3:
                            slot_name = f"Afternoon Train {i+1}"
                        else:
                            slot_name = f"Evening Train {i+1}"
                    
                    # Handle day rollover: if a slot's UK wall-clock time crosses midnight
                    # (i.e. wrapped around to 0-4 AM) it belongs to the next calendar day.
                    actual_day = day_of_week
                    if current_time.hour < 5:
                        actual_day = (day_of_week + 1) % 7
                    
                    # Use the pre-calculated notification timing
                    
                    # Add week offset to description if applicable
                    description_text = f"{duration_minutes}-minute raid train slot"
                    if weeks_ahead > 0:
                        week_text = "next week" if weeks_ahead == 1 else f"{weeks_ahead} weeks ahead"
                        description_text += f" [WEEK_OFFSET:{weeks_ahead}]"
                    
                    schedule = TrainSchedule(
                        guild_id=ctx.guild.id,
                        name=slot_name,
                        day_of_week=actual_day,
                        start_time=current_time,
                        duration_minutes=duration_minutes,
                        notify_before_minutes=notify_before,
                        is_active=True,
                        description=description_text,
                        max_participants=1  # Only 1 person per slot
                    )
                    
                    session.add(schedule)
                    created_schedules.append(schedule)
                    
                    # Calculate next slot time
                    next_time = datetime.combine(datetime.today(), current_time) + timedelta(minutes=duration_minutes)
                    current_time = next_time.time()
                
                session.commit()
                
                # Invalidate schedule cache for this guild
                invalidate_schedule_cache(ctx.guild.id)
                
                # Update notification settings for 25-minute intervals and 3-message limit
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=ctx.guild.id
                ).first()
                
                if not settings:
                    settings = NotificationSettings(
                        guild_id=ctx.guild.id,
                        auto_ping_enabled=True,
                        backup_system_enabled=True,
                        ping_timeout_minutes=60  # 1-hour notification window for backup system
                    )
                    session.add(settings)
                else:
                    # Use setattr for proper SQLAlchemy attribute assignment
                    setattr(settings, 'ping_timeout_minutes', 60)  # 1-hour notification window for backup system
                    setattr(settings, 'auto_ping_enabled', True)
                
                session.commit()
                
                # Invalidate schedule cache for this guild (after notification settings update)
                invalidate_schedule_cache(ctx.guild.id)
                
                # Build title with week offset info
                week_info = ""
                if weeks_ahead == 1:
                    week_info = " (Next Week)"
                elif weeks_ahead > 1:
                    week_info = f" ({weeks_ahead} Weeks Ahead)"
                
                embed = discord.Embed(
                    title=f"🚂 Custom Train Schedule Created{week_info}!",
                    description=f"Created **{len(created_schedules)} train slots** for **{day_name.title()}**{week_info}",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                # Show created schedules
                import pytz
                uk_tz_display = pytz.timezone('Europe/London')
                schedule_list = []
                for i, schedule in enumerate(created_schedules, 1):
                    # Times are stored as UK wall clock time - localize against today for DST-aware display
                    naive_dt = datetime.combine(datetime.today(), schedule.start_time)
                    uk_dt = uk_tz_display.localize(naive_dt)
                    
                    uk_time = uk_dt.strftime('%H:%M')
                    
                    # Calculate end time using actual duration
                    end_datetime_uk = uk_dt + timedelta(minutes=schedule.duration_minutes)
                    uk_end = end_datetime_uk.strftime('%H:%M')
                    
                    tz_abbr = uk_dt.strftime('%Z')  # GMT or BST
                    schedule_list.append(f"**{i}.** {schedule.name} - {uk_time}-{uk_end} {tz_abbr}")
                
                embed.add_field(
                    name="📅 Train Slots Created",
                    value="\n".join(schedule_list),
                    inline=False
                )
                
                # Calculate total coverage time
                total_duration_minutes = num_slots * duration_minutes
                total_hours = total_duration_minutes // 60
                total_minutes = total_duration_minutes % 60
                
                # Format start and end times for display (stored as UK wall clock time)
                naive_start_dt = datetime.combine(datetime.today(), parsed_start_time)
                uk_start_dt = uk_tz_display.localize(naive_start_dt)
                start_time_est = uk_start_dt.strftime('%H:%M')
                
                uk_end_dt = uk_start_dt + timedelta(minutes=total_duration_minutes)
                end_time_est = uk_end_dt.strftime('%H:%M')
                
                tz_abbr = uk_start_dt.strftime('%Z')  # GMT or BST
                
                embed.add_field(
                    name="⚙️ Schedule Settings",
                    value=f"• **Slots:** {num_slots} train slots\n• **Duration:** {duration_minutes} minutes per slot\n• **Attendance:** Every {notify_before} minutes\n• **Total Time:** {total_hours}h {total_minutes}m\n• **Coverage:** {start_time_est} - {end_time_est} {tz_abbr}",
                    inline=False
                )
                
                embed.add_field(
                    name="🎯 Next Steps",
                    value="• Use `!timeslots` to view all slots\n• Use `!jointrain <id>` to join trains\n• Use `!setupnotifications` to configure channels",
                    inline=False
                )
                
                embed.set_footer(text=f"Schedule ID range: {created_schedules[0].id}-{created_schedules[-1].id}")
                
                await ctx.send(embed=embed)
                self.logger.info(f"Created {len(created_schedules)} train schedule slots for {day_name} by {ctx.author}")
                
        except Exception as e:
            self.logger.error(f"Error setting up train schedule: {e}")
            await ctx.send(f"❌ Failed to set up train schedule: {str(e)}")

    @is_admin_or_trusted()
    @commands.guild_only()
    @commands.command(name='setupweekschedule', help='Set up train schedule for entire week')
    async def setup_week_schedule(self, ctx):
        """Set up the complete train schedule for the entire week."""
        try:
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            total_created = 0
            
            embed = discord.Embed(
                title="🚂 Setting Up Weekly Train Schedule...",
                description="Creating train slots for all 7 days",
                color=0xffa500
            )
            await ctx.send(embed=embed)
            
            with DatabaseSession() as session:
                # Clear existing schedules
                existing = session.query(TrainSchedule).filter_by(guild_id=ctx.guild.id).all()
                for schedule in existing:
                    setattr(schedule, 'is_active', False)
                
                # Create schedules for each day
                for day_idx, day_name in enumerate(days):
                    slots = [
                        ("Morning Train", time(15, 0)),      # 10:00am EST
                        ("Late Morning", time(16, 30)),      # 11:30am EST  
                        ("Early Afternoon", time(18, 0)),    # 1:00pm EST
                        ("Mid Afternoon", time(19, 30)),     # 2:30pm EST
                        ("Late Afternoon", time(21, 0)),     # 4:00pm EST
                        ("Early Evening", time(22, 30)),     # 5:30pm EST
                        ("Prime Time 1", time(0, 0)),        # 7:00pm EST (next day UTC)
                        ("Prime Time 2", time(1, 30)),       # 8:30pm EST (next day UTC)
                        ("Night Train", time(3, 0)),         # 10:00pm EST (next day UTC)
                    ]
                    
                    for slot_name, start_time in slots:
                        # Handle day rollover for late slots
                        actual_day = day_idx
                        if start_time.hour < 5:  # Overnight UK slot (before 5 AM UK) rolls to next day
                            actual_day = (day_idx + 1) % 7
                        
                        schedule = TrainSchedule(
                            guild_id=ctx.guild.id,
                            name=f"{day_name.title()} {slot_name}",
                            day_of_week=actual_day,
                            start_time=start_time,
                            duration_minutes=90,
                            notify_before_minutes=25,
                            is_active=True,
                            description=f"90-minute raid train slot with 25-minute attendance intervals",
                            max_participants=1  # Only 1 person per slot
                        )
                        
                        session.add(schedule)
                        total_created += 1
                
                # Set up notification settings
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=ctx.guild.id
                ).first()
                
                if not settings:
                    settings = NotificationSettings(
                        guild_id=ctx.guild.id,
                        auto_ping_enabled=True,
                        backup_system_enabled=True,
                        ping_timeout_minutes=25
                    )
                    session.add(settings)
                else:
                    # Use setattr for proper SQLAlchemy attribute assignment
                    setattr(settings, 'ping_timeout_minutes', 25)
                    setattr(settings, 'auto_ping_enabled', True)
                
                session.commit()
                
                embed = discord.Embed(
                    title="✅ Weekly Train Schedule Complete!",
                    description=f"Successfully created **{total_created} train slots** across **7 days**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📊 Schedule Summary",
                    value=f"• **{len(days)} days** covered\n• **9 slots per day** (90 minutes each)\n• **{total_created} total slots** created\n• **10:00am - 11:30pm EST** daily coverage",
                    inline=False
                )
                
                embed.add_field(
                    name="⚙️ Attendance Settings",
                    value="• **Interval:** Every 25 minutes\n• **Messages:** 3 per stream maximum\n• **Auto-ping:** Enabled\n• **Backup system:** Enabled",
                    inline=False
                )
                
                embed.add_field(
                    name="🎯 Quick Commands",
                    value="`!timeslots` - View all train times\n`!jointrain <id>` - Join a train\n`!mytrains` - Your signed up trains\n`!trainroster <id>` - See participants",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Created weekly train schedule ({total_created} slots) by {ctx.author}")
                
        except Exception as e:
            self.logger.error(f"Error setting up weekly schedule: {e}")
            await ctx.send(f"❌ Failed to set up weekly schedule: {str(e)}")

    @is_admin_or_trusted()
    @commands.guild_only()
    @commands.command(name='trainconfig', help='Configure train notification settings')
    async def train_config(self, ctx, max_messages: int = 3, interval_minutes: int = 25):
        """Configure train notification settings."""
        try:
            with DatabaseSession() as session:
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=ctx.guild.id
                ).first()
                
                if not settings:
                    settings = NotificationSettings(
                        guild_id=ctx.guild.id,
                        auto_ping_enabled=True,
                        backup_system_enabled=True,
                        ping_timeout_minutes=interval_minutes
                    )
                    session.add(settings)
                else:
                    # Use setattr for proper SQLAlchemy attribute assignment
                    setattr(settings, 'ping_timeout_minutes', interval_minutes)
                    setattr(settings, 'auto_ping_enabled', True)
                
                session.commit()
                
                embed = discord.Embed(
                    title="⚙️ Train Configuration Updated",
                    description="Notification settings have been configured",
                    color=0x667eea,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📊 Current Settings",
                    value=f"• **Max messages per stream:** {max_messages}\n• **Attendance interval:** {interval_minutes} minutes\n• **Auto-ping:** Enabled\n• **Backup system:** Enabled",
                    inline=False
                )
                
                embed.add_field(
                    name="📝 How It Works",
                    value=f"During each 90-minute train slot:\n• Attendance notifications every {interval_minutes} minutes\n• Maximum {max_messages} messages per stream\n• Automatic backup streamer escalation",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Updated train config: {interval_minutes}min intervals, {max_messages} max messages by {ctx.author}")
                
        except Exception as e:
            self.logger.error(f"Error configuring train settings: {e}")
            await ctx.send(f"❌ Failed to configure settings: {str(e)}")

    # ── /setupattendance ──────────────────────────────────────────────────────

    @app_commands.command(
        name="setupattendance",
        description="Set an attendance channel for this server (no ping channels required)"
    )
    @app_commands.describe(
        attendance_channel="Channel where per-session attendance reports are posted",
        report_channel="Channel where daily/weekend summary reports are posted (optional, defaults to attendance channel)",
    )
    @app_commands.guild_only()
    async def setup_attendance(
        self,
        interaction: discord.Interaction,
        attendance_channel: discord.TextChannel,
        report_channel: discord.TextChannel = None,
    ):
        """Configure attendance tracking without requiring ping channels."""
        guild_id = interaction.guild.id if interaction.guild else None
        if not (
            interaction.user.guild_permissions.manage_guild
            or interaction.user.guild_permissions.administrator
            or await self.bot.is_trusted_user(interaction.user.id, guild_id)
        ):
            await interaction.response.send_message(
                "❌ You need Manage Server permission.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        report_ch = report_channel or attendance_channel

        try:
            with DatabaseSession() as session:
                settings = (
                    session.query(NotificationSettings)
                    .filter_by(guild_id=interaction.guild_id)
                    .first()
                )
                if not settings:
                    settings = NotificationSettings(
                        guild_id=interaction.guild_id,
                        auto_ping_enabled=False,
                        backup_system_enabled=False,
                        ping_timeout_minutes=60,
                        attendance_channel_id=attendance_channel.id,
                        comprehensive_report_channel_id=report_ch.id,
                        comprehensive_reports_enabled=True,
                    )
                    session.add(settings)
                else:
                    settings.attendance_channel_id = attendance_channel.id
                    settings.comprehensive_report_channel_id = report_ch.id
                    settings.comprehensive_reports_enabled = True
                session.commit()

                # Count existing active schedules for this guild
                schedule_count = (
                    session.query(TrainSchedule)
                    .filter_by(guild_id=interaction.guild_id, is_active=True)
                    .count()
                )

            embed = discord.Embed(
                title="✅ Attendance Tracking Set Up!",
                color=0x2ecc71,
            )
            embed.add_field(
                name="📋 Attendance Channel",
                value=attendance_channel.mention,
                inline=True,
            )
            embed.add_field(
                name="📊 Report Channel",
                value=report_ch.mention,
                inline=True,
            )
            embed.add_field(
                name="🔔 Pings",
                value="Not required — attendance tracked via Twitch chat monitoring",
                inline=False,
            )

            if schedule_count == 0:
                embed.add_field(
                    name="⚠️ No Schedules Yet",
                    value=(
                        "You don't have any train schedules yet.\n"
                        "Run `!setuptrainschedule <day> [slots] [duration] [start_time]`\n"
                        "**Example:** `!setuptrainschedule saturday 9 90 10:00am`\n\n"
                        "Or use `/createschedule` for a guided slash-command version."
                    ),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="📅 Schedules Found",
                    value=f"{schedule_count} active schedule slot(s) already set up. "
                          "Attendance will be tracked when trains run.",
                    inline=False,
                )

            embed.set_footer(text="Use /sheetsync setup to also sync to Google Sheets")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            self.logger.error(f"setupattendance error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Failed to save settings: {e}")

    # ── /createschedule ──────────────────────────────────────────────────────

    @app_commands.command(
        name="createschedule",
        description="Create train schedule slots for a day (slash command version)"
    )
    @app_commands.describe(
        day="Day of the week (e.g. saturday, sunday)",
        slots="Number of train slots (default 9)",
        duration="Duration of each slot in minutes (default 90)",
        start_time="UK start time e.g. 10:00am, 14:30 (default 10:00am)",
        weeks_ahead="0 = this week, 1 = next week, etc. (default 0)",
    )
    @app_commands.choices(day=[
        app_commands.Choice(name="Monday",    value="monday"),
        app_commands.Choice(name="Tuesday",   value="tuesday"),
        app_commands.Choice(name="Wednesday", value="wednesday"),
        app_commands.Choice(name="Thursday",  value="thursday"),
        app_commands.Choice(name="Friday",    value="friday"),
        app_commands.Choice(name="Saturday",  value="saturday"),
        app_commands.Choice(name="Sunday",    value="sunday"),
    ])
    @app_commands.guild_only()
    async def create_schedule(
        self,
        interaction: discord.Interaction,
        day: str,
        slots: int = 9,
        duration: int = 90,
        start_time: str = "10:00am",
        weeks_ahead: int = 0,
    ):
        """Slash command wrapper around !setuptrainschedule."""
        guild_id = interaction.guild.id if interaction.guild else None
        if not (
            interaction.user.guild_permissions.manage_guild
            or interaction.user.guild_permissions.administrator
            or await self.bot.is_trusted_user(interaction.user.id, guild_id)
        ):
            await interaction.response.send_message(
                "❌ You need Manage Server permission.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        # Validate inputs
        days_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        day_of_week = days_map.get(day.lower())
        if day_of_week is None:
            await interaction.followup.send("❌ Invalid day.")
            return
        if not (1 <= slots <= 24):
            await interaction.followup.send("❌ Slots must be between 1 and 24.")
            return
        if not (15 <= duration <= 480):
            await interaction.followup.send("❌ Duration must be between 15 and 480 minutes.")
            return
        if not (0 <= weeks_ahead <= 4):
            await interaction.followup.send("❌ weeks_ahead must be 0–4.")
            return

        parsed_time = self.parse_time_input(start_time, day_of_week)
        if not parsed_time:
            await interaction.followup.send(
                "❌ Invalid start time. Examples: `10:00am`, `14:30`, `7pm`"
            )
            return

        try:
            from datetime import date

            # Calculate the specific date for the schedule
            today = date.today()
            days_ahead_delta = day_of_week - today.weekday()
            if days_ahead_delta <= 0:
                days_ahead_delta += 7
            schedule_date = today + timedelta(days=days_ahead_delta + (weeks_ahead * 7))

            with DatabaseSession() as session:
                # Deactivate existing schedules for this day in this guild
                existing = (
                    session.query(TrainSchedule)
                    .filter_by(guild_id=interaction.guild_id, day_of_week=day_of_week)
                    .all()
                )
                for s in existing:
                    s.is_active = False

                created = []
                current_t = parsed_time

                for i in range(slots):
                    if i == 0:
                        slot_name = "Opening Train"
                    elif i == slots - 1:
                        slot_name = "Final Train"
                    elif slots > 6:
                        if i < slots // 3:
                            slot_name = f"Morning Train {i+1}"
                        elif i < (2 * slots) // 3:
                            slot_name = f"Afternoon Train {i+1}"
                        else:
                            slot_name = f"Evening Train {i+1}"
                    else:
                        slot_name = f"Train Slot {i+1}"

                    actual_day = day_of_week
                    if current_t.hour < 5:
                        actual_day = (day_of_week + 1) % 7

                    sched = TrainSchedule(
                        guild_id=interaction.guild_id,
                        name=slot_name,
                        day_of_week=actual_day,
                        start_time=current_t,
                        duration_minutes=duration,
                        notify_before_minutes=60,
                        is_active=True,
                        description=f"{duration}-minute raid train slot",
                        max_participants=1,
                        specific_date=schedule_date,
                        schedule_type="one-time",
                    )
                    session.add(sched)
                    created.append(sched)

                    next_dt = datetime.combine(datetime.today(), current_t) + timedelta(minutes=duration)
                    current_t = next_dt.time()

                # Ensure notification settings exist (attendance only, no pings)
                ns = session.query(NotificationSettings).filter_by(guild_id=interaction.guild_id).first()
                if not ns:
                    ns = NotificationSettings(
                        guild_id=interaction.guild_id,
                        auto_ping_enabled=False,
                        backup_system_enabled=False,
                        ping_timeout_minutes=60,
                    )
                    session.add(ns)

                session.commit()
                invalidate_schedule_cache(interaction.guild_id)

                # Build display
                schedule_list = []
                for i, s in enumerate(created, 1):
                    naive_dt = datetime.combine(datetime.today(), s.start_time)
                    uk_dt = UK_TZ.localize(naive_dt)
                    end_dt = uk_dt + timedelta(minutes=s.duration_minutes)
                    tz_abbr = uk_dt.strftime("%Z")
                    schedule_list.append(
                        f"**{i}.** {s.name} — {uk_dt.strftime('%H:%M')}–{end_dt.strftime('%H:%M')} {tz_abbr}"
                    )

                week_label = ""
                if weeks_ahead == 1:
                    week_label = " (next week)"
                elif weeks_ahead > 1:
                    week_label = f" ({weeks_ahead} weeks ahead)"

                embed = discord.Embed(
                    title=f"✅ Schedule Created — {day.title()}{week_label}",
                    description=(
                        f"**{slots}** slot(s) · {duration} min each · "
                        f"Date: **{schedule_date.strftime('%d/%m/%Y')}**"
                    ),
                    color=0x2ecc71,
                )
                # Show first 15 slots to avoid embed length limit
                embed.add_field(
                    name="📅 Train Slots",
                    value="\n".join(schedule_list[:15]) + (
                        f"\n…and {len(schedule_list)-15} more" if len(schedule_list) > 15 else ""
                    ),
                    inline=False,
                )

                has_attendance = ns and ns.attendance_channel_id
                next_steps = []
                if not has_attendance:
                    next_steps.append(
                        "• Run `/setupattendance` to set an attendance reporting channel"
                    )
                next_steps.append("• Use `/addtotrain` to add participants")
                next_steps.append("• Use `/sheetsync setup` to sync to Google Sheets")
                embed.add_field(
                    name="🎯 Next Steps",
                    value="\n".join(next_steps),
                    inline=False,
                )
                embed.set_footer(text=f"IDs: {created[0].id}–{created[-1].id}")
                await interaction.followup.send(embed=embed)

        except Exception as e:
            self.logger.error(f"createschedule error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Failed to create schedule: {e}")


async def setup(bot):
    await bot.add_cog(ScheduleSetupCommands(bot))