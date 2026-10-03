"""Manager for comprehensive train attendance reports."""

import logging
import json
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List
import pytz
import discord
from database import DatabaseSession
from models import (
    ComprehensiveTrainReport,
    TwitchChatAttendance,
    TrainSchedule,
    NotificationSettings,
    Guild,
    User
)

logger = logging.getLogger('discord_bot.comprehensive_report_manager')

class ComprehensiveReportManager:
    """Manages comprehensive daily/weekend train attendance reports."""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def generate_and_schedule_reports(self):
        """
        Check for completed train days and generate comprehensive reports.
        Schedules them to be sent at 12pm UTC the next day.
        """
        try:
            utc_tz = pytz.UTC
            now_utc = datetime.now(utc_tz)
            
            with DatabaseSession() as session:
                # Get all guilds with comprehensive reports enabled
                settings_list = session.query(NotificationSettings).filter_by(
                    comprehensive_reports_enabled=True
                ).all()
                
                for settings in settings_list:
                    guild_id = settings.guild_id
                    
                    # Get all active schedules for this guild
                    schedules = session.query(TrainSchedule).filter_by(
                        guild_id=guild_id,
                        is_active=True
                    ).all()
                    
                    if not schedules:
                        continue
                    
                    # Group schedules by day of week
                    train_days = sorted(set(s.day_of_week for s in schedules))
                    
                    # Determine if we should generate a report
                    # Logic: If today is the day after the last train day
                    today_day_of_week = now_utc.weekday()
                    
                    # Check if yesterday was a train day
                    yesterday = (now_utc - timedelta(days=1)).date()
                    yesterday_day_of_week = (today_day_of_week - 1) % 7
                    
                    if yesterday_day_of_week in train_days:
                        # Check if this is the last consecutive train day
                        is_last_day = self._is_last_consecutive_day(yesterday_day_of_week, train_days)
                        
                        if is_last_day:
                            # Determine which days to include in the report
                            report_days = self._get_consecutive_days_ending_with(yesterday_day_of_week, train_days)
                            
                            # Check if a report already exists for this day. Do NOT filter
                            # on is_sent — once a report has been sent, a sent=True row must
                            # still block regeneration, otherwise a frequently-running
                            # scheduler would create and re-send duplicates after the first
                            # send.
                            existing_report = session.query(ComprehensiveTrainReport).filter_by(
                                guild_id=guild_id,
                                report_date=yesterday
                            ).first()
                            
                            if not existing_report:
                                # Generate the report
                                report = await self._generate_report(
                                    session,
                                    guild_id,
                                    yesterday,
                                    report_days
                                )
                                
                                if report:
                                    session.add(report)
                                    session.commit()
                                    logger.info(f"✅ Generated comprehensive report for guild {guild_id}, covering {len(report_days)} day(s)")
        
        except Exception as e:
            logger.error(f"❌ Error generating comprehensive reports: {e}", exc_info=True)
    
    def _is_last_consecutive_day(self, day: int, train_days: List[int]) -> bool:
        """Check if this day is the last in a sequence of consecutive train days."""
        if day not in train_days:
            return False
        
        next_day = (day + 1) % 7
        return next_day not in train_days
    
    def _get_consecutive_days_ending_with(self, end_day: int, train_days: List[int]) -> List[int]:
        """Get all consecutive train days ending with the specified day."""
        result = [end_day]
        current = end_day
        
        while True:
            previous = (current - 1) % 7
            if previous in train_days:
                result.insert(0, previous)
                current = previous
            else:
                break
        
        return result
    
    async def _generate_report(
        self,
        session,
        guild_id: int,
        report_date: date,
        train_days: List[int]
    ) -> Optional[ComprehensiveTrainReport]:
        """Generate a comprehensive report for the specified days."""
        try:
            # Collect all attendance records for these days
            all_attendance = []
            train_breakdown = []
            
            for day_of_week in train_days:
                # Get schedules for this day
                schedules = session.query(TrainSchedule).filter_by(
                    guild_id=guild_id,
                    day_of_week=day_of_week,
                    is_active=True
                ).all()
                
                for schedule in schedules:
                    # Get attendance for this schedule on the report date (or nearby dates)
                    # We need to find the actual train_date
                    # Scope attendance to this report's actual coverage span. train_date is an
                    # EST date while report_date is derived in UTC, so a small window is needed
                    # for the offset, but a flat 7-day lookback would re-include attendance from
                    # an expired one-time train that still shares this weekday (it stays
                    # is_active and keeps its day_of_week), producing a duplicate report next
                    # week. Bounding to the consecutive-day span keeps recurring/weekend reports
                    # intact while excluding stale prior-week data.
                    window_start = report_date - timedelta(days=len(train_days))
                    attendance_records = session.query(TwitchChatAttendance).filter_by(
                        schedule_id=schedule.id,
                        guild_id=guild_id
                    ).filter(
                        TwitchChatAttendance.train_date >= window_start,
                        TwitchChatAttendance.train_date <= report_date
                    ).all()
                    
                    if attendance_records:
                        all_attendance.extend(attendance_records)
                        
                        # Build per-train stats
                        unique_chatters_this_train = set(r.twitch_username for r in attendance_records)
                        total_messages_this_train = sum(r.total_messages for r in attendance_records)
                        
                        train_breakdown.append({
                            "train_name": schedule.name,
                            "day_of_week": day_of_week,
                            "unique_chatters": len(unique_chatters_this_train),
                            "total_messages": total_messages_this_train,
                            "chatter_list": sorted(list(unique_chatters_this_train))
                        })
            
            if not all_attendance:
                logger.info(f"No attendance data found for guild {guild_id} on {report_date}")
                return None
            
            # Aggregate statistics
            unique_chatters = set(r.twitch_username for r in all_attendance)
            total_messages = sum(r.total_messages for r in all_attendance)
            
            # Calculate scheduled send time (12pm UTC next day)
            utc_tz = pytz.UTC
            next_day = report_date + timedelta(days=1)
            scheduled_send = datetime.combine(next_day, datetime.min.time()).replace(hour=12, tzinfo=utc_tz)
            
            # Create report
            report = ComprehensiveTrainReport(
                guild_id=guild_id,
                report_date=report_date,
                train_days=train_days,
                total_trains=len(train_breakdown),
                total_unique_chatters=len(unique_chatters),
                total_messages=total_messages,
                chatter_names=sorted(list(unique_chatters)),
                train_breakdown=json.dumps(train_breakdown, indent=2),
                scheduled_send_time=scheduled_send,
                report_generated_at=datetime.now(utc_tz).replace(tzinfo=None),
                is_sent=False
            )
            
            return report
            
        except Exception as e:
            logger.error(f"❌ Error generating report: {e}", exc_info=True)
            return None
    
    async def send_pending_reports(self):
        """Send all comprehensive reports scheduled to be sent now."""
        try:
            utc_tz = pytz.UTC
            now_utc = datetime.now(utc_tz)
            
            with DatabaseSession() as session:
                # Find reports scheduled to be sent now or earlier
                pending_reports = session.query(ComprehensiveTrainReport).filter(
                    ComprehensiveTrainReport.is_sent == False,
                    ComprehensiveTrainReport.scheduled_send_time <= now_utc
                ).all()
                
                # Suppress very old pending reports instead of dumping them into the
                # channel. Without this, wiring up the scheduler would immediately post
                # any report that has been sitting unsent for months.
                stale_cutoff = datetime.utcnow() - timedelta(days=3)
                
                for report in pending_reports:
                    scheduled = report.scheduled_send_time
                    if scheduled is not None and scheduled.tzinfo is not None:
                        scheduled = scheduled.replace(tzinfo=None)
                    if scheduled is not None and scheduled < stale_cutoff:
                        report.is_sent = True
                        report.report_sent_at = datetime.now(utc_tz).replace(tzinfo=None)
                        session.commit()
                        logger.info(f"⏭️ Suppressed stale comprehensive report for guild {report.guild_id} (was scheduled {scheduled})")
                        continue
                    
                    success = await self._send_report_to_discord(report)
                    
                    if success:
                        report.is_sent = True
                        report.report_sent_at = datetime.now(utc_tz).replace(tzinfo=None)
                        session.commit()
                        logger.info(f"✅ Sent comprehensive report for guild {report.guild_id}")
                    else:
                        logger.warning(f"⚠️ Failed to send report for guild {report.guild_id}")
        
        except Exception as e:
            logger.error(f"❌ Error sending pending reports: {e}", exc_info=True)
    
    async def _send_report_to_discord(self, report: ComprehensiveTrainReport) -> bool:
        """Send a comprehensive report to Discord."""
        try:
            # Validate report data
            if not report or not report.guild_id:
                logger.error("Invalid report data - missing guild_id")
                return False
            
            with DatabaseSession() as session:
                # Get notification settings
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=report.guild_id
                ).first()
                
                if not settings or not settings.comprehensive_report_channel_id:
                    logger.warning(f"No comprehensive report channel set for guild {report.guild_id}")
                    return False
                
                # Get guild
                guild = self.bot.get_guild(report.guild_id)
                if not guild:
                    logger.warning(f"Guild {report.guild_id} not found")
                    return False
                
                # Get channel
                channel = guild.get_channel(settings.comprehensive_report_channel_id)
                if not channel:
                    logger.warning(f"Channel {settings.comprehensive_report_channel_id} not found in guild {guild.name}")
                    return False
                
                # Build embed
                embed = discord.Embed(
                    title="📊 Comprehensive Train Attendance Report",
                    description=f"Report for {self._format_date_range(report.report_date, report.train_days)}",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(pytz.UTC)
                )
                
                # Overall statistics
                embed.add_field(
                    name="📈 Overall Statistics",
                    value=(
                        f"**Total Trains:** {report.total_trains}\n"
                        f"**Unique Chatters:** {report.total_unique_chatters}"
                    ),
                    inline=False
                )
                
                # Top Supporters — rank chatters by how many trains they attended.
                # Only meaningful when more than one train ran; for a single train the
                # full participant list below already covers everyone.
                if report.train_breakdown and report.total_trains > 1:
                    from collections import Counter
                    attendance_counter: Counter = Counter()
                    for train in json.loads(report.train_breakdown):
                        for chatter in train.get('chatter_list', []):
                            attendance_counter[chatter] += 1
                    top_supporters = attendance_counter.most_common(10)
                    if top_supporters:
                        medals = ["🥇", "🥈", "🥉"]
                        lines = []
                        for idx, (name, count) in enumerate(top_supporters):
                            prefix = medals[idx] if idx < 3 else f"`{idx + 1}.`"
                            slot_word = "train" if count == 1 else "trains"
                            lines.append(f"{prefix} `{name}` — {count} {slot_word}")
                        embed.add_field(
                            name="🏆 Top Supporters",
                            value="\n".join(lines),
                            inline=False
                        )
                
                # Per-train breakdown — show names per slot
                if report.train_breakdown:
                    breakdown = json.loads(report.train_breakdown)
                    day_names_full = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    
                    breakdown_chunks = []
                    current_chunk = ""
                    for train in breakdown:
                        day_name = day_names_full[train['day_of_week']]
                        names = ", ".join(f"`{n}`" for n in sorted(train.get('chatter_list', []))) or "—"
                        line = (
                            f"**{train['train_name']}** ({day_name})\n"
                            f"└ {train['unique_chatters']} chatters\n"
                            f"└ {names}\n\n"
                        )
                        if len(current_chunk) + len(line) > 1024:
                            breakdown_chunks.append(current_chunk.strip())
                            current_chunk = line
                        else:
                            current_chunk += line
                    if current_chunk.strip():
                        breakdown_chunks.append(current_chunk.strip())
                    
                    for idx, chunk in enumerate(breakdown_chunks):
                        field_name = "🚂 Train Breakdown" if idx == 0 else f"🚂 Train Breakdown (cont. {idx+1})"
                        embed.add_field(name=field_name, value=chunk, inline=False)
                
                # Full participant list — send all names, split across fields if needed
                if report.chatter_names:
                    all_names = sorted(report.chatter_names)
                    numbered = [f"`{i+1}.` {name}" for i, name in enumerate(all_names)]
                    # Discord field value limit is 1024 chars — chunk into groups
                    chunks = []
                    current = ""
                    for entry in numbered:
                        if len(current) + len(entry) + 2 > 1024:
                            chunks.append(current.strip())
                            current = entry + "\n"
                        else:
                            current += entry + "\n"
                    if current.strip():
                        chunks.append(current.strip())
                    
                    for idx, chunk in enumerate(chunks):
                        field_name = "👥 All Participants" if idx == 0 else f"👥 Participants (cont. {idx+1})"
                        embed.add_field(name=field_name, value=chunk, inline=False)
                
                embed.add_field(
                    name="ℹ️ How attendance is counted",
                    value=(
                        "Counts everyone the bot detected in Twitch chat. For channels where "
                        "the bot isn't a moderator, only people who **typed in chat** are "
                        "counted — silent lurkers may be missed, so totals are a floor, not a ceiling."
                    ),
                    inline=False
                )
                embed.set_footer(text="Comprehensive attendance tracked via Twitch chat")
                
                # Send to channel — may need multiple messages if embed is too large
                try:
                    await channel.send(embed=embed)
                    logger.info(f"✅ Comprehensive report sent to {guild.name} #{channel.name}")
                    return True
                except discord.HTTPException as e:
                    if e.status == 400 and 'embeds' in str(e).lower():
                        # Embed too large — split into two messages
                        try:
                            summary_embed = discord.Embed(
                                title=embed.title,
                                description=embed.description,
                                color=embed.color,
                                timestamp=embed.timestamp
                            )
                            for field in embed.fields[:3]:
                                summary_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                            summary_embed.set_footer(text=embed.footer.text)
                            await channel.send(embed=summary_embed)
                            
                            participant_fields = [f for f in embed.fields if 'Participant' in f.name]
                            if participant_fields:
                                p_embed = discord.Embed(title="👥 Full Participant List", color=embed.color)
                                for field in participant_fields:
                                    p_embed.add_field(name=field.name, value=field.value, inline=field.inline)
                                await channel.send(embed=p_embed)
                            return True
                        except Exception as split_err:
                            logger.error(f"❌ Failed to send split report: {split_err}")
                            return False
                    logger.error(f"❌ Discord API error sending report: {e}")
                    return False
                except discord.Forbidden:
                    logger.error(f"❌ Missing permissions to send to {guild.name} #{channel.name}")
                    return False
                
        except Exception as e:
            logger.error(f"❌ Error sending report to Discord: {e}", exc_info=True)
            return False
    
    def _format_date_range(self, end_date: date, train_days: List[int]) -> str:
        """Format a date range for the report."""
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        
        if len(train_days) == 1:
            return f"{day_names[train_days[0]]}, {end_date.strftime('%B %d, %Y')}"
        else:
            start_date = end_date - timedelta(days=len(train_days) - 1)
            return f"{start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')}"
