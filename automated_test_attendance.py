#!/usr/bin/env python3
"""
Automated attendance testing system for Discord raid trains.

This module provides functionality for automated testing of the attendance tracking system,
sending notifications at regular intervals and monitoring Twitch chat presence.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from database import DatabaseSession
from models import TrainSchedule, NotificationSettings, User, TrainParticipant
import discord

logger = logging.getLogger(__name__)

# Global task tracking
_automated_test_task: Optional[asyncio.Task] = None
_is_running = False

async def start_automated_attendance_test(bot, guild_id: int, schedule_id: int, notification_channel_id: int, duration_hours: int = 12):
    """
    Start automated attendance testing for the specified schedule.
    
    Args:
        bot: The Discord bot instance
        guild_id: Discord guild ID
        schedule_id: Train schedule ID to test
        notification_channel_id: Channel to send notifications to
        duration_hours: How long to run the test in hours (default: 12)
    """
    global _automated_test_task, _is_running
    
    if _is_running:
        logger.warning("⚠️ Automated test is already running")
        return False
    
    logger.info(f"🧪 Starting automated attendance test for schedule {schedule_id} - Duration: {duration_hours} hours")
    
    # Start the automated test task
    _automated_test_task = asyncio.create_task(
        _run_automated_test(bot, guild_id, schedule_id, notification_channel_id, duration_hours)
    )
    
    _is_running = True
    return True

async def stop_automated_attendance_test():
    """Stop the automated attendance testing."""
    global _automated_test_task, _is_running
    
    if not _is_running or not _automated_test_task:
        return False
    
    logger.info("🛑 Stopping automated attendance test")
    _automated_test_task.cancel()
    _is_running = False
    
    try:
        await _automated_test_task
    except asyncio.CancelledError:
        pass
    
    return True

def is_test_running():
    """Check if automated test is currently running."""
    return _is_running

async def _run_automated_test(bot, guild_id: int, schedule_id: int, notification_channel_id: int, duration_hours: int = 12):
    """
    Internal function to run the automated test.
    Sends notifications every 15 minutes for the specified duration.
    If duration_hours is 0, runs indefinitely until manually stopped.
    """
    global _is_running
    
    try:
        notification_count = 0
        interval_minutes = 15
        
        # Calculate number of notifications based on duration
        if duration_hours == 0:
            max_notifications = None  # Unlimited
            logger.info(f"🚀 Automated test started - will send notifications every {interval_minutes} minutes until manually stopped")
        else:
            max_notifications = (duration_hours * 60) // interval_minutes
            logger.info(f"🚀 Automated test started - will send {max_notifications} notifications every {interval_minutes} minutes ({duration_hours} hours)")
        
        # Get channel
        channel = bot.get_channel(notification_channel_id)
        if not channel:
            logger.error(f"❌ Could not find notification channel {notification_channel_id}")
            return
        
        # Start Twitch chat monitoring if available
        await _start_twitch_monitoring(bot, schedule_id)
        
        # Run until stopped or max notifications reached (if set)
        while _is_running and (max_notifications is None or notification_count < max_notifications):
            notification_count += 1
            
            if max_notifications:
                logger.info(f"📢 Sending attendance report {notification_count}/{max_notifications}")
            else:
                logger.info(f"📢 Sending attendance report #{notification_count}")
            
            # Send the actual attendance report showing who's in the chat
            if hasattr(bot, 'twitch_chat_monitor'):
                try:
                    await bot.twitch_chat_monitor._generate_chat_attendance_report(schedule_id)
                    logger.info(f"✅ Attendance report sent successfully")
                except Exception as e:
                    logger.error(f"❌ Error sending attendance report: {e}")
            else:
                logger.warning("⚠️ Twitch chat monitor not available")
            
            # Wait for the next interval (unless this is the last notification and we have a limit)
            if (max_notifications is None or notification_count < max_notifications) and _is_running:
                logger.info(f"⏰ Waiting {interval_minutes} minutes until next report...")
                await asyncio.sleep(interval_minutes * 60)  # Convert to seconds
        
        logger.info(f"✅ Automated test completed - sent {notification_count} notifications")
        
        # Send completion message
        if _is_running:
            await _send_completion_message(bot, channel, schedule_id, notification_count)
            
    except asyncio.CancelledError:
        logger.info("🛑 Automated test was cancelled")
        raise
    except Exception as e:
        logger.error(f"❌ Error in automated test: {e}")
    finally:
        _is_running = False

async def _start_twitch_monitoring(bot, schedule_id: int):
    """Start Twitch chat monitoring for the test schedule."""
    try:
        if hasattr(bot, 'twitch_chat_monitor'):
            logger.info(f"🎮 Starting Twitch chat monitoring for schedule {schedule_id}")
            await bot.twitch_chat_monitor.start_train_monitoring(schedule_id)
        else:
            logger.warning("⚠️ Twitch chat monitor not available")
    except Exception as e:
        logger.error(f"❌ Failed to start Twitch monitoring: {e}")

async def _send_test_notification(bot, channel, guild_id: int, schedule_id: int, notification_num: int, total_notifications: int):
    """Send a single test attendance notification."""
    try:
        with DatabaseSession() as session:
            # Get schedule info
            schedule = session.query(TrainSchedule).filter_by(
                id=schedule_id,
                guild_id=guild_id,
                is_active=True
            ).first()
            
            if not schedule:
                logger.error(f"❌ Schedule {schedule_id} not found")
                return
            
            # Get participants count
            participants_count = session.query(TrainParticipant).filter_by(
                schedule_id=schedule_id,
                is_active=True
            ).count()
            
            # Create the test notification embed
            total_display = "∞" if total_notifications is None else str(total_notifications)
            next_display = "25 min" if total_notifications is None or notification_num < total_notifications else "Complete"
            
            embed = discord.Embed(
                title="🧪 **AUTOMATED ATTENDANCE TEST**",
                description=f"**Test Notification {notification_num}/{total_display}**\n\nThis is an automated test of the attendance tracking system with real-time Twitch chat monitoring!",
                color=0xff6b35,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📋 Test Schedule",
                value=f"**Name:** {schedule.name}\n**ID:** {schedule_id}\n**Participants:** {participants_count}",
                inline=True
            )
            
            embed.add_field(
                name="⏰ Test Progress", 
                value=f"**Notification:** {notification_num} of {total_display}\n**Interval:** 15 minutes\n**Next:** {next_display}",
                inline=True
            )
            
            embed.add_field(
                name="🎮 How to Participate",
                value=(
                    "**Just be in the Twitch chat when the streamer is live!**\n\n"
                    "The bot will automatically detect your presence in chat and track your participation. "
                    "No need to do anything else - the system gradually discovers users from the stream as they join and chat."
                ),
                inline=False
            )
            
            embed.add_field(
                name="📊 What's Being Tested",
                value=(
                    "• **Notification Responses** - Traditional ready/not ready tracking\n"
                    "• **Twitch Chat Monitoring** - Real-time detection of chat presence\n"
                    "• **Combined Reports** - Enhanced attendance data with both metrics\n"
                    "• **Live Updates** - Instant feedback on participation changes"
                ),
                inline=False
            )
            
            embed.set_footer(
                text=f"Automated Test • Use /sessionsummary {schedule_id} to view results"
            )
            
            # Send the message
            message = await channel.send(embed=embed)
            
            # Add reaction options
            await message.add_reaction('✅')  # Ready
            await message.add_reaction('❌')  # Not ready
            await message.add_reaction('🔄')  # Backup
            
            logger.info(f"✅ Sent test notification {notification_num}/{total_notifications}")
            
    except Exception as e:
        logger.error(f"❌ Error sending test notification: {e}")

async def _send_completion_message(bot, channel, schedule_id: int, notification_count: int):
    """Send completion message when automated test finishes."""
    try:
        embed = discord.Embed(
            title="🎉 **Automated Test Complete!**",
            description=f"Successfully completed {notification_count} automated attendance notifications with real-time Twitch monitoring!",
            color=0x00ff00,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="📊 Test Results",
            value=f"Use `/sessionsummary {schedule_id}` to view detailed attendance data combining both notification responses and Twitch chat presence tracking.",
            inline=False
        )
        
        embed.add_field(
            name="✨ Key Features Tested",
            value=(
                "✅ **Automated Notifications** - Notifications sent every 15 minutes\n"
                "✅ **Real-time Chat Monitoring** - Twitch presence tracking\n" 
                "✅ **Combined Data Collection** - Reactions + chat presence\n"
                "✅ **Enhanced Reporting** - Comprehensive attendance analytics"
            ),
            inline=False
        )
        
        embed.set_footer(text="Automated testing system completed successfully")
        
        await channel.send(embed=embed)
        logger.info("✅ Sent automated test completion message")
        
    except Exception as e:
        logger.error(f"❌ Error sending completion message: {e}")

# Status functions for monitoring
def get_test_status():
    """Get current status of automated testing."""
    return {
        'is_running': _is_running,
        'task_exists': _automated_test_task is not None,
        'task_done': _automated_test_task.done() if _automated_test_task else True
    }