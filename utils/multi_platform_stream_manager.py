"""
Multi-Platform Stream Manager - YouTube, Kick, Twitch Clips/VODs
"""

import discord
import aiohttp
import asyncio
import logging
from database import get_db_session
from models import StreamPlatform, ClipNotification, VODNotification, User
from datetime import datetime, timedelta
import os

logger = logging.getLogger('discord_bot.multi_platform_stream')

class MultiPlatformStreamManager:
    def __init__(self, bot):
        self.bot = bot
        self.youtube_api_key = os.getenv('YOUTUBE_API_KEY')
        self.twitch_client_id = os.getenv('TWITCH_CLIENT_ID')
        self.twitch_client_secret = os.getenv('TWITCH_CLIENT_SECRET')
        self.twitch_access_token = None
        self.last_token_refresh = None
        
    async def get_twitch_access_token(self):
        """Get Twitch app access token."""
        if self.twitch_access_token and self.last_token_refresh:
            if (datetime.now() - self.last_token_refresh).total_seconds() < 3000:
                return self.twitch_access_token
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://id.twitch.tv/oauth2/token',
                    params={
                        'client_id': self.twitch_client_id,
                        'client_secret': self.twitch_client_secret,
                        'grant_type': 'client_credentials'
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.twitch_access_token = data['access_token']
                        self.last_token_refresh = datetime.now()
                        return self.twitch_access_token
        except Exception as e:
            logger.error(f"Error getting Twitch access token: {e}")
        
        return None

    async def check_youtube_live(self, channel_id: str):
        """Check if a YouTube channel is live."""
        if not self.youtube_api_key:
            return None
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://www.googleapis.com/youtube/v3/search',
                    params={
                        'part': 'snippet',
                        'channelId': channel_id,
                        'eventType': 'live',
                        'type': 'video',
                        'key': self.youtube_api_key
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('items'):
                            stream = data['items'][0]
                            return {
                                'is_live': True,
                                'stream_id': stream['id']['videoId'],
                                'title': stream['snippet']['title'],
                                'url': f"https://www.youtube.com/watch?v={stream['id']['videoId']}"
                            }
                    return {'is_live': False}
        except Exception as e:
            logger.error(f"Error checking YouTube live status: {e}")
            return None

    async def check_kick_live(self, username: str):
        """Check if a Kick channel is live."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f'https://kick.com/api/v2/channels/{username}'
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        livestream = data.get('livestream')
                        if livestream:
                            return {
                                'is_live': True,
                                'stream_id': str(livestream.get('id')),
                                'title': livestream.get('session_title', 'Untitled Stream'),
                                'url': f"https://kick.com/{username}"
                            }
                    return {'is_live': False}
        except Exception as e:
            logger.error(f"Error checking Kick live status: {e}")
            return None

    async def get_twitch_clips(self, broadcaster_id: str, limit: int = 5):
        """Get recent Twitch clips for a broadcaster."""
        token = await self.get_twitch_access_token()
        if not token:
            return []
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.twitch.tv/helix/clips',
                    headers={
                        'Client-ID': self.twitch_client_id,
                        'Authorization': f'Bearer {token}'
                    },
                    params={
                        'broadcaster_id': broadcaster_id,
                        'first': limit,
                        'started_at': (datetime.now() - timedelta(hours=24)).isoformat() + 'Z'
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('data', [])
        except Exception as e:
            logger.error(f"Error getting Twitch clips: {e}")
        
        return []

    async def get_twitch_vods(self, user_id: str, limit: int = 5):
        """Get recent Twitch VODs for a user."""
        token = await self.get_twitch_access_token()
        if not token:
            return []
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.twitch.tv/helix/videos',
                    headers={
                        'Client-ID': self.twitch_client_id,
                        'Authorization': f'Bearer {token}'
                    },
                    params={
                        'user_id': user_id,
                        'first': limit,
                        'type': 'archive'
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('data', [])
        except Exception as e:
            logger.error(f"Error getting Twitch VODs: {e}")
        
        return []

    async def get_twitch_schedule(self, broadcaster_id: str):
        """Get Twitch stream schedule for a broadcaster."""
        token = await self.get_twitch_access_token()
        if not token:
            return []
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.twitch.tv/helix/schedule',
                    headers={
                        'Client-ID': self.twitch_client_id,
                        'Authorization': f'Bearer {token}'
                    },
                    params={
                        'broadcaster_id': broadcaster_id
                    }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('data', {}).get('segments', [])
        except Exception as e:
            logger.error(f"Error getting Twitch schedule: {e}")
        
        return []

    async def check_platforms(self):
        """Check all platform statuses and send notifications."""
        if not self.bot.is_ready():
            return
        
        session = get_db_session()
        try:
            platforms = session.query(StreamPlatform).filter_by(notify_live=True).all()
            
            for platform_entry in platforms:
                try:
                    if platform_entry.platform == 'youtube':
                        await self.check_youtube_platform(platform_entry, session)
                    elif platform_entry.platform == 'kick':
                        await self.check_kick_platform(platform_entry, session)
                    elif platform_entry.platform == 'twitch':
                        await self.check_twitch_extras(platform_entry, session)
                    
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Error checking platform {platform_entry.platform}: {e}")
            
        except Exception as e:
            logger.error(f"Error in platform check loop: {e}")
        finally:
            session.close()

    async def check_youtube_platform(self, platform_entry, session):
        """Check YouTube platform status."""
        status = await self.check_youtube_live(platform_entry.platform_user_id)
        
        if not status:
            return
        
        if status['is_live'] and not platform_entry.is_live:
            platform_entry.is_live = True
            platform_entry.last_stream_id = status['stream_id']
            platform_entry.last_checked = datetime.now()
            session.commit()
            
            if platform_entry.notification_channel_id:
                await self.send_live_notification(
                    platform_entry.notification_channel_id,
                    platform_entry.platform_username,
                    status['title'],
                    status['url'],
                    'YouTube'
                )
        
        elif not status['is_live'] and platform_entry.is_live:
            platform_entry.is_live = False
            platform_entry.last_checked = datetime.now()
            session.commit()

    async def check_kick_platform(self, platform_entry, session):
        """Check Kick platform status."""
        status = await self.check_kick_live(platform_entry.platform_username)
        
        if not status:
            return
        
        if status['is_live'] and not platform_entry.is_live:
            platform_entry.is_live = True
            platform_entry.last_stream_id = status['stream_id']
            platform_entry.last_checked = datetime.now()
            session.commit()
            
            if platform_entry.notification_channel_id:
                await self.send_live_notification(
                    platform_entry.notification_channel_id,
                    platform_entry.platform_username,
                    status['title'],
                    status['url'],
                    'Kick'
                )
        
        elif not status['is_live'] and platform_entry.is_live:
            platform_entry.is_live = False
            platform_entry.last_checked = datetime.now()
            session.commit()

    async def check_twitch_extras(self, platform_entry, session):
        """Check Twitch clips and VODs."""
        if platform_entry.notify_clips:
            clips = await self.get_twitch_clips(platform_entry.platform_user_id)
            for clip in clips:
                await self.process_clip(clip, platform_entry, session)
        
        if platform_entry.notify_vods:
            vods = await self.get_twitch_vods(platform_entry.platform_user_id)
            for vod in vods:
                await self.process_vod(vod, platform_entry, session)

    async def process_clip(self, clip_data, platform_entry, session):
        """Process and notify about a clip."""
        clip_id = clip_data['id']
        
        existing = session.query(ClipNotification).filter_by(clip_id=clip_id).first()
        if existing:
            return
        
        clip_notification = ClipNotification(
            user_id=platform_entry.user_id,
            guild_id=platform_entry.guild_id,
            platform='twitch',
            clip_id=clip_id,
            clip_url=clip_data['url'],
            clip_title=clip_data.get('title'),
            clip_creator=clip_data.get('creator_name'),
            posted_to_channel_id=platform_entry.notification_channel_id
        )
        
        session.add(clip_notification)
        session.commit()
        
        if platform_entry.notification_channel_id:
            await self.send_clip_notification(
                platform_entry.notification_channel_id,
                platform_entry.platform_username,
                clip_data
            )

    async def process_vod(self, vod_data, platform_entry, session):
        """Process and notify about a VOD."""
        vod_id = vod_data['id']
        
        existing = session.query(VODNotification).filter_by(vod_id=vod_id).first()
        if existing:
            return
        
        created_at = datetime.fromisoformat(vod_data['created_at'].replace('Z', '+00:00'))
        if (datetime.now() - created_at.replace(tzinfo=None)).total_seconds() > 7200:
            return
        
        duration_str = vod_data.get('duration', '0h0m0s')
        duration_parts = duration_str.replace('h', ':').replace('m', ':').replace('s', '').split(':')
        duration_seconds = sum(int(x) * 60 ** i for i, x in enumerate(reversed(duration_parts)))
        
        vod_notification = VODNotification(
            user_id=platform_entry.user_id,
            guild_id=platform_entry.guild_id,
            platform='twitch',
            vod_id=vod_id,
            vod_url=vod_data['url'],
            vod_title=vod_data.get('title'),
            vod_duration=duration_seconds,
            posted_to_channel_id=platform_entry.notification_channel_id
        )
        
        session.add(vod_notification)
        session.commit()
        
        if platform_entry.notification_channel_id:
            await self.send_vod_notification(
                platform_entry.notification_channel_id,
                platform_entry.platform_username,
                vod_data
            )

    async def send_live_notification(self, channel_id, username, title, url, platform):
        """Send live stream notification."""
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                return
            
            embed = discord.Embed(
                title=f"🔴 {username} is now LIVE on {platform}!",
                description=title,
                url=url,
                color=discord.Color.red()
            )
            embed.add_field(name="Watch Now", value=f"[Click here to watch]({url})", inline=False)
            
            await channel.send(embed=embed)
            logger.info(f"Sent {platform} live notification for {username}")
        except Exception as e:
            logger.error(f"Error sending live notification: {e}")

    async def send_clip_notification(self, channel_id, username, clip_data):
        """Send clip notification."""
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                return
            
            embed = discord.Embed(
                title=f"🎬 New Clip: {clip_data.get('title', 'Untitled')}",
                description=f"Created by: {clip_data.get('creator_name', 'Unknown')}",
                url=clip_data['url'],
                color=discord.Color.purple()
            )
            embed.add_field(name="Streamer", value=username, inline=True)
            embed.add_field(name="Views", value=clip_data.get('view_count', 0), inline=True)
            
            if clip_data.get('thumbnail_url'):
                embed.set_image(url=clip_data['thumbnail_url'])
            
            await channel.send(f"📌 New clip from **{username}**!", embed=embed)
            logger.info(f"Sent clip notification for {username}")
        except Exception as e:
            logger.error(f"Error sending clip notification: {e}")

    async def send_vod_notification(self, channel_id, username, vod_data):
        """Send VOD notification."""
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                return
            
            duration = vod_data.get('duration', '0h0m0s')
            
            embed = discord.Embed(
                title=f"📹 VOD Available: {vod_data.get('title', 'Untitled')}",
                url=vod_data['url'],
                color=discord.Color.blue()
            )
            embed.add_field(name="Streamer", value=username, inline=True)
            embed.add_field(name="Duration", value=duration, inline=True)
            embed.add_field(name="Views", value=vod_data.get('view_count', 0), inline=True)
            
            if vod_data.get('thumbnail_url'):
                thumbnail = vod_data['thumbnail_url'].replace('%{width}', '320').replace('%{height}', '180')
                embed.set_image(url=thumbnail)
            
            await channel.send(f"🎥 New VOD from **{username}**!", embed=embed)
            logger.info(f"Sent VOD notification for {username}")
        except Exception as e:
            logger.error(f"Error sending VOD notification: {e}")

    async def start_checker(self):
        """Start the platform checker loop."""
        await self.bot.wait_until_ready()
        logger.info("✅ Multi-platform stream checker started")
        
        while not self.bot.is_closed():
            try:
                await self.check_platforms()
                await asyncio.sleep(120)
            except Exception as e:
                logger.error(f"Error in platform checker loop: {e}")
                await asyncio.sleep(120)
