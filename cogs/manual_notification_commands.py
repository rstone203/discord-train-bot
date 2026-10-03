"""
Manual Notification Commands - Send train notifications directly from Discord
These commands allow server admins to manually trigger notifications without accessing Replit.
"""

import discord
from discord.ext import commands
from discord import app_commands
from database import DatabaseSession
from models import TrainSchedule, TrainNotification
from datetime import datetime, date, timedelta
import pytz
import logging
from sqlalchemy import text

class ManualNotificationCommands(commands.Cog):
    """Commands for manually sending train notifications from Discord."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        self.est_tz = pytz.timezone('Europe/London')

    async def respond(self, interaction, *args, **kwargs):
        """Helper method to safely respond to an interaction."""
        if interaction.response.is_done():
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)
    
    @commands.command(name='sendping', help='Manually send a train notification')
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def send_manual_ping(self, ctx, schedule_name: str = None):
        """Manually send a train notification for any schedule."""
        if not schedule_name:
            await ctx.send(
                "❌ **Usage:** `/sendping <schedule_name>`\n"
                "**Examples:**\n"
                "`/sendping Slot 4`\n"
                "`/sendping Saturday Train - Slot 6`\n"
                "\n**Tip:** Use `/listschedules` to see all available schedules."
            )
            return
        
        try:
            with DatabaseSession() as session:
                # Find the schedule (case-insensitive partial match)
                schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.is_active == True
                ).all()
                
                matching_schedule = None
                for schedule in schedules:
                    if schedule_name.lower() in schedule.name.lower():
                        matching_schedule = schedule
                        break
                
                if not matching_schedule:
                    # Show available schedules
                    schedule_list = "\n".join([f"• {s.name}" for s in schedules[:10]])
                    await ctx.send(
                        f"❌ **Schedule not found:** `{schedule_name}`\n\n"
                        f"**Available schedules:**\n{schedule_list}\n"
                        f"Use `/listschedules` to see all schedules."
                    )
                    return
                
                # Always send to pings channel, not where command was typed
                pings_channel = ctx.guild.get_channel(1294082148839591956)
                if not pings_channel:
                    await ctx.send("❌ Pings channel not found! Cannot send notification.")
                    return
                
                # Send the manual notification to pings channel
                success = await self.send_train_notification(matching_schedule, pings_channel)
                
                # Log the manual command usage
                session.execute(text("""
                    INSERT INTO notification_audit_log 
                    (command_type, user_id, username, guild_id, schedule_id, schedule_name, channel_id, success)
                    VALUES (:cmd_type, :user_id, :username, :guild_id, :schedule_id, :schedule_name, :channel_id, :success)
                """), {
                    'cmd_type': 'sendping',
                    'user_id': ctx.author.id,
                    'username': str(ctx.author),
                    'guild_id': ctx.guild.id,
                    'schedule_id': matching_schedule.id,
                    'schedule_name': matching_schedule.name,
                    'channel_id': pings_channel.id,
                    'success': success
                })
                session.commit()
                
                if success:
                    embed = discord.Embed(
                        title='✅ Manual Notification Sent!',
                        description=f'Successfully sent notification for **{matching_schedule.name}**',
                        color=0x00ff00
                    )
                    embed.add_field(
                        name='📋 Schedule Details',
                        value=f'**Start Time:** {matching_schedule.start_time.strftime("%I:%M %p")}\n'
                              f'**Duration:** {matching_schedule.duration_minutes} minutes',
                        inline=False
                    )
                    await ctx.send(embed=embed)
                else:
                    await ctx.send("❌ Failed to send notification. Check logs for details.")
                    
        except Exception as e:
            self.logger.error(f"Error in manual ping command: {e}")
            await ctx.send("❌ Error sending manual notification. Please try again.")
    
    @commands.command(name='manualtestping', help='Send a real train notification ping to yourself')
    @commands.guild_only()
    async def test_ping(self, ctx):
        """Send the exact same notification participants normally receive, pinging the command user."""
        try:
            now = datetime.now(self.est_tz)
            
            READY_EMOJI = "✅"
            NOT_READY_EMOJI = "❌"
            BACKUP_EMOJI = "🔄"

            from models import Guild as GuildModel
            with DatabaseSession() as session:
                guild_record = session.query(GuildModel).filter_by(id=ctx.guild.id).first()
                server_description = guild_record.server_description if guild_record and guild_record.server_description else ""

            description_text = f'**Saturday 04:00 PM Train** - Starting in about 1 hour'
            if server_description:
                description_text += f'\n📍 {server_description}'

            embed = discord.Embed(
                title='🚂 Raid Train Starting Soon!',
                description=description_text,
                color=0x9146ff
            )

            embed.add_field(
                name='📅 Train Details',
                value=f'**Start Time:** {now.strftime("%I:%M %p %Z")}\n**Duration:** 90 minutes',
                inline=False
            )

            embed.add_field(
                name='📊 Status',
                value='⏰ **Primary:** Waiting for confirmation...',
                inline=False
            )

            embed.add_field(
                name='🎯 Instructions',
                value=f'{READY_EMOJI} = Ready to stream\n{NOT_READY_EMOJI} = Can\'t make it\n{BACKUP_EMOJI} = Need backup',
                inline=False
            )

            embed.set_footer(text=f'Stage 1 • {now.strftime("%H:%M %Z")}')

            pings_channel = ctx.guild.get_channel(1294082148839591956)
            target_channel = pings_channel or ctx.channel

            message_content = f"🎯 **TRAIN RIDER:** <@{ctx.author.id}>"

            message = await target_channel.send(content=message_content, embed=embed)

            await message.add_reaction(READY_EMOJI)
            await message.add_reaction(NOT_READY_EMOJI)
            await message.add_reaction(BACKUP_EMOJI)

            if target_channel != ctx.channel:
                await ctx.send(f"✅ Test notification sent to {target_channel.mention}! Go react to it.")

        except Exception as e:
            self.logger.error(f"Error in test ping: {e}")
            await ctx.send("❌ Error sending test ping. Please try again.")

    @commands.command(name='quickping', help='Send emergency train notification with custom message')
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)  
    async def quick_ping(self, ctx, participant_id: str = None, *, custom_message: str = None):
        """Send a quick emergency train notification."""
        if not participant_id:
            await ctx.send(
                "❌ **Usage:** `/quickping <user_id> [custom message]`\n"
                "**Examples:**\n"
                "`/quickping 1366150486117454008 Your train starts in 10 minutes!`\n"
                "`/quickping @NastyElimsTV Emergency train notification`"
            )
            return
        
        try:
            # Clean participant ID (remove @ and <> if present)
            clean_id = participant_id.replace('<@', '').replace('>', '').replace('!', '')
            
            # Create emergency notification
            embed = discord.Embed(
                title='🚨 Emergency Train Notification',
                description=custom_message or 'Manual train notification sent by admin',
                color=0xff4444
            )
            
            embed.add_field(
                name='📋 Instructions',
                value='React with ✅ if you\'re ready to stream!\n'
                      'React with ❌ if you can\'t make it\n'
                      'React with 🔄 if you need a backup',
                inline=False
            )
            
            now = datetime.now(self.est_tz)
            embed.set_footer(text=f'Sent manually at {now.strftime("%I:%M %p %Z")}')
            
            # Always send emergency pings to pings channel
            pings_channel = ctx.guild.get_channel(1294082148839591956)
            if not pings_channel:
                await ctx.send("❌ Pings channel not found! Cannot send emergency notification.")
                return
            
            # Send the message to pings channel
            message_content = f"<@{clean_id}> 🚨 **EMERGENCY TRAIN NOTIFICATION**"
            message = await pings_channel.send(content=message_content, embed=embed)
            
            # Add reactions
            await message.add_reaction('✅')
            await message.add_reaction('❌') 
            await message.add_reaction('🔄')
            
            # Log the manual command usage
            with DatabaseSession() as log_session:
                log_session.execute(text("""
                    INSERT INTO notification_audit_log 
                    (command_type, user_id, username, guild_id, target_user_id, custom_message, channel_id, success)
                    VALUES (:cmd_type, :user_id, :username, :guild_id, :target_user_id, :custom_message, :channel_id, :success)
                """), {
                    'cmd_type': 'quickping',
                    'user_id': ctx.author.id,
                    'username': str(ctx.author),
                    'guild_id': ctx.guild.id,
                    'target_user_id': int(clean_id),
                    'custom_message': custom_message,
                    'channel_id': pings_channel.id,
                    'success': True
                })
                log_session.commit()
            
            # Confirm success with channel info
            await ctx.send(f"✅ Emergency notification sent to <@{clean_id}> in {pings_channel.mention}!", delete_after=10)
            
        except Exception as e:
            self.logger.error(f"Error in quick ping command: {e}")
            await ctx.send("❌ Error sending quick ping. Check the user ID and try again.")
    
    @commands.command(name='listschedules', help='List all train schedules')
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def list_schedules(self, ctx):
        """List all available train schedules."""
        try:
            with DatabaseSession() as session:
                schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.guild_id == ctx.guild.id,
                    TrainSchedule.is_active == True
                ).order_by(TrainSchedule.day_of_week, TrainSchedule.start_time).all()
                
                if not schedules:
                    await ctx.send("❌ No active schedules found for this server.")
                    return
                
                embed = discord.Embed(
                    title='📅 Available Train Schedules',
                    description='Use `/sendping <schedule_name>` to manually trigger notifications',
                    color=0x9146ff
                )
                
                days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                
                for day_num in range(7):
                    day_schedules = [s for s in schedules if s.day_of_week == day_num]
                    if day_schedules:
                        schedule_list = []
                        for schedule in day_schedules:
                            status = "🟢" if schedule.is_active else "🔴"
                            schedule_list.append(
                                f"{status} **{schedule.name}** - {schedule.start_time.strftime('%I:%M %p')}"
                            )
                        
                        embed.add_field(
                            name=f'📆 {days[day_num]}',
                            value='\n'.join(schedule_list),
                            inline=False
                        )
                
                embed.set_footer(text='Use the exact schedule name (or partial match) with /sendping')
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error listing schedules: {e}")
            await ctx.send("❌ Error retrieving schedules. Please try again.")
    
    @app_commands.command(name='nexttrain', description='Check when the next train notification will be sent')
    async def next_train(self, interaction: discord.Interaction):
        """Show when the next train notification will be sent."""
        try:
            now = datetime.now(self.est_tz)
            current_day = now.weekday()
            today_date = now.date()
            
            with DatabaseSession() as session:
                # Get schedules for today and tomorrow
                upcoming_trains = []
                tomorrow_date = today_date + timedelta(days=1)
                tomorrow_day = (current_day + 1) % 7
                
                # Fetch all active guild schedules once, then resolve each to its effective
                # date. One-time (specific_date) schedules use their actual date (with a <5am
                # rollover to the next calendar day); recurring schedules use weekday math.
                # This keeps expired one-time trains from re-appearing on their weekday next week.
                all_schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.guild_id == interaction.guild.id,
                    TrainSchedule.is_active == True
                ).order_by(TrainSchedule.start_time).all()
                
                for schedule in all_schedules:
                    _sd = getattr(schedule, 'specific_date', None)
                    if _sd:
                        if isinstance(_sd, str):
                            _sd = datetime.strptime(_sd, '%Y-%m-%d').date()
                        eff_date = _sd + timedelta(days=1) if schedule.start_time.hour < 5 else _sd
                        if eff_date not in (today_date, tomorrow_date):
                            continue
                    else:
                        if schedule.day_of_week == current_day:
                            eff_date = today_date
                        elif schedule.day_of_week == tomorrow_day:
                            eff_date = tomorrow_date
                        else:
                            continue
                    
                    start_datetime = self.est_tz.localize(
                        datetime.combine(eff_date, schedule.start_time)
                    )
                    stage_1_time = start_datetime - timedelta(minutes=60)
                    
                    # Only show future notifications
                    if stage_1_time > now:
                        time_until = stage_1_time - now
                        upcoming_trains.append({
                                'name': schedule.name,
                                'notification_time': stage_1_time,
                                'start_time': start_datetime,
                                'time_until': time_until
                            })
                
                if not upcoming_trains:
                    await self.respond(interaction, "🤷 No upcoming train notifications scheduled for the next 24 hours.")
                    return
                
                # Sort by notification time and take first 5
                upcoming_trains.sort(key=lambda x: x['notification_time'])
                
                embed = discord.Embed(
                    title='🚂 Next Train Notifications',
                    description='Automatic notifications will be sent at these times',
                    color=0x9146ff
                )
                
                for i, train in enumerate(upcoming_trains[:5]):
                    hours = int(train['time_until'].total_seconds() // 3600)
                    minutes = int((train['time_until'].total_seconds() % 3600) // 60)
                    
                    if hours > 0:
                        time_str = f"in {hours}h {minutes}m"
                    else:
                        time_str = f"in {minutes}m"
                    
                    embed.add_field(
                        name=f'#{i+1}: {train["name"]}',
                        value=f'**Notification:** {train["notification_time"].strftime("%I:%M %p %Z")} ({time_str})\n'
                              f'**Train Starts:** {train["start_time"].strftime("%I:%M %p %Z")}',
                        inline=False
                    )
                
                embed.set_footer(text='Use /sendping to manually trigger any notification')
                await self.respond(interaction, embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error getting next train: {e}")
            await self.respond(interaction, "❌ Error checking next trains. Please try again.", ephemeral=True)
    
    async def send_train_notification(self, schedule, channel):
        """Send a train notification for the given schedule."""
        try:
            # Get participant to ping
            participant_ping = ""
            if schedule.participant_ids and len(schedule.participant_ids) > 0:
                participant_id = schedule.participant_ids[0]
                participant_ping = f"<@{participant_id}> "
            
            # Create embed
            embed = discord.Embed(
                title='🚂 Raid Train Starting Soon!',
                description=f'**{schedule.name}** notification (Manual)',
                color=0x9146ff
            )
            
            # Calculate timing
            now = datetime.now(self.est_tz)
            today_date = now.date()
            start_datetime = self.est_tz.localize(
                datetime.combine(today_date, schedule.start_time)
            )
            
            time_until = start_datetime - now
            if time_until.total_seconds() > 0:
                minutes_until = int(time_until.total_seconds() / 60)
                embed.add_field(
                    name='⏰ Train Start Time',
                    value=f'{start_datetime.strftime("%I:%M %p %Z")} (in {minutes_until} minutes)',
                    inline=False
                )
            else:
                embed.add_field(
                    name='⏰ Train Start Time',
                    value=f'{start_datetime.strftime("%I:%M %p %Z")} (NOW)',
                    inline=False
                )
            
            embed.add_field(
                name='📋 Instructions',
                value='React with ✅ if you\'re ready to stream!\n'
                      'React with ❌ if you can\'t make it\n'
                      'React with 🔄 if you need a backup',
                inline=False
            )
            
            # Send message
            message_content = f'{participant_ping}🚂 **Your train slot is starting soon!** (MANUAL)'
            message = await channel.send(content=message_content, embed=embed)
            
            # Add reactions
            await message.add_reaction('✅')
            await message.add_reaction('❌')
            await message.add_reaction('🔄')
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error sending train notification: {e}")
            return False

async def setup(bot):
    await bot.add_cog(ManualNotificationCommands(bot))