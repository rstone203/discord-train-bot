"""
Interactive schedule creation commands with step-by-step prompts.
"""

import logging
import discord
import asyncio
import pytz
from discord.ext import commands
from datetime import datetime, time, timedelta
from database import DatabaseSession
from models import TrainSchedule

class InteractiveScheduleCommands(commands.Cog):
    """Interactive commands for creating train schedules step by step."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        # Store ongoing schedule creation sessions
        self.schedule_sessions = {}

    def is_admin_or_trusted():
        """Check if user is bot owner, has manage_guild permission, OR is a trusted user."""
        async def predicate(ctx):
            # Check if user is bot owner first
            if await ctx.bot.is_owner(ctx.author):
                return True
            # Check manage_guild permission
            if ctx.author.guild_permissions.manage_guild:
                return True
            # Check if user is trusted (pass guild_id for role-based check)
            guild_id = ctx.guild.id if ctx.guild else None
            return await ctx.bot.is_trusted_user(ctx.author.id, guild_id)
        return commands.check(predicate)

    @is_admin_or_trusted()
    @commands.guild_only()
    @commands.command(name='createschedule', help='Create a custom train schedule. Usage: !createschedule [day] [time] [slots] [duration]')
    async def create_schedule(self, ctx, *args):
        """Start interactive schedule creation process or create directly with args."""
        
        # Quick mode: if all arguments provided, create immediately
        if len(args) >= 4:
            try:
                await self._create_schedule_quick(ctx, args)
                return
            except Exception as e:
                await ctx.send(f"❌ Error creating schedule: {str(e)}\n\nTry the interactive mode by using `!createschedule` without arguments.")
                self.logger.error(f"Quick schedule creation failed: {e}")
                return
        
        # Interactive mode
        session_key = f"{ctx.guild.id}_{ctx.author.id}"
        
        # Clear any existing session to prevent conflicts
        if session_key in self.schedule_sessions:
            self.logger.warning(f"Clearing existing session for {ctx.author} to prevent conflicts")
            self.schedule_sessions.pop(session_key, None)
        
        # Initialize new session
        self.schedule_sessions[session_key] = {
            'step': 1,
            'guild_id': ctx.guild.id,
            'user_id': ctx.author.id,
            'channel_id': ctx.channel.id,
            'data': {}
        }
        
        embed = discord.Embed(
            title="🚂 Train Schedule Creator",
            description="Let's create your custom train schedule! I'll guide you through each step.",
            color=0x667eea,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="📝 Step 1 of 5: Day Selection",
            value="**What day should the train happen?**\n\nPlease choose the day of the week:\n\nExamples:\n• `monday` or `mon`\n• `tuesday` or `tue`\n• `wednesday` or `wed`\n• `thursday` or `thu`\n• `friday` or `fri`\n• `saturday` or `sat`\n• `sunday` or `sun`\n\n💡 **Quick Mode**: Use `!createschedule Thursday 18:00 4 90` to create instantly!",
            inline=False
        )
        
        embed.add_field(
            name="📋 Process Overview",
            value="1️⃣ Day of week\n2️⃣ Start time (UK)\n3️⃣ Number of slots\n4️⃣ Duration per slot\n5️⃣ Review & create",
            inline=False
        )
        
        embed.set_footer(text="Type your answer or use !cancelschedule to stop")
        
        try:
            await ctx.send(embed=embed)
            self.logger.info(f"✅ Successfully sent schedule creation prompt for {ctx.author} in {ctx.guild}")
            self.logger.debug(f"Active sessions: {list(self.schedule_sessions.keys())}")
        except Exception as e:
            self.logger.error(f"❌ Failed to send schedule creation prompt: {e}")
            # Clean up session if send failed
            self.schedule_sessions.pop(session_key, None)
            raise
    
    async def _create_schedule_quick(self, ctx, args):
        """Create multiple schedule slots directly from command arguments.
        Format: !createschedule Thursday 18:00 4 90 - user_id1 user_id2 user_id3 user_id4
        This creates 4 separate 90-minute slots starting at 6PM, each with 1 participant."""
        from models import get_est_time, TrainParticipant
        from datetime import timedelta
        
        # Parse arguments
        day_str = args[0].lower()
        time_str = args[1]
        num_slots = int(args[2])  # Number of time slots to create
        duration_minutes = int(args[3])
        
        # Parse optional user IDs (after position 4, ignoring dashes)
        user_ids = []
        if len(args) > 4:
            # Join all remaining args and parse
            remaining = ' '.join(args[4:])
            # Remove dashes and split by commas
            remaining = remaining.replace('-', '').replace(',', ' ')
            # Extract all numeric IDs
            for part in remaining.split():
                part = part.strip()
                if part.isdigit():
                    user_ids.append(int(part))
        
        # Validate day
        day_mapping = {
            'monday': 0, 'mon': 0,
            'tuesday': 1, 'tue': 1, 'tues': 1,
            'wednesday': 2, 'wed': 2,
            'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
            'friday': 4, 'fri': 4,
            'saturday': 5, 'sat': 5,
            'sunday': 6, 'sun': 6
        }
        
        if day_str not in day_mapping:
            raise ValueError(f"Invalid day: {day_str}. Use monday-sunday or mon-sun")
        
        day_of_week = day_mapping[day_str]
        day_name = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][day_of_week]
        
        # Parse time
        try:
            if ':' in time_str:
                hour, minute = map(int, time_str.split(':'))
            else:
                hour = int(time_str)
                minute = 0
            
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Hour must be 0-23, minute must be 0-59")
            
            start_time = time(hour, minute)
        except Exception as e:
            raise ValueError(f"Invalid time format: {time_str}. Use HH:MM or HH (24-hour format)")
        
        # Validate number of slots
        if not (1 <= num_slots <= 20):
            raise ValueError("Number of slots must be between 1 and 20")
        
        # Validate duration
        if not (1 <= duration_minutes <= 1440):
            raise ValueError("Duration must be between 1 and 1440 minutes (24 hours)")
        
        # Create multiple schedules (one per slot)
        created_schedules = []
        with DatabaseSession() as session:
            current_time = start_time

            for slot_index in range(num_slots):
                if slot_index > 0:
                    total_minutes = start_time.hour * 60 + start_time.minute + (duration_minutes * slot_index)
                    slot_hour = (total_minutes // 60) % 24
                    slot_minute = total_minutes % 60
                    current_time = time(slot_hour, slot_minute)
                else:
                    total_minutes = start_time.hour * 60 + start_time.minute

                # Determine UK day of week accounting for midnight crossover
                extra_days = total_minutes // 1440
                uk_slot_day = (day_of_week + extra_days) % 7

                schedule = TrainSchedule(
                    guild_id=ctx.guild.id,
                    name=f"{day_name} {current_time.strftime('%I:%M %p')} Train",
                    host_user_id=ctx.author.id,
                    day_of_week=uk_slot_day,
                    start_time=current_time,
                    duration_minutes=duration_minutes,
                    max_participants=1,  # One participant per slot
                    schedule_type='recurring',
                    is_active=True,
                    created_at=get_est_time(),
                    updated_at=get_est_time()
                )
                
                session.add(schedule)
                session.commit()
                session.refresh(schedule)  # Get the ID
                
                # Add participant if provided
                participant_name = None
                if slot_index < len(user_ids):
                    user_id = user_ids[slot_index]
                    
                    # Try to get member info
                    try:
                        member = ctx.guild.get_member(user_id)
                        if member:
                            username = member.name
                            display_name = member.display_name
                            participant_name = display_name
                        else:
                            username = str(user_id)
                            display_name = f"User_{user_id}"
                            participant_name = f"<@{user_id}>"
                    except:
                        username = str(user_id)
                        display_name = f"User_{user_id}"
                        participant_name = f"<@{user_id}>"
                    
                    # Add participant
                    participant = TrainParticipant(
                        schedule_id=schedule.id,
                        guild_id=ctx.guild.id,
                        user_id=user_id,
                        username=username,
                        display_name=display_name,
                        signed_up_at=get_est_time()
                    )
                    session.add(participant)
                    session.commit()
                    
                    # Send Twitch link reminder if needed (works for both online and offline users)
                    from utils.auto_link_helper import send_link_reminder_if_needed
                    asyncio.create_task(send_link_reminder_if_needed(ctx.bot, user_id, ctx.guild.id))
                
                created_schedules.append({
                    'id': schedule.id,
                    'time': current_time,
                    'participant': participant_name
                })
            
            # Success message
            embed = discord.Embed(
                title="✅ Train Schedules Created!",
                description=f"Created **{num_slots}** separate time slots for {day_name}.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            # Show each slot
            slots_info = []
            for i, slot in enumerate(created_schedules, 1):
                slot_text = f"**Slot {i}:** {slot['time'].strftime('%I:%M %p')} UK ({duration_minutes}min)"
                if slot['participant']:
                    slot_text += f" → {slot['participant']}"
                else:
                    slot_text += " → *Empty*"
                slots_info.append(slot_text)
            
            embed.add_field(
                name="🚂 Time Slots",
                value="\n".join(slots_info),
                inline=False
            )
            
            embed.add_field(name="📅 Day", value=day_name, inline=True)
            embed.add_field(name="⏱️ Duration", value=f"{duration_minutes} min/slot", inline=True)
            embed.add_field(name="📊 Status", value="Active", inline=True)
            
            schedule_ids = [str(s['id']) for s in created_schedules]
            embed.set_footer(text=f"Schedule IDs: {', '.join(schedule_ids)}")
            
            await ctx.send(embed=embed)
            self.logger.info(f"✅ Quick schedule created: {num_slots} slots on {day_name} starting at {start_time} ({duration_minutes}min each) by {ctx.author} in {ctx.guild}")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for responses to interactive schedule creation."""
        if message.author.bot:
            return
            
        try:
            # Check for maintenance mode first - skip all interactive processing
            from models import SystemSettings
            from database import DatabaseSession
            with DatabaseSession() as maintenance_session:
                maintenance_setting = maintenance_session.query(SystemSettings).filter_by(
                    setting_key='maintenance_mode'
                ).first()
                
                if maintenance_setting and maintenance_setting.is_enabled:
                    # Skip all interactive schedule processing in maintenance mode
                    return
        except Exception as e:
            self.logger.debug(f"Maintenance mode check failed, proceeding normally: {e}")
            
        session_key = f"{message.guild.id}_{message.author.id}"
        
        if session_key not in self.schedule_sessions:
            return
            
        session = self.schedule_sessions[session_key]
        
        # Make sure response is in the same channel
        if message.channel.id != session['channel_id']:
            return
        
        # Handle the response based on current step
        await self.handle_schedule_step(message, session)

    async def handle_schedule_step(self, message, session):
        """Handle each step of the schedule creation process."""
        try:
            step = session['step']
            content = message.content.strip().lower()
            
            # Cancel command
            if content in ['!cancelschedule', 'cancel', 'stop', 'quit']:
                await self.cancel_schedule_session(message, session)
                return
            
            if step == 1:
                # Parse day of week
                day_of_week = self.parse_day_input(content)
                if day_of_week is None:
                    embed = discord.Embed(
                        title="❌ Invalid Day Format",
                        description="Please provide a valid day of the week.\n\nExamples: `monday`, `tue`, `wednesday`, `fri`, `saturday`",
                        color=0xff0000
                    )
                    await message.channel.send(embed=embed)
                    return
                
                day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                session['data']['day_of_week'] = day_of_week
                
                # Check if it's Saturday or Sunday - ask for schedule type
                if day_of_week == 5 or day_of_week == 6:  # Saturday (5) or Sunday (6)
                    session['step'] = 2  # Schedule type selection step
                    
                    embed = discord.Embed(
                        title="✅ Day Selected",
                        description=f"Day: **{day_names[day_of_week]}**",
                        color=0x00ff00
                    )
                    
                    embed.add_field(
                        name="📝 Step 2 of 6: Schedule Type",
                        value=f"**Which type of {day_names[day_of_week]} schedule do you want?**\n\nSince you have two different {day_names[day_of_week].lower()} schedules, please choose:\n\nExamples:\n• `type1` or `schedule1` or `option1`\n• `type2` or `schedule2` or `option2`\n• `standard` or `alternative`",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="📋 Updated Process Overview",
                        value="1️⃣ Day of week\n2️⃣ Schedule type (weekend only)\n3️⃣ Start time (UK)\n4️⃣ Number of slots\n5️⃣ Duration per slot\n6️⃣ Review & create",
                        inline=False
                    )
                    
                    await message.channel.send(embed=embed)
                else:
                    # For weekdays, skip schedule type selection
                    session['step'] = 3  # Go directly to start time
                    
                    embed = discord.Embed(
                        title="✅ Day Selected",
                        description=f"Day: **{day_names[day_of_week]}**",
                        color=0x00ff00
                    )
                    
                    embed.add_field(
                        name="📝 Step 2 of 5: Start Time",
                        value="**What time should the train start?**\n\nPlease provide the start time in **UK timezone**.\n\nExamples:\n• `10:00am` or `10am`\n• `2:30pm` or `14:30`\n• `7pm` or `19:00`",
                        inline=False
                    )
                    
                    await message.channel.send(embed=embed)
                
            elif step == 2:
                # Handle weekend schedule type selection
                day_of_week = session['data']['day_of_week']
                if day_of_week == 5 or day_of_week == 6:  # Saturday or Sunday
                    # Parse schedule type
                    schedule_type = None
                    content_lower = content.lower()
                    
                    if content_lower in ['type1', 'schedule1', 'option1', '1', 'first', 'standard']:
                        schedule_type = 'Type 1'
                    elif content_lower in ['type2', 'schedule2', 'option2', '2', 'second', 'alternative']:
                        schedule_type = 'Type 2'
                    
                    if schedule_type is None:
                        embed = discord.Embed(
                            title="❌ Invalid Schedule Type",
                            description="Please choose a valid schedule type.\n\nValid options:\n• `type1`, `schedule1`, `option1`, `1`, `standard`\n• `type2`, `schedule2`, `option2`, `2`, `alternative`",
                            color=0xff0000
                        )
                        await message.channel.send(embed=embed)
                        return
                    
                    session['data']['schedule_type'] = schedule_type
                    session['step'] = 3  # Move to start time
                    
                    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                    embed = discord.Embed(
                        title="✅ Schedule Type Selected",
                        description=f"Day: **{day_names[day_of_week]}**\nSchedule Type: **{schedule_type}**",
                        color=0x00ff00
                    )
                    
                    embed.add_field(
                        name="📝 Step 3 of 6: Start Time",
                        value="**What time should the train start?**\n\nPlease provide the start time in **UK timezone**.\n\nExamples:\n• `10:00am` or `10am`\n• `2:30pm` or `14:30`\n• `7pm` or `19:00`",
                        inline=False
                    )
                    
                    await message.channel.send(embed=embed)
                else:
                    # This shouldn't happen for weekdays in step 2, but handle gracefully
                    await message.channel.send("❌ Unexpected error in schedule creation. Please use `!cancelschedule` and try again.")
                    
            elif step == 3:
                # Parse start time (now step 3 for all days)
                start_time = self.parse_time_input(content)
                if not start_time:
                    embed = discord.Embed(
                        title="❌ Invalid Time Format",
                        description="Please provide a valid time format.\n\nExamples: `10:00am`, `2:30pm`, `14:30`, `7pm`",
                        color=0xff0000
                    )
                    await message.channel.send(embed=embed)
                    return
                
                session['data']['start_time'] = start_time
                session['step'] = 4  # Move to number of slots
                
                # Determine if this is a weekend schedule (has schedule_type) or weekday
                day_of_week = session['data']['day_of_week']
                has_schedule_type = 'schedule_type' in session['data']
                
                if has_schedule_type:
                    step_display = "Step 4 of 6"
                else:
                    step_display = "Step 3 of 5"
                
                embed = discord.Embed(
                    title="✅ Start Time Set",
                    description=f"Start time: **{self.format_time_display(start_time)}**",
                    color=0x00ff00
                )
                
                embed.add_field(
                    name=f"📝 {step_display}: Number of Slots",
                    value="**How many train slots do you want?**\n\nEnter a number between 1 and 20.\n\nExamples:\n• `5` - for 5 train slots\n• `9` - for 9 train slots",
                    inline=False
                )
                
                await message.channel.send(embed=embed)
                
            elif step == 4:
                # Parse number of slots (now step 4 for all days)
                try:
                    num_slots = int(content)
                    if num_slots < 1 or num_slots > 20:
                        raise ValueError()
                except ValueError:
                    embed = discord.Embed(
                        title="❌ Invalid Number",
                        description="Please enter a number between 1 and 20.",
                        color=0xff0000
                    )
                    await message.channel.send(embed=embed)
                    return
                
                session['data']['num_slots'] = num_slots
                session['step'] = 5  # Move to duration
                
                embed = discord.Embed(
                    title="✅ Slots Set",
                    description=f"Number of slots: **{num_slots}**",
                    color=0x00ff00
                )
                
                # Determine step display based on weekend/weekday
                has_schedule_type = 'schedule_type' in session['data']
                if has_schedule_type:
                    step_display = "Step 5 of 6: Slot Duration"
                else:
                    step_display = "Step 4 of 5: Slot Duration"
                
                embed.add_field(
                    name=f"📝 {step_display}",
                    value="**How long should each slot be?**\n\nProvide duration in minutes.\n\nExamples:\n• `90` - for 90 minutes (1.5 hours)\n• `60` - for 60 minutes (1 hour)\n• `120` - for 120 minutes (2 hours)",
                    inline=False
                )
                
                await message.channel.send(embed=embed)
                
            elif step == 5:
                # Parse slot duration (now step 5 for all days)
                try:
                    duration = int(content)
                    if duration < 15 or duration > 300:  # 15 minutes to 5 hours
                        raise ValueError()
                except ValueError:
                    embed = discord.Embed(
                        title="❌ Invalid Duration",
                        description="Please enter a duration between 15 and 300 minutes (15 minutes to 5 hours).",
                        color=0xff0000
                    )
                    await message.channel.send(embed=embed)
                    return
                
                session['data']['duration'] = duration
                session['step'] = 6  # Move to final confirmation
                
                # Calculate schedule overview
                day_of_week = session['data']['day_of_week']
                start_time = session['data']['start_time']
                num_slots = session['data']['num_slots']
                
                day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                slots_preview = self.calculate_schedule_preview(start_time, num_slots, duration)
                total_duration = num_slots * duration
                total_hours = total_duration // 60
                total_minutes = total_duration % 60
                
                embed = discord.Embed(
                    title="📊 Schedule Preview",
                    description="Here's what your train schedule will look like:",
                    color=0x667eea,
                    timestamp=datetime.utcnow()
                )
                
                # Show schedule type if it's a weekend schedule
                schedule_type_text = ""
                if 'schedule_type' in session['data']:
                    schedule_type_text = f"\n**Schedule Type:** {session['data']['schedule_type']}"
                    step_display = "Step 6 of 6: Confirmation"
                else:
                    step_display = "Step 5 of 5: Confirmation"
                
                embed.add_field(
                    name="📋 Configuration",
                    value=f"**Day:** {day_names[day_of_week]}{schedule_type_text}\n**Start:** {self.format_time_display(start_time)}\n**Slots:** {num_slots}\n**Duration:** {duration} minutes each\n**Total:** {total_hours}h {total_minutes}m",
                    inline=True
                )
                
                embed.add_field(
                    name="⏰ Schedule",
                    value=slots_preview,
                    inline=True
                )
                
                embed.add_field(
                    name=f"📝 {step_display}",
                    value="**Ready to create this schedule?**\n\nType:\n• `yes` or `confirm` to create\n• `no` or `cancel` to cancel",
                    inline=False
                )
                
                await message.channel.send(embed=embed)
                
            elif step == 6:
                # Final confirmation (now step 6 for all days)
                if content in ['yes', 'confirm', 'y', 'create']:
                    await self.create_final_schedule(message, session)
                elif content in ['no', 'cancel', 'n']:
                    await self.cancel_schedule_session(message, session)
                else:
                    embed = discord.Embed(
                        title="❓ Confirmation Required",
                        description="Please type `yes` to create the schedule or `no` to cancel.",
                        color=0xffa500
                    )
                    await message.channel.send(embed=embed)
                    
        except Exception as e:
            self.logger.error(f"Error in schedule step handling: {e}")
            await message.channel.send("❌ An error occurred. Please try again with `!createschedule`.")
            session_key = f"{message.guild.id}_{message.author.id}"
            self.schedule_sessions.pop(session_key, None)

    def parse_day_input(self, day_str):
        """Parse day of week input and return day number (0=Monday, 6=Sunday)."""
        day_str = day_str.strip().lower()
        
        day_mapping = {
            # Full names
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6,
            # Short names
            'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3,
            'fri': 4, 'sat': 5, 'sun': 6,
            # Alternative short names
            'tues': 1, 'weds': 2, 'thurs': 3
        }
        
        return day_mapping.get(day_str)

    def parse_time_input(self, time_str):
        """Parse various time input formats. Returns a time object in UK wall-clock time.
        Times are stored as-entered (UK local) and localized against the actual schedule
        date at notification time so DST is handled automatically. No UTC conversion."""
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
        
        from datetime import time
        return time(hour, minute)

    def format_time_display(self, time_obj):
        """Format UK wall-clock time for display (times are stored as UK local, no conversion needed)."""
        import pytz
        uk_tz = pytz.timezone('Europe/London')
        uk_dt = uk_tz.localize(datetime.combine(datetime.today(), time_obj))
        return uk_dt.strftime('%I:%M%p %Z').lower().lstrip('0')

    def calculate_schedule_preview(self, start_time, num_slots, duration_minutes):
        """Generate a preview of the schedule slots."""
        import pytz
        uk_tz = pytz.timezone('Europe/London')
        preview = []
        current_time = uk_tz.localize(datetime.combine(datetime.today(), start_time))
        
        for i in range(min(num_slots, 5)):
            uk_time = current_time
            end_time = uk_time + timedelta(minutes=duration_minutes)
            
            time_str = uk_time.strftime('%I:%M%p').lower().replace('0', '', 1) if uk_time.strftime('%I').startswith('0') else uk_time.strftime('%I:%M%p').lower()
            end_str = end_time.strftime('%I:%M%p').lower().replace('0', '', 1) if end_time.strftime('%I').startswith('0') else end_time.strftime('%I:%M%p').lower()
            
            preview.append(f"**Slot {i+1}:** {time_str}-{end_str}")
            current_time += timedelta(minutes=duration_minutes)
        
        if num_slots > 5:
            preview.append(f"... and {num_slots - 5} more slots")
            
        return '\n'.join(preview)

    async def create_final_schedule(self, message, session):
        """Create the actual train schedule in the database."""
        try:
            data = session['data']
            start_time = data['start_time']
            num_slots = data['num_slots']
            duration = data['duration']
            
            # Use the day selected by the user
            day_of_week = data['day_of_week']
            day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            day_name = day_names[day_of_week]
            
            created_schedules = []
            schedule_ids = []
            
            with DatabaseSession() as db_session:
                from datetime import datetime, timedelta
                import pytz
                
                uk_tz = pytz.timezone('Europe/London')
                today = datetime.now(uk_tz).date()
                days_until_target = (day_of_week - today.weekday()) % 7
                if days_until_target == 0 and datetime.now(uk_tz).time() > start_time:
                    days_until_target = 7
                target_date = today + timedelta(days=days_until_target)
                
                uk_start_datetime = uk_tz.localize(datetime.combine(target_date, start_time))
                
                current_uk_datetime = uk_start_datetime
                
                for i in range(num_slots):
                    # Store UK wall clock time — notification system localises against schedule date for DST
                    uk_day = current_uk_datetime.weekday()
                    uk_time = current_uk_datetime.time()

                    # Create schedule name with type if it's a weekend schedule
                    schedule_type_suffix = ""
                    description_suffix = ""
                    if 'schedule_type' in data:
                        schedule_type_suffix = f" ({data['schedule_type']})"
                        description_suffix = f" - {data['schedule_type']}"

                    schedule = TrainSchedule(
                        guild_id=session['guild_id'],
                        name=f"Custom Slot {i+1}{schedule_type_suffix}",
                        day_of_week=uk_day,
                        start_time=uk_time,
                        duration_minutes=duration,
                        notify_before_minutes=60,
                        is_active=True,
                        description=f"Custom {duration}-minute train slot{description_suffix}",
                        max_participants=1  # Only 1 person per slot
                    )
                    
                    db_session.add(schedule)
                    created_schedules.append(schedule)
                    
                    current_uk_datetime += timedelta(minutes=duration)
                
                db_session.commit()
                
                # Get the IDs after commit while still in session
                for schedule in created_schedules:
                    db_session.refresh(schedule)
                    schedule_ids.append(schedule.id)
            
            # Success message
            total_duration = num_slots * duration
            total_hours = total_duration // 60
            total_minutes = total_duration % 60
            
            # Include schedule type in success message if it's a weekend schedule
            schedule_type_text = ""
            if 'schedule_type' in data:
                schedule_type_text = f" ({data['schedule_type']})"
                
            embed = discord.Embed(
                title="✅ Schedule Created Successfully!",
                description=f"Created **{num_slots} train slots** for **{day_name}{schedule_type_text}**",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 Schedule Details",
                value=f"**Start:** {self.format_time_display(start_time)}\n**Slots:** {num_slots}\n**Duration:** {duration} min each\n**Total:** {total_hours}h {total_minutes}m",
                inline=True
            )
            
            embed.add_field(
                name="🎯 Next Steps",
                value="• Use `!timeslots` to view all slots\n• Use `!jointrain <id>` to join trains\n• Use dashboard to manage participants",
                inline=True
            )
            
            if schedule_ids:
                embed.set_footer(text=f"Schedule IDs: {schedule_ids[0]}-{schedule_ids[-1]}")
            
            await message.channel.send(embed=embed)
            
            # Clean up session
            session_key = f"{message.guild.id}_{message.author.id}"
            self.schedule_sessions.pop(session_key, None)
            
            self.logger.info(f"Created {num_slots} custom train schedules for {message.author} in {message.guild}")
            
        except Exception as e:
            self.logger.error(f"Error creating schedule: {e}")
            await message.channel.send(f"❌ Failed to create schedule: {str(e)}")
            
            # Clean up session
            session_key = f"{message.guild.id}_{message.author.id}"
            self.schedule_sessions.pop(session_key, None)

    async def cancel_schedule_session(self, message, session):
        """Cancel the current schedule creation session."""
        embed = discord.Embed(
            title="❌ Schedule Creation Cancelled",
            description="Schedule creation has been cancelled. No changes were made.",
            color=0xff9900
        )
        
        embed.add_field(
            name="🔄 Start Again",
            value="Use `!createschedule` to start a new schedule creation process.",
            inline=False
        )
        
        await message.channel.send(embed=embed)
        
        # Clean up session
        session_key = f"{message.guild.id}_{message.author.id}"
        self.schedule_sessions.pop(session_key, None)

    @is_admin_or_trusted()
    @commands.guild_only()
    @commands.command(name='cancelschedule', help='Cancel ongoing schedule creation')
    async def cancel_schedule_command(self, ctx):
        """Cancel ongoing schedule creation session."""
        session_key = f"{ctx.guild.id}_{ctx.author.id}"
        
        if session_key not in self.schedule_sessions:
            embed = discord.Embed(
                title="ℹ️ No Active Session",
                description="You don't have an active schedule creation session.",
                color=0x667eea
            )
            await ctx.send(embed=embed)
            return
        
        session = self.schedule_sessions[session_key]
        await self.cancel_schedule_session(ctx, session)

async def setup(bot):
    await bot.add_cog(InteractiveScheduleCommands(bot))