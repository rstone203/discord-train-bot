"""
Twitch Chat Attendance Monitor - Real-time tracking of users in Twitch chat
for accurate attendance reporting during raid trains.
"""
import asyncio
import logging
import websockets
import json
import os
import aiohttp
import discord
from datetime import datetime, timedelta
from typing import Dict, Set, List, Optional
from database import DatabaseSession, AsyncDatabaseSession
from models import TrainParticipant, TrainSchedule, User, NotificationSettings, TwitchOAuthToken
from discord.ext import tasks
from sqlalchemy import func

logger = logging.getLogger('discord_bot.twitch_chat_monitor')

class TwitchChatMonitor:
    """Monitor Twitch chat for real-time attendance tracking."""
    
    def __init__(self, bot):
        self.bot = bot
        self.client_id = os.getenv('TWITCH_CLIENT_ID')
        self.client_secret = os.getenv('TWITCH_CLIENT_SECRET')
        self.access_token = None
        self.moderator_user_id = None
        
        # Track active monitoring sessions
        self.active_monitors: Dict[str, asyncio.Task] = {}
        self.chat_participants: Dict[str, Set[str]] = {}  # channel -> set of usernames (cumulative)
        self.current_cycle_chatters: Dict[str, Set[str]] = {}  # channel -> set of usernames (current poll only)
        # Per-channel attendance tracking quality for the current session:
        #   'full'      -> chatters API worked (we see everyone in chat)
        #   'chat_only' -> chatters API blocked (403/401), only IRC speakers counted
        self.channel_tracking_quality: Dict[str, str] = {}
        self.current_live_channels: List[str] = []  # currently live channels for attendance
        self.channel_to_schedule: Dict[str, int] = {}  # channel -> schedule_id mapping
        self.broadcaster_ids: Dict[str, str] = {}  # twitch_login -> broadcaster_id mapping
        
        # Auto-shoutout tracking
        self.shouted_out_users: Dict[str, Dict[str, datetime]] = {}  # channel -> {username: last_shoutout_time}
        self.joined_channels: Set[str] = set()
        self.expected_channels: Set[str] = set()
        
        # WebSocket connection for Twitch IRC
        self.websocket = None
        self.is_connected = False
        self.listener_task = None
        self._connection_lock = asyncio.Lock()  # Prevent concurrent connection attempts
        self._irc_username = 'gl_stoney'  # Always connect to IRC as gl_stoney (owner's Twitch account)
        # Auth failure cooldown — when Twitch rejects our token, pause for 10 min before retrying
        # to avoid hammering Twitch IRC and spinning in a crash loop.
        self._irc_auth_failed_until: Optional[datetime] = None
        
        # Initialize Twitch chat bot for r3labs-style features
        from utils.twitch_chat_bot import TwitchChatBot
        self.chat_bot = TwitchChatBot(bot)
        
        # Start proactive token refresh task
        self.token_refresh_task.start()
        
        # Start attendance flush task (saves chat data to database every 3 minutes)
        self.attendance_flush_task.start()
        
        # Start IRC health check task (monitors and restarts listener if it dies)
        self.irc_health_check_task.start()
        
    def _get_irc_token_from_db(self, session, exclude_ids=None) -> Optional[object]:
        """
        Find the best available IRC token using a priority fallback chain:
          1. gl_stoney (owner's primary Twitch account)
          2. unicornsp4rkl3s (designated backup)
          3. Any active token from a trusted user in Game Lounge (1183084958110191616)
        Returns the ORM token object or None.
        exclude_ids: set of token IDs to skip (used when a token was rejected by IRC).
        """
        from models import TwitchOAuthToken, TrustedUser, User
        GAME_LOUNGE_ID = 1183084958110191616
        exclude_ids = exclude_ids or set()

        # 1. Owner's primary account — prefer a token that has chat:read in its scopes.
        #    A token saved without scope metadata may lack IRC access even if it looks active.
        gl_tokens = session.query(TwitchOAuthToken).filter(
            TwitchOAuthToken.is_active == True,
            TwitchOAuthToken.twitch_username == 'gl_stoney'
        ).order_by(TwitchOAuthToken.expires_at.desc()).all()
        if gl_tokens:
            # Prefer tokens that explicitly have chat:read scope and aren't excluded
            chat_tokens = [t for t in gl_tokens if t.scopes and 'chat:read' in t.scopes and t.id not in exclude_ids]
            if chat_tokens:
                return chat_tokens[0]
            # Fallback to any non-excluded gl_stoney token with a warning
            non_excluded = [t for t in gl_tokens if t.id not in exclude_ids]
            if non_excluded:
                logger.warning("⚠️ gl_stoney IRC token has no scope metadata — may lack chat:read. Run /twitchoauth to fix.")
                return non_excluded[0]
            # All gl_stoney tokens excluded (failed) — fall through to unicornsp4rkl3s

        # 2. Designated backup — unicornsp4rkl3s
        token = session.query(TwitchOAuthToken).filter(
            TwitchOAuthToken.is_active == True,
            TwitchOAuthToken.twitch_username == 'unicornsp4rkl3s'
        ).order_by(TwitchOAuthToken.expires_at.desc()).first()
        if token and token.id not in exclude_ids:
            logger.warning("⚠️ IRC fallback: using unicornsp4rkl3s token (gl_stoney unavailable)")
            return token

        # 3. Any trusted user in Game Lounge with an active token
        trusted_user_ids = [
            row.user_id for row in
            session.query(TrustedUser).filter(TrustedUser.is_active == True).all()
        ]
        if trusted_user_ids:
            # Restrict to users whose primary guild is Game Lounge
            gl_user_ids = [
                row.id for row in
                session.query(User).filter(
                    User.id.in_(trusted_user_ids),
                    User.guild_id == GAME_LOUNGE_ID
                ).all()
            ]
            if gl_user_ids:
                token = session.query(TwitchOAuthToken).filter(
                    TwitchOAuthToken.is_active == True,
                    TwitchOAuthToken.user_id.in_(gl_user_ids)
                ).order_by(TwitchOAuthToken.expires_at.desc()).first()
                if token:
                    logger.warning(f"⚠️ IRC fallback: using trusted user token ({token.twitch_username}) — gl_stoney & unicornsp4rkl3s both unavailable")
                    return token

        logger.error("❌ IRC token fallback exhausted — no usable token found in gl_stoney, unicornsp4rkl3s, or trusted Game Lounge users")
        return None

    async def refresh_access_token(self, twitch_user_id: str = None) -> Optional[str]:
        """Refresh expired access token using refresh token. Can target a specific user or default to gl_stoney."""
        try:
            from models import TwitchOAuthToken
            async with AsyncDatabaseSession() as session:
                if twitch_user_id:
                    active_token = session.query(TwitchOAuthToken).filter(
                        TwitchOAuthToken.is_active == True,
                        TwitchOAuthToken.twitch_user_id == str(twitch_user_id)
                    ).order_by(TwitchOAuthToken.expires_at.desc()).first()
                else:
                    active_token = self._get_irc_token_from_db(session)

                if not active_token or not active_token.refresh_token:
                    logger.error(f"❌ No refresh token available for user {twitch_user_id or 'default'}")
                    return None
                
                logger.info(f"🔄 Attempting to refresh Twitch OAuth token for {active_token.twitch_username}...")
                
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
                            
                            if not twitch_user_id or active_token.twitch_username == 'gl_stoney':
                                self.access_token = new_access_token
                            logger.info(f"✅ Token refreshed for {active_token.twitch_username}! Expires in {expires_in}s")
                            return new_access_token
                        else:
                            error_text = await resp.text()
                            logger.error(f"❌ Failed to refresh token for {active_token.twitch_username}: {resp.status} - {error_text}")
                            return None
                            
        except Exception as e:
            logger.error(f"❌ Error refreshing Twitch access token: {e}")
            return None
    
    async def get_access_token(self, allow_client_credentials: bool = False) -> Optional[str]:
        """Get OAuth access token for Twitch chat (from database if available, falls back to client credentials for API)."""
        try:
            # First try to get user OAuth token from database (for IRC chat)
            try:
                from models import TwitchOAuthToken
                async with AsyncDatabaseSession() as session:
                    active_token = self._get_irc_token_from_db(session)
                    if active_token:
                        # Check if token is expired
                        if active_token.expires_at and active_token.expires_at < datetime.now():
                            logger.warning("⚠️ Access token expired, attempting automatic refresh...")
                            # Try to refresh the token automatically
                            refreshed_token = await self.refresh_access_token()
                            if refreshed_token:
                                logger.info("✅ Token automatically refreshed!")
                                return refreshed_token
                            else:
                                # Refresh failed - notify user
                                logger.error("❌ AUTOMATIC TOKEN REFRESH FAILED - Chat monitoring unavailable!")
                                logger.error("📋 Action required: Run /twitchoauth to manually refresh token")
                                await self._notify_token_expired()
                                if not allow_client_credentials:
                                    return None
                        else:
                            logger.info("✅ Using stored user OAuth token for chat monitoring")
                            self.access_token = active_token.access_token
                            return self.access_token
            except Exception as e:
                logger.warning(f"Could not retrieve stored OAuth token: {e}")
            
            # Fall back to client credentials for API calls only (not chat)
            if self.access_token:
                return self.access_token
            
            # Only allow client credentials if explicitly requested (for API calls, not chat)
            if not allow_client_credentials:
                logger.error("❌ No valid user OAuth token available for Twitch chat")
                return None
                
            url = "https://id.twitch.tv/oauth2/token"
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        self.access_token = result['access_token']
                        logger.info("✅ Twitch API token obtained (client credentials)")
                        return self.access_token
                    else:
                        logger.error(f"Failed to get Twitch access token: {resp.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error getting Twitch access token: {e}")
            return None
    
    async def get_user_id(self, username: str) -> Optional[str]:
        """Get Twitch user ID from username."""
        try:
            token = await self.get_access_token()
            if not token:
                return None
                
            url = f"https://api.twitch.tv/helix/users?login={username}"
            headers = {
                'Client-ID': self.client_id,
                'Authorization': f'Bearer {token}'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data['data']:
                            return data['data'][0]['id']
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting user ID for {username}: {e}")
            return None
    
    async def get_moderator_user_id(self) -> Optional[str]:
        """Get the user ID of the authenticated user (moderator)."""
        if self.moderator_user_id:
            return self.moderator_user_id
            
        try:
            token = await self.get_access_token(allow_client_credentials=False)
            if not token:
                return None
                
            url = "https://api.twitch.tv/helix/users"
            headers = {
                'Client-ID': self.client_id,
                'Authorization': f'Bearer {token}'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data['data']:
                            self.moderator_user_id = data['data'][0]['id']
                            logger.info(f"✅ Moderator user ID: {self.moderator_user_id}")
                            return self.moderator_user_id
                    else:
                        logger.error(f"Failed to get moderator user ID: {resp.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting moderator user ID: {e}")
            return None
    
    async def _get_app_token(self) -> Optional[str]:
        """Always fetch a fresh client credentials (app) token — never returns a user OAuth token.
        Use this for /helix/streams checks where user tokens return 0 results."""
        try:
            url = "https://id.twitch.tv/oauth2/token"
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials'
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result['access_token']
                    else:
                        logger.error(f"Failed to get app token for stream check: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching app token: {e}")
            return None

    async def get_stream_viewer_count(self, broadcaster_login: str) -> Optional[int]:
        """Get the current viewer count for a live stream.
        Returns viewer count (>= 0) if live, -1 if offline, None on error."""
        try:
            # Must use client credentials (app token) — user OAuth tokens return 0 from /helix/streams
            token = await self._get_app_token()
            if not token:
                logger.error(f"No access token available for stream check of {broadcaster_login}")
                return None
            
            url = f"https://api.twitch.tv/helix/streams?user_login={broadcaster_login}"
            headers = {
                'Client-ID': self.client_id,
                'Authorization': f'Bearer {token}'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info(f"📡 Twitch API response for {broadcaster_login}: {len(data.get('data', []))} streams found")
                        if data['data'] and len(data['data']) > 0:
                            stream = data['data'][0]
                            viewer_count = stream.get('viewer_count', 0)
                            stream_type = stream.get('type', 'unknown')
                            logger.info(f"👁️ {broadcaster_login}: {viewer_count} viewers, type={stream_type}")
                            return viewer_count
                        else:
                            logger.info(f"📡 {broadcaster_login}: No stream data returned - stream is offline")
                            return -1
                    elif resp.status == 401:
                        logger.error(f"🔑 Twitch API 401 for {broadcaster_login} - token may be expired, refreshing...")
                        self.access_token = None
                        self.token_expiry = None
                        return None
                    else:
                        body = await resp.text()
                        logger.error(f"Failed to get viewer count for {broadcaster_login}: {resp.status} - {body}")
                        return None
        except Exception as e:
            logger.error(f"Error getting viewer count for {broadcaster_login}: {e}")
            return None
    
    async def get_chatters_from_api(self, broadcaster_login: str, guild_id: int = None, schedule_id: int = None) -> Set[str]:
        """Get list of users currently in chat using Twitch API."""
        try:
            # Get broadcaster ID
            if broadcaster_login not in self.broadcaster_ids:
                broadcaster_id = await self.get_user_id(broadcaster_login)
                if not broadcaster_id:
                    # Could be a transient Twitch API hiccup — log only, no DM
                    logger.warning(f"⚠️ Could not get broadcaster ID for {broadcaster_login} - skipping poll this cycle")
                    return set()
                self.broadcaster_ids[broadcaster_login] = broadcaster_id
            else:
                broadcaster_id = self.broadcaster_ids[broadcaster_login]
            
            # Prefer the broadcaster's own token (a broadcaster can always query their own chatters).
            # The bot account (rstone203) is often not modded in participants' channels → 403.
            moderator_id = broadcaster_id  # default: broadcaster queries their own chat
            token = None
            try:
                async with AsyncDatabaseSession() as db_sess:
                    broadcaster_token_rec = db_sess.query(TwitchOAuthToken).filter(
                        TwitchOAuthToken.twitch_user_id == str(broadcaster_id),
                        TwitchOAuthToken.is_active == True
                    ).first()
                    if broadcaster_token_rec:
                        token = broadcaster_token_rec.access_token
                        logger.info(f"Using broadcaster's own token for chatters API: {broadcaster_login}")
            except Exception as tok_err:
                logger.warning(f"Could not look up broadcaster token for {broadcaster_login}: {tok_err}")

            # Fall back to bot's own token + bot as moderator if no broadcaster token available
            if not token:
                moderator_id = await self.get_moderator_user_id()
                if not moderator_id:
                    # Log only — not worth a DM, this recovers automatically when token is refreshed
                    logger.warning(f"⚠️ No moderator ID available for chatters API ({broadcaster_login}) - skipping poll")
                    return set()
                token = await self.get_access_token(allow_client_credentials=False)
                if not token:
                    # Token expiry is already handled by _notify_token_expired — don't send a second DM
                    logger.warning(f"⚠️ No OAuth token for chatters API ({broadcaster_login}) - skipping poll")
                    return set()
                logger.info(f"No broadcaster token found for {broadcaster_login} - using bot token (may 403 if not modded)")
            
            # Call the chatters API
            url = f"https://api.twitch.tv/helix/chat/chatters?broadcaster_id={broadcaster_id}&moderator_id={moderator_id}&first=1000"
            headers = {
                'Client-ID': self.client_id,
                'Authorization': f'Bearer {token}'
            }
            
            chatters = set()
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for chatter in data.get('data', []):
                            chatters.add(chatter['user_login'].lower())
                        total = data.get('total', len(chatters))
                        logger.info(f"📊 {broadcaster_login}: {len(chatters)} chatters in channel (total: {total})")
                        self.channel_tracking_quality[broadcaster_login.lower()] = 'full'
                        return chatters
                    elif resp.status == 403:
                        # 403 is expected for participant channels where the bot isn't a mod —
                        # log at debug level only, never DM. Mark as chat-only so reports can
                        # explain why this channel's attendance may be undercounted.
                        logger.debug(f"⚠️ No permission to view chatters in #{broadcaster_login} (bot not modded there - expected)")
                        self.channel_tracking_quality[broadcaster_login.lower()] = 'chat_only'
                        return set()
                    elif resp.status == 401:
                        # Token rejected — already covered by token expiry notifications
                        logger.warning(f"⚠️ 401 for chatters in #{broadcaster_login} - token may have just expired, will refresh next cycle")
                        self.channel_tracking_quality[broadcaster_login.lower()] = 'chat_only'
                        return set()
                    else:
                        error_text = await resp.text()
                        logger.error(f"Failed to get chatters for {broadcaster_login}: {resp.status} - {error_text}")
                        if guild_id:
                            await self._notify_attendance_error(guild_id, broadcaster_login, f"Twitch API error: {resp.status}", schedule_id=schedule_id)
                        return set()
                        
        except Exception as e:
            logger.error(f"Error getting chatters for {broadcaster_login}: {e}")
            if guild_id:
                await self._notify_attendance_error(guild_id, broadcaster_login, f"Network error: {str(e)}", schedule_id=schedule_id)
            return set()
    
    async def connect_to_twitch_irc(self):
        """Connect to Twitch IRC WebSocket."""
        # If a previous auth attempt was rejected, observe a 10-minute cooldown before
        # trying again.  This prevents a crash loop where every websocket close (caused
        # by the bad token) immediately triggers another doomed connection attempt.
        if self._irc_auth_failed_until and datetime.utcnow() < self._irc_auth_failed_until:
            remaining = (self._irc_auth_failed_until - datetime.utcnow()).seconds // 60
            logger.warning(
                f"⏸️ IRC auth cooldown active — waiting {remaining}m before retrying. "
                "Run /twitchoauth as gl_stoney to restore connectivity sooner."
            )
            return

        # Use lock to prevent concurrent connection attempts
        if self._connection_lock.locked():
            logger.debug("Connection already in progress, skipping...")
            return
        
        async with self._connection_lock:
            try:
                if self.is_connected and self.websocket:
                    logger.info("Already connected to Twitch IRC")
                    return
                
                # ── Cancel the old listener task BEFORE opening a new websocket ──
                # This prevents the ConcurrencyError where both the old listener
                # and the new auth loop call recv() on the same websocket object.
                if self.listener_task and not self.listener_task.done():
                    self.listener_task.cancel()
                    try:
                        await self.listener_task
                    except asyncio.CancelledError:
                        pass
                    self.listener_task = None
                
                # Close and discard the old websocket cleanly
                if self.websocket:
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    self.websocket = None
                
                # Try tokens in priority order; if one is rejected by Twitch IRC,
                # immediately fall back to the next rather than giving up entirely.
                failed_token_ids: set = set()
                connected = False

                for _attempt in range(3):
                    # Pick the best available token, excluding any that already failed.
                    # Extract all needed fields INSIDE the session to avoid SQLAlchemy
                    # "detached instance" errors after the session context closes.
                    async with AsyncDatabaseSession() as _sess:
                        _row = self._get_irc_token_from_db(_sess, exclude_ids=failed_token_ids)
                        if _row:
                            token_row_id    = _row.id
                            token           = _row.access_token
                            irc_nick        = (_row.twitch_username or 'gl_stoney').lower()
                            _twitch_user_id = _row.twitch_user_id
                        else:
                            token_row_id = token = irc_nick = _twitch_user_id = None

                    if not token:
                        logger.error("❌ Cannot connect to Twitch IRC - no valid OAuth token available")
                        logger.error("📋 Run /twitchoauth to obtain a valid token")
                        break

                    # Validate the token with Twitch's /oauth2/validate endpoint before
                    # attempting IRC — this shows the real scopes and, crucially, detects
                    # expired tokens so we can refresh them inline rather than skipping.
                    try:
                        import aiohttp
                        async with aiohttp.ClientSession() as _http:
                            async with _http.get(
                                'https://id.twitch.tv/oauth2/validate',
                                headers={'Authorization': f'OAuth {token}'},
                                timeout=aiohttp.ClientTimeout(total=8)
                            ) as _vr:
                                if _vr.status == 200:
                                    _vdata = await _vr.json()
                                    _real_scopes = _vdata.get('scopes') or []
                                    logger.info(
                                        f"🔍 Token id={token_row_id} ({irc_nick}) validated — "
                                        f"scopes: {_real_scopes}"
                                    )
                                    if 'chat:read' not in _real_scopes:
                                        logger.error(
                                            f"❌ Token id={token_row_id} ({irc_nick}) lacks chat:read — "
                                            f"has: {_real_scopes}. "
                                            f"Owner: run !resetmytwitch then /twitchoauth to re-grant scopes."
                                        )
                                        failed_token_ids.add(token_row_id)
                                        continue
                                elif _vr.status == 401:
                                    # Token is expired — try to refresh it inline before giving up.
                                    logger.warning(
                                        f"⚠️ Token id={token_row_id} ({irc_nick}) expired — "
                                        f"refreshing inline..."
                                    )
                                    refreshed = await self.refresh_access_token(twitch_user_id=_twitch_user_id)
                                    if refreshed:
                                        # Re-read the fresh token from DB for this attempt
                                        async with AsyncDatabaseSession() as _sess2:
                                            _row2 = self._get_irc_token_from_db(_sess2, exclude_ids=failed_token_ids)
                                            if _row2 and _row2.id == token_row_id:
                                                token = _row2.access_token
                                            elif _row2:
                                                # Refreshed a different slot; pick it up next iteration
                                                token = _row2.access_token
                                                token_row_id = _row2.id
                                                irc_nick = (_row2.twitch_username or irc_nick).lower()
                                        logger.info(f"✅ Inline refresh succeeded for {irc_nick} — retrying validate")
                                        # Brief pause — Twitch sometimes takes a moment to
                                        # propagate a freshly-issued token across its servers.
                                        await asyncio.sleep(2)
                                        # Re-validate the fresh token
                                        async with aiohttp.ClientSession() as _http2:
                                            async with _http2.get(
                                                'https://id.twitch.tv/oauth2/validate',
                                                headers={'Authorization': f'OAuth {token}'},
                                                timeout=aiohttp.ClientTimeout(total=8)
                                            ) as _vr2:
                                                if _vr2.status == 200:
                                                    _vdata2 = await _vr2.json()
                                                    _real_scopes = _vdata2.get('scopes') or []
                                                    logger.info(
                                                        f"🔍 Refreshed token id={token_row_id} ({irc_nick}) — "
                                                        f"scopes: {_real_scopes}"
                                                    )
                                                    if 'chat:read' not in _real_scopes:
                                                        logger.error(
                                                            f"❌ Refreshed token ({irc_nick}) lacks chat:read — "
                                                            f"has: {_real_scopes}. Run !resetmytwitch then /twitchoauth."
                                                        )
                                                        failed_token_ids.add(token_row_id)
                                                        continue
                                                else:
                                                    logger.error(
                                                        f"❌ Re-validate after refresh failed (HTTP {_vr2.status}) for {irc_nick} — skipping."
                                                    )
                                                    failed_token_ids.add(token_row_id)
                                                    continue
                                    else:
                                        logger.error(
                                            f"❌ Inline refresh failed for {irc_nick} (id={token_row_id}) — skipping."
                                        )
                                        failed_token_ids.add(token_row_id)
                                        continue
                                else:
                                    logger.warning(
                                        f"⚠️ Token validate returned HTTP {_vr.status} for id={token_row_id} — proceeding anyway"
                                    )
                    except Exception as _ve:
                        logger.warning(f"⚠️ Could not validate token id={token_row_id}: {_ve} — proceeding anyway")

                    logger.info(
                        f"🔌 IRC attempt {_attempt+1}: connecting as '{irc_nick}' "
                        f"(token id={token_row_id}, prefix={token[:8]}…)"
                    )

                    self.websocket = await websockets.connect('wss://irc-ws.chat.twitch.tv:443')

                    # Send authentication
                    await self.websocket.send('CAP REQ :twitch.tv/membership twitch.tv/tags twitch.tv/commands')
                    await self.websocket.send(f'PASS oauth:{token}')
                    await self.websocket.send(f'NICK {irc_nick}')

                    # Wait for authentication confirmation messages
                    auth_confirmed = False
                    login_failed   = False
                    try:
                        for _ in range(10):
                            message = await asyncio.wait_for(self.websocket.recv(), timeout=2.0)
                            logger.info(f"📨 IRC message during auth: {message[:150]}")

                            if message.startswith('PING'):
                                await self.websocket.send('PONG :tmi.twitch.tv')
                                logger.info("✅ Responded to PING during auth phase")
                                continue

                            # Twitch uses two different phrasings depending on context
                            if 'Login unsuccessful' in message or 'Login authentication failed' in message:
                                logger.error(
                                    f"❌ IRC auth rejected for '{irc_nick}' (token id={token_row_id}) "
                                    f"— Twitch notice: {message.strip()[:120]}"
                                )
                                login_failed = True
                                break

                            if '001' in message or 'Welcome, GLHF!' in message:
                                auth_confirmed = True
                                logger.info("✅ Auth confirmed (001 Welcome received), exiting auth loop")
                                break
                    except asyncio.TimeoutError:
                        logger.warning("Twitch auth confirmation timeout, proceeding anyway")

                    if login_failed:
                        # Close this websocket and try the next token in the chain
                        try:
                            await self.websocket.close()
                        except Exception:
                            pass
                        self.websocket = None
                        failed_token_ids.add(token_row_id)
                        logger.warning(
                            f"⚠️ Token for '{irc_nick}' (id={token_row_id}) rejected — trying next fallback token..."
                        )
                        continue  # retry loop

                    # Auth succeeded (or timed out — proceed optimistically)
                    connected = True
                    # Update the in-memory IRC username so the message listener
                    # knows which account we're connected as.
                    self._irc_username = irc_nick
                    break  # exit retry loop

                if not connected:
                    self.is_connected = False
                    # Set a 10-minute cooldown only after ALL tokens have failed
                    self._irc_auth_failed_until = datetime.utcnow() + timedelta(minutes=10)
                    logger.error(
                        "❌ IRC connection aborted — all available tokens rejected. "
                        "Pausing reconnect attempts for 10 minutes. "
                        "Run /twitchoauth (gl_stoney or unicornsp4rkl3s) to restore connectivity."
                    )
                    return

                self.is_connected = True
                logger.info(f"✅ Connected to Twitch IRC as '{self._irc_username}' (auth_confirmed: {auth_confirmed})")
                
                # Start new message listener
                self.listener_task = asyncio.create_task(self._listen_to_messages())
                
                # Auto-join channels that have auto-shoutout enabled
                asyncio.create_task(self._join_auto_shoutout_channels())
                
            except Exception as e:
                logger.error(f"Failed to connect to Twitch IRC: {e}", exc_info=True)
                self.is_connected = False
    
    async def _join_auto_shoutout_channels(self):
        """Auto-join all Twitch channels that have auto-shoutout enabled."""
        try:
            await asyncio.sleep(3)
            from models import AutoShoutoutSettings, Guild
            async with AsyncDatabaseSession() as session:
                enabled_settings = session.query(AutoShoutoutSettings).filter_by(enabled=True).all()
                
                if not enabled_settings:
                    logger.debug("📢 No auto-shoutout channels to join")
                    return

                # Build set of guild IDs with live role tracking enabled
                live_tracking_guilds = set(
                    g.id for g in session.query(Guild).filter_by(live_tracking_enabled=True).all()
                )

                joined = 0
                for settings in enabled_settings:
                    user = session.query(User).filter_by(id=settings.user_id).first()
                    if user and user.twitch_login:
                        channel_name = user.twitch_login.lower()
                        await self.join_channel(channel_name)
                        joined += 1
                        await asyncio.sleep(2)  # Wait for JOIN confirmation before sending message

                        has_live_tracking = settings.guild_id in live_tracking_guilds
                        if has_live_tracking:
                            msg = (
                                "🤖 Bot is now active! Auto-shoutouts and live role tracking "
                                "are enabled for this channel."
                            )
                        else:
                            msg = (
                                "🤖 Auto-shoutouts are now active! Incoming raids will be "
                                "automatically shouted out in chat."
                            )
                        await self.send_message(channel_name, msg)
                        await asyncio.sleep(0.5)
                
                logger.info(f"📢 Auto-joined {joined} channels for auto-shoutout monitoring")
                
        except Exception as e:
            logger.error(f"Error joining auto-shoutout channels: {e}", exc_info=True)

    async def _listen_to_messages(self):
        """Listen for incoming Twitch IRC messages with auto-recovery."""
        current_task = asyncio.current_task()
        while True:
            # Exit immediately if a newer listener task has been started
            if self.listener_task is not current_task:
                logger.debug("🔇 Listener task superseded by a newer instance — exiting stale task")
                return
            try:
                while self.is_connected and self.websocket:
                    try:
                        message = await asyncio.wait_for(self.websocket.recv(), timeout=60.0)
                        await self._process_irc_message(message)
                    except asyncio.TimeoutError:
                        if self.websocket and self.is_connected:
                            try:
                                await self.websocket.send('PING :tmi.twitch.tv')
                            except Exception:
                                logger.warning("Failed to send PING, connection likely lost")
                                self.is_connected = False
                                break
                        continue
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Twitch IRC connection closed, attempting reconnect...")
                self.is_connected = False
            except asyncio.CancelledError:
                logger.info("IRC listener task cancelled, stopping")
                return
            except Exception as e:
                logger.error(f"Error listening to Twitch IRC: {e}", exc_info=True)
                self.is_connected = False
            
            await self._attempt_reconnect()
            if not self.is_connected:
                logger.warning("⏳ All reconnect attempts failed, waiting 60s before trying again...")
                await asyncio.sleep(60)
    
    async def _attempt_reconnect(self):
        """Attempt to reconnect to Twitch IRC with extended retries."""
        max_retries = 5
        retry_count = 0
        
        while retry_count < max_retries and not self.is_connected:
            try:
                retry_count += 1
                wait_time = min(10 * retry_count, 60)
                logger.info(f"Reconnection attempt {retry_count}/{max_retries} (waiting {wait_time}s)...")
                await asyncio.sleep(wait_time)
                await self.connect_to_twitch_irc()
                if self.is_connected:
                    logger.info("✅ Successfully reconnected to Twitch IRC")
                    asyncio.create_task(self._join_auto_shoutout_channels())
                    return
            except Exception as e:
                logger.error(f"Reconnection attempt {retry_count} failed: {e}", exc_info=True)
    
    async def _process_irc_message(self, message: str):
        """Process incoming IRC message to track user presence."""
        try:
            if message.startswith('PING'):
                await self.websocket.send('PONG :tmi.twitch.tv')
                return
            
            # Track JOIN events - when users enter chat (includes lurkers)
            if ' JOIN ' in message:
                # Format: :username!username@username.tmi.twitch.tv JOIN #channel
                parts = message.split(' ')
                if len(parts) >= 3:
                    username = parts[0].split('!')[0][1:].lower()  # Remove the ':'
                    channel = parts[2].replace('#', '').replace('\r', '').replace('\n', '').lower()
                    if ':' in channel:
                        channel = channel.split(':')[0]
                    
                    if username == self._irc_username.lower():
                        self.joined_channels.add(channel)
                        logger.debug(f"✅ JOIN confirmed for #{channel}")
                    
                    if channel not in self.chat_participants:
                        self.chat_participants[channel] = set()
                    
                    if username not in self.chat_participants[channel]:
                        self.chat_participants[channel].add(username)
                        logger.debug(f"👋 {username} joined #{channel} chat (Total: {len(self.chat_participants[channel])})")
            
            # Track PART events - when users leave chat
            elif ' PART ' in message:
                # Format: :username!username@username.tmi.twitch.tv PART #channel
                parts = message.split(' ')
                if len(parts) >= 3:
                    username = parts[0].split('!')[0][1:].lower()  # Remove the ':'
                    channel = parts[2].replace('#', '').replace('\r', '').replace('\n', '').lower()
                    if ':' in channel:
                        channel = channel.split(':')[0]
                    
                    if channel in self.chat_participants and username in self.chat_participants[channel]:
                        self.chat_participants[channel].remove(username)
                        logger.debug(f"👋 {username} left #{channel} chat (Total: {len(self.chat_participants[channel])})")
                
            # Parse chat messages to track users (backup method)
            elif 'PRIVMSG' in message:
                # Extract IRC tags if present (contains badge info for moderator check)
                tags = {}
                message_without_tags = message
                if message.startswith('@'):
                    # IRC tags format: @badge-info=...;badges=...;color=... :username!...
                    tag_end = message.find(' :')
                    if tag_end > 0:
                        tag_string = message[1:tag_end]  # Remove the '@' prefix
                        message_without_tags = message[tag_end + 1:]
                        
                        # Parse tags into a dictionary
                        for tag in tag_string.split(';'):
                            if '=' in tag:
                                key, value = tag.split('=', 1)
                                tags[key] = value
                
                # Extract username and channel from IRC message
                parts = message_without_tags.split(' ')
                if len(parts) >= 4:
                    # Format: :username!username@username.tmi.twitch.tv PRIVMSG #channel :message text
                    username = parts[0].split('!')[0][1:].lower()  # Remove the ':'
                    channel = parts[2].replace('#', '').lower()  # parts[2] is the channel, remove '#'
                    
                    # Extract message text (everything after the second ':')
                    message_text = ''
                    if ':' in message_without_tags:
                        message_parts = message_without_tags.split(':', 2)
                        if len(message_parts) >= 3:
                            message_text = message_parts[2].strip()
                    
                    # Check if this is a new chatter (first time chatting in this session)
                    is_new_chatter = channel not in self.chat_participants or username not in self.chat_participants.get(channel, set())
                    
                    # Add user to active participants for this channel
                    if channel not in self.chat_participants:
                        self.chat_participants[channel] = set()
                    self.chat_participants[channel].add(username)
                    
                    logger.debug(f"💬 User {username} chatted in #{channel}: {message_text[:50]}...")
                    
                    # Check for auto-shoutout (for first-time chatters only)
                    if is_new_chatter:
                        logger.info(f"🆕 New chatter detected: {username} in #{channel} - triggering auto-shoutout check")
                        asyncio.create_task(self._check_and_send_auto_shoutout(channel, username))
                    
                    # Check for bot commands
                    if message_text:
                        if message_text.lower().startswith('!raidnext'):
                            logger.info(f"🔍 Detected !raidnext-like message from {username} in #{channel}: '{message_text}' (repr: {repr(message_text)})")
                        command = self.chat_bot.parse_chat_command(message_text)
                        if command == 'raidnext':
                            # Check if user is a moderator or broadcaster
                            is_mod = self._check_moderator_status(tags)
                            badges_str = tags.get('badges', 'none')
                            logger.info(f"🎯 !raidnext command from {username} in #{channel} - mod_check={is_mod}, badges={badges_str}")
                            
                            if is_mod:
                                logger.info(f"🎯 !raidnext command ACCEPTED from moderator {username} in #{channel}")
                                # Handle raidnext command in background task
                                asyncio.create_task(self._handle_raidnow_command(channel, username))
                            else:
                                logger.warning(f"❌ !raidnext command denied - {username} is not a moderator in #{channel} (badges: {badges_str})")
                                # Send error message to chat
                                asyncio.create_task(self._send_mod_only_error(channel))

            elif 'USERNOTICE' in message:
                # Parse IRC tags from the message
                tags = {}
                if message.startswith('@'):
                    tag_end = message.find(' :')
                    if tag_end > 0:
                        tag_string = message[1:tag_end]
                        for tag in tag_string.split(';'):
                            if '=' in tag:
                                key, value = tag.split('=', 1)
                                tags[key] = value

                if tags.get('msg-id') == 'raid':
                    usernotice_idx = message.find(' USERNOTICE ')
                    channel = ''
                    if usernotice_idx > 0:
                        rest = message[usernotice_idx + len(' USERNOTICE '):]
                        channel = rest.split(' ')[0].replace('#', '').lower().strip()

                    raider_login = tags.get('msg-param-login', '')
                    raider_display = tags.get('msg-param-displayName', raider_login)
                    try:
                        viewer_count = int(tags.get('msg-param-viewerCount', '0'))
                    except ValueError:
                        viewer_count = 0

                    if channel and raider_login:
                        logger.info(f"🚀 Incoming raid detected: {raider_display} ({viewer_count} viewers) → #{channel}")
                        asyncio.create_task(self._handle_incoming_raid(channel, raider_login, raider_display, viewer_count))

        except Exception as e:
            logger.error(f"Error processing IRC message: {e}")
    
    def _check_moderator_status(self, tags: dict) -> bool:
        """
        Check if user has moderator or broadcaster privileges based on IRC tags.
        
        Args:
            tags: Dictionary of IRC tags from the message
            
        Returns:
            True if user is a moderator or broadcaster, False otherwise
        """
        try:
            badges = tags.get('badges', '')
            
            # Check for broadcaster or moderator badges
            # Badges format: "broadcaster/1,subscriber/12,moderator/1"
            if 'broadcaster/1' in badges or 'moderator/1' in badges:
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking moderator status: {e}")
            return False
    
    async def _send_mod_only_error(self, channel: str):
        """
        Send an error message to Twitch chat when a non-moderator tries to use !raidnext.
        
        Args:
            channel: The Twitch channel name (without #)
        """
        try:
            # Get broadcaster ID for the channel
            async with AsyncDatabaseSession() as session:
                user = session.query(User).filter(
                    func.lower(User.twitch_login) == channel.lower()
                ).first()
                
                if user and user.twitch_id:
                    error_message = "⛔ The !raidnext command is restricted to moderators and the broadcaster only."
                    await self.chat_bot.send_chat_message(user.twitch_id, error_message)
                    logger.info(f"📤 Sent moderator-only error to #{channel}")
                else:
                    logger.warning(f"Could not send mod-only error - channel {channel} not found in database")
                    
        except Exception as e:
            logger.error(f"Error sending mod-only error message: {e}")
    
    async def _post_shoutout_to_discord(self, channel_name: str, username: str, profile: dict,
                                        guild_id: int, is_community_member: bool,
                                        community_discord_id: Optional[int],
                                        community_display_name: Optional[str]):
        """Post a Discord embed when an auto-shoutout fires in Twitch chat."""
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter_by(guild_id=guild_id).first()
                if not settings or not settings.shoutout_discord_channel_id:
                    return
                discord_channel_id = settings.shoutout_discord_channel_id

            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            discord_channel = guild.get_channel(discord_channel_id)
            if not discord_channel:
                return

            display_name = profile.get('display_name') or username
            game = profile.get('game', '')
            title = profile.get('title', '')
            is_live = profile.get('is_live', False)
            viewers = profile.get('viewers', 0)

            if is_community_member:
                color = 0x00d4aa
                embed_title = "🏘️ Community Member Spotted in Chat!"
                desc = (f"**{display_name}** (one of ours!) just chatted in "
                        f"[{channel_name}](https://twitch.tv/{channel_name})'s stream!")
            else:
                color = 0x9146ff
                embed_title = "👏 Auto-Shoutout Fired!"
                desc = (f"**{display_name}** just chatted in "
                        f"[{channel_name}](https://twitch.tv/{channel_name})'s stream!")

            embed = discord.Embed(
                title=embed_title,
                description=desc,
                color=color,
                timestamp=datetime.utcnow()
            )

            if is_community_member and community_discord_id:
                embed.add_field(name="Discord", value=f"<@{community_discord_id}>", inline=True)

            if game:
                embed.add_field(name="🎮 Streams", value=game, inline=True)

            if is_live:
                embed.add_field(name="🔴 Live", value=f"{viewers:,} viewers", inline=True)

            if title:
                embed.add_field(name="📺 Title", value=title, inline=False)

            embed.add_field(
                name="🔗 Twitch",
                value=f"[twitch.tv/{username}](https://twitch.tv/{username})",
                inline=False
            )
            embed.set_footer(text=f"Shouted out in #{channel_name}'s stream")

            await discord_channel.send(embed=embed)
            logger.info(f"📢 Posted Discord shoutout embed for {username} (community={is_community_member})")

        except Exception as e:
            logger.error(f"Error posting shoutout to Discord: {e}", exc_info=True)

    async def _handle_incoming_raid(self, channel: str, raider_login: str,
                                    raider_display: str, viewer_count: int):
        """Handle an incoming Twitch raid — thank them in chat and post a Discord alert."""
        try:
            # Send thank-you in Twitch chat
            thank_you = (
                f"🎉 RAID! Welcome to all {viewer_count} raiders from {raider_display}! "
                f"Show them some love at twitch.tv/{raider_login} 💜"
            )
            await self.send_message(channel, thank_you)

            # Find the channel owner's guild and shoutout channel
            guild_id = None
            discord_channel_id = None
            async with AsyncDatabaseSession() as session:
                broadcaster = session.query(User).filter(
                    func.lower(User.twitch_login) == channel.lower()
                ).first()
                if not broadcaster:
                    return

                settings = session.query(NotificationSettings).filter_by(
                    guild_id=broadcaster.guild_id
                ).first()
                if not settings or not settings.shoutout_discord_channel_id:
                    return

                guild_id = broadcaster.guild_id
                discord_channel_id = settings.shoutout_discord_channel_id

            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            discord_channel = guild.get_channel(discord_channel_id)
            if not discord_channel:
                return

            # Fetch raider's Twitch profile for the embed
            profile = await self._get_twitch_channel_info(raider_login)
            game = profile.get('game', '')
            is_live = profile.get('is_live', False)
            raider_viewers = profile.get('viewers', 0)

            embed = discord.Embed(
                title="🚀 Incoming Raid!",
                description=(
                    f"**{raider_display}** just raided **#{channel}** "
                    f"with **{viewer_count:,} viewers!**"
                ),
                color=0xff6600,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="Raider",
                value=f"[twitch.tv/{raider_login}](https://twitch.tv/{raider_login})",
                inline=True
            )
            embed.add_field(name="👥 Raiders", value=f"{viewer_count:,}", inline=True)
            if game:
                embed.add_field(name="🎮 They Stream", value=game, inline=True)
            if is_live:
                embed.add_field(name="🔴 Their Stream", value=f"{raider_viewers:,} viewers", inline=True)
            embed.set_footer(text=f"Raided into #{channel}")

            await discord_channel.send(embed=embed)
            logger.info(f"✅ Posted raid alert to Discord: {raider_display} ({viewer_count}) → #{channel}")

        except Exception as e:
            logger.error(f"Error handling incoming raid {raider_login} → #{channel}: {e}", exc_info=True)

    async def join_channel(self, channel_name: str):
        """Join a Twitch channel for monitoring."""
        try:
            channel_name = channel_name.lower()
            if not self.is_connected:
                await self.connect_to_twitch_irc()
            
            if self.websocket and self.is_connected:
                self.expected_channels.add(channel_name)
                await self.websocket.send(f'JOIN #{channel_name}')
                logger.info(f"✅ Joined Twitch channel #{channel_name}")
                
                if channel_name not in self.chat_participants:
                    self.chat_participants[channel_name] = set()
                    
        except Exception as e:
            logger.error(f"Failed to join Twitch channel #{channel_name}: {e}")
    
    async def join_channel_for_user(self, twitch_username: str):
        """Called after a successful /twitchoauth to immediately reconnect IRC and join channels.
        Clears any auth cooldown so the fresh token is used right away."""
        try:
            # Clear the auth failure cooldown — the user just supplied a fresh token
            self._irc_auth_failed_until = None
            logger.info(f"🔑 Fresh token received for {twitch_username} — clearing IRC cooldown and reconnecting")

            if not self.is_connected:
                await self.connect_to_twitch_irc()

            if self.is_connected:
                asyncio.create_task(self._join_auto_shoutout_channels())
            else:
                logger.warning("⚠️ IRC still not connected after fresh token — health check will retry")
        except Exception as e:
            logger.error(f"join_channel_for_user error for {twitch_username}: {e}")

    async def leave_channel(self, channel_name: str):
        """Leave a Twitch channel."""
        try:
            channel_name = channel_name.lower()
            if self.websocket and self.is_connected:
                await self.websocket.send(f'PART #{channel_name}')
                logger.info(f"Left Twitch channel #{channel_name}")
                
                self.joined_channels.discard(channel_name)
                self.expected_channels.discard(channel_name)
                
                if channel_name in self.chat_participants:
                    del self.chat_participants[channel_name]
                    
        except Exception as e:
            logger.error(f"Failed to leave Twitch channel #{channel_name}: {e}")
    
    async def send_message(self, channel_name: str, message: str):
        """Send a message to a Twitch channel.
        
        Args:
            channel_name: The Twitch channel to send the message to (without #)
            message: The message to send
        """
        try:
            if not self.is_connected:
                logger.error("Cannot send Twitch message - not connected to IRC")
                return False
            
            if not self.websocket:
                logger.error("Cannot send Twitch message - websocket not available")
                return False
            
            # Send the message using IRC PRIVMSG format
            await self.websocket.send(f'PRIVMSG #{channel_name.lower()} :{message}')
            logger.info(f"📤 Sent Twitch message to #{channel_name}: {message}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Twitch message to #{channel_name}: {e}")
            return False
    
    async def _get_twitch_channel_info(self, username: str) -> dict:
        """
        Fetch Twitch profile info for a user to personalise shoutout messages.
        Returns a dict with: display_name, game, title, is_live, viewers, bio.
        All fields have safe fallback values so callers never have to guard for None.
        """
        result = {
            'display_name': username,
            'game': '',
            'title': '',
            'is_live': False,
            'viewers': 0,
            'bio': '',
        }
        try:
            token = await self.get_access_token()
            if not token:
                return result

            headers = {
                'Client-ID': self.client_id,
                'Authorization': f'Bearer {token}'
            }

            async with aiohttp.ClientSession() as http:
                # 1. User info → display_name, bio, and user_id for the channel call
                async with http.get(
                    f'https://api.twitch.tv/helix/users?login={username}',
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('data'):
                            u = data['data'][0]
                            result['display_name'] = u.get('display_name', username)
                            result['bio'] = u.get('description', '')
                            user_id = u.get('id')

                            # 2. Channel info → game + stream title (last/current)
                            if user_id:
                                async with http.get(
                                    f'https://api.twitch.tv/helix/channels?broadcaster_id={user_id}',
                                    headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=5)
                                ) as ch_resp:
                                    if ch_resp.status == 200:
                                        ch_data = await ch_resp.json()
                                        if ch_data.get('data'):
                                            ch = ch_data['data'][0]
                                            result['game'] = ch.get('game_name', '')
                                            result['title'] = ch.get('title', '')

                # 3. Stream check → is_live, viewers, and live game/title override
                app_token = await self._get_app_token()
                if app_token:
                    live_headers = {
                        'Client-ID': self.client_id,
                        'Authorization': f'Bearer {app_token}'
                    }
                    async with http.get(
                        f'https://api.twitch.tv/helix/streams?user_login={username}',
                        headers=live_headers,
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as s_resp:
                        if s_resp.status == 200:
                            s_data = await s_resp.json()
                            if s_data.get('data'):
                                stream = s_data['data'][0]
                                result['is_live'] = True
                                result['viewers'] = stream.get('viewer_count', 0)
                                # Live data is more accurate than channel metadata
                                if stream.get('game_name'):
                                    result['game'] = stream['game_name']
                                if stream.get('title'):
                                    result['title'] = stream['title']

        except Exception as e:
            logger.warning(f"Could not fetch Twitch profile for {username}: {e}")

        return result

    def _build_shoutout_message(self, template: str, profile: dict, raw_username: str) -> str:
        """
        Replace all supported placeholders in a shoutout message template.

        Supported placeholders:
          {username}  — display name (capitalised as Twitch shows it)
          {game}      — current / last game streamed  (falls back to 'their streams')
          {title}     — current / last stream title   (omitted if empty)
          {live_tag}  — '🔴 LIVE ' when live, '' when offline
          {viewers}   — viewer count when live, '' when offline
          {bio}       — their Twitch bio (omitted if empty)
        """
        display = profile.get('display_name') or raw_username
        game    = profile.get('game') or 'their streams'
        title   = profile.get('title') or ''
        is_live = profile.get('is_live', False)
        viewers = profile.get('viewers', 0)
        bio     = profile.get('bio') or ''

        msg = template
        msg = msg.replace('{username}', display)
        msg = msg.replace('{game}',     game)
        msg = msg.replace('{title}',    title)
        msg = msg.replace('{live_tag}', '🔴 LIVE ' if is_live else '')
        msg = msg.replace('{viewers}',  str(viewers) if is_live else '')
        msg = msg.replace('{bio}',      bio)
        return msg

    async def _check_and_send_auto_shoutout(self, channel_name: str, username: str):
        """
        Check if auto-shoutout is enabled for this channel and send shoutout if needed.

        Args:
            channel_name: The Twitch channel (broadcaster's channel)
            username: The user who just chatted
        """
        try:
            # Don't shoutout the broadcaster themselves or the bot
            if username.lower() == channel_name.lower() or username.lower() == 'rstone203bot':
                return

            # List of common Twitch bots to exclude from shoutouts
            bot_usernames = {
                'nightbot', 'moobot', 'streamelements', 'streamlabs',
                'fossabot', 'wizebot', 'cloudbot', 'pretzelrocks',
                'songlist', 'ankhbot', 'phantombot', 'ohbot',
                'coebot', 'vivbot', 'botisimo', 'streamjar',
                'deepbot', 'supibot', 'pokemoncommunitygame', 'stay_hydrated_bot',
                'commanderroot', 'p0lizei_', 'electricallongboard', 'soundalerts',
                'sery_bot', 'ownaj', 'decafsmurf', 'mikul_bot',
                'virgoproz', 'blesseduibot', 'creatisbot', 'thepositivebot',
                'lurxx', 'twitchprimereminder', 'twitchtoolkit', 'alfredonoodlebot',
                'r3ddybot', 'sport_scores_bot', 'stonesupport', 'overlayexpert'
            }

            if username.lower() in bot_usernames:
                logger.debug(f"⚙️ Skipping auto-shoutout for bot: {username}")
                return

            # Snapshot settings from DB — exit session before any awaits
            from models import AutoShoutoutSettings
            message_template = None
            delay_seconds = 3
            cooldown_minutes = 60
            broadcaster_guild_id = None
            is_community_member = False
            community_discord_id = None
            community_display_name = None

            async with AsyncDatabaseSession() as session:
                broadcaster = session.query(User).filter(
                    func.lower(User.twitch_login) == channel_name.lower()
                ).first()

                if not broadcaster:
                    return

                settings = session.query(AutoShoutoutSettings).filter_by(
                    user_id=broadcaster.id,
                    enabled=True
                ).first()

                if not settings:
                    return

                # Check cooldown
                if channel_name not in self.shouted_out_users:
                    self.shouted_out_users[channel_name] = {}

                last_shoutout = self.shouted_out_users[channel_name].get(username)
                if last_shoutout:
                    time_since_last = (datetime.now() - last_shoutout).total_seconds() / 60
                    if time_since_last < settings.cooldown_per_user_minutes:
                        logger.debug(f"⏳ Skipping shoutout for {username} - on cooldown ({time_since_last:.1f}/{settings.cooldown_per_user_minutes} min)")
                        return

                # Snapshot values before closing session
                message_template = settings.shoutout_message
                delay_seconds    = settings.delay_seconds
                cooldown_minutes = settings.cooldown_per_user_minutes
                broadcaster_guild_id = settings.guild_id

                # Check if this chatter is a known Discord community member with a linked Twitch account
                chatter_user = session.query(User).filter(
                    func.lower(User.twitch_login) == username.lower()
                ).first()
                if chatter_user:
                    is_community_member = True
                    community_discord_id = chatter_user.id
                    community_display_name = chatter_user.display_name or chatter_user.username

            # Fetch Twitch profile data for personalised placeholders
            profile = await self._get_twitch_channel_info(username)

            # Build the final message with all placeholder substitutions
            shoutout_message = self._build_shoutout_message(message_template, profile, username)

            # Delay before sending to prevent spam
            await asyncio.sleep(delay_seconds)

            # Send the shoutout
            success = await self.send_message(channel_name, shoutout_message)

            if success:
                self.shouted_out_users[channel_name][username] = datetime.now()
                game_note = f" (playing {profile['game']})" if profile.get('game') else ''
                live_note = ' [LIVE]' if profile.get('is_live') else ''
                logger.info(f"👏 Auto-shouted out {username} in #{channel_name}{live_note}{game_note}")
                # Post Discord embed if a shoutout channel is configured for this guild
                if broadcaster_guild_id:
                    asyncio.create_task(self._post_shoutout_to_discord(
                        channel_name, username, profile, broadcaster_guild_id,
                        is_community_member, community_discord_id, community_display_name
                    ))

        except Exception as e:
            logger.error(f"Error sending auto-shoutout: {e}", exc_info=True)
    
    async def start_train_monitoring(self, schedule_id: int):
        """Start monitoring all linked streamers for a specific train session."""
        try:
            async with AsyncDatabaseSession() as session:
                from sqlalchemy import or_
                participants = session.query(TrainParticipant, User).outerjoin(
                    User, TrainParticipant.user_id == User.id
                ).filter(
                    TrainParticipant.schedule_id == schedule_id,
                    TrainParticipant.is_active == True,
                    or_(
                        TrainParticipant.twitch_username.isnot(None),
                        User.twitch_login.isnot(None)
                    )
                ).all()
                
                schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
                
                if not schedule:
                    logger.error(f"Schedule {schedule_id} not found")
                    return
                
                # Store monitoring task — check before joining channels to avoid re-joining.
                # Reserve the slot immediately (before any awaits) so two concurrent callers
                # — e.g. the notification scheduler and a rider going live — can't both pass
                # this check and spawn duplicate monitors.
                task_key = f"train_{schedule_id}"
                if task_key in self.active_monitors:
                    logger.debug(f"✅ Chat monitoring already active for {schedule.name} — skipping")
                    return
                self.active_monitors[task_key] = None  # reservation placeholder

                logger.info(f"🚂 Starting chat monitoring for {schedule.name} with {len(participants)} linked streamers")

                for participant, user in participants:
                    channel_name = None
                    if participant.twitch_username:
                        channel_name = participant.twitch_username.lower()
                    elif user and user.twitch_login:
                        channel_name = user.twitch_login.lower()
                    
                    if channel_name:
                        await self.join_channel(channel_name)
                        self.channel_to_schedule[channel_name] = schedule_id
                        logger.info(f"📺 Monitoring channel #{channel_name} for {participant.username}")
                
                self.active_monitors[task_key] = asyncio.create_task(
                    self._monitor_train_session(schedule_id, schedule.duration_minutes)
                )
                    
        except Exception as e:
            logger.error(f"Error starting train monitoring for schedule {schedule_id}: {e}")
            # Clear a dangling reservation placeholder so a later attempt can retry.
            tk = f"train_{schedule_id}"
            if self.active_monitors.get(tk) is None:
                self.active_monitors.pop(tk, None)
    
    async def start_tracking_adhoc(self, twitch_login: str, guild_id: int):
        """Start ad-hoc chat monitoring without a train schedule (for testing)."""
        try:
            logger.info(f"🎮 Starting ad-hoc chat monitoring for {twitch_login}")
            
            # Join the streamer's channel
            channel_name = twitch_login.lower()
            await self.join_channel(channel_name)
            
            # Store ad-hoc monitoring task with guild context
            task_key = f"adhoc_{guild_id}_{twitch_login}"
            if task_key not in self.active_monitors:
                # Monitor for 2 hours by default for ad-hoc sessions
                self.active_monitors[task_key] = asyncio.create_task(
                    self._monitor_adhoc_session(twitch_login, guild_id, duration_minutes=120)
                )
                logger.info(f"✅ Started ad-hoc monitoring for {twitch_login} (2 hour duration)")
                
        except Exception as e:
            logger.error(f"Error starting ad-hoc monitoring for {twitch_login}: {e}")
    
    async def _monitor_adhoc_session(self, twitch_login: str, guild_id: int, duration_minutes: int):
        """Monitor an ad-hoc chat session (no train schedule)."""
        try:
            logger.info(f"🎯 Monitoring ad-hoc session for {twitch_login} for {duration_minutes} minutes")
            
            # Initialize chat participants tracking
            channel_name = twitch_login.lower()
            self.chat_participants[channel_name] = set()
            # Reset tracking quality so a fresh session starts unknown, not carrying
            # over a stale 'full'/'chat_only' label from a previous run.
            self.channel_tracking_quality.pop(channel_name, None)
            
            # Wait for the duration
            await asyncio.sleep(duration_minutes * 60)
            
            logger.info(f"✅ Ad-hoc monitoring session completed for {twitch_login}")
            
        except asyncio.CancelledError:
            logger.info(f"⏹️ Ad-hoc monitoring cancelled for {twitch_login}")
        except Exception as e:
            logger.error(f"Error in ad-hoc monitoring session for {twitch_login}: {e}")
    
    async def _monitor_train_session(self, schedule_id: int, duration_minutes: int):
        """Monitor a train session for the specified duration with periodic API polling."""
        task_key = f"train_{schedule_id}"
        try:
            logger.info(f"🎯 Monitoring train session {schedule_id} for {duration_minutes} minutes")

            # Catch broken Twitch tokens while there's still time to fix them before
            # attendance tracking runs. The 6-hour cooldown inside check_token_health
            # prevents duplicate owner DMs when this overlaps the startup check.
            await self.check_token_health(reason="pre-train")

            async with AsyncDatabaseSession() as session:
                from sqlalchemy import or_
                participants = session.query(TrainParticipant, User).outerjoin(
                    User, TrainParticipant.user_id == User.id
                ).filter(
                    TrainParticipant.schedule_id == schedule_id,
                    TrainParticipant.is_active == True,
                    or_(
                        TrainParticipant.twitch_username.isnot(None),
                        User.twitch_login.isnot(None)
                    )
                ).all()
                
                channels_to_monitor = []
                for participant, user in participants:
                    if participant.twitch_username:
                        channels_to_monitor.append(participant.twitch_username.lower())
                    elif user and user.twitch_login:
                        channels_to_monitor.append(user.twitch_login.lower())
                
                # Get custom attendance interval from server settings
                schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
                if schedule:
                    settings = session.query(NotificationSettings).filter_by(guild_id=schedule.guild_id).first()
                    interval_minutes = settings.attendance_interval_minutes if settings and settings.attendance_interval_minutes else 25
                else:
                    interval_minutes = 25

                # No signups yet — fall back to the host's own Twitch channel so a
                # host-only train is still tracked (mirrors the notification flow which
                # pings the host when nobody has signed up).
                if not channels_to_monitor and schedule and schedule.host_user_id:
                    host_user = session.query(User).filter_by(id=schedule.host_user_id).first()
                    if host_user and host_user.twitch_login:
                        channels_to_monitor.append(host_user.twitch_login.lower())
                        logger.info(f"📣 No signups for schedule {schedule_id} — monitoring host's channel #{host_user.twitch_login.lower()}")
                    else:
                        logger.info(f"⏭️ Schedule {schedule_id} has a host but no linked Twitch account — cannot monitor host's stream")
            
            if not channels_to_monitor:
                logger.info(f"No Twitch channels to monitor for schedule {schedule_id}")
                return
            
            # Reset tracking quality for this session's channels so each train starts
            # fresh — a previous run's 'full'/'chat_only' label must not leak forward.
            for _ch in channels_to_monitor:
                self.channel_tracking_quality.pop(_ch, None)
            
            # Poll chatters API every 2 minutes during the train
            poll_interval = 120  # 2 minutes
            report_interval = interval_minutes * 60  # Convert custom minutes to seconds
            elapsed = 0
            max_duration = duration_minutes * 60
            next_report_time = report_interval  # First report at configured interval
            
            logger.info(f"📊 Attendance reports will be sent every {interval_minutes} minutes")
            
            host_channel = None
            schedule_guild_id = None
            schedule_name = None
            async with AsyncDatabaseSession() as session:
                schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
                if schedule:
                    schedule_guild_id = schedule.guild_id
                    schedule_name = schedule.name
                
                first_participant = session.query(TrainParticipant, User).outerjoin(
                    User, TrainParticipant.user_id == User.id
                ).filter(
                    TrainParticipant.schedule_id == schedule_id,
                    TrainParticipant.is_active == True
                ).order_by(TrainParticipant.id.asc()).first()
                
                if first_participant:
                    participant, user = first_participant
                    if participant.twitch_username:
                        host_channel = participant.twitch_username.lower()
                    elif user and user.twitch_login:
                        host_channel = user.twitch_login.lower()
                
                logger.info(f"🏠 Host channel for live check: {host_channel}")
            
            stream_offline_detected = False
            if host_channel:
                viewer_count = await self.get_stream_viewer_count(host_channel)
                if viewer_count is not None and viewer_count == -1:
                    logger.info(f"🔴 Stream is not live for {host_channel} - not starting attendance tracking")
                    stream_offline_detected = True
                    return
                elif viewer_count is not None and viewer_count >= 0:
                    logger.info(f"✅ Stream IS live for {host_channel} with {viewer_count} viewers - starting attendance tracking")
            
            # If we get here, stream is live - send the "tracking started" message
            if schedule_guild_id:
                await self._send_tracking_started_message_v2(schedule_id, schedule_guild_id, schedule_name, interval_minutes, duration_minutes)
            
            participant_twitch_set = set(ch.lower() for ch in channels_to_monitor)

            while elapsed < max_duration and not stream_offline_detected:
                live_channels = []
                for channel in channels_to_monitor:
                    viewer_count = await self.get_stream_viewer_count(channel)
                    if viewer_count is not None and viewer_count >= 0:
                        live_channels.append(channel)

                if not live_channels and host_channel:
                    hv = await self.get_stream_viewer_count(host_channel)
                    if hv is not None and hv == -1:
                        logger.info(f"🔴 No live channels detected and host {host_channel} is offline - stopping attendance tracking")
                        stream_offline_detected = True
                        break

                channels_to_poll = live_channels if live_channels else channels_to_monitor
                self.current_live_channels = live_channels.copy()
                logger.info(f"📡 Polling {len(channels_to_poll)} channels for chatters (live: {[c for c in live_channels]})")

                self.current_cycle_chatters.clear()

                for channel in channels_to_poll:
                    try:
                        guild_id = None
                        async with AsyncDatabaseSession() as db_session:
                            schedule = db_session.query(TrainSchedule).filter_by(id=schedule_id).first()
                            if schedule:
                                guild_id = schedule.guild_id
                        
                        chatters = await self.get_chatters_from_api(channel, guild_id=guild_id, schedule_id=schedule_id)
                        if chatters:
                            if channel not in self.chat_participants:
                                self.chat_participants[channel] = set()
                            self.chat_participants[channel].update(chatters)
                            self.current_cycle_chatters[channel] = chatters.copy()
                            logger.debug(f"Updated {channel}: {len(self.chat_participants[channel])} total participants tracked")

                            train_chatters_found = chatters.intersection(participant_twitch_set)
                            if train_chatters_found:
                                logger.info(f"🎯 Found {len(train_chatters_found)} train participants in #{channel}: {train_chatters_found}")
                    except Exception as e:
                        logger.error(f"Error polling chatters for {channel}: {e}")
                
                # Wait for next poll interval or remaining time
                wait_time = min(poll_interval, max_duration - elapsed)
                await asyncio.sleep(wait_time)
                elapsed += wait_time
                
                # Check if it's time to send an attendance report
                if elapsed >= next_report_time:
                    report_number = int(elapsed / report_interval)
                    logger.info(f"📊 Generating attendance report #{report_number} for schedule {schedule_id} at {elapsed}s")
                    await self._generate_chat_attendance_report(schedule_id)
                    next_report_time += report_interval  # Schedule next report
            
            if stream_offline_detected:
                logger.info(f"⏹️ Train monitoring stopped early - stream went offline")
            else:
                logger.info(f"✅ Train monitoring complete for schedule {schedule_id}")
            
            # Always generate final attendance report at the end of monitoring
            logger.info(f"📊 Generating final attendance report for schedule {schedule_id}")
            await self._generate_chat_attendance_report(schedule_id, is_final=stream_offline_detected)
            
            # Clean up
            await self.stop_train_monitoring(schedule_id)
            
        except Exception as e:
            logger.error(f"Error during train session monitoring {schedule_id}: {e}")
        finally:
            # Always remove task from active monitors when function exits
            # This ensures the tracking started message can only be sent once
            if task_key in self.active_monitors:
                del self.active_monitors[task_key]
                logger.debug(f"🧹 Removed monitoring task {task_key} from active monitors")
    
    async def _generate_chat_attendance_report(self, schedule_id: int, is_final: bool = False):
        """Generate attendance report based on chat presence in the current live channel only."""
        try:
            async with AsyncDatabaseSession() as session:
                all_chatters = set()
                report_channel = None
                if self.current_live_channels:
                    report_channel = self.current_live_channels[0]
                    if report_channel in self.current_cycle_chatters:
                        all_chatters = self.current_cycle_chatters[report_channel].copy()
                    elif report_channel in self.chat_participants:
                        all_chatters = self.chat_participants[report_channel].copy()
                    logger.info(f"📺 Attendance report focused on current rider: {report_channel} ({len(all_chatters)} chatters)")
                elif self.chat_participants:
                    # Stream went offline - use all accumulated chat data from this session
                    for ch, chatters in self.chat_participants.items():
                        all_chatters.update(chatters)
                        if not report_channel:
                            report_channel = ch
                    logger.info(f"📺 Stream offline - using accumulated chat data ({len(all_chatters)} total chatters from {len(self.chat_participants)} channel(s))")

                # Always merge with DB-persisted attendance so bot restarts don't lose data
                try:
                    from models import TwitchChatAttendance
                    db_records = session.query(TwitchChatAttendance).filter(
                        TwitchChatAttendance.schedule_id == schedule_id,
                        TwitchChatAttendance.was_present_in_chat == True
                    ).all()
                    if db_records:
                        db_chatters = {r.twitch_username.lower() for r in db_records if r.twitch_username}
                        before = len(all_chatters)
                        all_chatters.update(db_chatters)
                        if not report_channel:
                            # Try to find the rider's channel from DB if in-memory is empty
                            for r in db_records:
                                if r.twitch_username:
                                    report_channel = r.twitch_username.lower()
                                    break
                        logger.info(f"📂 Merged {len(db_chatters)} DB-persisted chatters into report (was {before}, now {len(all_chatters)} total)")
                except Exception as db_err:
                    logger.warning(f"⚠️ Could not merge DB attendance records: {db_err}")

                if not all_chatters:
                    logger.info(f"⚠️ No live channels or chat data for attendance report - skipping")
                    return
                
                # Get train participants — use LEFT JOIN so participants without a
                # users row are still included.  We fall back to train_participants.twitch_username
                # so attendance works even when users.twitch_login is not populated.
                from sqlalchemy.orm import outerjoin
                participants = session.query(TrainParticipant, User).outerjoin(
                    User, TrainParticipant.user_id == User.id
                ).filter(
                    TrainParticipant.schedule_id == schedule_id,
                    TrainParticipant.is_active == True
                ).all()
                
                # Create attendance lists
                chat_present = []
                chat_absent = []
                no_twitch_link = []
                
                # First, check train participants (skip those excluded from attendance)
                participant_twitches = set()
                for participant, user in participants:
                    # Resolve Twitch login: prefer users.twitch_login, fall back to
                    # participant.twitch_username then participant.username
                    twitch_login = None
                    if user and user.twitch_login:
                        twitch_login = user.twitch_login
                    elif participant.twitch_username:
                        twitch_login = participant.twitch_username
                    elif participant.username:
                        twitch_login = participant.username

                    if twitch_login:
                        participant_twitches.add(twitch_login.lower())
                        # Skip attendance tracking for users marked to exclude
                        if user and getattr(user, 'exclude_from_attendance', False):
                            continue
                        was_present = twitch_login.lower() in all_chatters
                        if was_present:
                            chat_present.append((participant.display_name or participant.username, twitch_login))
                        else:
                            chat_absent.append((participant.display_name or participant.username, twitch_login))
                    else:
                        no_twitch_link.append((participant.display_name or participant.username, None))
                
                # Then, add ALL other chatters who aren't train participants
                for chatter in all_chatters:
                    if chatter.lower() not in participant_twitches:
                        # This is a viewer who chatted but wasn't signed up for the train
                        chat_present.append((chatter, chatter))
                
                # Log the results
                logger.info(f"📊 Chat Attendance Report for Schedule {schedule_id} (channel: {report_channel or 'all'}):")
                logger.info(f"   ✅ Present in chat: {len(chat_present)} ({len(all_chatters)} total chatters)")
                logger.info(f"   ❌ Absent from chat: {len(chat_absent)} (signed-up participants)")
                logger.info(f"   🔗 No Twitch link: {len(no_twitch_link)}")
                
                # Store results for later retrieval
                self._store_chat_attendance(schedule_id, chat_present, chat_absent, no_twitch_link)
                
                # POST THE REPORT TO DISCORD
                schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
                if schedule:
                    settings = session.query(NotificationSettings).filter_by(guild_id=schedule.guild_id).first()
                    if settings and settings.attendance_channel_id:
                        guild = self.bot.get_guild(schedule.guild_id)
                        if guild:
                            channel = guild.get_channel(settings.attendance_channel_id)
                            if channel:
                                viewer_count = await self.get_stream_viewer_count(report_channel)
                                
                                # Calculate engagement percentage
                                engagement_pct = 0
                                if viewer_count and viewer_count > 0:
                                    engagement_pct = (len(all_chatters) / viewer_count) * 100
                                
                                # Create description with viewer stats
                                description_parts = [f"**Train:** {schedule.name}"]
                                if report_channel:
                                    description_parts.append(f"📺 **Current Stream:** [{report_channel}](https://twitch.tv/{report_channel})")
                                if viewer_count is not None:
                                    description_parts.append(f"👁️ **Total Viewers:** {viewer_count:,}")
                                    description_parts.append(f"💬 **In Chat:** {len(all_chatters)} ({engagement_pct:.1f}% engagement)")
                                else:
                                    description_parts.append(f"💬 **In Chat:** {len(all_chatters)} viewers")
                                
                                # Create embed
                                title = "🏁 Final Attendance Report (Stream Ended)" if is_final else "📊 Twitch Stream Attendance Report"
                                embed = discord.Embed(
                                    title=title,
                                    description="\n".join(description_parts),
                                    color=0x9146ff if not is_final else 0xff4444,
                                    timestamp=datetime.utcnow()
                                )
                                
                                # Add present in chat
                                if chat_present:
                                    present_list = [f"• {name} (`{twitch}`)" for name, twitch in chat_present[:20]]
                                    if len(chat_present) > 20:
                                        present_list.append(f"... and {len(chat_present) - 20} more")
                                    embed.add_field(
                                        name=f"✅ Identified in Chat ({len(chat_present)})",
                                        value="\n".join(present_list) or "None",
                                        inline=False
                                    )
                                
                                # Add absent (if any train participants)
                                if chat_absent:
                                    absent_list = [f"• {name} (`{twitch}`)" for name, twitch in chat_absent[:10]]
                                    embed.add_field(
                                        name=f"❌ Not in Chat ({len(chat_absent)})",
                                        value="\n".join(absent_list),
                                        inline=False
                                    )

                                # Warn when this channel's chatter list couldn't be read
                                # (bot not modded / token blocked) so the numbers above are
                                # understood to come from IRC speakers only, not everyone.
                                if report_channel and self.channel_tracking_quality.get(report_channel.lower()) == 'chat_only':
                                    embed.add_field(
                                        name="ℹ️ Limited Tracking",
                                        value=(
                                            f"Couldn't read the full chatter list for **{report_channel}** "
                                            "(the bot isn't a moderator there), so attendance counts only "
                                            "people who **typed in chat**. Lurkers may be missed. "
                                            f"To fix, have **{report_channel}** mod the bot or run `/twitchoauth`."
                                        ),
                                        inline=False
                                    )

                                embed.set_footer(text=f"Schedule ID: {schedule_id}")
                                
                                await channel.send(embed=embed)
                                logger.info(f"✅ Posted attendance report to #{channel.name}")
                            else:
                                logger.warning(f"⚠️ Attendance channel {settings.attendance_channel_id} not found")
                    else:
                        logger.warning(f"⚠️ No attendance channel configured for guild {schedule.guild_id}")
                
        except Exception as e:
            logger.error(f"Error generating chat attendance report for schedule {schedule_id}: {e}")
    
    def _store_chat_attendance(self, schedule_id: int, present: List, absent: List, no_link: List):
        """Store chat attendance results in memory for reporting."""
        # This could be expanded to store in database if needed
        attendance_key = f"chat_attendance_{schedule_id}_{datetime.now().strftime('%Y%m%d')}"
        
        # Store in bot's memory for now
        if not hasattr(self.bot, 'chat_attendance_cache'):
            self.bot.chat_attendance_cache = {}
            
        self.bot.chat_attendance_cache[attendance_key] = {
            'present': present,  # Already formatted as (display_name, twitch_login)
            'absent': absent,    # Already formatted as (display_name, twitch_login)
            'no_link': no_link,  # Already formatted as (display_name, None)
            'timestamp': datetime.utcnow()
        }
        
        logger.info(f"💾 Chat attendance data stored for schedule {schedule_id}")
    
    async def _send_tracking_started_message(self, schedule, interval_minutes: int, duration_minutes: int):
        """Send a message to the attendance channel announcing that tracking has started."""
        try:
            async with AsyncDatabaseSession() as session:
                # Get notification settings to find attendance channel
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=schedule.guild_id
                ).first()
                
                if not settings or not settings.attendance_channel_id:
                    logger.debug(f"No attendance channel configured for guild {schedule.guild_id}")
                    return
                
                # Get the guild and channel
                guild = self.bot.get_guild(schedule.guild_id)
                if not guild:
                    logger.warning(f"Guild {schedule.guild_id} not found")
                    return
                
                channel = guild.get_channel(settings.attendance_channel_id)
                if not channel:
                    logger.warning(f"Attendance channel {settings.attendance_channel_id} not found in {guild.name}")
                    return
                
                # Get participants for display
                participants = session.query(TrainParticipant).filter(
                    TrainParticipant.schedule_id == schedule.id,
                    TrainParticipant.is_active == True
                ).all()
                
                # Create embed
                import discord
                embed = discord.Embed(
                    title="🎯 Attendance Tracking Started",
                    description=f"Now tracking chat participation for **{schedule.name}**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="⏱️ Duration",
                    value=f"{duration_minutes} minutes",
                    inline=True
                )
                
                embed.add_field(
                    name="👥 Participants",
                    value=f"{len(participants)} participant(s) signed up",
                    inline=True
                )
                
                embed.add_field(
                    name="📈 Report Interval",
                    value=f"Every {interval_minutes} minutes",
                    inline=True
                )
                
                embed.add_field(
                    name="🎯 What's Being Tracked",
                    value="The bot is monitoring Twitch chat to track attendance. Everyone who appears in chat will be counted as present, whether they're signed up for the train or not.",
                    inline=False
                )
                
                embed.set_footer(text=f"Schedule ID: {schedule.id} • Next report in ~{interval_minutes} minutes")
                
                await channel.send(embed=embed)
                logger.info(f"✅ Sent tracking started message for {schedule.name} to attendance")
                
        except Exception as e:
            logger.error(f"Error sending tracking started message for schedule {schedule.id}: {e}")
    
    async def _send_tracking_started_message_v2(self, schedule_id: int, guild_id: int, schedule_name: str, interval_minutes: int, duration_minutes: int):
        """Send a message to the attendance channel announcing that tracking has started (session-safe version)."""
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=guild_id
                ).first()
                
                if not settings or not settings.attendance_channel_id:
                    logger.debug(f"No attendance channel configured for guild {guild_id}")
                    return
                
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    logger.warning(f"Guild {guild_id} not found")
                    return
                
                channel = guild.get_channel(settings.attendance_channel_id)
                if not channel:
                    logger.warning(f"Attendance channel {settings.attendance_channel_id} not found in {guild.name}")
                    return
                
                participants = session.query(TrainParticipant).filter(
                    TrainParticipant.schedule_id == schedule_id,
                    TrainParticipant.is_active == True
                ).all()
                
                import discord
                embed = discord.Embed(
                    title="🎯 Attendance Tracking Started",
                    description=f"Now tracking chat participation for **{schedule_name}**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(name="⏱️ Duration", value=f"{duration_minutes} minutes", inline=True)
                embed.add_field(name="👥 Participants", value=f"{len(participants)} participant(s) signed up", inline=True)
                embed.add_field(name="📈 Report Interval", value=f"Every {interval_minutes} minutes", inline=True)
                embed.add_field(
                    name="🎯 What's Being Tracked",
                    value="The bot is monitoring Twitch chat to track attendance. Everyone who appears in chat will be counted as present, whether they're signed up for the train or not.",
                    inline=False
                )
                embed.set_footer(text=f"Schedule ID: {schedule_id} • Next report in ~{interval_minutes} minutes")
                
                await channel.send(embed=embed)
                logger.info(f"✅ Sent tracking started message for {schedule_name} to attendance")
                
        except Exception as e:
            logger.error(f"Error sending tracking started message for schedule {schedule_id}: {e}")

    async def stop_train_monitoring(self, schedule_id: int):
        """Stop monitoring for a specific train session."""
        try:
            task_key = f"train_{schedule_id}"
            
            if task_key in self.active_monitors:
                existing_task = self.active_monitors[task_key]
                if existing_task is not None:
                    existing_task.cancel()
                del self.active_monitors[task_key]
                
            # Leave all channels for this train
            async with AsyncDatabaseSession() as session:
                participants = session.query(TrainParticipant, User).join(
                    User, TrainParticipant.user_id == User.id
                ).filter(
                    TrainParticipant.schedule_id == schedule_id,
                    TrainParticipant.is_active == True,
                    User.twitch_login.isnot(None)
                ).all()
                
                for participant, user in participants:
                    if user.twitch_login:
                        channel_name = user.twitch_login.lower()
                        await self.leave_channel(channel_name)
                        # Remove channel-to-schedule mapping
                        if channel_name in self.channel_to_schedule:
                            del self.channel_to_schedule[channel_name]
                        
            logger.info(f"🛑 Stopped monitoring for train session {schedule_id}")
            
        except Exception as e:
            logger.error(f"Error stopping train monitoring for schedule {schedule_id}: {e}")
    
    async def get_current_chat_participants(self, channel_name: str) -> Set[str]:
        """Get current participants in a Twitch channel."""
        return self.chat_participants.get(channel_name.lower(), set())
    
    async def _handle_raidnow_command(self, channel: str, username: str):
        """
        Handle !raidnext command from Twitch chat.
        
        Args:
            channel: Twitch channel name (lowercase, without #)
            username: Username who issued the command
        """
        try:
            logger.info(f"🚀 Processing !raidnext from {username} in #{channel}")
            
            # Get guild_id and broadcaster_id for this channel
            guild_id = None
            broadcaster_id = None
            
            async with AsyncDatabaseSession() as session:
                participant = session.query(TrainParticipant).filter(
                    TrainParticipant.twitch_username.ilike(channel),
                    TrainParticipant.is_active == True
                ).first()
                
                if participant:
                    schedule = session.query(TrainSchedule).filter_by(
                        id=participant.schedule_id, is_active=True
                    ).first()
                    if schedule:
                        guild_id = schedule.guild_id
                        logger.info(f"Found schedule {schedule.id} via participant twitch_username for #{channel}")
                    else:
                        logger.info(f"Participant found for #{channel} but no active schedule with id={participant.schedule_id}")
                else:
                    logger.info(f"No active participant with twitch_username={channel}")
                
                if not guild_id:
                    user = session.query(User).filter(
                        User.twitch_login.ilike(channel)
                    ).first()
                    
                    if user:
                        logger.info(f"Found user {user.id} with twitch_login={user.twitch_login}")
                        schedule = session.query(TrainSchedule).filter(
                            TrainSchedule.host_user_id == user.id,
                            TrainSchedule.is_active == True
                        ).first()
                        if schedule:
                            guild_id = schedule.guild_id
                            logger.info(f"Found schedule {schedule.id} via user twitch_login for #{channel}")
                        else:
                            all_schedules = session.query(TrainSchedule).filter(
                                TrainSchedule.is_active == True
                            ).all()
                            logger.info(f"No schedule with host_user_id={user.id}. Active schedules: {[(s.id, s.host_user_id, s.guild_id) for s in all_schedules[:5]]}")
                            
                            for s in all_schedules:
                                p = session.query(TrainParticipant).filter(
                                    TrainParticipant.schedule_id == s.id,
                                    TrainParticipant.user_id == user.id,
                                    TrainParticipant.is_active == True
                                ).first()
                                if p:
                                    guild_id = s.guild_id
                                    logger.info(f"Found schedule {s.id} via participant user_id={user.id} for #{channel}")
                                    break
                    else:
                        logger.info(f"No user found with twitch_login={channel}")
                
                if not guild_id:
                    logger.warning(f"⚠️ Could not find active schedule for channel #{channel}")
                    response = "⚠️ Unable to process raid command - no active train schedule found for this channel"
                    await self.chat_bot.send_chat_message(
                        self.broadcaster_ids.get(channel, channel),
                        response
                    )
                    return
            
            # Get broadcaster_id (Twitch user ID)
            broadcaster_id = self.broadcaster_ids.get(channel)
            logger.info(f"Broadcaster ID from cache for #{channel}: {broadcaster_id}")
            if not broadcaster_id:
                # Try to get it from the chat bot
                broadcaster_id = await self.chat_bot._get_twitch_user_id(channel)
                if broadcaster_id:
                    self.broadcaster_ids[channel] = broadcaster_id
                    logger.info(f"Fetched broadcaster_id={broadcaster_id} for #{channel} from Twitch API")
            
            if not guild_id or not broadcaster_id:
                logger.warning(f"⚠️ Missing guild_id={guild_id} or broadcaster_id={broadcaster_id} for !raidnext command")
                return
            
            # Handle the command
            response = await self.chat_bot.handle_raidnow_command(
                guild_id,
                channel,
                broadcaster_id
            )
            
            # Send response to chat
            if response:
                await self.chat_bot.send_chat_message(broadcaster_id, response)
                logger.info(f"📤 Sent raidnext response to #{channel}: {response}")
            
        except Exception as e:
            logger.error(f"Error handling !raidnext command from #{channel}: {e}", exc_info=True)
    
    async def _get_trusted_role_users(self, guild_id: int) -> List[int]:
        """Get all users with trusted roles in a guild."""
        try:
            from models import TrustedRole, Guild
            async with AsyncDatabaseSession() as session:
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
    
    async def _notify_attendance_error(self, guild_id: int, broadcaster_login: str, error_reason: str, schedule_id: int = None):
        """Send a detailed, actionable DM to the bot owner about an attendance tracking failure."""
        try:
            # Throttle: once per unique broadcaster per hour (key survives restart via guild+channel)
            if not hasattr(self, '_error_notifications'):
                self._error_notifications = {}
            notification_key = f"attendance_error_{guild_id}_{broadcaster_login}"
            last_notified = self._error_notifications.get(notification_key)
            if last_notified and (datetime.now() - last_notified).total_seconds() < 3600:
                logger.debug(f"Skipping attendance error notification for {broadcaster_login} (throttled)")
                return

            # Get bot owner
            app_info = await self.bot.application_info()
            owner = app_info.owner
            if not owner:
                return

            # Look up schedule and guild name for context
            schedule_name = None
            guild_name = None
            try:
                async with AsyncDatabaseSession() as db_sess:
                    if schedule_id:
                        sched = db_sess.query(TrainSchedule).filter_by(id=schedule_id).first()
                        if sched:
                            schedule_name = sched.name
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        guild_name = guild.name
            except Exception:
                pass

            # --- Build context-specific title, explanation and fix steps ---
            reason_lower = error_reason.lower()

            if "twitch api error: 429" in reason_lower:
                title = "⏳ Twitch Rate Limit Hit"
                what_happened = (
                    "Twitch temporarily blocked the chatters API request because the bot sent too many "
                    "requests in a short window."
                )
                action_needed = False
                fix_steps = (
                    "• **No action needed** — this is self-resolving.\n"
                    "• The next poll cycle (every 2 min) will succeed automatically.\n"
                    "• If it keeps happening every poll, the poll interval may need to be increased."
                )
                color = 0xFFD700

            elif "twitch api error: 5" in reason_lower or "twitch api error: 50" in reason_lower:
                # Extract status code
                import re as _re
                m = _re.search(r'twitch api error: (\d+)', reason_lower)
                status_code = m.group(1) if m else "5xx"
                title = f"🔴 Twitch Server Error ({status_code})"
                what_happened = (
                    f"Twitch's own API returned a `{status_code}` server error when the bot tried to "
                    f"fetch chatters for **{broadcaster_login}**. This is on Twitch's side, not the bot."
                )
                action_needed = False
                fix_steps = (
                    "• **No action needed** — Twitch server errors are temporary.\n"
                    "• Check https://status.twitch.tv if it persists for more than 15 minutes.\n"
                    "• Attendance data for affected poll cycles will be skipped but the session continues."
                )
                color = 0xFF6B6B

            elif "twitch api error:" in reason_lower:
                import re as _re
                m = _re.search(r'twitch api error: (\d+)', reason_lower)
                status_code = m.group(1) if m else "unknown"
                title = f"⚠️ Unexpected Twitch API Response ({status_code})"
                what_happened = (
                    f"The chatters API returned an unexpected HTTP `{status_code}` status for "
                    f"**{broadcaster_login}**. This may indicate an API change or a temporary issue."
                )
                action_needed = True
                fix_steps = (
                    f"• Check https://dev.twitch.tv/docs/api/reference/#get-chatters for API changes.\n"
                    f"• If status is `401` (unauthorised): run `/twitchoauth` to re-authorise the bot.\n"
                    f"• If status is `400` (bad request): Twitch username `{broadcaster_login}` may be wrong — check with `!checktwitch twitch:{broadcaster_login}`.\n"
                    f"• If it's a one-off: probably safe to ignore."
                )
                color = 0xFFA500

            elif "network error" in reason_lower or "timeout" in reason_lower:
                title = "🌐 Network / Timeout Error"
                what_happened = (
                    f"The request to Twitch's API timed out or failed with a network error while "
                    f"fetching chatters for **{broadcaster_login}**.\n\n"
                    f"**Raw error:** `{error_reason}`"
                )
                action_needed = False
                fix_steps = (
                    "• **Usually self-resolving** — retry happens automatically on the next poll.\n"
                    "• If it's happening repeatedly, check if Replit's internet is having issues.\n"
                    "• Check https://status.twitch.tv to rule out a Twitch outage."
                )
                color = 0x4A90D9

            else:
                title = "⚠️ Attendance Tracking Error"
                what_happened = f"An unexpected error occurred while tracking attendance for **{broadcaster_login}**."
                action_needed = True
                fix_steps = (
                    f"• **Raw error:** `{error_reason}`\n"
                    "• Run `/twitchoauth` if it looks auth-related.\n"
                    "• Check bot logs for more detail."
                )
                color = 0xFFA500

            # --- Build embed ---
            embed = discord.Embed(
                title=title,
                color=color,
                timestamp=datetime.utcnow()
            )

            # Context block
            context_lines = [f"**Channel:** [twitch.tv/{broadcaster_login}](https://twitch.tv/{broadcaster_login})"]
            if schedule_name:
                context_lines.append(f"**Schedule:** {schedule_name}")
            if guild_name:
                context_lines.append(f"**Server:** {guild_name}")
            embed.add_field(name="📍 Where", value="\n".join(context_lines), inline=False)

            embed.add_field(name="❓ What happened", value=what_happened, inline=False)
            embed.add_field(
                name="📊 Impact",
                value=(
                    "• This poll cycle's chatter data was skipped\n"
                    "• The attendance session is **still running** — only this one check failed\n"
                    "• Overall attendance report may be slightly incomplete"
                ),
                inline=False
            )
            embed.add_field(
                name=f"{'🔧 Action required' if action_needed else '✅ No action needed'}",
                value=fix_steps,
                inline=False
            )
            embed.set_footer(text="This DM is only sent once per hour per channel to avoid spam.")

            try:
                await owner.send(embed=embed)
                logger.info(f"✅ Sent detailed attendance error DM to bot owner ({error_reason}) for {broadcaster_login}")
                self._error_notifications[notification_key] = datetime.now()
            except Exception as e:
                logger.debug(f"Could not DM bot owner about attendance error: {e}")

        except Exception as e:
            logger.error(f"Error sending attendance error notification: {e}")
    
    async def _notify_token_expired(self):
        """Send a detailed, actionable DM to the bot owner about an expired Twitch OAuth token."""
        try:
            # Only send once per bot session to avoid repeated DMs
            if hasattr(self, '_expiry_notified'):
                return
            self._expiry_notified = True

            app_info = await self.bot.application_info()
            owner = app_info.owner
            if not owner:
                return

            embed = discord.Embed(
                title="🔑 Twitch OAuth Token Expired — Action Required",
                description=(
                    "The bot's Twitch OAuth token has expired. Until it's refreshed, the bot **cannot "
                    "fetch the list of viewers currently in Twitch chat**, so attendance reports will "
                    "be incomplete or empty."
                ),
                color=0xff0000,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="❓ Why did this happen?",
                value=(
                    "Twitch OAuth tokens expire roughly every 4 hours. The bot tries to refresh them "
                    "automatically in the background, but if the refresh token itself is invalid (e.g. "
                    "you revoked access on Twitch's side, or it's been too long since the last "
                    "authorisation), the auto-refresh fails and you need to re-authorise manually."
                ),
                inline=False
            )
            embed.add_field(
                name="📊 What's affected while this is broken?",
                value=(
                    "❌ Bot cannot see who is in Twitch chat (chatters API requires auth)\n"
                    "❌ Attendance reports will show no chat presence data\n"
                    "✅ IRC chat monitoring (auto-shoutouts, message detection) still works\n"
                    "✅ Live role detection still works"
                ),
                inline=False
            )
            embed.add_field(
                name="🔧 How to fix it (takes ~30 seconds)",
                value=(
                    "1. Go to your Discord server\n"
                    "2. Run `/twitchoauth` in any channel\n"
                    "3. Click the link the bot sends you\n"
                    "4. Log in to Twitch and click **Authorize**\n"
                    "5. The bot will confirm the token has been updated\n\n"
                    "You may need to do this once every few weeks if Twitch invalidates the refresh token."
                ),
                inline=False
            )
            embed.add_field(
                name="💡 Tip",
                value=(
                    "To check the current token status for any user at any time, run:\n"
                    "`!checktwitch @YourDiscordName` or `/oauthstatus`"
                ),
                inline=False
            )
            embed.set_footer(text="This DM is sent once per bot session. Restart the bot to reset.")

            try:
                await owner.send(embed=embed)
                logger.info(f"✅ Sent detailed token expiry DM to bot owner {owner.name}")
            except Exception as e:
                logger.error(f"Failed to DM bot owner about token expiry: {e}")

        except Exception as e:
            logger.error(f"Error sending token expiry notification: {e}")

    async def check_token_health(self, reason: str = "startup"):
        """Inspect stored Twitch tokens and DM the owner about ones that need re-linking.

        Flags two failure modes that break attendance/auto-raids silently:
          • the token is missing the ``chat:read`` scope (IRC + chatters won't work)
          • the token is expired AND has no refresh token (can't be auto-recovered)

        Tokens that are merely close to expiry are ignored — the hourly
        ``token_refresh_task`` handles those automatically. A 6-hour cooldown
        prevents repeat DMs when this runs at startup and again before trains.
        """
        try:
            from models import TwitchOAuthToken
            cooldown_key = "_token_health_last_notified"
            last = getattr(self, cooldown_key, None)
            if last and (datetime.utcnow() - last).total_seconds() < 6 * 3600:
                logger.debug("Token health check: skipping owner DM (within 6h cooldown)")
                return

            problems = []  # list of (twitch_username, issue)
            try:
                async with AsyncDatabaseSession() as session:
                    tokens = session.query(TwitchOAuthToken).filter_by(is_active=True).all()
                    for t in tokens:
                        name = t.twitch_username or f"id={t.twitch_user_id}"
                        scopes = t.scopes or []
                        if 'chat:read' not in scopes:
                            problems.append((name, "missing the `chat:read` permission"))
                            continue
                        if t.expires_at and t.expires_at < datetime.now() and not t.refresh_token:
                            problems.append((name, "expired and can't auto-refresh (no refresh token)"))
            except Exception as e:
                # The inspection itself couldn't run (e.g. DB unavailable). Without a
                # loud signal the owner has no idea a train may run with broken
                # attendance tracking — exactly the failure this check guards against.
                logger.error(
                    f"🚨 Token health check ({reason}) could NOT run its inspection: {e}",
                    exc_info=True,
                )
                await self._notify_owner_health_check_failed(reason, e)
                return

            if not problems:
                logger.info(f"✅ Token health check ({reason}): all active Twitch tokens look healthy")
                return

            logger.warning(f"⚠️ Token health check ({reason}): {len(problems)} token(s) need re-linking")

            app_info = await self.bot.application_info()
            owner = app_info.owner
            if not owner:
                logger.error(
                    f"🚨 Token health check ({reason}): {len(problems)} token issue(s) found "
                    "but the bot owner could not be resolved — NO ONE will be warned"
                )
                return

            lines = "\n".join(f"• **{name}** — {issue}" for name, issue in problems)
            embed = discord.Embed(
                title="⚠️ Twitch Token Needs Re-Linking",
                description=(
                    "One or more linked Twitch tokens won't work for attendance tracking "
                    "or auto-raids until they're re-authorised:\n\n"
                    f"{lines}"
                ),
                color=0xFFA500,
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="🔧 How to fix it (takes ~30 seconds)",
                value=(
                    "Each affected person should run `/twitchoauth` in the server, open the "
                    "link, and click **Authorize** on Twitch. The bot confirms automatically."
                ),
                inline=False
            )
            embed.set_footer(text=f"Checked at {reason} • run /oauthstatus anytime to review")

            try:
                await owner.send(embed=embed)
                setattr(self, cooldown_key, datetime.utcnow())
                logger.info(f"✅ Sent token health warning to bot owner {owner.name} ({len(problems)} issue(s))")
            except Exception as e:
                logger.error(f"Failed to DM bot owner about token health: {e}")

        except Exception as e:
            # Unexpected failure of the check itself — escalate and try to warn the
            # owner so a train doesn't silently run with broken attendance tracking.
            logger.error(f"🚨 Error during token health check ({reason}): {e}", exc_info=True)
            await self._notify_owner_health_check_failed(reason, e)

    async def _notify_owner_health_check_failed(self, reason: str, error: Exception):
        """Last-ditch alert when the token health check itself couldn't run.

        A failed inspection (e.g. DB unreachable, owner lookup error) means the owner
        gets no health signal and an upcoming train may run with broken attendance
        tracking. We escalate with a loud log and, when possible, a direct owner DM.
        A separate 6-hour cooldown stops a sustained outage from spamming the owner.
        """
        fail_cooldown_key = "_token_health_fail_last_notified"
        last_fail = getattr(self, fail_cooldown_key, None)
        if last_fail and (datetime.utcnow() - last_fail).total_seconds() < 6 * 3600:
            logger.warning(
                f"Token health check ({reason}) failed again within 6h cooldown — owner DM suppressed"
            )
            return

        try:
            app_info = await self.bot.application_info()
            owner = app_info.owner
        except Exception as e:
            logger.error(
                f"🚨 Token health check ({reason}) failed AND the bot owner could not be resolved "
                f"to send a fallback warning: {e}"
            )
            return

        if not owner:
            logger.error(
                f"🚨 Token health check ({reason}) failed AND the bot owner is unknown — "
                "no one will be warned that the check could not run"
            )
            return

        embed = discord.Embed(
            title="🚨 Twitch Token Check Could Not Run",
            description=(
                f"The Twitch token health check (`{reason}`) failed before it could inspect "
                "any tokens. This usually means the database was unreachable.\n\n"
                "**Attendance tracking and auto-raids may silently break for upcoming trains "
                "until this is resolved.**"
            ),
            color=0xFF0000,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Error", value=f"```{str(error)[:1000]}```", inline=False)
        embed.add_field(
            name="🔧 What to check",
            value=(
                "Confirm the bot's database is reachable, then run `/oauthstatus` to verify "
                "Twitch tokens manually before the next train."
            ),
            inline=False,
        )
        embed.set_footer(text=f"Triggered during {reason} token health check")

        try:
            await owner.send(embed=embed)
            setattr(self, fail_cooldown_key, datetime.utcnow())
            logger.warning(
                f"📨 Warned bot owner {owner.name} that the token health check ({reason}) could not run"
            )
        except Exception as e:
            logger.error(f"🚨 Could not DM bot owner about the failed token health check ({reason}): {e}")

    @tasks.loop(hours=1)
    async def token_refresh_task(self):
        """Proactively check and refresh ALL active Twitch OAuth tokens before they expire."""
        try:
            from models import TwitchOAuthToken
            async with AsyncDatabaseSession() as session:
                all_active_tokens = session.query(TwitchOAuthToken).filter_by(is_active=True).all()
                
                if not all_active_tokens:
                    return
                
                token_ids_to_refresh = []
                for token in all_active_tokens:
                    if token.expires_at:
                        time_until_expiry = token.expires_at - datetime.now()
                        hours_until_expiry = time_until_expiry.total_seconds() / 3600
                        
                        if hours_until_expiry < 2:
                            token_ids_to_refresh.append((token.twitch_user_id, token.twitch_username))
                            logger.info(f"⏰ Token for {token.twitch_username} expires in {hours_until_expiry:.1f}h, needs refresh")
                        else:
                            logger.debug(f"✓ Token for {token.twitch_username} valid for {hours_until_expiry:.1f}h")
                    else:
                        token_ids_to_refresh.append((token.twitch_user_id, token.twitch_username))
                        logger.info(f"⏰ Token for {token.twitch_username} has no expiry set, refreshing")

            for user_id, username in token_ids_to_refresh:
                refreshed = await self.refresh_access_token(twitch_user_id=user_id)
                if refreshed:
                    logger.info(f"✅ Proactive refresh successful for {username}!")
                else:
                    logger.error(f"❌ Proactive refresh failed for {username}")
                await asyncio.sleep(1)

            if not token_ids_to_refresh:
                logger.debug(f"✓ All {len(all_active_tokens)} tokens still valid")
                        
        except Exception as e:
            logger.error(f"Error in token refresh task: {e}")
    
    @token_refresh_task.before_loop
    async def before_token_refresh(self):
        """Wait for bot to be ready before starting token refresh task."""
        await self.bot.wait_until_ready()
    
    @tasks.loop(minutes=5)
    async def irc_health_check_task(self):
        """Periodically check if the IRC listener is alive and restart if needed."""
        try:
            if not self.is_connected:
                logger.warning("🏥 Health check: IRC not connected, attempting reconnect...")
                self.joined_channels.clear()
                await self.connect_to_twitch_irc()
                if self.is_connected:
                    logger.info("🏥 Health check: Reconnected successfully")
                    asyncio.create_task(self._join_auto_shoutout_channels())
            
            if self.is_connected and (self.listener_task is None or self.listener_task.done()):
                logger.warning("🏥 Health check: Listener task died, restarting...")
                self.listener_task = asyncio.create_task(self._listen_to_messages())
                asyncio.create_task(self._join_auto_shoutout_channels())
                logger.info("🏥 Health check: Listener task restarted")
            
            if self.is_connected:
                try:
                    await self.websocket.send('PING :tmi.twitch.tv')
                    logger.debug("🏥 Health check: PING sent successfully")
                except Exception:
                    logger.warning("🏥 Health check: PING failed, forcing reconnect...")
                    self.is_connected = False
                    self.joined_channels.clear()
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    self.websocket = None
                    await self.connect_to_twitch_irc()
                    if self.is_connected:
                        self.listener_task = asyncio.create_task(self._listen_to_messages())
                        asyncio.create_task(self._join_auto_shoutout_channels())
                        logger.info("🏥 Health check: Force reconnected and rejoined channels")
            
            if self.is_connected and self.expected_channels:
                missing = self.expected_channels - self.joined_channels
                if missing:
                    rejoin_batch = list(missing)[:10]
                    logger.warning(f"🏥 Health check: {len(missing)} channels missing JOIN confirmation, rejoining {len(rejoin_batch)}")
                    for ch in rejoin_batch:
                        try:
                            await self.websocket.send(f'JOIN #{ch}')
                            await asyncio.sleep(1)
                        except Exception as e:
                            logger.error(f"🏥 Failed to rejoin #{ch}: {e}")
                            break
        except Exception as e:
            logger.error(f"🏥 Health check error: {e}", exc_info=True)
    
    @irc_health_check_task.before_loop
    async def before_irc_health_check(self):
        """Wait for bot to be ready before starting health check."""
        await self.bot.wait_until_ready()
    
    @tasks.loop(minutes=5)
    async def attendance_flush_task(self):
        """Periodically flush chat attendance data to database (every 5 minutes)."""
        try:
            if not self.chat_participants:
                return
            
            from models import TwitchChatAttendance, get_est_time
            from sqlalchemy import or_
            
            async with AsyncDatabaseSession() as session:
                current_time = get_est_time()
                current_date = current_time.date()
                
                if not self.channel_to_schedule:
                    logger.debug("No active train schedule mappings, skipping attendance flush")
                    return
                
                schedule_to_channels = {}
                for channel, sched_id in self.channel_to_schedule.items():
                    if sched_id not in schedule_to_channels:
                        schedule_to_channels[sched_id] = set()
                    schedule_to_channels[sched_id].add(channel)
                
                for schedule_id, mapped_channels in schedule_to_channels.items():
                    schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
                    if not schedule:
                        logger.warning(f"Schedule {schedule_id} not found, skipping flush")
                        continue
                    
                    guild_id = schedule.guild_id
                    
                    schedule_chatters = set()
                    for channel in mapped_channels:
                        if channel in self.chat_participants:
                            schedule_chatters.update(self.chat_participants[channel])
                    
                    if not schedule_chatters:
                        continue
                    
                    train_participants = session.query(TrainParticipant, User).outerjoin(
                        User, TrainParticipant.user_id == User.id
                    ).filter(
                        TrainParticipant.schedule_id == schedule_id,
                        TrainParticipant.is_active == True
                    ).all()
                    
                    participant_user_ids = set()
                    twitch_to_user = {}
                    for participant, user in train_participants:
                        participant_user_ids.add(participant.user_id)
                        twitch_name = None
                        if participant.twitch_username:
                            twitch_name = participant.twitch_username.lower()
                        elif user and user.twitch_login:
                            twitch_name = user.twitch_login.lower()
                        if twitch_name:
                            twitch_to_user[twitch_name] = participant.user_id
                    
                    for twitch_username in schedule_chatters:
                        user_id = twitch_to_user.get(twitch_username.lower())
                        
                        if not user_id:
                            continue
                        
                        attendance = session.query(TwitchChatAttendance).filter_by(
                            schedule_id=schedule_id,
                            user_id=user_id,
                            train_date=current_date
                        ).first()
                        
                        if attendance:
                            attendance.was_present_in_chat = True
                            attendance.last_chat_message_at = current_time
                        else:
                            new_attendance = TwitchChatAttendance(
                                schedule_id=schedule_id,
                                user_id=user_id,
                                twitch_username=twitch_username,
                                guild_id=guild_id,
                                train_date=current_date,
                                was_present_in_chat=True,
                                first_chat_message_at=current_time,
                                last_chat_message_at=current_time
                            )
                            session.add(new_attendance)
                
                session.commit()
                logger.debug(f"✅ Flushed attendance data for {len(schedule_to_channels)} schedules")
                
        except Exception as e:
            logger.error(f"Error flushing attendance data: {e}", exc_info=True)
    
    @attendance_flush_task.before_loop
    async def before_attendance_flush(self):
        """Wait for bot to be ready before starting attendance flush task."""
        await self.bot.wait_until_ready()
    
    async def cleanup(self):
        """Clean up all monitoring connections."""
        try:
            # Do final flush of attendance data before shutting down
            if self.chat_participants:
                logger.info("📊 Performing final attendance flush before shutdown...")
                try:
                    await self.attendance_flush_task()
                except Exception as flush_error:
                    logger.error(f"Error during final flush: {flush_error}")
            
            # Stop attendance flush task
            if self.attendance_flush_task.is_running():
                self.attendance_flush_task.cancel()
            
            # Stop IRC health check task
            if self.irc_health_check_task.is_running():
                self.irc_health_check_task.cancel()
            
            # Stop token refresh task
            if self.token_refresh_task.is_running():
                self.token_refresh_task.cancel()
            
            # Cancel all monitoring tasks
            for task in self.active_monitors.values():
                if task is not None:
                    task.cancel()
            self.active_monitors.clear()
            
            # Close WebSocket connection
            if self.websocket:
                await self.websocket.close()
                
            self.is_connected = False
            logger.info("🧹 Twitch chat monitor cleaned up")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")