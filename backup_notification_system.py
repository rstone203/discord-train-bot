"""
Backup Notification System - Ensures notifications are NEVER missed
This runs parallel to the main scheduler and catches any missed notifications.
"""

import asyncio
import discord
from datetime import datetime, timedelta
import pytz
import logging
from database import DatabaseSession
from models import TrainSchedule, TrainNotification

logger = logging.getLogger(__name__)

class BackupNotificationSystem:
    """Bulletproof backup system to ensure notifications are never missed."""
    
    def __init__(self, bot):
        self.bot = bot
        self.est_tz = pytz.timezone('Europe/London')
        
    async def start_backup_monitoring(self):
        """Start the backup monitoring system."""
        while True:
            try:
                await self.check_and_recover_missed_notifications()
                # Run every 2 minutes (more frequently than main scheduler)
                await asyncio.sleep(120)
            except Exception as e:
                logger.error(f"Backup system error: {e}")
                await asyncio.sleep(60)  # Shorter delay on error
    
    async def check_and_recover_missed_notifications(self):
        """Check for missed notifications and send them immediately."""
        try:
            # Check for maintenance mode first
            try:
                from models import SystemSettings
                with DatabaseSession() as maintenance_session:
                    maintenance_setting = maintenance_session.query(SystemSettings).filter_by(
                        setting_key='maintenance_mode'
                    ).first()
                    
                    if maintenance_setting and maintenance_setting.is_enabled:
                        # Skip all backup notification processing in maintenance mode
                        return
            except Exception as e:
                logger.debug(f"Maintenance mode check failed, proceeding normally: {e}")
            
            now = datetime.now(self.est_tz)
            today_date = now.date()
            current_day = now.weekday()
            
            logger.info(f"🔍 Backup system scanning at {now.strftime('%I:%M %p EST')}")
            
            with DatabaseSession() as session:
                # Get active schedules for today
                schedules = session.query(TrainSchedule).filter_by(
                    day_of_week=current_day,
                    is_active=True
                ).all()
                
                recovery_count = 0
                
                for schedule in schedules:
                    # Calculate timing
                    start_datetime = self.est_tz.localize(
                        datetime.combine(today_date, schedule.start_time)
                    )

                    # Use the schedule's configured lead time (default 60 min)
                    notify_before = getattr(schedule, 'notify_before_minutes', 60) or 60

                    # Check each stage — mirror the main notification system's windows
                    stages = [
                        (1, start_datetime - timedelta(minutes=notify_before)),  # Stage 1: configurable lead
                        (2, start_datetime - timedelta(minutes=30)),              # Stage 2: 30 min before
                        (3, start_datetime - timedelta(minutes=20))               # Stage 3: 20 min before
                    ]
                    
                    for stage_num, stage_time in stages:
                        # Should this stage have been sent by now?
                        if stage_time <= now:
                            # Check if it exists and was sent
                            notification = session.query(TrainNotification).filter_by(
                                schedule_id=schedule.id,
                                notification_date=today_date,
                                stage=stage_num
                            ).first()
                            
                            # If missing or not sent, and within reasonable catch-up window
                            time_since = now - stage_time
                            if time_since.total_seconds() <= 7200:  # 2 hour catch-up window
                                if not notification:
                                    # Create missing notification
                                    await self.create_recovery_notification(
                                        schedule, stage_num, today_date, session
                                    )
                                    recovery_count += 1
                                    logger.info(f"🚨 RECOVERED: Created missing {schedule.name} Stage {stage_num}")
                                elif not notification.is_sent:
                                    # Send unsent notification
                                    await self.send_recovery_notification(notification, session)
                                    recovery_count += 1
                                    logger.info(f"🚨 RECOVERED: Sent pending {schedule.name} Stage {stage_num}")
                
                if recovery_count > 0:
                    logger.info(f"✅ Backup system recovered {recovery_count} missed notifications!")
                    
        except Exception as e:
            logger.error(f"Backup recovery error: {e}")
    
    async def create_recovery_notification(self, schedule, stage, today_date, session):
        """Create a missing notification record."""
        try:
            notification = TrainNotification(
                schedule_id=schedule.id,
                guild_id=schedule.guild_id,
                notification_date=today_date,
                stage=stage,
                channel_id=1294082148839591956  # pings channel
            )
            session.add(notification)
            session.commit()
            
            # Immediately send it
            await self.send_recovery_notification(notification, session)
            
        except Exception as e:
            logger.error(f"Error creating recovery notification: {e}")
    
    async def send_recovery_notification(self, notification, session):
        """Send a recovered notification immediately."""
        try:
            guild = self.bot.get_guild(notification.guild_id)
            if not guild:
                return
                
            channel = guild.get_channel(notification.channel_id)
            if not channel:
                return
                
            # Get schedule details
            schedule = session.query(TrainSchedule).filter_by(
                id=notification.schedule_id
            ).first()
            
            if not schedule:
                return
            
            # Get participant to ping
            participant_ping = ""
            if schedule.participant_ids and len(schedule.participant_ids) > 0:
                participant_id = schedule.participant_ids[0]
                participant_ping = f"<@{participant_id}> "
            
            # Create embed
            embed = discord.Embed(
                title='🚂 Raid Train Starting Soon!',
                description=f'**{schedule.name}** notification (Stage {notification.stage})',
                color=0x9146ff
            )
            
            # Calculate timing
            est_tz = pytz.timezone('Europe/London')
            now = datetime.now(est_tz)
            start_datetime = est_tz.localize(
                datetime.combine(notification.notification_date, schedule.start_time)
            )
            time_until = start_datetime - now
            
            if time_until.total_seconds() > 0:
                minutes_until = int(time_until.total_seconds() / 60)
                embed.add_field(
                    name='⏰ Train Start Time',
                    value=f'{start_datetime.strftime("%I:%M %p EST")} (in {minutes_until} minutes)',
                    inline=False
                )
            else:
                embed.add_field(
                    name='⏰ Train Start Time',
                    value=f'{start_datetime.strftime("%I:%M %p EST")} (NOW)',
                    inline=False
                )
            
            embed.add_field(
                name='📋 Instructions',
                value='React with ✅ if you\'re ready to stream!\nReact with ❌ if you can\'t make it\nReact with 🔄 if you need a backup',
                inline=False
            )
            
            # Send message
            message_content = f'{participant_ping}🚂 **Your train slot is starting soon!** (RECOVERED)'
            message = await channel.send(content=message_content, embed=embed)
            
            # Add reactions
            await message.add_reaction('✅')
            await message.add_reaction('❌')
            await message.add_reaction('🔄')
            
            # Update database with EST time
            est_now = datetime.now(self.est_tz)
            session.query(TrainNotification).filter_by(
                id=notification.id
            ).update({
                'is_sent': True,
                'sent_at': est_now,
                'message_id': message.id
            })
            session.commit()
            
            logger.info(f"✅ RECOVERY SUCCESS: Sent {schedule.name} Stage {notification.stage}")
            
        except Exception as e:
            logger.error(f"Error sending recovery notification: {e}")