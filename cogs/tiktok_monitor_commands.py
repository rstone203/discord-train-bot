"""
TikTok Monitor Commands
Lets admins add/remove TikTok accounts to watch per server.
The background task polls every 15 minutes and posts new videos.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from datetime import datetime
import asyncio

from database import DatabaseSession
from models import Guild


logger = logging.getLogger('discord_bot.tiktok_monitor_commands')


class TikTokMonitorCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logger
        self._poll_task_started = False

    async def cog_load(self):
        pass

    def start_poller(self):
        if not self._poll_task_started:
            self._poll_task_started = True
            self.tiktok_poller.start()
            self.logger.info('✅ TikTok poller started')

    async def respond(self, interaction, *args, **kwargs):
        if interaction.response.is_done():
            if 'view' in kwargs and kwargs['view'] is None:
                del kwargs['view']
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)

    # ------------------------------------------------------------------ #
    #  Background polling task                                             #
    # ------------------------------------------------------------------ #

    @tasks.loop(minutes=15)
    async def tiktok_poller(self):
        """Poll all watched TikTok accounts for new videos."""
        try:
            from utils.tiktok_monitor import get_latest_video
            import psycopg2, os

            conn = psycopg2.connect(os.environ['DATABASE_URL'])
            cur = conn.cursor()
            cur.execute("""
                SELECT id, guild_id, tiktok_username, feed_channel_id, last_video_id
                FROM tiktok_monitored_accounts
                WHERE is_active = TRUE
            """)
            accounts = cur.fetchall()
            conn.close()

            if not accounts:
                return

            self.logger.info(f'TikTok poller: checking {len(accounts)} account(s)')

            for (row_id, guild_id, username, feed_channel_id, last_video_id) in accounts:
                try:
                    await asyncio.sleep(2)  # gentle pacing between accounts
                    result = await get_latest_video(username)
                    if not result:
                        continue

                    new_id = result['video_id']

                    # Update last_checked regardless
                    conn2 = psycopg2.connect(os.environ['DATABASE_URL'])
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "UPDATE tiktok_monitored_accounts SET last_checked = NOW() WHERE id = %s",
                        (row_id,)
                    )
                    conn2.commit()

                    if last_video_id == new_id:
                        conn2.close()
                        continue  # no new video

                    # Save new video ID
                    cur2.execute(
                        "UPDATE tiktok_monitored_accounts SET last_video_id = %s WHERE id = %s",
                        (new_id, row_id)
                    )
                    conn2.commit()
                    conn2.close()

                    # Skip posting on first check (just establishes baseline)
                    if last_video_id is None:
                        self.logger.info(f'TikTok @{username}: baseline set to {new_id}')
                        continue

                    # Post to Discord
                    channel = self.bot.get_channel(feed_channel_id)
                    if not channel:
                        self.logger.warning(f'TikTok monitor: channel {feed_channel_id} not found for @{username}')
                        continue

                    embed = discord.Embed(
                        title=result['title'],
                        url=result['url'],
                        color=0x010101,
                        timestamp=datetime.utcnow()
                    )
                    embed.set_author(
                        name=f'🎵 @{username} posted a new TikTok!',
                        url=result['author_url'],
                    )
                    if result.get('thumbnail'):
                        embed.set_image(url=result['thumbnail'])
                    embed.set_footer(text='TikTok')

                    await channel.send(embed=embed)
                    self.logger.info(f'📱 Posted new TikTok from @{username} → #{channel.name}')

                except Exception as e:
                    self.logger.error(f'TikTok poller error for @{username}: {e}', exc_info=True)

        except Exception as e:
            self.logger.error(f'TikTok poller task error: {e}', exc_info=True)

    @tiktok_poller.before_loop
    async def before_tiktok_poller(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)  # let bot fully settle first

    @tiktok_poller.error
    async def tiktok_poller_error(self, error):
        self.logger.error(f'TikTok poller crashed: {error}', exc_info=True)
        await asyncio.sleep(60)
        self.tiktok_poller.restart()

    # ------------------------------------------------------------------ #
    #  Commands                                                            #
    # ------------------------------------------------------------------ #

    @commands.command(name='addtiktok', aliases=['tiktokwatch', 'watchiktok'])
    async def add_tiktok(self, ctx, username: str, channel: discord.TextChannel = None):
        """
        Owner/trusted only: watch a TikTok account and post new videos to a channel.
        Usage: !addtiktok @username #channel
        If no channel given, uses the current channel.
        """
        if not await self.bot.is_owner_or_trusted(ctx.author):
            await ctx.send('❌ This command requires owner or trusted access.')
            return
        if not ctx.guild:
            await ctx.send('❌ Server only.')
            return

        username = username.lstrip('@').lower().strip()
        target_channel = channel or ctx.channel

        try:
            import psycopg2, os
            conn = psycopg2.connect(os.environ['DATABASE_URL'])
            cur = conn.cursor()

            # Ensure guild exists
            cur.execute("SELECT id FROM guilds WHERE id = %s", (ctx.guild.id,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO guilds (id, name, owner_id, member_count) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (ctx.guild.id, ctx.guild.name, ctx.guild.owner_id, ctx.guild.member_count)
                )

            # Check limit (max 10 per server)
            cur.execute(
                "SELECT COUNT(*) FROM tiktok_monitored_accounts WHERE guild_id = %s AND is_active = TRUE",
                (ctx.guild.id,)
            )
            count = cur.fetchone()[0]
            if count >= 10:
                conn.close()
                await ctx.send('❌ Maximum 10 TikTok accounts per server. Remove one first with `!removetiktok`.')
                return

            # Insert or reactivate
            cur.execute("""
                INSERT INTO tiktok_monitored_accounts
                    (guild_id, tiktok_username, feed_channel_id, added_by, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (guild_id, tiktok_username)
                DO UPDATE SET
                    feed_channel_id = EXCLUDED.feed_channel_id,
                    is_active = TRUE,
                    last_video_id = NULL,
                    last_checked = NULL
            """, (ctx.guild.id, username, target_channel.id, ctx.author.id))
            conn.commit()
            conn.close()

            embed = discord.Embed(
                title='✅ TikTok Account Added',
                description=f'Now watching **@{username}** for new TikTok videos.',
                color=0x010101
            )
            embed.add_field(name='📺 Posts to', value=target_channel.mention, inline=True)
            embed.add_field(name='⏱️ Check interval', value='Every 15 minutes', inline=True)
            embed.add_field(
                name='ℹ️ First check',
                value='The first poll sets the baseline — only videos posted **after** setup will be shared.',
                inline=False
            )
            embed.set_footer(text=f'Use !tiktokstatus to see all watched accounts')
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f'❌ Error: {e}')
            self.logger.error(f'addtiktok error: {e}', exc_info=True)

    @commands.command(name='removetiktok', aliases=['tiktokremove', 'unwatchtiktok'])
    async def remove_tiktok(self, ctx, username: str):
        """
        Owner/trusted only: stop watching a TikTok account in this server.
        Usage: !removetiktok @username
        """
        if not await self.bot.is_owner_or_trusted(ctx.author):
            await ctx.send('❌ This command requires owner or trusted access.')
            return
        if not ctx.guild:
            await ctx.send('❌ Server only.')
            return

        username = username.lstrip('@').lower().strip()
        try:
            import psycopg2, os
            conn = psycopg2.connect(os.environ['DATABASE_URL'])
            cur = conn.cursor()
            cur.execute(
                "UPDATE tiktok_monitored_accounts SET is_active = FALSE WHERE guild_id = %s AND tiktok_username = %s",
                (ctx.guild.id, username)
            )
            affected = cur.rowcount
            conn.commit()
            conn.close()

            if affected:
                await ctx.send(f'✅ Stopped watching **@{username}** — no more posts from that account.')
            else:
                await ctx.send(f'❌ @{username} was not being watched in this server.')

        except Exception as e:
            await ctx.send(f'❌ Error: {e}')
            self.logger.error(f'removetiktok error: {e}', exc_info=True)

    @commands.command(name='tiktokstatus', aliases=['tiktokwatching', 'tiktoklist'])
    async def tiktok_status(self, ctx):
        """
        Show all TikTok accounts being watched in this server and their status.
        """
        if not await self.bot.is_owner_or_trusted(ctx.author):
            await ctx.send('❌ This command requires owner or trusted access.')
            return
        if not ctx.guild:
            await ctx.send('❌ Server only.')
            return

        try:
            import psycopg2, os
            conn = psycopg2.connect(os.environ['DATABASE_URL'])
            cur = conn.cursor()
            cur.execute("""
                SELECT tiktok_username, feed_channel_id, last_video_id, last_checked, added_at
                FROM tiktok_monitored_accounts
                WHERE guild_id = %s AND is_active = TRUE
                ORDER BY added_at
            """, (ctx.guild.id,))
            rows = cur.fetchall()
            conn.close()

            if not rows:
                embed = discord.Embed(
                    title='📱 TikTok Monitor',
                    description='No TikTok accounts are being watched in this server.\n\nUse `!addtiktok @username #channel` to add one.',
                    color=0x010101
                )
                await ctx.send(embed=embed)
                return

            embed = discord.Embed(
                title=f'📱 TikTok Monitor — {len(rows)} account(s)',
                description='Polling every **15 minutes** for new videos.',
                color=0x010101,
                timestamp=datetime.utcnow()
            )

            for (username, channel_id, last_vid, last_checked, added_at) in rows:
                channel = ctx.guild.get_channel(channel_id)
                ch_text = channel.mention if channel else f'#{channel_id} (not found)'
                checked_text = last_checked.strftime('%d %b %H:%M UTC') if last_checked else 'Not yet'
                status = '✅ Active' if last_vid else '⏳ Awaiting first check'
                embed.add_field(
                    name=f'🎵 @{username}',
                    value=f'**Posts to:** {ch_text}\n**Status:** {status}\n**Last checked:** {checked_text}',
                    inline=True
                )

            embed.set_footer(text='!addtiktok @user #channel  •  !removetiktok @user')
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f'❌ Error: {e}')
            self.logger.error(f'tiktokstatus error: {e}', exc_info=True)


async def setup(bot):
    cog = TikTokMonitorCommands(bot)
    await bot.add_cog(cog)
    cog.start_poller()
