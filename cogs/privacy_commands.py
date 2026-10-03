"""
Privacy and data management commands for TOS compliance.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from datetime import datetime
from database import DatabaseSession
from models import User, TwitchChatAttendance, TrainParticipant, TrainAttendance, Message

logger = logging.getLogger('discord_bot.privacy_commands')

class PrivacyCommands(commands.Cog):
    """Commands for managing user data and privacy."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.privacy_commands')
    
    @app_commands.command(name='deletemydata', description='Request deletion of your attendance and participation data')
    @app_commands.describe(
        scope='What data to delete: attendance_only (default) or all_data (everything)'
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name='Attendance Data Only', value='attendance_only'),
        app_commands.Choice(name='All My Data (Complete Removal)', value='all_data')
    ])
    async def delete_my_data(self, interaction: discord.Interaction, scope: str = 'attendance_only'):
        """
        Allow users to request deletion of their data for TOS compliance.
        
        Twitch Developer Agreement requires providing users ability to:
        - Request data deletion
        - Opt-out of data retention
        - Block data collection
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            user_id = interaction.user.id
            deleted_items = []
            
            with DatabaseSession() as session:
                if scope == 'attendance_only':
                    # Delete chat attendance records
                    chat_attendance = session.query(TwitchChatAttendance).filter_by(user_id=user_id).all()
                    chat_count = len(chat_attendance)
                    for record in chat_attendance:
                        session.delete(record)
                    
                    # Delete readiness/reaction attendance
                    train_attendance = session.query(TrainAttendance).filter_by(user_id=user_id).all()
                    reaction_count = len(train_attendance)
                    for record in train_attendance:
                        session.delete(record)
                    
                    deleted_items.append(f"✅ {chat_count} chat attendance records")
                    deleted_items.append(f"✅ {reaction_count} readiness tracking records")
                    
                    session.commit()
                    
                    # Remove from active monitoring sessions
                    if hasattr(self.bot, 'twitch_monitor'):
                        for channel_name, participants in self.bot.twitch_monitor.chat_participants.items():
                            # Get user's Twitch username to remove from active tracking
                            user_record = session.query(User).filter_by(id=user_id).first()
                            if user_record and user_record.twitch_login:
                                if user_record.twitch_login.lower() in participants:
                                    participants.remove(user_record.twitch_login.lower())
                                    logger.info(f"Removed {user_record.twitch_login} from active monitoring")
                    
                    embed = discord.Embed(
                        title="✅ Attendance Data Deleted",
                        description=(
                            "Your attendance and participation data has been removed:\n\n"
                            + "\n".join(deleted_items) + "\n\n"
                            "**What was deleted:**\n"
                            "• Twitch chat attendance records\n"
                            "• Train readiness tracking\n"
                            "• Active monitoring session data\n\n"
                            "**What remains:**\n"
                            "• Your Discord profile and train sign-ups\n"
                            "• Twitch account linking (if you have one)\n\n"
                            "To delete ALL your data completely, use:\n"
                            "`/deletemydata scope: All My Data`"
                        ),
                        color=discord.Color.green(),
                        timestamp=datetime.utcnow()
                    )
                    
                elif scope == 'all_data':
                    # Complete data removal
                    
                    # 1. Delete chat attendance
                    chat_attendance = session.query(TwitchChatAttendance).filter_by(user_id=user_id).delete()
                    
                    # 2. Delete train attendance/readiness
                    train_attendance = session.query(TrainAttendance).filter_by(user_id=user_id).delete()
                    
                    # 3. Delete train participations
                    participants = session.query(TrainParticipant).filter_by(user_id=user_id).delete()
                    
                    # 4. Delete messages
                    messages = session.query(Message).filter_by(user_id=user_id).delete()
                    
                    # 5. Delete user record (profile + Twitch linking)
                    user_record = session.query(User).filter_by(id=user_id).first()
                    if user_record:
                        session.delete(user_record)
                    
                    session.commit()
                    
                    # Remove from active monitoring
                    if hasattr(self.bot, 'twitch_monitor'):
                        for channel_name, participants in self.bot.twitch_monitor.chat_participants.items():
                            participants.discard(interaction.user.name.lower())
                    
                    embed = discord.Embed(
                        title="✅ All Data Deleted",
                        description=(
                            "**Your data has been completely removed from the system:**\n\n"
                            "✅ Chat attendance records\n"
                            "✅ Train readiness tracking\n"
                            "✅ Train sign-ups and participation\n"
                            "✅ Twitch account linking\n"
                            "✅ User profile and settings\n"
                            "✅ Forwarded messages\n"
                            "✅ Active monitoring sessions\n\n"
                            "**What this means:**\n"
                            "• You've been removed from all trains\n"
                            "• Your Twitch account is unlinked\n"
                            "• No attendance data is retained\n"
                            "• You can sign up again anytime\n\n"
                            "If you rejoin trains, a new profile will be created."
                        ),
                        color=discord.Color.red(),
                        timestamp=datetime.utcnow()
                    )
                
                embed.set_footer(text="Data deletion requested via /deletemydata • TOS Compliance")
                await interaction.followup.send(embed=embed, ephemeral=True)
                
                # Log the deletion for compliance tracking
                logger.info(f"🗑️ Data deletion request processed: User {user_id} ({interaction.user.name}) - Scope: {scope}")
                
        except Exception as e:
            logger.error(f"Error processing data deletion request for user {user_id}: {e}")
            await interaction.followup.send(
                "❌ An error occurred while processing your data deletion request. "
                "Please contact a server administrator.",
                ephemeral=True
            )
    
    @app_commands.command(name='mydata', description='View what data is stored about you')
    async def my_data(self, interaction: discord.Interaction):
        """Show users what data the bot has stored about them."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            user_id = interaction.user.id
            
            with DatabaseSession() as session:
                # Get user record
                user = session.query(User).filter_by(id=user_id).first()
                
                # Count records
                chat_attendance_count = session.query(TwitchChatAttendance).filter_by(user_id=user_id).count()
                train_attendance_count = session.query(TrainAttendance).filter_by(user_id=user_id).count()
                train_participation_count = session.query(TrainParticipant).filter_by(user_id=user_id, is_active=True).count()
                message_count = session.query(Message).filter_by(user_id=user_id).count()
                
                embed = discord.Embed(
                    title="📊 Your Data Summary",
                    description="Here's what data we have stored about you:",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                
                # Profile section
                if user:
                    profile_info = [
                        f"**Username:** {user.username}",
                        f"**Display Name:** {user.display_name or 'Not set'}",
                        f"**First Seen:** {user.first_seen.strftime('%Y-%m-%d')}",
                        f"**Messages:** {user.message_count}"
                    ]
                    
                    if user.twitch_login:
                        profile_info.append(f"**Twitch:** {user.twitch_login}")
                        profile_info.append(f"**Linked:** {user.twitch_linked_at.strftime('%Y-%m-%d')}")
                    
                    embed.add_field(
                        name="👤 Profile",
                        value="\n".join(profile_info),
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="👤 Profile",
                        value="No profile data stored",
                        inline=False
                    )
                
                # Attendance section
                embed.add_field(
                    name="📈 Attendance Records",
                    value=(
                        f"**Chat Attendance:** {chat_attendance_count} records\n"
                        f"**Readiness Tracking:** {train_attendance_count} records"
                    ),
                    inline=False
                )
                
                # Participation section
                embed.add_field(
                    name="🚂 Train Participation",
                    value=f"**Active Trains:** {train_participation_count} sign-ups",
                    inline=False
                )
                
                # Messages section
                if message_count > 0:
                    embed.add_field(
                        name="💬 Messages",
                        value=f"**Forwarded Messages:** {message_count}",
                        inline=False
                    )
                
                embed.add_field(
                    name="🔒 Your Rights",
                    value=(
                        "• View this data anytime with `/mydata`\n"
                        "• Delete attendance data with `/deletemydata`\n"
                        "• Request complete removal with `/deletemydata scope: All My Data`"
                    ),
                    inline=False
                )
                
                embed.set_footer(text="Data transparency • TOS Compliance")
                await interaction.followup.send(embed=embed, ephemeral=True)
                
        except Exception as e:
            logger.error(f"Error fetching data summary for user {user_id}: {e}")
            await interaction.followup.send(
                "❌ An error occurred while fetching your data summary.",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(PrivacyCommands(bot))
