"""
Analytics and statistics commands for train management.
"""

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
import logging
import csv
import io
from database import DatabaseSession
from models import TrainSchedule, TrainParticipant, TrainNotification, User, TwitchChatAttendance
from sqlalchemy import func, desc

class AnalyticsCommands(commands.Cog):
    """Commands for viewing train analytics and statistics."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
    
    async def is_admin_or_owner(ctx):
        """Check if user is bot owner or has admin permissions."""
        if ctx.author.id == ctx.bot.owner_id:
            return True
        if hasattr(ctx.author, 'guild_permissions'):
            return ctx.author.guild_permissions.manage_guild
        return False
    
    @commands.command(name='trainstats', help='Show statistics about train schedules')
    @commands.guild_only()
    @commands.check(is_admin_or_owner)
    async def train_stats(self, ctx):
        """Display analytics about train sessions."""
        try:
            with DatabaseSession() as session:
                guild_id = ctx.guild.id
                
                # Get total active schedules
                total_schedules = session.query(TrainSchedule).filter_by(
                    guild_id=guild_id,
                    is_active=True
                ).count()
                
                # Get total participants
                total_participants = session.query(TrainParticipant).filter_by(
                    guild_id=guild_id,
                    is_active=True
                ).count()
                
                # Get most popular trains (by participant count)
                popular_trains = session.query(
                    TrainSchedule.name,
                    TrainSchedule.id,
                    func.count(TrainParticipant.id).label('participant_count')
                ).join(
                    TrainParticipant,
                    TrainSchedule.id == TrainParticipant.schedule_id
                ).filter(
                    TrainSchedule.guild_id == guild_id,
                    TrainSchedule.is_active == True,
                    TrainParticipant.is_active == True
                ).group_by(
                    TrainSchedule.id,
                    TrainSchedule.name
                ).order_by(
                    desc('participant_count')
                ).limit(5).all()
                
                # Get empty trains
                empty_trains = session.query(TrainSchedule).outerjoin(
                    TrainParticipant,
                    (TrainSchedule.id == TrainParticipant.schedule_id) & 
                    (TrainParticipant.is_active == True)
                ).filter(
                    TrainSchedule.guild_id == guild_id,
                    TrainSchedule.is_active == True,
                    TrainParticipant.id == None
                ).count()
                
                # Get notification statistics (last 7 days)
                week_ago = datetime.utcnow() - timedelta(days=7)
                recent_notifications = session.query(TrainNotification).filter(
                    TrainNotification.guild_id == guild_id,
                    TrainNotification.sent_at >= week_ago
                ).count()
                
                # Calculate utilization rate
                utilization = 0.0
                if total_schedules > 0:
                    filled_trains = total_schedules - empty_trains
                    utilization = (filled_trains / total_schedules) * 100
                
                embed = discord.Embed(
                    title="📊 Train Statistics Dashboard",
                    description=f"Analytics for {ctx.guild.name}",
                    color=0x3498db,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📈 Overview",
                    value=f"**Total Trains:** {total_schedules}\n"
                          f"**Total Signups:** {total_participants}\n"
                          f"**Empty Trains:** {empty_trains}\n"
                          f"**Utilization:** {utilization:.1f}%",
                    inline=True
                )
                
                embed.add_field(
                    name="📅 Activity (7 days)",
                    value=f"**Notifications Sent:** {recent_notifications:,}\n"
                          f"**Avg per Day:** {recent_notifications/7:.1f}",
                    inline=True
                )
                
                if popular_trains:
                    popular_list = []
                    for train in popular_trains:
                        popular_list.append(f"**{train.name}** - {train.participant_count} participants")
                    
                    embed.add_field(
                        name="🏆 Most Popular Trains",
                        value="\n".join(popular_list[:5]),
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="🏆 Most Popular Trains",
                        value="No train data available",
                        inline=False
                    )
                
                # Add interpretation
                if utilization >= 80:
                    status = "🟢 Excellent - Trains are well utilized"
                elif utilization >= 60:
                    status = "🟡 Good - Most trains have participants"
                elif utilization >= 40:
                    status = "🟠 Fair - Some trains are underused"
                else:
                    status = "🔴 Low - Consider consolidating trains"
                
                embed.add_field(
                    name="💡 Status",
                    value=status,
                    inline=False
                )
                
                embed.set_footer(text="Use !schedulestats for detailed schedule analysis")
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error in train stats: {e}")
            await ctx.send(f"❌ Failed to generate train statistics: {str(e)}")
    
    @commands.command(name='userstats', help='Show user participation statistics')
    @commands.guild_only()
    @commands.check(is_admin_or_owner)
    async def user_stats(self, ctx):
        """Display user participation analytics."""
        try:
            with DatabaseSession() as session:
                guild_id = ctx.guild.id
                
                # Get most active users
                active_users = session.query(
                    TrainParticipant.user_id,
                    func.count(TrainParticipant.id).label('train_count')
                ).filter_by(
                    guild_id=guild_id,
                    is_active=True
                ).group_by(
                    TrainParticipant.user_id
                ).order_by(
                    desc('train_count')
                ).limit(10).all()
                
                # Get total unique users
                unique_users = session.query(TrainParticipant.user_id).filter_by(
                    guild_id=guild_id,
                    is_active=True
                ).distinct().count()
                
                # Get users with Twitch linked
                twitch_linked = session.query(TrainParticipant).filter_by(
                    guild_id=guild_id,
                    is_active=True
                ).filter(
                    TrainParticipant.twitch_username != None
                ).distinct(TrainParticipant.user_id).count()
                
                embed = discord.Embed(
                    title="👥 User Participation Statistics",
                    description=f"User analytics for {ctx.guild.name}",
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📊 Summary",
                    value=f"**Total Active Users:** {unique_users}\n"
                          f"**With Twitch Linked:** {twitch_linked}\n"
                          f"**Twitch Link Rate:** {(twitch_linked/unique_users*100) if unique_users > 0 else 0:.1f}%",
                    inline=False
                )
                
                if active_users:
                    # Get Discord user objects
                    user_list = []
                    for idx, (user_id, count) in enumerate(active_users, 1):
                        try:
                            member = await ctx.guild.fetch_member(user_id)
                            user_list.append(f"**{idx}.** {member.mention} - {count} train{'s' if count != 1 else ''}")
                        except:
                            user_list.append(f"**{idx}.** User {user_id} - {count} train{'s' if count != 1 else ''}")
                    
                    embed.add_field(
                        name="🏆 Most Active Users",
                        value="\n".join(user_list[:10]),
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="🏆 Most Active Users",
                        value="No participation data available",
                        inline=False
                    )
                
                embed.set_footer(text="Participation counts show active train signups")
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error in user stats: {e}")
            await ctx.send(f"❌ Failed to generate user statistics: {str(e)}")
    
    @commands.command(name='schedulestats', help='Show detailed schedule analytics')
    @commands.guild_only()
    @commands.check(is_admin_or_owner)
    async def schedule_stats(self, ctx):
        """Display detailed schedule analytics."""
        try:
            with DatabaseSession() as session:
                guild_id = ctx.guild.id
                
                # Get day of week distribution
                import pytz
                eastern_tz = pytz.timezone('Europe/London')
                
                day_distribution = session.query(
                    TrainSchedule.day_of_week,
                    func.count(TrainSchedule.id).label('count')
                ).filter_by(
                    guild_id=guild_id,
                    is_active=True
                ).group_by(
                    TrainSchedule.day_of_week
                ).all()
                
                # Get capacity utilization
                capacity_stats = session.query(
                    TrainSchedule.max_participants,
                    func.count(TrainParticipant.id).label('current_participants')
                ).outerjoin(
                    TrainParticipant,
                    (TrainSchedule.id == TrainParticipant.schedule_id) &
                    (TrainParticipant.is_active == True)
                ).filter(
                    TrainSchedule.guild_id == guild_id,
                    TrainSchedule.is_active == True,
                    TrainSchedule.max_participants != None
                ).group_by(
                    TrainSchedule.id,
                    TrainSchedule.max_participants
                ).all()
                
                embed = discord.Embed(
                    title="📅 Schedule Analytics",
                    description=f"Detailed schedule statistics for {ctx.guild.name}",
                    color=0x667eea,
                    timestamp=datetime.utcnow()
                )
                
                # Day distribution
                if day_distribution:
                    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                    day_list = []
                    for day_num, count in sorted(day_distribution):
                        day_name = days[day_num] if day_num < len(days) else f"Day {day_num}"
                        day_list.append(f"**{day_name}:** {count} train{'s' if count != 1 else ''}")
                    
                    embed.add_field(
                        name="📆 Distribution by Day",
                        value="\n".join(day_list),
                        inline=True
                    )
                
                # Capacity analysis
                if capacity_stats:
                    total_capacity = sum(s.max_participants or 0 for s in capacity_stats)
                    total_filled = sum(s.current_participants for s in capacity_stats)
                    
                    fill_rate = (total_filled / total_capacity * 100) if total_capacity > 0 else 0
                    
                    embed.add_field(
                        name="🎯 Capacity Analysis",
                        value=f"**Total Capacity:** {total_capacity}\n"
                              f"**Currently Filled:** {total_filled}\n"
                              f"**Fill Rate:** {fill_rate:.1f}%",
                        inline=True
                    )
                
                # Recommendations
                recommendations = []
                if day_distribution:
                    max_day = max(day_distribution, key=lambda x: x[1])
                    min_day = min(day_distribution, key=lambda x: x[1])
                    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                    
                    if max_day[1] > min_day[1] * 2:
                        recommendations.append(f"⚖️ Consider adding more trains on {days[min_day[0]]}")
                
                if capacity_stats:
                    fill_rate = (total_filled / total_capacity * 100) if total_capacity > 0 else 0
                    if fill_rate > 90:
                        recommendations.append("📈 Consider increasing train capacity")
                    elif fill_rate < 50:
                        recommendations.append("📉 Consider reducing train slots")
                
                if recommendations:
                    embed.add_field(
                        name="💡 Recommendations",
                        value="\n".join(recommendations),
                        inline=False
                    )
                
                embed.set_footer(text="Use !trainstats for overall statistics")
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error in schedule stats: {e}")
            await ctx.send(f"❌ Failed to generate schedule statistics: {str(e)}")

    @commands.command(name='exportschedules', help='Export all train schedules to CSV')
    @commands.guild_only()
    @commands.check(is_admin_or_owner)
    async def export_schedules(self, ctx):
        """Export all train schedules to a CSV file."""
        try:
            with DatabaseSession() as session:
                guild_id = ctx.guild.id
                
                # Get all active schedules
                schedules = session.query(TrainSchedule).filter_by(
                    guild_id=guild_id,
                    is_active=True
                ).all()
                
                if not schedules:
                    await ctx.send("❌ No active schedules to export.")
                    return
                
                # Create CSV in memory
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Write header
                writer.writerow([
                    'ID',
                    'Name',
                    'Day of Week',
                    'Start Time (UTC)',
                    'Duration (minutes)',
                    'Max Participants',
                    'Description',
                    'Notify Before (minutes)',
                    'Created At'
                ])
                
                # Write schedule data
                days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                for schedule in schedules:
                    day_name = days[schedule.day_of_week] if schedule.day_of_week < len(days) else str(schedule.day_of_week)
                    
                    writer.writerow([
                        schedule.id,
                        schedule.name,
                        day_name,
                        schedule.start_time.strftime('%H:%M:%S'),
                        schedule.duration_minutes,
                        schedule.max_participants or 'Unlimited',
                        schedule.description or '',
                        schedule.notify_before_minutes,
                        schedule.created_at.strftime('%Y-%m-%d %H:%M:%S') if schedule.created_at else ''
                    ])
                
                # Create file from string buffer
                output.seek(0)
                file = discord.File(
                    io.BytesIO(output.getvalue().encode('utf-8')),
                    filename=f'train_schedules_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
                )
                
                embed = discord.Embed(
                    title="📤 Schedule Export",
                    description=f"Exported {len(schedules)} train schedule(s)",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="📊 Export Info",
                    value=f"**Total Schedules:** {len(schedules)}\n"
                          f"**Format:** CSV\n"
                          f"**Guild:** {ctx.guild.name}",
                    inline=False
                )
                
                await ctx.send(embed=embed, file=file)
                self.logger.info(f"Exported {len(schedules)} schedules for guild {guild_id}")
                
        except Exception as e:
            self.logger.error(f"Error exporting schedules: {e}")
            await ctx.send(f"❌ Failed to export schedules: {str(e)}")
    
    @commands.command(name='exportparticipants', help='Export all train participants to CSV')
    @commands.guild_only()
    @commands.check(is_admin_or_owner)
    async def export_participants(self, ctx):
        """Export all train participants to a CSV file."""
        try:
            with DatabaseSession() as session:
                guild_id = ctx.guild.id
                
                # Get all active participants with schedule info
                participants = session.query(
                    TrainParticipant,
                    TrainSchedule.name.label('schedule_name')
                ).join(
                    TrainSchedule,
                    TrainParticipant.schedule_id == TrainSchedule.id
                ).filter(
                    TrainParticipant.guild_id == guild_id,
                    TrainParticipant.is_active == True
                ).all()
                
                if not participants:
                    await ctx.send("❌ No active participants to export.")
                    return
                
                # Create CSV in memory
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Write header
                writer.writerow([
                    'User ID',
                    'Discord Username',
                    'Train Schedule',
                    'Schedule ID',
                    'Is Host',
                    'Twitch Username',
                    'Signed Up At',
                    'Notes'
                ])
                
                # Write participant data
                for participant, schedule_name in participants:
                    # Try to get Discord username
                    try:
                        member = await ctx.guild.fetch_member(participant.user_id)
                        username = f"{member.name}#{member.discriminator}" if member.discriminator != "0" else member.name
                    except:
                        username = f"Unknown ({participant.user_id})"
                    
                    writer.writerow([
                        participant.user_id,
                        username,
                        schedule_name,
                        participant.schedule_id,
                        'Yes' if participant.is_host else 'No',
                        participant.twitch_username or '',
                        participant.signed_up_at.strftime('%Y-%m-%d %H:%M:%S') if participant.signed_up_at else '',
                        participant.notes or ''
                    ])
                
                # Create file from string buffer
                output.seek(0)
                file = discord.File(
                    io.BytesIO(output.getvalue().encode('utf-8')),
                    filename=f'train_participants_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
                )
                
                embed = discord.Embed(
                    title="📤 Participant Export",
                    description=f"Exported {len(participants)} participant(s)",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                # Count Twitch linked
                twitch_linked = sum(1 for p, _ in participants if p.twitch_username)
                
                embed.add_field(
                    name="📊 Export Info",
                    value=f"**Total Participants:** {len(participants)}\n"
                          f"**With Twitch:** {twitch_linked}\n"
                          f"**Format:** CSV\n"
                          f"**Guild:** {ctx.guild.name}",
                    inline=False
                )
                
                await ctx.send(embed=embed, file=file)
                self.logger.info(f"Exported {len(participants)} participants for guild {guild_id}")
                
        except Exception as e:
            self.logger.error(f"Error exporting participants: {e}")
            await ctx.send(f"❌ Failed to export participants: {str(e)}")

    @app_commands.command(name='attendancestats', description='View attendance statistics across all trains')
    @app_commands.describe(
        days='Number of days to look back (default: 30)',
        user='Optional: Check stats for a specific user'
    )
    async def attendance_stats(self, interaction: discord.Interaction, days: int = 30, user: discord.Member = None):
        """Show aggregate attendance statistics from Twitch chat monitoring."""
        try:
            await interaction.response.defer()
            
            with DatabaseSession() as session:
                from models import TwitchChatAttendance, User as DBUser
                from datetime import date, timedelta
                
                # Calculate date range
                end_date = date.today()
                start_date = end_date - timedelta(days=days)
                
                # Build query
                if user:
                    # Stats for specific user
                    attendance_records = session.query(TwitchChatAttendance).filter(
                        TwitchChatAttendance.guild_id == interaction.guild.id,
                        TwitchChatAttendance.user_id == user.id,
                        TwitchChatAttendance.train_date >= start_date,
                        TwitchChatAttendance.was_present_in_chat == True
                    ).all()
                    
                    if not attendance_records:
                        await interaction.followup.send(
                            f"❌ No attendance records found for {user.mention} in the last {days} days.",
                            ephemeral=True
                        )
                        return
                    
                    # Group by schedule to count 25-minute intervals
                    schedule_counts = {}
                    for record in attendance_records:
                        if record.schedule_id not in schedule_counts:
                            schedule_counts[record.schedule_id] = {
                                'count': 0,
                                'dates': set()
                            }
                        schedule_counts[record.schedule_id]['count'] += 1
                        schedule_counts[record.schedule_id]['dates'].add(record.train_date)
                    
                    embed = discord.Embed(
                        title=f"📊 Attendance Statistics for {user.display_name}",
                        description=f"Last {days} days • {len(attendance_records)} total check-ins",
                        color=0x9146ff,
                        timestamp=datetime.utcnow()
                    )
                    
                    # Add stats
                    total_intervals = len(attendance_records)
                    unique_trains = len(schedule_counts)
                    unique_days = len(set(r.train_date for r in attendance_records))
                    
                    embed.add_field(
                        name="📈 Summary",
                        value=f"**Total 25-min Check-ins:** {total_intervals}\n"
                              f"**Unique Train Slots:** {unique_trains}\n"
                              f"**Days Active:** {unique_days}/{days}",
                        inline=False
                    )
                    
                    # Show top trains
                    top_trains = sorted(schedule_counts.items(), key=lambda x: x[1]['count'], reverse=True)[:5]
                    if top_trains:
                        train_list = []
                        for schedule_id, data in top_trains:
                            schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
                            if schedule:
                                train_list.append(f"**{schedule.name}**: {data['count']} check-ins")
                        
                        if train_list:
                            embed.add_field(
                                name="🎯 Most Active Trains",
                                value="\n".join(train_list),
                                inline=False
                            )
                    
                    await interaction.followup.send(embed=embed)
                    
                else:
                    # Server-wide stats
                    attendance_records = session.query(
                        TwitchChatAttendance.user_id,
                        TwitchChatAttendance.twitch_username,
                        func.count(TwitchChatAttendance.id).label('check_in_count'),
                        func.count(func.distinct(TwitchChatAttendance.schedule_id)).label('unique_trains'),
                        func.count(func.distinct(TwitchChatAttendance.train_date)).label('unique_days')
                    ).filter(
                        TwitchChatAttendance.guild_id == interaction.guild.id,
                        TwitchChatAttendance.train_date >= start_date,
                        TwitchChatAttendance.was_present_in_chat == True
                    ).group_by(
                        TwitchChatAttendance.user_id,
                        TwitchChatAttendance.twitch_username
                    ).order_by(
                        desc('check_in_count')
                    ).limit(25).all()
                    
                    if not attendance_records:
                        await interaction.followup.send(
                            f"❌ No attendance records found for this server in the last {days} days.",
                            ephemeral=True
                        )
                        return
                    
                    embed = discord.Embed(
                        title="📊 Server Attendance Leaderboard",
                        description=f"Last {days} days • Sorted by check-ins\n*Each check-in = 25-minute interval*",
                        color=0x9146ff,
                        timestamp=datetime.utcnow()
                    )
                    
                    # Build leaderboard
                    leaderboard_lines = []
                    for idx, (user_id, twitch_name, check_ins, unique_trains, unique_days) in enumerate(attendance_records, 1):
                        # Try to get Discord member
                        try:
                            member = await interaction.guild.fetch_member(user_id)
                            user_display = member.mention
                        except:
                            user_display = f"`{twitch_name}`"
                        
                        # Emoji for top 3
                        emoji = ""
                        if idx == 1:
                            emoji = "🥇 "
                        elif idx == 2:
                            emoji = "🥈 "
                        elif idx == 3:
                            emoji = "🥉 "
                        
                        leaderboard_lines.append(
                            f"{emoji}**{idx}.** {user_display} - {check_ins} check-ins ({unique_trains} trains, {unique_days} days)"
                        )
                    
                    # Split into multiple fields if needed (25 lines max per field)
                    for i in range(0, len(leaderboard_lines), 10):
                        chunk = leaderboard_lines[i:i+10]
                        embed.add_field(
                            name=f"Top {i+1}-{min(i+10, len(leaderboard_lines))}" if i > 0 else "Top Participants",
                            value="\n".join(chunk),
                            inline=False
                        )
                    
                    # Add summary footer
                    total_participants = len(attendance_records)
                    embed.set_footer(text=f"Showing top {total_participants} participants")
                    
                    await interaction.followup.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error showing attendance stats: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ Error retrieving attendance statistics: {str(e)}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ Error retrieving attendance statistics: {str(e)}", ephemeral=True)
            except:
                pass

async def setup(bot):
    await bot.add_cog(AnalyticsCommands(bot))
