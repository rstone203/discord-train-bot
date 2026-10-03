"""
Twitch Chat Bot for r3dlabs-style features.
Handles chat commands, auto-announcements, and raid management.
"""
import logging
import asyncio
import os
import re
from typing import Optional, Dict, List
from datetime import datetime, timedelta, time
import pytz
import requests
import aiohttp
from database import DatabaseSession
from models import TrainSchedule, TrainParticipant, User, TwitchOAuthToken

logger = logging.getLogger('discord_bot.twitch_chat_bot')


class TwitchChatBot:
    """Handles Twitch chat interactions, commands, and raid management."""
    
    def __init__(self, bot):
        self.bot = bot
        self.client_id = os.getenv('TWITCH_CLIENT_ID')
        self.client_secret = os.getenv('TWITCH_CLIENT_SECRET')
        
        self.command_pattern = re.compile(r'^\s*!raidnext\s*$', re.IGNORECASE)
        
        # Track active schedules for each guild
        self.active_schedules: Dict[int, List[dict]] = {}  # guild_id -> list of schedules
        
        logger.info("✅ Twitch chat bot initialized")
    
    def get_oauth_token(self) -> Optional[str]:
        """Get active OAuth token from database."""
        try:
            with DatabaseSession() as session:
                active_token = session.query(TwitchOAuthToken).filter_by(is_active=True).first()
                if active_token:
                    return active_token.access_token
                return None
        except Exception as e:
            logger.error(f"Error getting OAuth token: {e}")
            return None
    
    def get_user_id_from_token(self) -> Optional[str]:
        """Get the Twitch user ID from the OAuth token."""
        try:
            with DatabaseSession() as session:
                active_token = session.query(TwitchOAuthToken).filter_by(is_active=True).first()
                if active_token:
                    return active_token.twitch_user_id
                return None
        except Exception as e:
            logger.error(f"Error getting user ID from token: {e}")
            return None
    
    async def send_chat_message(self, broadcaster_id: str, message: str) -> bool:
        """
        Send a message to Twitch chat via Helix API using the broadcaster's own OAuth token.
        Automatically refreshes expired tokens on 401 errors.
        """
        try:
            token = self.get_oauth_token_for_user(broadcaster_id)
            sender_id = broadcaster_id

            if not token:
                logger.error(f"❌ No OAuth token available for broadcaster {broadcaster_id}")
                return False
            
            url = "https://api.twitch.tv/helix/chat/messages"
            headers = {
                "Authorization": f"Bearer {token}",
                "Client-Id": self.client_id,
                "Content-Type": "application/json"
            }
            data = {
                "broadcaster_id": broadcaster_id,
                "sender_id": sender_id,
                "message": message
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(url, headers=headers, json=data, timeout=10)
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Chat message sent to channel {broadcaster_id}: {message}")
                return True
            elif response.status_code == 401:
                logger.warning(f"⚠️ Chat message got 401 - attempting token refresh for broadcaster {broadcaster_id}...")
                new_token = await self.refresh_token_for_user(broadcaster_id)
                if new_token:
                    headers["Authorization"] = f"Bearer {new_token}"
                    response2 = await loop.run_in_executor(
                        None,
                        lambda: requests.post(url, headers=headers, json=data, timeout=10)
                    )
                    if response2.status_code == 200:
                        logger.info(f"✅ Chat message sent after token refresh to {broadcaster_id}: {message}")
                        return True
                    else:
                        logger.error(f"❌ Chat message failed after refresh: {response2.status_code} - {response2.text}")
                        return False
                else:
                    logger.error(f"❌ Token refresh failed, cannot send chat message")
                    return False
            else:
                logger.error(f"❌ Failed to send chat message: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending chat message: {e}", exc_info=True)
            return False
    
    def get_oauth_token_for_user(self, twitch_user_id: str) -> Optional[str]:
        """Get OAuth token for a specific Twitch user."""
        try:
            with DatabaseSession() as session:
                token = session.query(TwitchOAuthToken).filter_by(
                    twitch_user_id=str(twitch_user_id),
                    is_active=True
                ).first()
                if token:
                    return token.access_token
                return None
        except Exception as e:
            logger.error(f"Error getting OAuth token for user {twitch_user_id}: {e}")
            return None

    async def refresh_token_for_user(self, twitch_user_id: str = None) -> Optional[str]:
        """Refresh an expired OAuth token for a specific user or any active token."""
        try:
            with DatabaseSession() as session:
                if twitch_user_id:
                    active_token = session.query(TwitchOAuthToken).filter_by(
                        twitch_user_id=str(twitch_user_id),
                        is_active=True
                    ).first()
                else:
                    active_token = session.query(TwitchOAuthToken).filter_by(is_active=True).first()

                if not active_token or not active_token.refresh_token:
                    logger.error(f"❌ No refresh token available for user {twitch_user_id}")
                    return None

                logger.info(f"🔄 Refreshing OAuth token for {active_token.twitch_username} (ID: {twitch_user_id})...")

                url = "https://id.twitch.tv/oauth2/token"
                data = {
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'grant_type': 'refresh_token',
                    'refresh_token': active_token.refresh_token
                }

                async with aiohttp.ClientSession() as aio_session:
                    async with aio_session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            new_access_token = result.get('access_token')
                            new_refresh_token = result.get('refresh_token')
                            expires_in = result.get('expires_in')

                            active_token.access_token = new_access_token
                            if new_refresh_token:
                                active_token.refresh_token = new_refresh_token
                            if expires_in:
                                active_token.expires_at = datetime.now() + timedelta(seconds=expires_in)
                            active_token.last_used_at = datetime.now()

                            session.commit()

                            logger.info(f"✅ OAuth token refreshed for {active_token.twitch_username}! Expires in {expires_in}s")
                            return new_access_token
                        else:
                            error_text = await resp.text()
                            logger.error(f"❌ Failed to refresh token for {active_token.twitch_username}: {resp.status} - {error_text}")
                            return None
        except Exception as e:
            logger.error(f"❌ Error refreshing token: {e}", exc_info=True)
            return None

    async def start_raid(self, from_broadcaster_id: str, to_broadcaster_id: str) -> bool:
        """
        Start a raid from one channel to another via Helix API.
        Uses the broadcaster's own OAuth token if available.
        Automatically refreshes expired tokens on 401 errors.
        """
        try:
            token = self.get_oauth_token_for_user(from_broadcaster_id)
            token_user_id = from_broadcaster_id
            
            if not token:
                logger.warning(f"No OAuth token found for broadcaster {from_broadcaster_id}, trying default token")
                token = self.get_oauth_token()
                token_user_id = None
            
            if not token:
                logger.error("❌ No OAuth token available for raid")
                return False
            
            url = "https://api.twitch.tv/helix/raids"
            headers = {
                "Authorization": f"Bearer {token}",
                "Client-Id": self.client_id
            }
            params = {
                "from_broadcaster_id": from_broadcaster_id,
                "to_broadcaster_id": to_broadcaster_id
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(url, headers=headers, params=params, timeout=10)
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Raid started from {from_broadcaster_id} to {to_broadcaster_id}")
                return True
            elif response.status_code == 401:
                logger.warning(f"⚠️ Raid got 401 - attempting token refresh for {token_user_id}...")
                new_token = await self.refresh_token_for_user(token_user_id)
                if new_token:
                    headers["Authorization"] = f"Bearer {new_token}"
                    response2 = await loop.run_in_executor(
                        None,
                        lambda: requests.post(url, headers=headers, params=params, timeout=10)
                    )
                    if response2.status_code == 200:
                        logger.info(f"✅ Raid started after token refresh from {from_broadcaster_id} to {to_broadcaster_id}")
                        return True
                    else:
                        logger.error(f"❌ Raid failed after token refresh: {response2.status_code} - {response2.text}")
                        return False
                else:
                    logger.error(f"❌ Token refresh failed, cannot start raid")
                    return False
            else:
                logger.error(f"❌ Failed to start raid: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error starting raid: {e}", exc_info=True)
            return False
    
    def parse_chat_command(self, message: str) -> Optional[str]:
        """
        Parse IRC PRIVMSG for bot commands.
        
        Args:
            message: Raw chat message text
            
        Returns:
            Command name if found, None otherwise
        """
        if self.command_pattern.match(message):
            return 'raidnext'
        return None
    
    def _effective_schedule_date(self, schedule, now_est):
        """Compute the UK calendar date a schedule actually fires on.

        One-time (specific_date) schedules use their stored date directly; early-morning
        slots (<5am) roll to the next calendar day. Recurring schedules derive their date
        from day_of_week relative to today. This keeps one-time trains from re-firing on
        the same weekday once their date has passed.
        """
        specific_date = getattr(schedule, 'specific_date', None)
        if specific_date:
            if isinstance(specific_date, str):
                specific_date = datetime.strptime(specific_date, '%Y-%m-%d').date()
            eff = specific_date
            if schedule.start_time.hour < 5:
                eff = eff + timedelta(days=1)
            return eff
        current_day = now_est.weekday()
        days_offset = (schedule.day_of_week - current_day) % 7
        return now_est.date() + timedelta(days=days_offset)

    async def get_next_rider(self, guild_id: int, current_broadcaster_login: str) -> Optional[dict]:
        """
        Determine the next rider in the train schedule.
        Looks at participants assigned to each time slot, not just the host.
        
        Args:
            guild_id: Discord guild ID
            current_broadcaster_login: Current broadcaster's Twitch username
            
        Returns:
            Dict with next rider info: {'twitch_login': str, 'twitch_id': str, 'time': datetime}
        """
        try:
            with DatabaseSession() as session:
                eastern_tz = pytz.timezone('Europe/London')
                now_est = datetime.now(eastern_tz)
                today = now_est.date()
                tomorrow = today + timedelta(days=1)

                # Build today's train session from BOTH recurring and one-time schedules.
                # Recurring schedules match by day_of_week; one-time (specific_date) schedules
                # must match by their stored date so a past one-time train never re-fires on
                # the same weekday and pings last week's rider.
                all_active = session.query(TrainSchedule).filter(
                    TrainSchedule.guild_id == guild_id,
                    TrainSchedule.is_active == True
                ).order_by(TrainSchedule.start_time).all()

                schedules = []
                for s in all_active:
                    eff_date = self._effective_schedule_date(s, now_est)
                    if eff_date is None:
                        continue
                    if eff_date == today:
                        schedules.append((s, eff_date))
                    elif eff_date == tomorrow and s.start_time.hour < 5:
                        # early-morning slot that belongs to tonight's session
                        schedules.append((s, eff_date))

                if not schedules:
                    return None
                
                slot_riders = []
                for schedule, schedule_date in schedules:
                    # start_time stored as UK wall clock — localize directly
                    naive_uk = datetime.combine(schedule_date, schedule.start_time)
                    est_dt = eastern_tz.localize(naive_uk)
                    est_start_time = est_dt.time()
                    est_start_datetime = est_dt
                    
                    participants = session.query(TrainParticipant, User).outerjoin(
                        User, TrainParticipant.user_id == User.id
                    ).filter(
                        TrainParticipant.schedule_id == schedule.id,
                        TrainParticipant.is_active == True
                    ).order_by(TrainParticipant.id.asc()).all()
                    
                    if participants:
                        for p, user in participants:
                            twitch_login = p.twitch_username if p.twitch_username else (user.twitch_login if user else None)
                            discord_name = (user.display_name or user.username) if user else (p.twitch_username or 'Unknown')
                            slot_riders.append({
                                'twitch_login': twitch_login,
                                'twitch_id': (user.twitch_id if user and twitch_login else None),
                                'time': schedule.start_time,
                                'est_time': est_start_time,
                                'est_datetime': est_start_datetime,
                                'schedule_id': schedule.id,
                                'user_id': user.id if user else None,
                                'discord_username': discord_name,
                                'discord_user_id': user.id if user else None
                            })
                    else:
                        host = session.query(User).filter_by(id=schedule.host_user_id).first()
                        if host:
                            twitch_login = host.twitch_login
                            slot_riders.append({
                                'twitch_login': twitch_login,
                                'twitch_id': host.twitch_id if twitch_login else None,
                                'time': schedule.start_time,
                                'est_time': est_start_time,
                                'est_datetime': est_start_datetime,
                                'schedule_id': schedule.id,
                                'user_id': host.id,
                                'discord_username': host.display_name or host.username,
                                'discord_user_id': host.id
                            })
                
                # Sort by full localized datetime (not time-of-day) so overnight sessions
                # crossing midnight order correctly — e.g. 22:00 → 23:00 → 00:30 → 01:00.
                slot_riders.sort(key=lambda r: r.get('est_datetime'))
                
                logger.info(f"🚂 Built rider list with {len(slot_riders)} riders: {[r.get('twitch_login') for r in slot_riders]}")
                
                if not slot_riders:
                    return None
                
                current_index = None
                broadcaster_lower = current_broadcaster_login.lower()
                for i, rider in enumerate(slot_riders):
                    rider_twitch = rider.get('twitch_login')
                    if rider_twitch and rider_twitch.lower() == broadcaster_lower:
                        current_index = i
                        break
                
                if current_index is not None:
                    for i in range(current_index + 1, len(slot_riders)):
                        next_rider = slot_riders[i]
                        next_twitch = next_rider.get('twitch_login')
                        if not next_twitch or next_twitch.lower() != broadcaster_lower:
                            if next_twitch and not next_rider.get('twitch_id'):
                                next_rider['twitch_id'] = await self._get_twitch_user_id(next_twitch)
                            return next_rider
                else:
                    for rider in slot_riders:
                        rider_twitch = rider.get('twitch_login')
                        if not rider_twitch or rider_twitch.lower() != broadcaster_lower:
                            if rider.get('est_datetime') and rider['est_datetime'] > now_est:
                                if rider_twitch and not rider.get('twitch_id'):
                                    rider['twitch_id'] = await self._get_twitch_user_id(rider_twitch)
                                return rider
                
                return None
                
        except Exception as e:
            logger.error(f"Error getting next rider: {e}", exc_info=True)
            return None
    
    async def _get_twitch_user_id(self, twitch_login: str) -> Optional[str]:
        """Get Twitch user ID from username via Helix API."""
        try:
            token = self.get_oauth_token()
            if not token:
                return None
            
            url = f"https://api.twitch.tv/helix/users?login={twitch_login}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Client-Id": self.client_id
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers=headers, timeout=10)
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('data'):
                    return data['data'][0]['id']
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting Twitch user ID for {twitch_login}: {e}")
            return None
    
    async def handle_raidnow_command(self, guild_id: int, current_broadcaster_login: str, current_broadcaster_id: str) -> Optional[str]:
        """
        Handle !raidnext command from Twitch chat.
        
        Args:
            guild_id: Discord guild ID
            current_broadcaster_login: Current broadcaster's Twitch username
            current_broadcaster_id: Current broadcaster's Twitch user ID
            
        Returns:
            Response message to send in chat, None if command failed
        """
        try:
            logger.info(f"🚂 handle_raidnow_command: guild={guild_id}, broadcaster={current_broadcaster_login}, id={current_broadcaster_id}")
            
            # Get next rider
            next_rider = await self.get_next_rider(guild_id, current_broadcaster_login)
            
            if not next_rider:
                logger.info(f"⚠️ No next rider found for guild {guild_id}, broadcaster {current_broadcaster_login}")
                return "⚠️ No next rider found in today's train schedule!"
            
            discord_name = next_rider.get('discord_username', 'Unknown')
            twitch_login = next_rider.get('twitch_login')
            logger.info(f"🚂 Next rider: {discord_name} (twitch: {twitch_login}, twitch_id: {next_rider.get('twitch_id')})")
            
            if not twitch_login:
                return f"⚠️ Next rider is {discord_name} (Discord) but they don't have a Twitch account linked! An admin can use /addtotrain with their Twitch username to fix this."
            
            if not next_rider['twitch_id']:
                next_rider['twitch_id'] = await self._get_twitch_user_id(twitch_login)
                logger.info(f"🔍 Looked up twitch_id for {twitch_login}: {next_rider['twitch_id']}")
            
            if not next_rider['twitch_id']:
                return f"⚠️ Next rider is {discord_name} (Twitch: {twitch_login}) but could not find their Twitch ID. Check the username is correct!"
            
            if next_rider.get('time'):
                from datetime import datetime, timedelta
                import pytz
                
                # start_time is stored as UK wall clock — localize directly as Europe/London
                uk_tz = pytz.timezone('Europe/London')
                now = datetime.now(uk_tz)
                
                next_start_time_obj = next_rider['time']
                today = now.date()
                next_start_datetime = uk_tz.localize(datetime.combine(today, next_start_time_obj))
                
                time_until_next = (next_start_datetime - now).total_seconds()
                logger.info(f"⏰ Time check: next rider starts at {next_start_datetime.strftime('%I:%M %p %Z')}, time_until={time_until_next}s ({int(time_until_next/60)}min)")
                
                if time_until_next > 600:
                    minutes_left = int(time_until_next / 60)
                    return f"⏳ Too early! {twitch_login} ({discord_name}) starts in {minutes_left} minute(s). Wait until closer to their start time!"
            
            user_token = self.get_oauth_token_for_user(current_broadcaster_id)
            
            if user_token:
                logger.info(f"🎯 Attempting auto-raid from {current_broadcaster_id} to {next_rider['twitch_id']} ({twitch_login})")
                success = await self.start_raid(current_broadcaster_id, next_rider['twitch_id'])
                
                if success:
                    return f"🎯 Starting raid to {twitch_login} ({discord_name})! twitch.tv/{twitch_login}"
                else:
                    return f"🚂 Next up: {twitch_login} ({discord_name}) 👉 Raid them at twitch.tv/{twitch_login}"
            else:
                return f"🚂 Next up: {twitch_login} ({discord_name}) 👉 Raid them at twitch.tv/{twitch_login} (Auto-raid unavailable - use /linktwitch to enable)"
                
        except Exception as e:
            logger.error(f"Error handling raidnow command: {e}", exc_info=True)
            return "❌ Error processing raid command"
    
    async def send_next_rider_announcement(self, guild_id: int, current_broadcaster_login: str, current_broadcaster_id: str):
        """
        Send announcement when next rider goes live.
        
        Args:
            guild_id: Discord guild ID
            current_broadcaster_login: Current broadcaster's Twitch username
            current_broadcaster_id: Current broadcaster's Twitch user ID
        """
        try:
            next_rider = await self.get_next_rider(guild_id, current_broadcaster_login)
            
            if next_rider:
                message = f"🔴 @{current_broadcaster_login} Next rider is LIVE! 👉 {next_rider['twitch_login']} is streaming now at twitch.tv/{next_rider['twitch_login']} - Raid when ready!"
                await self.send_chat_message(current_broadcaster_id, message)
                logger.info(f"📢 Sent next rider announcement for {next_rider['twitch_login']}")
                
        except Exception as e:
            logger.error(f"Error sending next rider announcement: {e}", exc_info=True)
    
    async def send_10min_warning(self, guild_id: int, current_broadcaster_id: str, next_rider_login: str):
        """
        Send 10-minute warning before next rider's scheduled time.
        
        Args:
            guild_id: Discord guild ID
            current_broadcaster_id: Current broadcaster's Twitch user ID
            next_rider_login: Next rider's Twitch username
        """
        try:
            message = f"⏰ Reminder: {next_rider_login} goes live in 10 minutes! Get ready to raid at twitch.tv/{next_rider_login}"
            await self.send_chat_message(current_broadcaster_id, message)
            logger.info(f"⏰ Sent 10-minute warning for {next_rider_login}")
            
        except Exception as e:
            logger.error(f"Error sending 10-minute warning: {e}", exc_info=True)
