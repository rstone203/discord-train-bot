"""
Test commands for Twitch chat bot functionality.
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging
from database import DatabaseSession
from models import TrainSchedule, User
from utils.slash_permissions import owner_or_trusted

logger = logging.getLogger('discord_bot.chatbot_test_commands')


class ChatBotTestCommands(commands.Cog):
    """Commands to test Twitch chat bot features."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logger
    
    @app_commands.command(name='testraidnow', description='Test the !raidnext command logic (owner/trusted only)')
    @app_commands.describe(
        current_broadcaster='Current broadcaster Twitch username (e.g., rstone203)'
    )
    @owner_or_trusted()
    async def test_raidnow(self, interaction: discord.Interaction, current_broadcaster: str):
        """Test the raidnext command logic without actually raiding."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await interaction.followup.send("❌ Twitch chat monitor not available", ephemeral=True)
                return
            
            monitor = self.bot.twitch_chat_monitor
            chat_bot = monitor.chat_bot
            
            # Get guild_id
            guild_id = interaction.guild_id
            
            # Get broadcaster ID
            broadcaster_id = await chat_bot._get_twitch_user_id(current_broadcaster)
            
            if not broadcaster_id:
                await interaction.followup.send(
                    f"❌ Could not find Twitch user ID for **{current_broadcaster}**",
                    ephemeral=True
                )
                return
            
            # Get next rider
            next_rider = await chat_bot.get_next_rider(guild_id, current_broadcaster)
            
            if not next_rider:
                await interaction.followup.send(
                    f"⚠️ No next rider found in today's train schedule for **{current_broadcaster}**\n\n"
                    "**Possible reasons:**\n"
                    "• No active train schedules today\n"
                    "• Current broadcaster not in any schedule\n"
                    "• Current broadcaster is the last rider",
                    ephemeral=True
                )
                return
            
            # Create test embed
            embed = discord.Embed(
                title="🧪 !raidnext Command Test",
                description=f"Testing raid logic for **{current_broadcaster}**",
                color=0x9146ff
            )
            
            embed.add_field(
                name="📺 Current Broadcaster",
                value=f"{current_broadcaster} (ID: {broadcaster_id})",
                inline=False
            )
            
            embed.add_field(
                name="🎯 Next Rider Found",
                value=f"**{next_rider['twitch_login']}** (ID: {next_rider.get('twitch_id', 'Unknown')})",
                inline=False
            )
            
            embed.add_field(
                name="⏰ Scheduled Time",
                value=next_rider['time'].strftime('%I:%M %p') + ' UK' if next_rider.get('time') else 'Unknown',
                inline=True
            )
            
            embed.add_field(
                name="🔗 Raid Target",
                value=f"twitch.tv/{next_rider['twitch_login']}",
                inline=True
            )
            
            # Simulate what would happen
            embed.add_field(
                name="📤 What Would Happen",
                value=(
                    f"1. Start raid from `{broadcaster_id}` to `{next_rider.get('twitch_id', 'Unknown')}`\n"
                    f"2. Send chat message: *🎯 Starting raid to {next_rider['twitch_login']}! twitch.tv/{next_rider['twitch_login']}*\n"
                    "3. Twitch pops up 90-second raid countdown"
                ),
                inline=False
            )
            
            embed.set_footer(text="✅ Logic test successful - No actual raid initiated")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            self.logger.info(f"✅ Raid logic test completed for {current_broadcaster} → {next_rider['twitch_login']}")
            
        except Exception as e:
            self.logger.error(f"Error testing raidnext command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name='testchatmessage', description='Test sending a message to Twitch chat (owner/trusted only)')
    @app_commands.describe(
        channel='Twitch channel to send message to',
        message='Test message to send'
    )
    @owner_or_trusted()
    async def test_chat_message(self, interaction: discord.Interaction, channel: str, message: str):
        """Test sending a message to Twitch chat."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await interaction.followup.send("❌ Twitch chat monitor not available", ephemeral=True)
                return
            
            monitor = self.bot.twitch_chat_monitor
            chat_bot = monitor.chat_bot
            
            # Get broadcaster ID
            broadcaster_id = await chat_bot._get_twitch_user_id(channel)
            
            if not broadcaster_id:
                await interaction.followup.send(
                    f"❌ Could not find Twitch user ID for **{channel}**",
                    ephemeral=True
                )
                return
            
            # Send the message
            success = await chat_bot.send_chat_message(broadcaster_id, message)
            
            if success:
                embed = discord.Embed(
                    title="✅ Chat Message Sent",
                    description=f"Successfully sent message to **{channel}**'s chat",
                    color=0x00ff00
                )
                embed.add_field(name="Channel", value=f"twitch.tv/{channel}", inline=True)
                embed.add_field(name="Broadcaster ID", value=broadcaster_id, inline=True)
                embed.add_field(name="Message", value=message, inline=False)
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                self.logger.info(f"✅ Test message sent to #{channel}: {message}")
            else:
                await interaction.followup.send(
                    f"❌ Failed to send message to **{channel}**\n\n"
                    "**Possible reasons:**\n"
                    "• Bot account needs to be mod in the channel\n"
                    "• OAuth token missing `user:write:chat` scope\n"
                    "• Channel doesn't exist or is banned",
                    ephemeral=True
                )
            
        except Exception as e:
            self.logger.error(f"Error testing chat message: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name='listnextrider', description='Show who the next rider would be for a given broadcaster')
    @app_commands.describe(
        current_broadcaster='Current broadcaster Twitch username'
    )
    async def list_next_rider(self, interaction: discord.Interaction, current_broadcaster: str):
        """List the next rider in the train schedule."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                await interaction.followup.send("❌ Twitch chat monitor not available", ephemeral=True)
                return
            
            chat_bot = self.bot.twitch_chat_monitor.chat_bot
            guild_id = interaction.guild_id
            
            # Get next rider
            next_rider = await chat_bot.get_next_rider(guild_id, current_broadcaster)
            
            if not next_rider:
                # Show today's schedule to help debug
                with DatabaseSession() as session:
                    from datetime import datetime, timedelta
                    import pytz as _pytz_cb
                    now = datetime.now(_pytz_cb.timezone('Europe/London'))
                    today_day_of_week = now.weekday()  # 0=Monday, 6=Sunday (UK wall clock)
                    today_date = now.date()
                    
                    # Fetch all active schedules, then keep ones happening today. One-time
                    # (specific_date) schedules must match today's actual date, not just the
                    # weekday, so expired one-time trains aren't shown as "today's schedule".
                    all_rows = session.query(TrainSchedule, User).join(
                        User, TrainSchedule.host_user_id == User.id
                    ).filter(
                        TrainSchedule.guild_id == guild_id,
                        TrainSchedule.is_active == True
                    ).order_by(TrainSchedule.start_time).all()
                    schedules = []
                    for _sch, _usr in all_rows:
                        _sd = getattr(_sch, 'specific_date', None)
                        if _sd:
                            if isinstance(_sd, str):
                                _sd = datetime.strptime(_sd, '%Y-%m-%d').date()
                            _eff = _sd + timedelta(days=1) if _sch.start_time.hour < 5 else _sd
                            if _eff == today_date:
                                schedules.append((_sch, _usr))
                        elif _sch.day_of_week == today_day_of_week:
                            schedules.append((_sch, _usr))
                    
                    if schedules:
                        import pytz as _pytz_ct
                        _uk_ct = _pytz_ct.timezone('Europe/London')
                        def _fmt_uk_time(t):
                            from datetime import datetime as _dct
                            _d = _uk_ct.localize(_dct.combine(_dct.today(), t))
                            return _d.strftime('%I:%M %p %Z').lstrip('0')
                        schedule_list = "\n".join([
                            f"• **{user.twitch_login or 'No Twitch linked'}** at {_fmt_uk_time(schedule.start_time)}"
                            for schedule, user in schedules
                        ])
                        
                        await interaction.followup.send(
                            f"⚠️ No next rider found for **{current_broadcaster}**\n\n"
                            f"**Today's Schedule ({len(schedules)} trains):**\n{schedule_list}\n\n"
                            f"**Note:** Make sure `{current_broadcaster}` is in the schedule with their Twitch account linked.",
                            ephemeral=True
                        )
                    else:
                        await interaction.followup.send(
                            f"⚠️ No active train schedules found for today in this server.",
                            ephemeral=True
                        )
                return
            
            embed = discord.Embed(
                title="🎯 Next Rider Info",
                description=f"Next rider after **{current_broadcaster}**",
                color=0x9146ff
            )
            
            embed.add_field(
                name="Twitch Username",
                value=next_rider['twitch_login'],
                inline=True
            )
            
            embed.add_field(
                name="Twitch ID",
                value=next_rider.get('twitch_id', 'Not found'),
                inline=True
            )
            
            embed.add_field(
                name="Scheduled Time",
                value=next_rider['time'].strftime('%I:%M %p') + ' UK',
                inline=True
            )
            
            embed.add_field(
                name="Twitch Channel",
                value=f"twitch.tv/{next_rider['twitch_login']}",
                inline=False
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            self.logger.error(f"Error listing next rider: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="test10minwarning", description="Manually send a 10-minute warning to test the system")
    @app_commands.describe(
        current_broadcaster="Twitch username of the current broadcaster"
    )
    @owner_or_trusted()
    async def test_10min_warning(
        self,
        interaction: discord.Interaction,
        current_broadcaster: str
    ):
        """Test the 10-minute warning by sending it manually."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            if not hasattr(self.bot, 'twitch_chat_monitor') or not hasattr(self.bot.twitch_chat_monitor, 'chat_bot'):
                await interaction.followup.send("❌ Twitch chat bot not available", ephemeral=True)
                return
            
            guild_id = interaction.guild.id
            chat_bot = self.bot.twitch_chat_monitor.chat_bot
            
            # Get next rider
            next_rider = await chat_bot.get_next_rider(guild_id, current_broadcaster)
            
            if not next_rider:
                await interaction.followup.send(
                    f"❌ No next rider found for {current_broadcaster} in today's schedule.",
                    ephemeral=True
                )
                return
            
            # Get current broadcaster's Twitch ID
            broadcaster_id = await chat_bot._get_twitch_user_id(current_broadcaster)
            
            if not broadcaster_id:
                await interaction.followup.send(
                    f"❌ Could not find Twitch ID for {current_broadcaster}",
                    ephemeral=True
                )
                return
            
            # Send warning message
            message = f"⏰ 10-MINUTE WARNING! Next rider {next_rider['twitch_login']} goes live in 10 minutes! Get ready to raid: twitch.tv/{next_rider['twitch_login']}"
            success = await chat_bot.send_chat_message(broadcaster_id, message)
            
            if success:
                embed = discord.Embed(
                    title="✅ 10-Minute Warning Sent",
                    description=f"Test warning sent to **{current_broadcaster}**'s chat",
                    color=0x00ff00
                )
                embed.add_field(name="Next Rider", value=next_rider['twitch_login'], inline=True)
                embed.add_field(name="Target Channel", value=f"twitch.tv/{current_broadcaster}", inline=True)
                embed.add_field(name="Message Sent", value=message, inline=False)
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                self.logger.info(f"⏰ Test 10-min warning sent to {current_broadcaster} for {next_rider['twitch_login']}")
            else:
                await interaction.followup.send(
                    "❌ Failed to send warning message. Check bot logs for details.",
                    ephemeral=True
                )
                
        except Exception as e:
            self.logger.error(f"Error testing 10-min warning: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error: {str(e)}",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(ChatBotTestCommands(bot))
