import logging
import os
import aiohttp
import asyncio
import discord
from typing import Set, Dict, Optional, List
from datetime import datetime, timedelta
import pytz
from database import DatabaseSession
from models import Guild, User, TrainSchedule, TrustedRole, StreamChatAnnouncement

logger = logging.getLogger('discord_bot.live_role_manager')

class LiveRoleManager:
    def __init__(self, bot):
        self.bot = bot
        self.client_id = bot.twitch_chat_monitor.client_id if hasattr(bot, 'twitch_chat_monitor') else None
        self.live_status_cache: Dict[str, bool] = {}
        self.live_alert_messages: Dict[str, int] = {}  # cache_key -> message_id
        
        # Auto-shoutout specific tracking
        self.shoutout_live_cache: Dict[str, datetime] = {}  # Track when users went live for shoutouts
        self.shoutout_joined_channels: Set[str] = set()  # Track channels we've already joined this session
        self._shoutout_watcher_running = False
        self._shoutout_check_count = 0  # Counter for periodic forced rejoins
        
    async def get_access_token(self) -> Optional[str]:
        """Always use client credentials app token for stream status checks.
        The user OAuth token returns incorrect results from /helix/streams."""
        try:
            monitor = self.bot.twitch_chat_monitor if hasattr(self.bot, 'twitch_chat_monitor') else None
            client_id = monitor.client_id if monitor else os.getenv('TWITCH_CLIENT_ID')
            client_secret = monitor.client_secret if monitor else os.getenv('TWITCH_CLIENT_SECRET')
            if not client_id or not client_secret:
                return None
            async with aiohttp.ClientSession() as session:
                async with session.post('https://id.twitch.tv/oauth2/token', data={
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'grant_type': 'client_credentials'
                }) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        token = result.get('access_token')
                        logger.debug("✅ Live role check: using client credentials app token")
                        return token
                    else:
                        logger.error(f"Failed to get app access token for live check: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Error getting app access token for live check: {e}")
            return None
    
    def _get_week_offset(self, schedule):
        """Extract week offset from schedule description if present."""
        import re
        if schedule.description:
            match = re.search(r'\[WEEK_OFFSET:(\d+)\]', schedule.description)
            if match:
                return int(match.group(1))
        return 0
    
    def has_active_train(self, user_id: int, guild_id: int, session) -> bool:
        """Check if a user has an active train scheduled (within notification window)."""
        try:
            eastern_tz = pytz.timezone('Europe/London')
            now = datetime.now(eastern_tz)
            today_date = now.date()
            current_day = now.weekday()
            
            # Get ALL schedules hosted by this user (we'll filter by checking each one)
            all_schedules = session.query(TrainSchedule).filter(
                TrainSchedule.guild_id == guild_id,
                TrainSchedule.host_user_id == user_id,
                TrainSchedule.is_active == True
            ).all()
            
            # Filter to current day schedules AND next day schedules with early UTC times.
            # One-time (specific_date) schedules must match today's actual date (or roll into
            # the early hours of tomorrow), not just the weekday, so expired one-time trains
            # don't re-activate live roles on their weekday next week.
            next_day = (current_day + 1) % 7
            tomorrow_date = today_date + timedelta(days=1)
            schedules_to_check = []
            for s in all_schedules:
                sd = getattr(s, 'specific_date', None)
                if sd:
                    if isinstance(sd, str):
                        sd = datetime.strptime(sd, '%Y-%m-%d').date()
                    eff = sd + timedelta(days=1) if s.start_time.hour < 5 else sd
                    if eff == today_date or (eff == tomorrow_date and s.start_time.hour < 5):
                        schedules_to_check.append(s)
                else:
                    if s.day_of_week == current_day:
                        schedules_to_check.append(s)
                    elif s.day_of_week == next_day and s.start_time.hour < 5:
                        schedules_to_check.append(s)
            
            # Now process each schedule using the exact notification system logic
            for schedule in schedules_to_check:
                schedule_day = schedule.day_of_week
                specific_date = getattr(schedule, 'specific_date', None)
                if specific_date:
                    if isinstance(specific_date, str):
                        specific_date = datetime.strptime(specific_date, '%Y-%m-%d').date()
                    schedule_date = specific_date
                    if schedule.start_time.hour < 5:
                        schedule_date = schedule_date + timedelta(days=1)
                    days_until_schedule = 0
                else:
                    days_until_schedule = (schedule_day - current_day) % 7
                    schedule_date = today_date + timedelta(days=days_until_schedule)
                    
                    # Apply week offset if schedule is for future weeks
                    week_offset = self._get_week_offset(schedule)
                    if week_offset > 0:
                        schedule_date = schedule_date + timedelta(weeks=week_offset)
                
                # start_time is stored as UK wall clock time — localize directly (matches notification system)
                naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
                start_datetime = eastern_tz.localize(naive_uk_datetime)

                # Check which UK day the schedule actually belongs to after localization
                actual_est_day = start_datetime.weekday()
                
                # Skip if doesn't match current day and is in the future
                if actual_est_day != current_day and start_datetime > now:
                    continue
                
                # Handle past events
                time_since_start_seconds = (now - start_datetime).total_seconds()
                within_2hour_window = 0 <= time_since_start_seconds <= 7200
                
                if start_datetime < now and not within_2hour_window:
                    if specific_date:
                        # One-time train fully in the past — never roll forward to next week
                        continue
                    # If actual EST day matches current day or days_until_schedule is 0, move to next week
                    if actual_est_day == current_day or days_until_schedule == 0:
                        schedule_date = schedule_date + timedelta(days=7)
                        naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
                        start_datetime = eastern_tz.localize(naive_uk_datetime)
                    else:
                        continue
                
                # Check if train is within the active window (60 min before to 30 min after)
                time_to_start = (start_datetime - now).total_seconds()
                
                # time_to_start is positive before start, negative after
                # 60 min before: time_to_start = 3600
                # 30 min after: time_to_start = -1800
                if -1800 <= time_to_start <= 3600:
                    logger.debug(f"✅ {user_id} has active train '{schedule.name}' at {start_datetime.strftime('%I:%M %p %Z')} (time_to_start={time_to_start/60:.1f} min)")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Error checking active trains for user {user_id}: {e}")
            return False
    
    async def _get_trusted_role_users(self, guild_id: int) -> List[int]:
        """Get all users with trusted roles in a guild."""
        try:
            with DatabaseSession() as session:
                guild = session.query(Guild).filter_by(id=guild_id).first()
                if not guild:
                    return []
                
                # Get all trusted roles for this guild
                trusted_roles = session.query(TrustedRole).filter_by(guild_id=guild_id).all()
                if not trusted_roles:
                    return []
                
                # Get Discord guild
                discord_guild = self.bot.get_guild(guild_id)
                if not discord_guild:
                    return []
                
                # Collect all users with trusted roles
                trusted_users = set()
                for trusted_role in trusted_roles:
                    role = discord_guild.get_role(trusted_role.role_id)
                    if role:
                        for member in role.members:
                            trusted_users.add(member.id)
                
                return list(trusted_users)
                
        except Exception as e:
            logger.error(f"Error getting trusted role users for guild {guild_id}: {e}")
            return []
    
    async def _notify_live_status_error(self, guild_id: int, error_reason: str):
        """Send DM to trusted roles about live status detection failure."""
        try:
            # Prevent spam - only notify once per guild per hour
            notification_key = f"live_status_error_{guild_id}"
            if hasattr(self, '_error_notifications'):
                last_notified = self._error_notifications.get(notification_key)
                if last_notified and (datetime.now() - last_notified).total_seconds() < 3600:
                    logger.debug(f"Skipping live status error notification for guild {guild_id} (recently sent)")
                    return
            else:
                self._error_notifications = {}
            
            # Get bot owner only (not trusted roles)
            app_info = await self.bot.application_info()
            owner = app_info.owner
            if not owner:
                logger.debug(f"Could not find bot owner, skipping live status error notification")
                return
            
            # Create warning embed
            embed = discord.Embed(
                title="⚠️ Live Status Detection Error",
                description=(
                    f"The bot is unable to detect live stream status for this server.\n\n"
                    f"**Error:** {error_reason}\n\n"
                    "**Impact:**\n"
                    "❌ Live role may not update automatically\n"
                    "❌ Cannot detect when streamers go live\n"
                    "❌ Live tracking features unavailable\n\n"
                    "**Possible Solutions:**\n"
                    "• Run `/twitchoauth` to refresh Twitch authentication\n"
                    "• Verify Twitch API credentials are configured\n"
                    "• Check Twitch usernames are linked correctly"
                ),
                color=0xFFA500,
                timestamp=datetime.utcnow()
            )
            
            # Send DM to bot owner only
            try:
                await owner.send(embed=embed)
                logger.info(f"✅ Sent live status error notification to bot owner for guild {guild_id}")
                self._error_notifications[notification_key] = datetime.now()
            except Exception as e:
                logger.debug(f"Could not DM bot owner about live status error: {e}")
            
        except Exception as e:
            logger.error(f"Error sending live status error notification: {e}")

    async def backfill_missing_twitch_ids(self):
        """On startup, look up Twitch user IDs for any user that has a twitch_login but no twitch_id."""
        try:
            # Run DB fetch in a thread so it doesn't block the asyncio event loop
            # (blocking here caused Discord heartbeat timeouts and restart loops on startup)
            def _fetch_missing():
                with DatabaseSession() as session:
                    users = session.query(User).filter(
                        User.twitch_login.isnot(None),
                        User.twitch_login != '',
                        User.twitch_id.is_(None)
                    ).all()
                    return [u.twitch_login.lower() for u in users]

            logins = await asyncio.to_thread(_fetch_missing)
            if not logins:
                logger.debug("Twitch ID backfill: all users already have IDs, nothing to do")
                return
            logger.info(f"🔧 Twitch ID backfill: looking up {len(logins)} missing IDs ({', '.join(logins[:10])}{'...' if len(logins) > 10 else ''})")

            token = await self.get_access_token()
            if not token:
                logger.warning("Twitch ID backfill: no access token, skipping")
                return

            headers = {'Client-ID': self.client_id, 'Authorization': f'Bearer {token}'}
            login_to_id = {}
            for i in range(0, len(logins), 100):
                batch = logins[i:i+100]
                params = [('login', l) for l in batch]
                async with aiohttp.ClientSession() as http:
                    async with http.get(
                        'https://api.twitch.tv/helix/users',
                        params=params, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for u in data.get('data', []):
                                login_to_id[u['login'].lower()] = u['id']
                        else:
                            logger.error(f"Twitch ID backfill: helix/users returned {resp.status}")

            if not login_to_id:
                return

            def _update_users():
                with DatabaseSession() as session:
                    updated = 0
                    users = session.query(User).filter(
                        User.twitch_login.isnot(None),
                        User.twitch_login != '',
                        User.twitch_id.is_(None)
                    ).all()
                    for u in users:
                        tid = login_to_id.get(u.twitch_login.lower())
                        if tid:
                            u.twitch_id = tid
                            updated += 1
                    session.commit()
                    return updated

            updated = await asyncio.to_thread(_update_users)
            logger.info(f"✅ Twitch ID backfill complete: updated {updated} user(s)")
        except Exception as e:
            logger.error(f"Twitch ID backfill error: {e}", exc_info=True)

    async def check_streams_status(self, twitch_user_ids: list, guild_id: int = None) -> Set[str]:
        if not twitch_user_ids or not self.client_id:
            return set()
        
        try:
            token = await self.get_access_token()
            if not token:
                logger.warning("❌ No Twitch access token available for live role checks")
                if guild_id:
                    await self._notify_live_status_error(guild_id, "No valid Twitch access token available")
                return set()
            
            live_user_ids = set()
            
            for i in range(0, len(twitch_user_ids), 100):
                batch = twitch_user_ids[i:i+100]
                
                url = "https://api.twitch.tv/helix/streams"
                params = {"user_id": batch}
                headers = {
                    'Client-ID': self.client_id,
                    'Authorization': f'Bearer {token}'
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            logger.info(f"🔎 Live check API response: {len(data.get('data', []))} streams found for {len(batch)} IDs checked")
                            for stream in data.get('data', []):
                                logger.info(f"  ▶️ Live: {stream.get('user_login')} (id={stream.get('user_id')})")
                                live_user_ids.add(stream['user_id'])
                        elif resp.status == 429:
                            reset_time = resp.headers.get('Ratelimit-Reset', 'unknown')
                            logger.warning(f"⚠️ Twitch API rate limited. Reset at: {reset_time}")
                            if guild_id:
                                await self._notify_live_status_error(guild_id, "Twitch API rate limit exceeded")
                            await asyncio.sleep(5)
                        else:
                            error_text = await resp.text()
                            logger.error(f"❌ Twitch API error {resp.status}: {error_text}")
                            if guild_id:
                                await self._notify_live_status_error(guild_id, f"Twitch API error: {resp.status}")
            
            return live_user_ids
            
        except Exception as e:
            logger.error(f"❌ Error checking stream status: {e}")
            if guild_id:
                await self._notify_live_status_error(guild_id, f"Network error: {str(e)}")
            return set()
    
    async def update_live_roles_for_guild(self, guild):
        try:
            with DatabaseSession() as session:
                guild_data = session.query(Guild).filter(Guild.id == guild.id).first()
                
                if not guild_data:
                    return
                
                # Determine what features need live checks
                has_live_role = guild_data.live_tracking_enabled and guild_data.live_role_id
                has_alert_channel = bool(guild_data.stream_alert_channel_id)

                # If neither live role tracking nor go-live alerts are configured, only do shoutouts
                if not has_live_role and not has_alert_channel:
                    await self._check_auto_shoutout_channels(guild, session)
                    return

                live_role = guild.get_role(guild_data.live_role_id) if has_live_role else None
                if has_live_role and not live_role:
                    logger.warning(f"⚠️ Live role {guild_data.live_role_id} not found in guild {guild.name}")
                
                # Primary: users whose home guild_id matches
                users_with_twitch = session.query(User).filter(
                    User.guild_id == guild.id,
                    User.twitch_id.isnot(None)
                ).all()

                # Cross-guild: members of this Discord guild who have Twitch
                # linked under a different guild_id (e.g. linked via another server)
                try:
                    member_ids = [m.id for m in guild.members if not m.bot]
                    if member_ids:
                        already_found = {u.id for u in users_with_twitch}
                        cross = session.query(User).filter(
                            User.id.in_(member_ids),
                            User.id.notin_(already_found),
                            User.twitch_id.isnot(None)
                        ).all()
                        if cross:
                            logger.info(f"🔗 Cross-guild Twitch users in {guild.name}: {[u.twitch_login for u in cross]}")
                            users_with_twitch = users_with_twitch + cross
                except Exception as _cg_err:
                    logger.debug(f"Cross-guild Twitch lookup error: {_cg_err}")

                if not users_with_twitch:
                    return
                
                twitch_id_to_user = {user.twitch_id: user for user in users_with_twitch}
                twitch_ids = list(twitch_id_to_user.keys())
                
                logger.info(f"🔍 Checking {len(twitch_ids)} Twitch accounts in {guild.name}...")
                live_twitch_ids = await self.check_streams_status(twitch_ids, guild_id=guild.id)
                logger.info(f"📊 Found {len(live_twitch_ids)} users live in {guild.name}")
                
                for twitch_id, user in twitch_id_to_user.items():
                    is_live = twitch_id in live_twitch_ids
                    cache_key = f"{guild.id}:{user.id}"
                    was_live = self.live_status_cache.get(cache_key, None)
                    
                    # Check if trains-only mode is enabled
                    should_have_role = is_live
                    if guild_data.live_role_trains_only and is_live:
                        # In trains-only mode, check if user has an active train
                        has_train = self.has_active_train(user.id, guild.id, session)
                        should_have_role = has_train
                        if not has_train:
                            logger.debug(f"🚂 {user.username} is live but has no active train (trains-only mode)")
                    
                    # If cache is empty (None), force sync by checking actual Discord role
                    startup_already_live = False
                    if was_live is None:
                        try:
                            member = guild.get_member(user.id)
                            if not member:
                                member = await guild.fetch_member(user.id)
                            if member:
                                was_live = live_role in member.roles
                            else:
                                was_live = False
                        except Exception:
                            was_live = False
                        # Track if they were already live when bot started
                        if was_live and is_live:
                            startup_already_live = True
                    
                    if should_have_role != was_live:
                        try:
                            member = guild.get_member(user.id)
                            if not member:
                                try:
                                    member = await guild.fetch_member(user.id)
                                except Exception as _fetch_err:
                                    logger.debug(f"Could not fetch member {user.id} in {guild.name}: {_fetch_err}")
                                    member = None
                            
                            if should_have_role:
                                if member:
                                    if live_role:
                                        await member.add_roles(live_role, reason="User went live on Twitch" + (" during active train" if guild_data.live_role_trains_only else ""))
                                        logger.info(f"🔴 Added live role to {member.display_name} in {guild.name}")
                                    
                                    # Auto-start attendance tracking when user goes live
                                    await self._auto_start_attendance_on_live(user, guild, session)
                                    
                                    # Auto-join Twitch channel for auto-shoutouts if enabled
                                    await self._auto_join_for_shoutouts(user, guild.id, session)
                                    
                                    # Send auto-announcement if this user is the next rider
                                    if hasattr(self.bot, 'twitch_chat_bot') and user.twitch_login:
                                        await self._send_next_rider_announcements(guild.id, user.twitch_login, session)
                                    
                                    # Send custom stream chat announcement if configured
                                    await self._send_custom_live_announcement(user, guild.id, session)

                                # Post go-live alert — works even if member not cached
                                if has_alert_channel:
                                    await self._send_go_live_alert(user, member, guild, guild_data)

                            elif not should_have_role:
                                if member and live_role:
                                    await member.remove_roles(live_role, reason="User is no longer live on Twitch" if not is_live else "Train ended")
                                if member:
                                    logger.info(f"⚪ Removed live role from {member.display_name} in {guild.name}")
                                # Delete the go-live alert message if we stored one
                                if has_alert_channel:
                                    alert_key = f"{guild.id}:{user.id}"
                                    stored = self.live_alert_messages.pop(alert_key, None)
                                    if stored:
                                        try:
                                            ch = guild.get_channel(stored[0])
                                            if ch:
                                                msg = await ch.fetch_message(stored[1])
                                                await msg.delete()
                                                logger.info(f"🗑️ Deleted go-live alert for {getattr(user, 'username', user.id)} in {guild.name}")
                                        except Exception as _del_err:
                                            logger.debug(f"Could not delete go-live alert: {_del_err}")

                            self.live_status_cache[f"{guild.id}:{user.id}"] = should_have_role

                        except discord.NotFound:
                            # User is no longer in this server — silence future checks by syncing the cache
                            logger.warning(f"⚠️ User {user.id} ({getattr(user, 'username', '?')}) not found in {guild.name} — skipping live role update")
                            self.live_status_cache[f"{guild.id}:{user.id}"] = should_have_role
                        except Exception as e:
                            logger.error(f"❌ Error updating role for user {user.id}: {e}")
                    
                    # Ensure bot is in Twitch channel for auto-shoutouts even if already live (e.g., after restart)
                    elif should_have_role and is_live:
                        await self._auto_join_for_shoutouts(user, guild.id, session)
                        # If bot restarted while user was already live, kick off attendance tracking too
                        if startup_already_live:
                            logger.info(f"🔄 Bot restarted mid-stream for {user.username} — auto-starting attendance tracking")
                            await self._auto_start_attendance_on_live(user, guild, session)
                
        except Exception as e:
            logger.error(f"❌ Error updating live roles for guild {guild.id}: {e}")
    
    async def _auto_start_attendance_on_live(self, user, guild, session):
        """Automatically start attendance tracking when a user goes live on Twitch."""
        try:
            from models import NotificationSettings, TrainParticipant
            
            # Find any active or upcoming trains hosted by this user
            eastern_tz = pytz.timezone('Europe/London')
            now = datetime.now(eastern_tz)
            
            # Get schedules where this user is the host OR an active participant.
            # Raid trains are streamed by participants (riders), not just the schedule
            # host, so matching only on host_user_id sent every rider to ad-hoc
            # monitoring, which posts no attendance reports during the train.
            from sqlalchemy import or_
            participant_schedule_ids = [
                row.schedule_id for row in session.query(TrainParticipant.schedule_id).filter(
                    TrainParticipant.user_id == user.id,
                    TrainParticipant.is_active == True
                ).all()
            ]
            schedules = session.query(TrainSchedule).filter(
                TrainSchedule.guild_id == guild.id,
                TrainSchedule.is_active == True,
                or_(
                    TrainSchedule.host_user_id == user.id,
                    TrainSchedule.id.in_(participant_schedule_ids)
                )
            ).all()
            
            # Try to find a schedule within ±2 hours
            matching_schedule = None
            
            if schedules:
                # Find the schedule happening closest to now (within a ±2 hour window).
                # Pick the NEAREST leg, not the first match — riders can be in several
                # back-to-back legs, so first-match could grab the wrong one.
                best_time_diff = None
                for schedule in schedules:
                    # Calculate schedule time in EST
                    today_date = now.date()
                    current_day = now.weekday()
                    schedule_day = schedule.day_of_week
                    specific_date = getattr(schedule, 'specific_date', None)
                    if specific_date:
                        # One-time schedule: use its actual date, never weekday math
                        if isinstance(specific_date, str):
                            specific_date = datetime.strptime(specific_date, '%Y-%m-%d').date()
                        schedule_date = specific_date
                        if schedule.start_time.hour < 5:
                            schedule_date = schedule_date + timedelta(days=1)
                    else:
                        days_until_schedule = (schedule_day - current_day) % 7
                        schedule_date = today_date + timedelta(days=days_until_schedule)
                        
                        # Apply week offset
                        week_offset = self._get_week_offset(schedule)
                        if week_offset > 0:
                            schedule_date = schedule_date + timedelta(weeks=week_offset)
                    
                    # start_time stored as UK wall clock — localize directly
                    naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
                    start_datetime = eastern_tz.localize(naive_uk_datetime)
                    end_datetime = start_datetime + timedelta(minutes=schedule.duration_minutes)
                    
                    # Check if we're within 2 hours before/after the scheduled time
                    time_diff = abs((now - start_datetime).total_seconds() / 60)  # minutes
                    
                    if time_diff <= 120 and (best_time_diff is None or time_diff < best_time_diff):
                        matching_schedule = schedule
                        best_time_diff = time_diff
            
            # If no schedule found, just start ad-hoc attendance tracking
            logger.info(f"🎮 User {user.username} went live - auto-starting attendance tracking")
            
            # Check if attendance tracking is already running
            if not hasattr(self.bot, 'twitch_chat_monitor'):
                logger.warning(f"⚠️ Twitch chat monitor not available")
                return
            
            monitor = self.bot.twitch_chat_monitor
            
            # Use schedule ID if we have one, otherwise use a special ad-hoc key
            if matching_schedule:
                task_key = f"train_{matching_schedule.id}"
            else:
                task_key = f"adhoc_{guild.id}_{user.id}"
            
            if task_key in monitor.active_monitors:
                logger.info(f"✅ Attendance tracking already active")
                return
            
            # Get notification settings
            settings = session.query(NotificationSettings).filter_by(
                guild_id=guild.id
            ).first()
            
            if not settings or not settings.attendance_channel_id:
                logger.warning(f"⚠️ No attendance channel configured for {guild.name}")
                return
            
            # Get attendance channel
            attendance_channel = guild.get_channel(settings.attendance_channel_id)
            if not attendance_channel:
                logger.warning(f"⚠️ Attendance channel not found in {guild.name}")
                return
            
            # Start attendance tracking
            if matching_schedule:
                # Full train session monitoring — joins all participant channels and generates periodic reports
                await monitor.start_train_monitoring(matching_schedule.id)
            else:
                # Ad-hoc monitoring — no train schedule, just track this stream
                await monitor.start_tracking_adhoc(user.twitch_login, guild.id)
            
            # Send "Attendance Tracking Started" message
            if matching_schedule:
                participants = session.query(TrainParticipant).filter_by(
                    schedule_id=matching_schedule.id,
                    is_active=True
                ).all()
                participant_count = len(participants)
                participant_names = [p.display_name for p in participants[:5]]
                participants_text = ", ".join(participant_names)
                if participant_count > 5:
                    participants_text += f" and {participant_count - 5} others"
                
                title = "📊 Attendance Tracking Started"
                description = f"**{matching_schedule.name}**\n\nNow monitoring Twitch chat for attendance!"
            else:
                participant_count = 0
                participants_text = "No train scheduled - tracking all viewers"
                title = "📊 Chat Monitoring Started"
                description = f"**Live Stream**\n\nNow monitoring Twitch chat!"
            
            embed = discord.Embed(
                title=title,
                description=description,
                color=0x00ff00,
                timestamp=now
            )
            embed.add_field(
                name="🎮 Stream",
                value=f"[twitch.tv/{user.twitch_login}](https://twitch.tv/{user.twitch_login})",
                inline=True
            )
            
            if matching_schedule:
                embed.add_field(
                    name="👥 Participants",
                    value=f"{participant_count} signed up",
                    inline=True
                )
                if participant_count > 0:
                    embed.add_field(
                        name="📋 Roster",
                        value=participants_text,
                        inline=False
                    )
            
            # Get attendance interval
            interval = getattr(settings, 'attendance_interval_minutes', 25)
            embed.add_field(
                name="⏰ Report Frequency",
                value=f"Every {interval} minutes",
                inline=True
            )
            
            embed.set_footer(text="Automatic attendance reports will be posted here")
            
            await attendance_channel.send(embed=embed)
            logger.info(f"✅ Sent attendance tracking start message to #{attendance_channel.name}")
                    
        except Exception as e:
            logger.error(f"❌ Error auto-starting attendance on live: {e}", exc_info=True)
    
    async def _send_next_rider_announcements(self, guild_id: int, next_rider_login: str, session):
        """
        Send announcement to current riders that the next person went live.
        
        Args:
            guild_id: Discord guild ID
            next_rider_login: Twitch login of the user who just went live
            session: Database session
        """
        try:
            from datetime import datetime
            import pytz
            from models import NotificationSettings
            
            # Check if auto-announcements are enabled for this server
            settings = session.query(NotificationSettings).filter_by(guild_id=guild_id).first()
            if not settings or not settings.twitch_chat_bot_enabled or not settings.auto_announcements_enabled:
                logger.debug(f"Auto-announcements disabled for guild {guild_id}")
                return
            
            eastern_tz = pytz.timezone('Europe/London')
            now = datetime.now(eastern_tz)
            today_date = now.date()
            current_day = now.weekday()
            
            # Get all active schedules for today sorted by time
            schedules = session.query(TrainSchedule).join(User).filter(
                TrainSchedule.guild_id == guild_id,
                TrainSchedule.is_active == True,
                User.twitch_login.isnot(None)
            ).order_by(TrainSchedule.start_time).all()
            
            if not schedules:
                return
            
            # Find the schedule for the next rider who just went live
            next_rider_schedule = None
            for schedule in schedules:
                host = session.query(User).filter_by(id=schedule.host_user_id).first()
                if host and host.twitch_login == next_rider_login:
                    # Check if this schedule is for today
                    schedule_day = schedule.day_of_week
                    specific_date = getattr(schedule, 'specific_date', None)
                    if specific_date:
                        # One-time schedule: use its actual date, never weekday math
                        if isinstance(specific_date, str):
                            specific_date = datetime.strptime(specific_date, '%Y-%m-%d').date()
                        schedule_date = specific_date
                        if schedule.start_time.hour < 5:
                            schedule_date = schedule_date + timedelta(days=1)
                    else:
                        days_until_schedule = (schedule_day - current_day) % 7
                        schedule_date = today_date + timedelta(days=days_until_schedule)
                        
                        week_offset = self._get_week_offset(schedule)
                        if week_offset > 0:
                            schedule_date = schedule_date + timedelta(weeks=week_offset)
                    
                    # start_time stored as UK wall clock — localize directly
                    naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
                    start_datetime = eastern_tz.localize(naive_uk_datetime)

                    # Check if this is within 2 hours (active window)
                    time_diff = (start_datetime - now).total_seconds()
                    if -7200 <= time_diff <= 7200:  # Within 2 hours before or after
                        next_rider_schedule = schedule
                        break
            
            if not next_rider_schedule:
                return
            
            # Find all schedules that come before the next rider
            for i, schedule in enumerate(schedules):
                if schedule.id == next_rider_schedule.id:
                    # This is the next rider - send announcement to all previous riders
                    if i > 0:  # There are riders before this one
                        for prev_schedule in schedules[:i]:
                            prev_host = session.query(User).filter_by(id=prev_schedule.host_user_id).first()
                            if prev_host and prev_host.twitch_id and prev_host.twitch_login:
                                # Send announcement to previous rider's chat
                                message = f"🔴 Next rider is LIVE! 👉 {next_rider_login} is streaming now at twitch.tv/{next_rider_login} - Raid when ready!"
                                await self.bot.twitch_chat_bot.send_chat_message(prev_host.twitch_id, message)
                                logger.info(f"📢 Sent next rider announcement to {prev_host.twitch_login}: {next_rider_login} is live")
                    break
                    
        except Exception as e:
            logger.error(f"❌ Error sending next rider announcements: {e}", exc_info=True)
    
    async def _check_auto_shoutout_channels(self, guild, session):
        """
        Check for users with auto-shoutout enabled and join their channels when live.
        This runs even when live tracking is disabled (attendance-only mode).
        
        Args:
            guild: Discord guild object
            session: Database session
        """
        try:
            from models import AutoShoutoutSettings
            
            # Get all users with auto-shoutout enabled in this guild
            shoutout_users = session.query(User).join(
                AutoShoutoutSettings,
                (AutoShoutoutSettings.user_id == User.id) & 
                (AutoShoutoutSettings.guild_id == guild.id) &
                (AutoShoutoutSettings.enabled == True)
            ).filter(
                User.guild_id == guild.id,
                User.twitch_id.isnot(None),
                User.twitch_login.isnot(None)
            ).all()
            
            if not shoutout_users:
                logger.debug(f"No users with auto-shoutout enabled in {guild.name}")
                return
            
            # Check which users are currently live
            twitch_ids = [user.twitch_id for user in shoutout_users]
            live_twitch_ids = await self.check_streams_status(twitch_ids, guild_id=guild.id)
            
            # Join channels for users who are live
            for user in shoutout_users:
                if user.twitch_id in live_twitch_ids:
                    await self._auto_join_for_shoutouts(user, guild.id, session)
                    logger.info(f"🔴 {user.username} is live - joined for auto-shoutouts (attendance-only mode)")
            
        except Exception as e:
            logger.error(f"❌ Error checking auto-shoutout channels: {e}", exc_info=True)
    
    async def _auto_join_for_shoutouts(self, user: User, guild_id: int, session):
        """
        Automatically join user's Twitch channel if auto-shoutout is enabled.
        
        Args:
            user: User object with twitch_login (person going live)
            guild_id: Discord guild ID
            session: Database session
        """
        try:
            from models import AutoShoutoutSettings
            
            if not user.twitch_login:
                return
            
            # Check if auto-shoutout is enabled for this user
            shoutout_settings = session.query(AutoShoutoutSettings).filter_by(
                user_id=user.id,
                guild_id=guild_id,
                enabled=True
            ).first()
            
            if not shoutout_settings:
                logger.debug(f"Auto-shoutout not enabled for {user.username}")
                return
            
            # Check if we have the Twitch chat monitor available
            if not hasattr(self.bot, 'twitch_chat_monitor') or not self.bot.twitch_chat_monitor:
                logger.warning("Twitch chat monitor not available for auto-shoutout")
                return
            
            # Join the user's Twitch channel to monitor chat
            await self.bot.twitch_chat_monitor.join_channel(user.twitch_login)
            logger.info(f"👏 Auto-joined #{user.twitch_login} for auto-shoutouts")
            
        except Exception as e:
            logger.error(f"Error auto-joining channel for shoutouts: {e}", exc_info=True)
    
    async def _send_custom_live_announcement(self, user: User, guild_id: int, session):
        """
        Send custom live announcement to friends' Twitch chats.
        
        Args:
            user: User object with twitch_login (person going live)
            guild_id: Discord guild ID
            session: Database session
        """
        try:
            if not user.twitch_login:
                return
            
            # Check if user has a custom live announcement configured
            announcement = session.query(StreamChatAnnouncement).filter_by(
                user_id=user.id,
                guild_id=guild_id,
                enabled=True
            ).first()
            
            if not announcement:
                logger.debug(f"No custom live announcement for {user.username}")
                return
            
            # Check if we have the Twitch chat bot available
            if not hasattr(self.bot, 'twitch_chat_monitor') or not self.bot.twitch_chat_monitor:
                logger.warning("Twitch chat monitor not available for custom announcements")
                return
            
            # Check if bot is connected to Twitch IRC
            if not self.bot.twitch_chat_monitor.is_connected:
                logger.warning("Twitch chat monitor not connected - cannot send announcement")
                return
            
            # Send the message to each friend's Twitch chat
            sent_count = 0
            failed_channels = []
            
            for friend_channel in announcement.target_twitch_channels:
                success = await self.bot.twitch_chat_monitor.send_message(
                    friend_channel,
                    announcement.announcement_message
                )
                
                if success:
                    sent_count += 1
                    logger.info(f"✅ Sent live announcement to {friend_channel}: {announcement.announcement_message}")
                else:
                    failed_channels.append(friend_channel)
                    logger.warning(f"Failed to send announcement to {friend_channel}")
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.5)
            
            if sent_count > 0:
                # Update last sent timestamp
                announcement.last_sent_at = datetime.now()
                session.commit()
                logger.info(f"✅ Sent custom live announcements to {sent_count}/{len(announcement.target_twitch_channels)} channels")
            
            if failed_channels:
                logger.warning(f"Failed to send to channels: {', '.join(failed_channels)}")
        
        except Exception as e:
            logger.error(f"Error sending custom live announcement: {e}")
    
    async def _get_stream_info(self, twitch_login: str) -> dict:
        """Fetch live stream details (title, game, viewer count) for a single Twitch login."""
        try:
            token = await self.get_access_token()
            if not token or not self.client_id:
                return {}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.twitch.tv/helix/streams',
                    params={'user_login': twitch_login},
                    headers={'Client-ID': self.client_id, 'Authorization': f'Bearer {token}'},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        streams = data.get('data', [])
                        return streams[0] if streams else {}
        except Exception as e:
            logger.warning(f"Could not fetch stream info for {twitch_login}: {e}")
        return {}

    async def _send_go_live_alert(self, user, member, guild, guild_data):
        """Post a go-live embed to the configured alert channel."""
        try:
            channel = guild.get_channel(guild_data.stream_alert_channel_id)
            if not channel:
                logger.warning(f"Go-live alert channel {guild_data.stream_alert_channel_id} not found in {guild.name}")
                return

            twitch_login = user.twitch_login or ''
            stream_info = await self._get_stream_info(twitch_login) if twitch_login else {}

            title = stream_info.get('title', '').strip() or 'No title set'
            game = stream_info.get('game_name', '').strip() or 'Unknown'
            viewers = stream_info.get('viewer_count', 0)
            twitch_url = f'https://twitch.tv/{twitch_login}' if twitch_login else ''
            thumbnail = stream_info.get('thumbnail_url', '').replace('{width}', '320').replace('{height}', '180')

            # Use member info if available, fall back to Twitch/DB display name
            display = (member.display_name if member else None) or getattr(user, 'twitch_display_name', None) or getattr(user, 'username', twitch_login) or twitch_login
            avatar = (member.display_avatar.url if member and member.display_avatar else None)

            embed = discord.Embed(
                title=f'🔴 {display} is now LIVE on Twitch!',
                description=f'**[{title}]({twitch_url})**' if twitch_url else f'**{title}**',
                color=0x9146FF,
                url=twitch_url or discord.Embed.Empty
            )
            embed.add_field(name='🎮 Game', value=game, inline=True)
            embed.add_field(name='👀 Viewers', value=str(viewers), inline=True)
            if twitch_url:
                embed.add_field(name='📺 Watch', value=f'[Click here]({twitch_url})', inline=True)
            if thumbnail:
                embed.set_image(url=thumbnail)
            if avatar:
                embed.set_thumbnail(url=avatar)
            embed.set_footer(text=f'Twitch • {twitch_login}' if twitch_login else 'Twitch')

            # Optional role ping
            content = ''
            if guild_data.stream_alert_mention_role_id:
                ping_role = guild.get_role(guild_data.stream_alert_mention_role_id)
                if ping_role:
                    content = ping_role.mention

            sent_msg = await channel.send(content=content or None, embed=embed)
            # Store message ID so we can delete it when the stream ends
            alert_key = f"{guild.id}:{user.id}"
            self.live_alert_messages[alert_key] = (channel.id, sent_msg.id)
            logger.info(f"📣 Go-live alert sent for {display} in {guild.name} (#{channel.name})")

        except Exception as e:
            logger.error(f"❌ Error sending go-live alert for user {user.id} in {guild.name}: {e}")

    async def update_all_guilds(self):
        logger.debug("🔄 Checking live status for all guilds...")
        for guild in self.bot.guilds:
            try:
                await self.update_live_roles_for_guild(guild)
            except Exception as e:
                logger.error(f"❌ Error in live role update for guild {guild.id}: {e}")
    
    async def start_auto_shoutout_watcher(self):
        """Start the dedicated auto-shoutout watcher task."""
        if self._shoutout_watcher_running:
            logger.debug("Auto-shoutout watcher already running")
            return
        
        self._shoutout_watcher_running = True
        logger.info("🎯 Starting dedicated auto-shoutout watcher (runs every 3 minutes)")
        
        while self._shoutout_watcher_running:
            try:
                await self._check_all_auto_shoutout_users()
            except Exception as e:
                logger.error(f"❌ Error in auto-shoutout watcher: {e}", exc_info=True)
            
            await asyncio.sleep(180)  # Check every 3 minutes
    
    async def stop_auto_shoutout_watcher(self):
        """Stop the auto-shoutout watcher task."""
        self._shoutout_watcher_running = False
        logger.info("🛑 Auto-shoutout watcher stopped")
    
    async def _check_all_auto_shoutout_users(self):
        """
        Check ALL users with auto-shoutout enabled across ALL guilds.
        This runs independently of the live role system.
        """
        try:
            from models import AutoShoutoutSettings
            
            # Increment check counter for periodic forced rejoins
            self._shoutout_check_count += 1
            
            # Every 5 cycles (~7.5 minutes), force rejoin to handle silent disconnects
            force_rejoin = (self._shoutout_check_count % 5 == 0)
            if force_rejoin:
                logger.info("🔄 Periodic forced rejoin - clearing cache to refresh connections")
                self.shoutout_joined_channels.clear()
            
            with DatabaseSession() as session:
                # Get ALL enabled auto-shoutout settings across all guilds
                all_shoutout_settings = session.query(AutoShoutoutSettings).filter(
                    AutoShoutoutSettings.enabled == True
                ).all()
                
                if not all_shoutout_settings:
                    logger.debug("No auto-shoutout settings enabled")
                    return
                
                # Get unique user IDs and their Twitch IDs
                user_ids = list(set([s.user_id for s in all_shoutout_settings]))
                
                users_with_twitch = session.query(User).filter(
                    User.id.in_(user_ids),
                    User.twitch_id.isnot(None),
                    User.twitch_login.isnot(None)
                ).all()
                
                if not users_with_twitch:
                    logger.debug("No auto-shoutout users have linked Twitch accounts")
                    return
                
                # Build Twitch ID to User mapping
                twitch_id_to_user = {user.twitch_id: user for user in users_with_twitch}
                twitch_ids = list(twitch_id_to_user.keys())
                
                logger.debug(f"🎯 Auto-shoutout watcher: Checking {len(twitch_ids)} users with auto-shoutout enabled")
                
                # Check which users are currently live
                live_twitch_ids = await self.check_streams_status(twitch_ids)
                
                if not live_twitch_ids:
                    # Clear joined channels cache when no one is live
                    self.shoutout_joined_channels.clear()
                    return
                
                logger.info(f"🔴 Auto-shoutout watcher: Found {len(live_twitch_ids)} live streamers with auto-shoutout enabled")
                
                # Join channels for users who are live
                for twitch_id in live_twitch_ids:
                    user = twitch_id_to_user.get(twitch_id)
                    if not user or not user.twitch_login:
                        continue
                    
                    channel_name = user.twitch_login.lower()
                    
                    # Get any guild_id for this user's shoutout settings (for logging)
                    user_settings = [s for s in all_shoutout_settings if s.user_id == user.id]
                    if not user_settings:
                        continue
                    
                    # Join the channel - always verify connection is active
                    if hasattr(self.bot, 'twitch_chat_monitor') and self.bot.twitch_chat_monitor:
                        monitor = self.bot.twitch_chat_monitor
                        # If not connected, clear cache and reconnect
                        if not monitor.is_connected:
                            logger.warning("Twitch chat monitor not connected - clearing cache and reconnecting")
                            self.shoutout_joined_channels.clear()
                            await monitor.connect_to_twitch_irc()
                            await asyncio.sleep(2)
                        
                        # Check if listener task died — use connect_to_twitch_irc which
                        # handles locking, auth, and proper task management (not a raw task create)
                        if monitor.is_connected and (not hasattr(monitor, 'listener_task') or monitor.listener_task is None or monitor.listener_task.done()):
                            logger.warning("🔄 IRC listener task not running - reconnecting via connect_to_twitch_irc")
                            await monitor.connect_to_twitch_irc()
                        
                        # Check if we've already joined this channel
                        if channel_name in self.shoutout_joined_channels:
                            logger.debug(f"Already joined #{channel_name} for auto-shoutouts")
                            continue
                        
                        # Join the channel
                        if self.bot.twitch_chat_monitor.is_connected:
                            await self.bot.twitch_chat_monitor.join_channel(channel_name)
                            self.shoutout_joined_channels.add(channel_name)
                            logger.info(f"🎯 Auto-shoutout watcher: Joined #{channel_name} (user is LIVE)")
                    
                    # Small delay between joins
                    await asyncio.sleep(0.5)
                
                # Clean up channels that are no longer live
                live_channels = {twitch_id_to_user[tid].twitch_login.lower() 
                                for tid in live_twitch_ids 
                                if tid in twitch_id_to_user and twitch_id_to_user[tid].twitch_login}
                channels_to_remove = self.shoutout_joined_channels - live_channels
                
                for channel in channels_to_remove:
                    self.shoutout_joined_channels.discard(channel)
                    logger.debug(f"Removed #{channel} from joined channels (no longer live)")
                
        except Exception as e:
            logger.error(f"❌ Error checking auto-shoutout users: {e}", exc_info=True)
