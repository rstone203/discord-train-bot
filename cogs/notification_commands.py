"""
Notification setup and configuration commands.
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import pytz
import logging
import asyncio
from discord.ext import tasks
from database import DatabaseSession, AsyncDatabaseSession
from models import NotificationSettings, Guild, Channel, TrainSchedule, TrainNotification, User, BannedUser, TrainParticipant, get_uk_time
from utils.slash_permissions import owner_only

logger = logging.getLogger('discord_bot.notification_commands')


def build_offer_view(schedule_id: int, user_id: int) -> discord.ui.View:
    """A DM view with restart-safe Accept/Decline buttons for an open-seat offer."""
    view = discord.ui.View(timeout=None)
    view.add_item(WaitlistOfferButton('accept', schedule_id, user_id))
    view.add_item(WaitlistOfferButton('decline', schedule_id, user_id))
    return view


async def _post_waitlist_promotion_notice(bot, guild, schedule, member_id: int):
    """Announce a waitlist promotion and ping the trusted role so docs get updated."""
    from models import SystemSettings, TrustedRole, PersistentMessageDisplay
    try:
        chan_id = None
        role_mentions = ""
        gid = guild.id
        async with AsyncDatabaseSession() as session:
            row = session.query(SystemSettings).filter_by(
                setting_key=f"waitlist_log_channel_{gid}").first()
            if row and row.setting_value:
                try:
                    chan_id = int(row.setting_value)
                except (TypeError, ValueError):
                    chan_id = None
            if not chan_id:
                disp = session.query(PersistentMessageDisplay).filter_by(
                    guild_id=gid, display_type='waitlist_panel').first()
                if disp:
                    chan_id = disp.channel_id
            trusted = session.query(TrustedRole).filter_by(
                guild_id=gid, is_active=True).all()
            role_mentions = " ".join(f"<@&{t.role_id}>" for t in trusted)
        if not chan_id:
            return
        channel = guild.get_channel(chan_id)
        if not channel:
            return
        member = guild.get_member(member_id)
        who = member.mention if member else f"<@{member_id}>"
        embed = discord.Embed(
            title="🎟️ Waitlist Seat Filled",
            description=(
                f"{who} accepted an open seat on **{schedule.name or f'Train #{schedule.id}'}** "
                f"from the waitlist.\nPlease update the schedule docs to reflect the new rider."
            ),
            color=0x00ff00,
            timestamp=datetime.utcnow(),
        )
        await channel.send(
            content=role_mentions or None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
    except Exception as e:
        logger.warning(f"Failed to post waitlist promotion notice: {e}")


async def apply_waitlist_decline(bot, schedule_id: int, user_id: int) -> str:
    """Remove a waitlister who is turning down their open-seat offer.

    This is the single decline code path shared by the DM 'Decline' button and the
    /mywaitlists command, so "decline" behaves identically wherever it is invoked.
    It only removes the declining user; the open-seat detector remains the sole
    owner of promotions and will re-offer the freed seat to the next person in line
    on its next pass. We never promote anyone here.

    Returns one of: 'declined' | 'not_found' | 'disabled'. Caller handles messaging
    and any display refresh. Raises on unexpected DB errors so callers can log them.
    """
    from utils.waitlist import remove_from_waitlist
    from utils.schedule_cache import invalidate_schedule_cache

    guild_id = None
    async with AsyncDatabaseSession() as session:
        row = session.query(TrainParticipant).filter_by(
            schedule_id=schedule_id, user_id=user_id,
            is_waitlisted=True, is_active=False).first()
        schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
        if not row or not schedule or not schedule.is_active:
            return 'not_found'
        guild_id = schedule.guild_id

        # Fail closed if the waitlist feature was turned off after the offer
        # was sent — no DB mutations should happen while the flag is off.
        perms = getattr(bot, 'permissions_manager', None)
        if perms and not await perms.is_feature_enabled(guild_id, 'waitlist'):
            return 'disabled'

        if not remove_from_waitlist(session, schedule_id, user_id):
            return 'not_found'
        session.commit()

    invalidate_schedule_cache(guild_id)
    return 'declined'


async def apply_waitlist_accept(bot, schedule_id: int, user_id: int):
    """Promote a front-of-queue waitlister into the open seat they were offered.

    This is the single accept code path shared by the DM 'Accept' button and the
    /mywaitlists command, so "accept" behaves identically wherever it is invoked.
    The open-seat detector remains the sole owner of *offering* seats; this only
    completes an offer the detector already extended to the front-of-queue rider.

    Re-checks at accept time, to guard against stale DM buttons / stale command
    selections:
      - the offer row still exists and the schedule is active
      - the waitlist feature is still enabled for the guild (fail closed)
      - the user is still the current front-of-queue holder
      - the offer has not expired (OFFER_TIMEOUT_MINUTES)
      - a seat is still open

    On success it promotes the rider, posts the promotion notice, and refreshes
    the persistent displays — the same side effects the DM button produced.

    Returns a (status, schedule_name) tuple where status is one of:
    'accepted' | 'not_found' | 'disabled' | 'expired' | 'filled'. Caller handles
    messaging. Raises on unexpected DB errors so callers can log them.
    """
    from utils.waitlist import (active_rider_count, slot_capacity, renumber_waitlist,
                                first_waitlister, OFFER_TIMEOUT_MINUTES)
    from utils.schedule_cache import invalidate_schedule_cache

    guild_id = None
    schedule_obj = None
    schedule_name = f"Train #{schedule_id}"
    async with AsyncDatabaseSession() as session:
        row = session.query(TrainParticipant).filter_by(
            schedule_id=schedule_id, user_id=user_id,
            is_waitlisted=True, is_active=False).first()
        schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
        if not row or not schedule or not schedule.is_active:
            return 'not_found', schedule_name
        guild_id = schedule.guild_id
        schedule_name = schedule.name or schedule_name

        # Fail closed if the waitlist feature was turned off after the offer
        # was sent — no DB mutations should happen while the flag is off.
        perms = getattr(bot, 'permissions_manager', None)
        if perms and not await perms.is_feature_enabled(guild_id, 'waitlist'):
            return 'disabled', schedule_name

        # The offer must still be valid for THIS user. The user must still be the
        # current front-of-queue holder, and their offer must be unexpired. After
        # a timeout the detector moves them to the back, so a stale accept must
        # not let them jump the queue.
        front = first_waitlister(session, schedule_id)
        offer_expired = (
            row.offer_sent_at is None
            or (datetime.utcnow() - row.offer_sent_at).total_seconds()
            >= OFFER_TIMEOUT_MINUTES * 60
        )
        if not front or front.user_id != user_id or offer_expired:
            return 'expired', schedule_name

        # Only works if a seat is still open
        if active_rider_count(session, schedule_id) >= slot_capacity(schedule):
            row.offer_sent_at = None  # let the detector re-evaluate later
            session.commit()
            return 'filled', schedule_name

        row.is_active = True
        row.is_waitlisted = False
        row.waitlist_position = None
        row.offer_sent_at = None
        renumber_waitlist(session, schedule_id)
        session.commit()
        invalidate_schedule_cache(guild_id)
        # Detach a lightweight copy of what we need after the session closes
        schedule_obj = type('S', (), {'id': schedule.id, 'name': schedule.name})()

    if guild_id and schedule_obj:
        guild = bot.get_guild(guild_id)
        if guild:
            await _post_waitlist_promotion_notice(bot, guild, schedule_obj, user_id)
        cog = bot.get_cog('TrainParticipantCommands')
        if cog:
            try:
                await cog.trigger_persistent_updates(guild_id)
            except Exception as e:
                logger.warning(f"Failed to refresh displays after promotion: {e}")

    return 'accepted', schedule_name


async def _handle_waitlist_offer_response(interaction: discord.Interaction, action: str,
                                          schedule_id: int, user_id: int):
    """Shared handler for the Accept/Decline buttons on an open-seat offer DM."""
    bot = interaction.client

    if interaction.user.id != user_id:
        await interaction.response.send_message(
            "This offer isn't for you.", ephemeral=True)
        return

    if action == 'decline':
        status = await apply_waitlist_decline(bot, schedule_id, user_id)
        if status == 'declined':
            content = ("👍 No problem — you've been removed from this waitlist. "
                       "We'll offer the seat to the next person in line.")
        else:
            content = "⌛ This offer is no longer available."
        await interaction.response.edit_message(content=content, view=None)
        return

    try:
        status, schedule_name = await apply_waitlist_accept(bot, schedule_id, user_id)
        if status == 'accepted':
            content = f"🎉 You're in! You've been added to **{schedule_name}**."
        elif status == 'filled':
            content = ("😔 That seat just filled up. You're still on the waitlist "
                       "and we'll let you know if another opens.")
        elif status == 'expired':
            content = ("⌛ This offer has expired or it's now someone else's turn. "
                       "You're still on the waitlist if a seat opens again.")
        else:  # not_found / disabled
            content = "⌛ This offer is no longer available."
        await interaction.response.edit_message(content=content, view=None)
    except Exception as e:
        logger.error(f"Error handling waitlist offer response: {e}", exc_info=True)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Something went wrong handling that. Please try again.", ephemeral=True)
        except Exception:
            pass


class WaitlistOfferButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'waitlist_offer:(?P<action>accept|decline):(?P<sid>\d+):(?P<uid>\d+)'
):
    """Restart-safe Accept/Decline button carrying the offer's schedule + user."""

    def __init__(self, action: str, schedule_id: int, user_id: int):
        self.action = action
        self.schedule_id = schedule_id
        self.user_id = user_id
        super().__init__(
            discord.ui.Button(
                label=("✅ Accept seat" if action == 'accept' else "❌ Decline"),
                style=(discord.ButtonStyle.success if action == 'accept'
                       else discord.ButtonStyle.secondary),
                custom_id=f"waitlist_offer:{action}:{schedule_id}:{user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match['action'], int(match['sid']), int(match['uid']))

    async def callback(self, interaction: discord.Interaction):
        await _handle_waitlist_offer_response(
            interaction, self.action, self.schedule_id, self.user_id)


class NotificationCommands(commands.Cog):
    """Commands for setting up and managing notifications."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.notification_commands')
        # Timezone reference for datetime calculations
        self.uk_tz = pytz.timezone('Europe/London')
        # Emojis for reactions
        self.READY_EMOJI = "✅"  # Ready emoji
        self.NOT_READY_EMOJI = "❌"  # Not ready emoji
        self.BACKUP_EMOJI = "🔄"  # Backup needed emoji
        
        # Enhanced duplicate prevention
        self.sent_notifications_cache = set()  # Cache to prevent immediate duplicates
        self.processing_lock = asyncio.Lock()  # Prevent concurrent notification processing

        self._twitch_app_token = None          # Cached Twitch app access token
        self._twitch_app_token_expiry = None   # Token expiry (unix timestamp)

        # Chat monitoring cooldown — prevents restarting monitoring for the same schedule
        # within 10 minutes (guards against rapid-restart loop when monitoring task exits early)
        self._monitoring_cooldowns: dict = {}  # schedule_id -> last_start datetime
    
    def is_admin_or_trusted():
        """Check if user has manage_guild permission OR is a trusted user."""
        async def predicate(ctx):
            # Check manage_guild permission first
            if ctx.author.guild_permissions.manage_guild:
                return True
            # Check if user is trusted, scoped to the current guild so that
            # the guild-level trusted_access_enabled flag is enforced.
            guild_id = ctx.guild.id if ctx.guild else None
            return await ctx.bot.is_trusted_user(ctx.author.id, guild_id)
        return commands.check(predicate)
    
    def get_active_participant_ids(self, session, schedule_id):
        """
        Get list of active Discord IDs for a schedule from TrainParticipant table.
        This is the source of truth - NEVER use schedule.participant_ids array!
        
        Args:
            session: Database session
            schedule_id: ID of the train schedule
            
        Returns:
            List of Discord user ID strings for active participants
        """
        from models import TrainParticipant
        
        participants = session.query(TrainParticipant).filter_by(
            schedule_id=schedule_id,
            is_active=True
        ).all()
        
        # Return list of Discord IDs as strings (column is user_id, not discord_user_id!)
        return [str(p.user_id) for p in participants if p.user_id]

    def _resolve_participant_mention(self, participant, guild) -> str:
        """Return a Discord mention string for a TrainParticipant.

        Priority:
        1. user_id is a non-zero int → look up the guild member; if found return their mention.
        2. user_id is 0 / missing → search guild members by username or display_name.
        3. Nothing found → return plain "@name" text so the message still identifies them.
        """
        uid = getattr(participant, 'user_id', 0) or 0
        if uid:
            member = guild.get_member(int(uid))
            if member:
                return member.mention
            # Member isn't cached but the ID looks valid — trust the ID
            return f"<@{uid}>"

        # user_id == 0: manually-added participant — try name-based lookup
        name = (getattr(participant, 'username', '') or
                getattr(participant, 'display_name', '') or '')
        if name and guild:
            lower_name = name.lower()
            member = discord.utils.find(
                lambda m: m.name.lower() == lower_name or m.display_name.lower() == lower_name,
                guild.members
            )
            if member:
                self.logger.info(
                    f"📌 Resolved participant '{name}' → {member.display_name} "
                    f"({member.id}) via name lookup"
                )
                return member.mention

        # Fallback: plain text so at least the name appears in the message
        fallback = name or f"unknown(id={uid})"
        self.logger.warning(
            f"⚠️ Could not resolve participant '{fallback}' to a Discord member "
            f"— showing plain text mention"
        )
        return f"@{fallback}"

    # ------------------------------------------------------------------ #
    # Go-live reminder helpers                                             #
    # ------------------------------------------------------------------ #

    async def _get_twitch_app_token(self):
        """Return a cached Twitch app access token, refreshing it if expired."""
        import os, time
        import aiohttp as _aio
        if self._twitch_app_token and self._twitch_app_token_expiry and time.time() < self._twitch_app_token_expiry:
            return self._twitch_app_token
        client_id = os.getenv('TWITCH_CLIENT_ID')
        client_secret = os.getenv('TWITCH_CLIENT_SECRET')
        if not client_id or not client_secret:
            return None
        try:
            async with _aio.ClientSession() as http:
                resp = await http.post(
                    'https://id.twitch.tv/oauth2/token',
                    params={'client_id': client_id, 'client_secret': client_secret, 'grant_type': 'client_credentials'},
                    timeout=_aio.ClientTimeout(total=10)
                )
                if resp.status != 200:
                    return None
                data = await resp.json()
                self._twitch_app_token = data.get('access_token')
                self._twitch_app_token_expiry = time.time() + data.get('expires_in', 3600) - 300
                return self._twitch_app_token
        except Exception as e:
            self.logger.error(f"Error obtaining Twitch app token: {e}")
            return None

    @tasks.loop(seconds=60)  # Check every 60 seconds (reduced from 45 to save resources)
    async def notification_scheduler(self):
        """Background task to send multi-stage train notifications."""
        try:
            self.logger.info("🔄 Notification scheduler loop executing...")
            
            from utils.maintenance_manager import maintenance_manager
            maintenance_mode = await maintenance_manager.get()
            
            # Update heartbeat at start of scheduler tick to prevent health monitor crashes
            from utils.bot_monitor import update_heartbeat, update_notification_check
            update_heartbeat()
            update_notification_check()
            
            # Always check for next day raids (even in maintenance mode) - critical for trusted users
            # await self.check_next_day_raids()  # Disabled - bot staying online, no need for activation pings
            
            if maintenance_mode:
                self.logger.debug("⏸️ Maintenance mode active - skipping notifications")
                update_heartbeat()
                return
            
            self.logger.debug("✅ Proceeding with notification check...")
            
            # Normal notification processing when not in maintenance mode
            # Skip cleanup of expired trains for recurring weekly schedules
            # await self.delete_expired_trains()  # Disabled to preserve weekly recurring schedules
            await self.check_and_send_notifications()
            # Catch up on any missed stage-1 notifications (handles schedules reactivated mid-window)
            await self.check_missed_notifications()
            # Clear Saturday train participants on Sunday for weekly reset
            await self.clear_saturday_participants_on_sunday()
            # Also check for completed sessions that need attendance summaries
            await self.check_completed_sessions()
            # Beta: DM opted-in participants before their own slot (per-guild flag)
            await self.check_train_reminders()
            # Beta: offer freed seats to waitlisters via DM accept/decline (per-guild flag)
            await self.check_waitlist_offers()
            
            # Update both heartbeat AND notification check at end of tick so the
            # health monitor never sees a stale timestamp from a long-running tick
            update_heartbeat()
            update_notification_check()
            
        except Exception as e:
            self.logger.exception(f"❌ Error in notification scheduler: {e}")
            # Always update heartbeat even on error to prevent health monitor crashes
            from utils.bot_monitor import update_heartbeat
            update_heartbeat()
    
    @notification_scheduler.error
    async def notification_scheduler_error(self, error):
        """Handle errors in notification_scheduler and restart it."""
        self.logger.error(f"⚠️ notification_scheduler crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting notification_scheduler in 60 seconds...")
        await asyncio.sleep(60)
        self.notification_scheduler.restart()
    
    @notification_scheduler.before_loop
    async def before_notification_scheduler(self):
        """Wait for bot to be ready before starting notifications."""
        await self.bot.wait_until_ready()
        self.logger.info("✅ Notification scheduler passed wait_until_ready() - loop will now start")
    
    
    async def check_waitlist_offers(self):
        """Detect open seats on slots that have waitlisters and DM the first in line.

        Gated per-guild behind the `waitlist` beta flag. Throttles re-offers via
        TrainParticipant.offer_sent_at; an offer expires after OFFER_TIMEOUT_MINUTES
        and the seat is offered to the next person.
        """
        from utils.waitlist import (slot_capacity, active_rider_count,
                                    first_waitlister, move_to_back, OFFER_TIMEOUT_MINUTES,
                                    recurring_schedules_query)
        perms = getattr(self.bot, 'permissions_manager', None)
        if not perms:
            return
        try:
            now = datetime.utcnow()
            pending = []  # (schedule_id, user_id, schedule_name) to DM after the session
            async with AsyncDatabaseSession() as session:
                # Slot waitlist targets recurring weekly slots only.
                schedules = recurring_schedules_query(session).all()
                for schedule in schedules:
                    gid = schedule.guild_id
                    try:
                        if not await perms.is_feature_enabled(gid, 'waitlist'):
                            continue
                    except Exception:
                        continue
                    # Only act when there's a genuinely open seat
                    if active_rider_count(session, schedule.id) >= slot_capacity(schedule):
                        continue
                    entry = first_waitlister(session, schedule.id)
                    if not entry:
                        continue
                    if entry.offer_sent_at is None:
                        # Record the attempt up front so a transient DM failure can't
                        # cause us to spam the same person every tick.
                        entry.offer_sent_at = now
                        session.commit()
                        pending.append((schedule.id, entry.user_id,
                                        schedule.name or f"Train #{schedule.id}"))
                    else:
                        elapsed = (now - entry.offer_sent_at).total_seconds()
                        if elapsed >= OFFER_TIMEOUT_MINUTES * 60:
                            # No response in time — advance to the next person in line.
                            move_to_back(session, schedule.id, entry)
                            session.commit()

            for schedule_id, user_id, schedule_name in pending:
                try:
                    user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                    if not user:
                        continue
                    embed = discord.Embed(
                        title="🎟️ A Seat Opened Up!",
                        description=(
                            f"A spot just opened on **{schedule_name}** and you're next "
                            f"on the waitlist.\n\nWant it? Tap **Accept seat** below within "
                            f"the next little while, or **Decline** to pass it on."
                        ),
                        color=0x5865F2,
                        timestamp=datetime.utcnow(),
                    )
                    await user.send(embed=embed, view=build_offer_view(schedule_id, user_id))
                    self.logger.info(f"Sent waitlist offer to {user_id} for schedule {schedule_id}")
                except discord.Forbidden:
                    self.logger.info(f"Could not DM waitlist offer to {user_id} (DMs closed)")
                except Exception as e:
                    self.logger.warning(f"Failed to send waitlist offer to {user_id}: {e}")
        except Exception as e:
            self.logger.error(f"Error in check_waitlist_offers: {e}", exc_info=True)

    async def check_waitlist_for_slot(self, schedule_id: int, guild_id: int):
        """Immediately run the open-seat detector for a single slot.

        Called right after an admin removes a rider so the waitlist offer goes
        out in seconds rather than waiting up to ~60 s for the next detector tick.
        Gated behind the same 'waitlist' beta flag as the full detector.
        Best-effort — never raises so it can't break the remove flow.
        """
        from utils.waitlist import (slot_capacity, active_rider_count,
                                    first_waitlister, OFFER_TIMEOUT_MINUTES,
                                    move_to_back)
        perms = getattr(self.bot, 'permissions_manager', None)
        if not perms:
            return
        try:
            if not await perms.is_feature_enabled(guild_id, 'waitlist'):
                return

            now = datetime.utcnow()
            pending = None  # (user_id, schedule_name)

            async with AsyncDatabaseSession() as session:
                from models import TrainSchedule as TS
                schedule = session.query(TS).filter_by(
                    id=schedule_id, is_active=True
                ).first()
                if not schedule:
                    return

                if active_rider_count(session, schedule_id) >= slot_capacity(schedule):
                    return  # seat not actually free yet

                entry = first_waitlister(session, schedule_id)
                if not entry:
                    return

                if entry.offer_sent_at is None:
                    entry.offer_sent_at = now
                    session.commit()
                    pending = (entry.user_id, schedule.name or f"Train #{schedule_id}")
                else:
                    elapsed = (now - entry.offer_sent_at).total_seconds()
                    if elapsed >= OFFER_TIMEOUT_MINUTES * 60:
                        move_to_back(session, schedule_id, entry)
                        session.commit()

            if pending:
                user_id, schedule_name = pending
                try:
                    user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                    if user:
                        embed = discord.Embed(
                            title="🎟️ A Seat Opened Up!",
                            description=(
                                f"A spot just opened on **{schedule_name}** and you're next "
                                f"on the waitlist.\n\nWant it? Tap **Accept seat** below within "
                                f"the next little while, or **Decline** to pass it on."
                            ),
                            color=0x5865F2,
                            timestamp=datetime.utcnow(),
                        )
                        await user.send(embed=embed, view=build_offer_view(schedule_id, user_id))
                        self.logger.info(
                            f"Immediate waitlist offer sent to {user_id} for schedule {schedule_id}"
                        )
                except discord.Forbidden:
                    self.logger.info(f"Could not DM waitlist offer to {user_id} (DMs closed)")
                except Exception as e:
                    self.logger.warning(f"Failed to send immediate waitlist offer to {user_id}: {e}")
        except Exception as e:
            self.logger.warning(f"check_waitlist_for_slot({schedule_id}): {e}")

    @commands.command(name='setwaitlistlog', help='[Beta] Set the channel for waitlist seat-filled notices: !setwaitlistlog #channel | off')
    @is_admin_or_trusted()
    async def set_waitlist_log(self, ctx, channel: str = None):
        """Configure where waitlist promotion notices (and trusted-role pings) post."""
        from models import SystemSettings
        gid = ctx.guild.id
        key = f"waitlist_log_channel_{gid}"
        try:
            if channel and channel.lower() in ('off', 'disable', 'clear'):
                async with AsyncDatabaseSession() as session:
                    row = session.query(SystemSettings).filter_by(setting_key=key).first()
                    if row:
                        session.delete(row)
                        session.commit()
                await ctx.send("✅ Waitlist log cleared. Promotion notices will fall back to the waitlist panel's channel.")
                return

            target = None
            if ctx.message.channel_mentions:
                target = ctx.message.channel_mentions[0]
            elif channel:
                try:
                    target = ctx.guild.get_channel(int(channel.strip('<#>')))
                except Exception:
                    target = None
            if target is None:
                await ctx.send("❌ Usage: `!setwaitlistlog #channel` or `!setwaitlistlog off`")
                return

            async with AsyncDatabaseSession() as session:
                row = session.query(SystemSettings).filter_by(setting_key=key).first()
                if row:
                    row.setting_value = str(target.id)
                else:
                    session.add(SystemSettings(setting_key=key, setting_value=str(target.id)))
                session.commit()
            await ctx.send(f"✅ Waitlist seat-filled notices will post to {target.mention} and ping the trusted role.")
        except Exception as e:
            self.logger.error(f"Error setting waitlist log channel: {e}")
            await ctx.send("❌ Error saving the waitlist log channel. Please try again.")

    async def cog_load(self):
        """Cog loaded - tasks will be started in on_ready after bot is ready."""
        self.logger.info("✅ Notification commands cog loaded - schedulers will start on bot ready")
    
    async def cog_unload(self):
        """Stop background tasks when the cog unloads."""
        self.notification_scheduler.cancel()
        
    @commands.Cog.listener()
    async def on_ready(self):
        """Start schedulers after bot is ready and check for missed notifications."""
        self.logger.info("🔄 Bot is ready - starting notification scheduler")
        
        await self.check_missed_notifications()
        
        if not self.notification_scheduler.is_running():
            self.notification_scheduler.start()
            self.logger.info("✅ Notification scheduler started in on_ready")
        else:
            self.logger.warning("⚠️ Notification scheduler already running (may be stuck in before_loop)")
    
    # on_reaction_add and on_reaction_remove intentionally removed.
    # on_raw_reaction_add/remove (below) handle ALL reactions (cached and uncached)
    # without double-firing. Having both sets caused duplicate "TRAIN RIDER READY!" messages.
    
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle reactions on uncached messages (messages not in bot's internal cache)."""
        if payload.user_id == self.bot.user.id:
            return  # Ignore bot's own reactions
        
        try:
            from utils.maintenance_manager import maintenance_manager
            if await maintenance_manager.get():
                return
            
            # Fetch the channel and message
            channel = self.bot.get_channel(payload.channel_id)
            if not channel:
                return
            
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                return
            except discord.Forbidden:
                return
            
            # Get the user
            user = self.bot.get_user(payload.user_id)
            if not user:
                try:
                    user = await self.bot.fetch_user(payload.user_id)
                except:
                    return
            
            # Get the reaction object
            reaction = discord.utils.get(message.reactions, emoji=payload.emoji.name)
            if not reaction:
                return
            
            # Process the reaction
            if await self.handle_backup_signup_reaction(reaction, user, True):
                return
            await self.handle_notification_reaction(reaction, user, True)
            
        except Exception as e:
            self.logger.error(f"Error handling raw reaction add: {e}")
    
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """Handle reaction removals on uncached messages."""
        if payload.user_id == self.bot.user.id:
            return
        
        try:
            from utils.maintenance_manager import maintenance_manager
            if await maintenance_manager.get():
                return
            
            # Fetch the channel and message
            channel = self.bot.get_channel(payload.channel_id)
            if not channel:
                return
            
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                return
            except discord.Forbidden:
                return
            
            # Get the user
            user = self.bot.get_user(payload.user_id)
            if not user:
                try:
                    user = await self.bot.fetch_user(payload.user_id)
                except:
                    return
            
            # Get the reaction object
            reaction = discord.utils.get(message.reactions, emoji=payload.emoji.name)
            if not reaction:
                # Create a mock reaction for removal handling
                reaction = type('Reaction', (), {'message': message, 'emoji': payload.emoji})()
            
            # Process the reaction removal
            if await self.handle_backup_signup_reaction(reaction, user, False):
                return
            await self.handle_notification_reaction(reaction, user, False)
            
        except Exception as e:
            self.logger.error(f"Error handling raw reaction remove: {e}")
    
    async def check_missed_notifications(self):
        """Check for notifications that should have been sent but were missed due to restart."""
        try:
            from utils.maintenance_manager import maintenance_manager
            if await maintenance_manager.get():
                self.logger.info("⏸️ Maintenance mode active - skipping missed notification check on startup")
                return

            async with AsyncDatabaseSession() as session:
                uk_tz = pytz.timezone('Europe/London')
                now = datetime.now(uk_tz)
                current_day = now.weekday()
                next_day = (current_day + 1) % 7
                
                self.logger.info(f"🔍 Checking for missed notifications on startup - Current time: {now.strftime('%I:%M %p %Z')}")
                
                current_day_schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.day_of_week == current_day,
                    TrainSchedule.is_active == True
                ).all()
                
                next_day_schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.day_of_week == next_day,
                    TrainSchedule.is_active == True
                ).all()
                next_day_early_utc = [s for s in next_day_schedules if s.start_time.hour < 5]
                all_schedules = current_day_schedules + next_day_early_utc

                # Filter out specific-date schedules that don't belong to today or tomorrow
                today_date = now.date()
                tomorrow_date = today_date + timedelta(days=1)
                def is_relevant_missed(schedule):
                    sd = getattr(schedule, 'specific_date', None)
                    if not sd:
                        return True  # Recurring — always relevant
                    if isinstance(sd, str):
                        from datetime import datetime as dt_class
                        sd = dt_class.strptime(sd, '%Y-%m-%d').date()
                    return sd == today_date or sd == tomorrow_date
                schedules = [s for s in all_schedules if is_relevant_missed(s)]

                missed_count = 0
                for schedule in schedules:
                    today_date = now.date()
                    tomorrow_date = today_date + timedelta(days=1)

                    # Use specific_date directly for one-time schedules
                    sd = getattr(schedule, 'specific_date', None)
                    if sd:
                        if isinstance(sd, str):
                            from datetime import datetime as dt_class
                            sd = dt_class.strptime(sd, '%Y-%m-%d').date()
                        schedule_date = sd
                        # Early-morning slots fire on the next calendar day
                        if schedule.start_time.hour < 5:
                            schedule_date = schedule_date + timedelta(days=1)
                    else:
                        days_until_schedule = (schedule.day_of_week - current_day) % 7
                        schedule_date = today_date + timedelta(days=days_until_schedule)

                    naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
                    start_uk = uk_tz.localize(naive_uk_datetime)
                    
                    notify_mins = getattr(schedule, 'notify_before_minutes', 60) or 60
                    notification_time = start_uk - timedelta(minutes=notify_mins)
                    
                    if now > notification_time and now < start_uk:
                        existing_notification = session.query(TrainNotification).filter(
                            TrainNotification.schedule_id == schedule.id,
                            TrainNotification.notification_date == now.date(),
                            TrainNotification.stage == 1
                        ).first()
                        
                        if not existing_notification:
                            sent = await self.send_missed_notification(schedule, start_uk, session)
                            if sent:
                                missed_count += 1
                                self.logger.info(f"📤 Sent missed notification for '{schedule.name}' (was scheduled for {notification_time.strftime('%I:%M %p %Z')})")
                
                if missed_count > 0:
                    self.logger.info(f"✅ Sent {missed_count} catch-up notifications for schedules active mid-window")
                else:
                    self.logger.debug("✅ No missed notifications to catch up on")
                
        except Exception as e:
            self.logger.error(f"Error checking missed notifications: {e}")
    
    async def send_missed_notification(self, schedule, original_time, session):
        """Send a notification that was missed due to restart."""
        try:
            from models import TrainParticipant
            # Get notification settings for this guild
            settings = session.query(NotificationSettings).filter_by(
                guild_id=schedule.guild_id
            ).first()
            
            if not settings or not settings.auto_ping_enabled:
                return False
                
            guild = self.bot.get_guild(schedule.guild_id)
            if not guild:
                return False
                
            channel = None
            if settings.raid_notification_channel_id:
                channel = guild.get_channel(settings.raid_notification_channel_id)
            
            if not channel:
                self.logger.warning(f"No raid notification channel set for guild {guild.name}")
                return False
            
            # Create notification record
            current_time = get_uk_time()
            notification = TrainNotification(
                schedule_id=schedule.id,
                guild_id=schedule.guild_id,
                notification_date=current_time.date(),
                stage=1,  # Missed notifications are stage 1
                channel_id=channel.id
            )
            
            session.add(notification)
            session.commit()  # Commit to get ID
            
            # Send the message with delay notice
            embed = discord.Embed(
                title='🚂 **DELAYED** - Raid Train Starting Soon!',
                description=f'**{schedule.name}** - ⚠️ *Notification delayed due to bot restart*',
                color=0xff6b35  # Orange color for delayed
            )
            
            original_time_str = original_time.strftime('%I:%M %p %Z') if hasattr(original_time, 'tzinfo') and original_time.tzinfo else original_time.strftime('%I:%M %p UK')
            current_time_str = current_time.strftime('%I:%M %p UK')
            
            embed.add_field(
                name='📅 Train Details',
                value=f'**Scheduled Time:** {original_time_str}\n**Delayed Until:** {current_time_str}\n**Duration:** {schedule.duration_minutes} minutes',
                inline=False
            )
            
            embed.add_field(
                name='📊 Status',
                value="⏰ **Primary:** Waiting for confirmation...",
                inline=False
            )
            
            embed.add_field(
                name='🎯 Instructions',
                value=f'{self.READY_EMOJI} = Ready to stream\n{self.NOT_READY_EMOJI} = Can\'t make it\n{self.BACKUP_EMOJI} = Need backup\n\n*Sorry for the delay - bot restarted during notification time.*',
                inline=False
            )
            
            embed.set_footer(text=f'Delayed Notification • {current_time_str}')
            
            # Send with participant ping - use ACTIVE participants from TrainParticipant table
            message_content = ""
            active_participant_ids = self.get_active_participant_ids(session, schedule.id)
            participant_count = len(active_participant_ids) if active_participant_ids else 0
            host_user_id = getattr(schedule, 'host_user_id', None)

            # Empty slot — skip entirely. Do not fall back to the host.
            if participant_count == 0:
                self.logger.info(f"⏭️  Skipping missed notification for '{schedule.name}' - slot has no active rider")
                session.delete(notification)
                session.commit()
                return False
            
            # Query full participant objects for name-based fallback when user_id=0
            active_parts = session.query(TrainParticipant).filter_by(
                schedule_id=schedule.id, is_active=True
            ).order_by(TrainParticipant.signed_up_at.asc()).all()
            if active_parts:
                first = active_parts[0]
                mention = self._resolve_participant_mention(first, guild)
                message_content = f"🎯 **TRAIN RIDER:** {mention}"
                self.logger.info(f"🔍 Delayed notification pinging active participant: {first.username} ({first.user_id})")
            elif host_user_id:
                # No signups yet — ping the host so the slot can still be claimed
                message_content = f"🎯 **TRAIN HOST:** <@{host_user_id}>"
                self.logger.info(f"📣 Delayed notification for '{schedule.name}' has no signups — pinging host {host_user_id}")
            
            message = await channel.send(content=message_content, embed=embed)
            await message.add_reaction(self.READY_EMOJI)
            await message.add_reaction(self.NOT_READY_EMOJI)
            await message.add_reaction(self.BACKUP_EMOJI)
            
            # Update notification record with message ID
            session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                'message_id': message.id,
                'is_sent': True,
                'sent_at': current_time
            })
            session.commit()
            
            self.logger.info(f"Sent delayed notification for '{schedule.name}' in guild {guild.name}")
            cache_key = f"{schedule.guild_id}_{schedule.id}_{current_time.date()}_1"
            self.sent_notifications_cache.add(cache_key)
            self.logger.info(f"🔒 Added to dedup cache: {cache_key}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error sending missed notification: {e}")
            return False
    
    async def delete_expired_trains(self):
        """Delete one-time train schedules that have passed their scheduled time + duration."""
        try:
            import pytz
            
            async with AsyncDatabaseSession() as session:
                current_time = get_uk_time()
                uk_tz = pytz.timezone('Europe/London')
                
                # Normalize current_time to be timezone-aware for comparison
                if current_time.tzinfo is None:
                    current_time = uk_tz.localize(current_time)
                else:
                    current_time = current_time.astimezone(uk_tz)
                
                current_date = current_time.date()
                current_day_of_week = current_date.weekday()  # Monday = 0, Sunday = 6
                
                # Only find schedules that are eligible for deletion:
                # 1. One-time schedules (schedule_type == 'one-time' OR specific_date is set)
                # Note: Remove is_active filter since one-time schedules may be marked inactive after processing
                schedules = session.query(TrainSchedule).filter(
                    # Only delete one-time schedules, not recurring ones
                    ((TrainSchedule.schedule_type == 'one-time') | 
                     (TrainSchedule.specific_date.isnot(None)))
                ).all()
                
                deleted_count = 0
                uk_tz = pytz.timezone('Europe/London')
                
                self.logger.info(f"🔍 Beginning expired-train cleanup at {current_time.strftime('%I:%M %p UK')}; candidates={len(schedules)}")
                
                for schedule in schedules:
                    # Use the specific_date if set, otherwise use current_date for same-day ad-hoc entries
                    schedule_date = schedule.specific_date if schedule.specific_date else current_date
                    
                    start_time_value = schedule.start_time  
                    duration_value = schedule.duration_minutes
                    start_naive_uk = datetime.combine(schedule_date, start_time_value)
                    start_uk = uk_tz.localize(start_naive_uk)
                    end_est = start_uk + timedelta(minutes=duration_value)
                    
                    self.logger.info(f"🔍 Checking train '{schedule.name}' (ID: {schedule.id}): scheduled {start_uk.strftime('%I:%M %p UK')} - {end_est.strftime('%I:%M %p UK')}, current: {current_time.strftime('%I:%M %p UK')}, is_active: {schedule.is_active}")
                    
                    # If the current time is past the end time, delete the schedule
                    if current_time >= end_est:
                        # Ensure we never delete recurring schedules (safety check)
                        if schedule.schedule_type == 'recurring' and schedule.specific_date is None:
                            self.logger.warning(f"⚠️ Skipping deletion of recurring schedule: '{schedule.name}' (safety check)")
                            continue
                        
                        # Also delete related notifications for this schedule
                        session.query(TrainNotification).filter_by(schedule_id=schedule.id).delete()
                        
                        # Delete the schedule itself
                        session.delete(schedule)
                        deleted_count += 1
                        
                        self.logger.info(f"🗑️ Deleted expired train: '{schedule.name}' (ended at {end_est.strftime('%I:%M %p UK')})")
                
                if deleted_count > 0:
                    session.commit()
                    self.logger.info(f"✅ Deleted {deleted_count} expired one-time train schedule(s)")
                else:
                    self.logger.debug("🔍 No expired one-time trains to delete")
                
        except Exception as e:
            self.logger.error(f"Error deleting expired trains: {e}")
    
    async def handle_backup_signup_reaction(self, reaction, user, added):
        """Handle reactions on backup signup messages."""
        try:
            async with AsyncDatabaseSession() as session:
                # Get guild settings
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=reaction.message.guild.id
                ).first()
                
                # Check if this message is configured for backup signup
                if not settings or not settings.backup_signup_message_id:
                    return False  # Not a backup signup message
                
                if reaction.message.id != settings.backup_signup_message_id:
                    return False  # Not the configured backup signup message
                
                # Check if the emoji matches
                emoji_str = str(reaction.emoji)
                if emoji_str != settings.backup_signup_emoji:
                    return False  # Wrong emoji
                
                # Get the backup role
                if not settings.backup_ping_role_id:
                    self.logger.warning(f"Backup signup message configured but no backup role set for guild {reaction.message.guild.id}")
                    return True  # Handled but couldn't process
                
                backup_role = reaction.message.guild.get_role(settings.backup_ping_role_id)
                if not backup_role:
                    self.logger.warning(f"Backup role {settings.backup_ping_role_id} not found in guild {reaction.message.guild.id}")
                    return True  # Handled but couldn't process
                
                # Resolve to a Member so we can manage roles (User objects have no .roles/.add_roles)
                member = reaction.message.guild.get_member(user.id)
                if not member:
                    try:
                        member = await reaction.message.guild.fetch_member(user.id)
                    except Exception:
                        self.logger.warning(f"Could not resolve member {user.id} in guild {reaction.message.guild.id}")
                        return True
                
                # Handle role assignment/removal
                if added:
                    # Check if user is banned from trains
                    banned = session.query(BannedUser).filter(
                        BannedUser.user_id == user.id,
                        BannedUser.is_active == True
                    ).first()
                    
                    if banned:
                        # Send ban notification and remove reaction
                        try:
                            reason_text = f"\n**Reason:** {banned.reason}" if banned.reason else ""
                            embed = discord.Embed(
                                title="🚫 You are banned from trains",
                                description=f"You cannot become a backup streamer because you are banned from joining trains.{reason_text}\n\nPlease contact a server administrator for more information.",
                                color=discord.Color.red()
                            )
                            await user.send(embed=embed)
                            self.logger.info(f"🚫 Banned user {user.id} attempted to become backup via reaction")
                        except discord.Forbidden:
                            self.logger.warning(f"Could not DM banned user {user.id}")
                        except Exception as e:
                            self.logger.error(f"Error sending ban notification: {e}")
                        
                        # Remove their reaction
                        try:
                            await reaction.remove(member)
                        except:
                            pass  # Ignore if we can't remove the reaction
                        
                        return True  # Handled (blocked)
                    
                    # Add backup role
                    if backup_role not in member.roles:
                        await member.add_roles(backup_role, reason="Backup signup via reaction")
                        self.logger.info(f"Added backup role to {user.name} via reaction in {reaction.message.guild.name}")
                        
                        # Check if user has Twitch linked
                        from models import User as DBUser
                        db_user = session.query(DBUser).filter_by(id=user.id).first()
                        has_twitch = db_user and db_user.twitch_login
                        
                        # Send confirmation DM with Twitch linking info
                        try:
                            if has_twitch:
                                # User already has Twitch linked
                                embed = discord.Embed(
                                    title="✅ Welcome Backup Streamer!",
                                    description=f"You now have the **{backup_role.name}** role in **{reaction.message.guild.name}**!\n\nYou'll be notified when backup streamers are needed.",
                                    color=0x00ff00
                                )
                                embed.add_field(
                                    name="🎮 Your Linked Twitch Account",
                                    value=f"`{db_user.twitch_username}`",
                                    inline=False
                                )
                            else:
                                # User doesn't have Twitch linked - prompt them to link it
                                embed = discord.Embed(
                                    title="✅ Welcome Backup Streamer & 🔗 Link Your Twitch!",
                                    description=f"You now have the **{backup_role.name}** role in **{reaction.message.guild.name}**!\n\nTo get the most out of our raid train system, consider linking your Twitch account for automatic attendance tracking.",
                                    color=0x00ff00
                                )
                                embed.add_field(
                                    name="🎮 Why Link Your Twitch?",
                                    value="• Automatic attendance tracking during trains\n• Show up in train rosters with your Twitch name\n• Get credit for participating in raids",
                                    inline=False
                                )
                                embed.add_field(
                                    name="🔗 How to Link Your Twitch Account",
                                    value=f"**Self-Service:** Use `/linktwitch YourTwitchUsername` to link your account instantly!\n\nOr ask an admin for assistance.",
                                    inline=False
                                )
                                embed.add_field(
                                    name="🔒 Privacy & Security",
                                    value="Your Twitch username will only be used for train participation tracking. You can request unlinking at any time.",
                                    inline=False
                                )
                            
                            await user.send(embed=embed)
                            self.logger.info(f"Sent backup confirmation DM to {user.name} (Twitch linked: {has_twitch})")
                        except discord.Forbidden:
                            self.logger.warning(f"Could not send DM to {user.name} - DMs disabled")
                        except Exception as e:
                            self.logger.error(f"Error sending backup confirmation DM: {e}")
                            
                else:
                    # Remove backup role (if configured to do so)
                    if settings.backup_remove_on_unreact and backup_role in member.roles:
                        await member.remove_roles(backup_role, reason="Removed backup signup reaction")
                        self.logger.info(f"Removed backup role from {user.name} via reaction removal in {reaction.message.guild.name}")
                        
                        # Send removal DM
                        try:
                            embed = discord.Embed(
                                title="🔄 Backup Role Removed",
                                description=f"Your **{backup_role.name}** role has been removed from **{reaction.message.guild.name}**.\n\nReact again if you want to rejoin!",
                                color=0xff9900
                            )
                            await user.send(embed=embed)
                        except:
                            pass  # DMs disabled, that's okay
                
                return True  # Successfully handled
                
        except Exception as e:
            self.logger.error(f"Error handling backup signup reaction: {e}")
            return False
    
    def _get_week_offset(self, schedule):
        """Extract week offset from schedule description if present."""
        import re
        if schedule.description:
            match = re.search(r'\[WEEK_OFFSET:(\d+)\]', schedule.description)
            if match:
                return int(match.group(1))
        return 0
    
    async def _check_start_chat_monitoring(self, schedule, now):
        """Check if we should start Twitch chat monitoring for this train session."""
        try:
            # Start monitoring if train is currently active (has started but not finished)
            uk_tz = pytz.timezone('Europe/London')
            today_date = now.date()
            
            # Calculate the correct date for this schedule (handle next-day trains and timezone shifts)
            current_day = now.weekday()  # 0=Monday, 6=Sunday
            schedule_day = schedule.day_of_week  # 0=Monday, 6=Sunday
            specific_date = getattr(schedule, 'specific_date', None)
            if specific_date:
                # One-time schedule: use its actual date, never weekday math
                if isinstance(specific_date, str):
                    from datetime import datetime as dt_class
                    specific_date = dt_class.strptime(specific_date, '%Y-%m-%d').date()
                schedule_date = specific_date
                # Early-morning slots fire on the next calendar day
                if schedule.start_time.hour < 5:
                    schedule_date = schedule_date + timedelta(days=1)
            else:
                days_until_schedule = (schedule_day - current_day) % 7
                schedule_date = today_date + timedelta(days=days_until_schedule)
                # Apply week offset if schedule is for future weeks
                week_offset = self._get_week_offset(schedule)
                if week_offset > 0:
                    schedule_date = schedule_date + timedelta(weeks=week_offset)
            
            # Localize stored UK wall clock time against actual schedule date (handles DST automatically)
            naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
            start_datetime = uk_tz.localize(naive_uk_datetime)
            
            # If the converted time is in the past, check if we need next week's occurrence
            # BUT: if train started recently (within duration window), keep current occurrence
            time_since_start = (now - start_datetime).total_seconds()
            duration_seconds = schedule.duration_minutes * 60
            within_active_window = 0 <= time_since_start <= duration_seconds
            
            if start_datetime < now and not within_active_window:
                if specific_date:
                    # One-time train fully in the past — never roll forward to next week
                    return
                days_until = (schedule_day - current_day) % 7
                # If we're 1 day before the scheduled day and it's in the past, check for timezone shift
                if start_datetime.weekday() < schedule_day and days_until == 1:
                    # This is a tomorrow train stored in early UTC - keep tomorrow's date
                    schedule_date = schedule_date  # Keep tomorrow
                else:
                    # Regular past event - move to next week
                    schedule_date = schedule_date + timedelta(days=7)
                
                naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
                start_datetime = uk_tz.localize(naive_uk_datetime)
                time_since_start = (now - start_datetime).total_seconds()
                within_active_window = 0 <= time_since_start <= duration_seconds
            
            # Start monitoring if train is active (started and not finished)
            if within_active_window:
                # Check if train has participants signed up and if host is excluded from attendance
                async with AsyncDatabaseSession() as session:
                    # Check if host user is excluded from attendance tracking
                    from models import User
                    host_user = session.query(User).filter_by(id=schedule.host_user_id).first()
                    if host_user and host_user.exclude_from_attendance:
                        self.logger.debug(f"⏭️ Skipping monitoring for {schedule.name} - host user excluded from attendance")
                        return
                    
                    # Check if train has participants signed up. With zero signups we
                    # still monitor when the schedule has a host so attendance/auto-raid
                    # for the host's stream is not silently skipped.
                    participant_count = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule.id,
                        is_active=True
                    ).count()
                    
                    if participant_count == 0 and not schedule.host_user_id:
                        self.logger.debug(f"⏭️ Skipping monitoring for {schedule.name} - no participants signed up and no host set")
                        return
                    if participant_count == 0:
                        self.logger.info(f"📣 Monitoring {schedule.name} with no signups — tracking host's stream")
                
                # Check if monitoring is already running for this train
                if hasattr(self.bot, 'twitch_chat_monitor'):
                    task_key = f"train_{schedule.id}"
                    already_active = task_key in self.bot.twitch_chat_monitor.active_monitors

                    # Cooldown guard — even if the task exited early, don't restart within 10 min
                    last_start = self._monitoring_cooldowns.get(schedule.id)
                    cooldown_active = (
                        last_start is not None and
                        (now - last_start).total_seconds() < 600  # 10 minutes
                    )

                    if already_active:
                        self.logger.debug(f"✅ Twitch monitoring already active for {schedule.name}")
                    elif cooldown_active:
                        self.logger.debug(f"⏳ Monitoring cooldown active for {schedule.name} — skipping restart")
                    else:
                        remaining_minutes = max(1, int((duration_seconds - time_since_start) / 60))
                        self.logger.info(f"🎯 Starting Twitch chat monitoring for {schedule.name} (remaining: {remaining_minutes} min)")
                        self._monitoring_cooldowns[schedule.id] = now
                        await self.bot.twitch_chat_monitor.start_train_monitoring(schedule.id)
                else:
                    self.logger.warning("Twitch chat monitor not available")
                    
        except Exception as e:
            self.logger.error(f"Error checking chat monitoring for schedule {schedule.id}: {e}")
    
    async def notify_host_of_readiness(self, schedule, notification, user, session):
        """Notify the train host that a participant has marked themselves ready."""
        try:
            from models import TrainParticipantReady, Guild
            
            # Check if we've already notified the host for this participant in this stage
            existing_ready = session.query(TrainParticipantReady).filter_by(
                notification_id=notification.id,
                user_id=user.id
            ).first()
            
            if existing_ready and existing_ready.host_notified:
                # Already notified, don't spam
                return
            
            # Get guild info for server description
            guild_record = session.query(Guild).filter_by(id=schedule.guild_id).first()
            server_description = guild_record.server_description if guild_record and guild_record.server_description else "your server"
            
            # Format the train time
            from datetime import datetime
            import pytz
            uk_tz = pytz.timezone('Europe/London')
            _sd_r = schedule.specific_date
            if _sd_r and isinstance(_sd_r, str):
                from datetime import datetime as _dtcr
                _sd_r = _dtcr.strptime(_sd_r, '%Y-%m-%d').date()
            _now_uk_r = datetime.now(uk_tz)
            _disp_r = _sd_r if _sd_r else (_now_uk_r.date() + timedelta(days=(schedule.day_of_week - _now_uk_r.weekday()) % 7))
            naive_time = datetime.combine(_disp_r, schedule.start_time)
            uk_time = uk_tz.localize(naive_time)
            start_time_formatted = uk_time.strftime('%I:%M %p %Z')
            start_date_formatted = uk_time.strftime('%A, %B %d, %Y')
            
            # Track readiness in database
            if not existing_ready:
                ready_record = TrainParticipantReady(
                    notification_id=notification.id,
                    schedule_id=schedule.id,
                    user_id=user.id,
                    username=user.name,
                    guild_id=schedule.guild_id,
                    notification_stage=notification.stage
                )
                session.add(ready_record)
            else:
                existing_ready.marked_ready_at = get_uk_time()
            
            # Get the host user ID
            host_user_id = schedule.host_user_id
            if not host_user_id:
                # No host assigned, skip notification
                return
            
            # Try to get the host user
            host_user = self.bot.get_user(host_user_id)
            if not host_user:
                try:
                    host_user = await self.bot.fetch_user(host_user_id)
                except:
                    self.logger.warning(f"Could not fetch host user {host_user_id} for notification")
                    return
            
            # Build notification message
            embed = discord.Embed(
                title="🎯 Participant Ready!",
                description=f"**{user.name}** has marked themselves ready for your train!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📍 Server",
                value=server_description,
                inline=False
            )
            
            embed.add_field(
                name="🚂 Train",
                value=schedule.name,
                inline=True
            )
            
            embed.add_field(
                name="📅 Date",
                value=start_date_formatted,
                inline=True
            )
            
            embed.add_field(
                name="🕐 Time",
                value=start_time_formatted,
                inline=True
            )
            
            embed.add_field(
                name="👤 Ready Participant",
                value=f"{user.mention} ({user.name})",
                inline=False
            )
            
            embed.set_footer(text=f"Notification Stage {notification.stage}")
            
            # Send DM to host
            try:
                await host_user.send(embed=embed)
                
                # Mark as notified
                if existing_ready:
                    existing_ready.host_notified = True
                    existing_ready.host_notified_at = get_uk_time()
                else:
                    ready_record.host_notified = True
                    ready_record.host_notified_at = get_uk_time()
                
                session.commit()
                self.logger.info(f"✅ Notified host {host_user.name} that {user.name} is ready for {schedule.name}")
                
            except discord.Forbidden:
                self.logger.warning(f"Could not DM host {host_user.name} - DMs disabled")
            except Exception as e:
                self.logger.error(f"Error sending DM to host: {e}")
                
        except Exception as e:
            self.logger.error(f"Error in notify_host_of_readiness: {e}")
    
    async def handle_notification_reaction(self, reaction, user, added):
        """Process notification reactions for readiness confirmation."""
        try:
            try:
                await asyncio.wait_for(self._handle_notification_reaction_inner(reaction, user, added), timeout=15.0)
            except asyncio.TimeoutError:
                self.logger.error(f"❌ TIMEOUT: Reaction handler took longer than 15 seconds for {user.name} - preventing bot freeze")
                return
        except Exception as e:
            self.logger.error(f"Error handling notification reaction: {e}")
    
    async def _handle_notification_reaction_inner(self, reaction, user, added):
        """Inner reaction handler with database operations."""
        try:
            async with AsyncDatabaseSession() as session:
                notification = session.query(TrainNotification).filter_by(
                    message_id=reaction.message.id
                ).first()
                
                if not notification:
                    return
                
                schedule = session.query(TrainSchedule).filter_by(id=notification.schedule_id).first()
                if not schedule:
                    return
                
                active_participant_ids = self.get_active_participant_ids(session, schedule.id)
                is_assigned_participant = str(user.id) in active_participant_ids
                
                self.logger.info(f"Reaction from {user.name} (ID: {user.id}) on {schedule.name}")
                self.logger.info(f"🔍 Active participants from DB: {active_participant_ids}")
                self.logger.info(f"Is assigned: {is_assigned_participant}")
                self.logger.info(f"Reaction emoji: {str(reaction.emoji)}")
                self.logger.info(f"Expected emojis: {self.READY_EMOJI}, {self.NOT_READY_EMOJI}, {self.BACKUP_EMOJI}")
                
                # Check reaction permissions based on notification stage
                # Stage 1: Only assigned participants can react (unless no one is assigned, then anyone can claim it)
                # Stage 2+: Anyone can react (backup system)
                has_assigned_participants = len(active_participant_ids) > 0
                if notification.stage == 1 and not is_assigned_participant and has_assigned_participants and added:
                    try:
                        await reaction.remove(user)
                        
                        # Send a message to the user explaining they can't react to initial ping
                        guild = self.bot.get_guild(notification.guild_id)
                        channel = guild.get_channel(notification.channel_id) if guild else None
                        
                        if channel:
                            await channel.send(
                                f"❌ {user.mention}, only the assigned rider can react to the initial notification. "
                                f"Wait for the backup ping if you want to volunteer as backup.",
                                delete_after=15
                            )
                        
                        self.logger.info(f"Removed unauthorized reaction from {user.name} on stage 1 notification (only assigned rider allowed)")
                        return
                        
                    except discord.errors.NotFound:
                        # Reaction was already removed or message was deleted
                        pass
                    except discord.errors.Forbidden:
                        # Bot doesn't have permission to remove reactions
                        guild = self.bot.get_guild(notification.guild_id)
                        if guild and guild.get_channel(notification.channel_id):
                            channel = guild.get_channel(notification.channel_id)
                            await channel.send(
                                f"❌ {user.mention}, only the assigned rider can react to the initial notification.",
                                delete_after=10
                            )
                        return
                    
                emoji = str(reaction.emoji)
                
                # Handle ready emoji
                if emoji == self.READY_EMOJI and added:
                    # Dedup guard: if already confirmed by this user, skip silently (prevents double-fire from raw+normal handlers)
                    if notification.primary_confirmed and notification.primary_user_id == user.id:
                        self.logger.debug(f"Ignoring duplicate ✅ reaction from {user.name} - already confirmed")
                        return
                    # For stage 1: Only assigned participant reaction OR anyone can claim empty slot
                    # For stage 2+: Anyone can react as backup
                    if notification.stage == 1 and is_assigned_participant:
                        # Primary assigned rider confirming
                        session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                            'primary_confirmed': True,
                            'primary_user_id': user.id
                        })
                        self.logger.info(f"Primary rider {user.name} confirmed ready for schedule {notification.schedule_id}")
                        
                        # Check if backup ping was already sent (late confirmation scenario)
                        backup_was_pinged = notification.backup_pinged
                        
                        # Send appropriate confirmation message
                        guild = self.bot.get_guild(notification.guild_id)
                        channel = guild.get_channel(notification.channel_id) if guild else None
                        if channel:
                            if backup_was_pinged:
                                # Late confirmation - original streamer responded after backup ping
                                await channel.send(
                                    f"🎯 **ORIGINAL RIDER NOW READY!** ✅\n{user.mention} (assigned rider) has confirmed they're **ready** for **{schedule.name}**! 🚂\n*Backup volunteers are no longer needed - thank you for standing by!*",
                                    delete_after=30
                                )
                                self.logger.info(f"Late confirmation: {user.name} confirmed after backup ping was sent for {schedule.name}")
                                
                                # Send notification to backup volunteers if any confirmed
                                await self.notify_backup_no_longer_needed(notification, schedule, session, user)
                            else:
                                # Normal confirmation - original streamer responded on time
                                await channel.send(
                                    f"🎯 **TRAIN RIDER READY!** ✅\n{user.mention} (assigned rider) has confirmed they're **ready** for **{schedule.name}**! 🚂\n*All set for the train departure!*",
                                    delete_after=20
                                )
                        
                        # Notify host that participant is ready
                        await self.notify_host_of_readiness(schedule, notification, user, session)
                    
                    elif notification.stage == 1 and not is_assigned_participant and not has_assigned_participants:
                        # SAFETY CHECK: Double-confirm this slot is truly empty before allowing claim (check TrainParticipant table)
                        current_active_participants = self.get_active_participant_ids(session, schedule.id)
                        current_has_participants = len(current_active_participants) > 0
                        
                        if current_has_participants:
                            # SECURITY: Slot was claimed by someone else between checks - reject this claim
                            try:
                                await reaction.remove(user)
                                guild = self.bot.get_guild(notification.guild_id)
                                channel = guild.get_channel(notification.channel_id) if guild else None
                                if channel:
                                    await channel.send(
                                        f"❌ {user.mention}, this slot was just claimed by someone else. Only the assigned rider can now react to this ping.",
                                        delete_after=15
                                    )
                                self.logger.warning(f"SECURITY: Prevented slot steal attempt by {user.name} - slot {schedule.name} was claimed between checks")
                                return
                            except:
                                pass
                        
                        # Stage 1 with no assigned participants - anyone can claim the empty slot
                        session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                            'primary_confirmed': True,
                            'primary_user_id': user.id
                        })
                        
                        # Assign this person to the schedule
                        session.query(TrainSchedule).filter(TrainSchedule.id == schedule.id).update({
                            'participant_ids': [str(user.id)]  # Assign them as the primary rider
                        })
                        
                        # CRITICAL: Commit changes to database so they persist!
                        session.commit()
                        
                        self.logger.info(f"User {user.name} claimed empty slot for schedule {notification.schedule_id}")
                        
                        # Send confirmation message
                        guild = self.bot.get_guild(notification.guild_id)
                        channel = guild.get_channel(notification.channel_id) if guild else None
                        if channel:
                            await channel.send(
                                f"🎯 **SLOT CLAIMED!** ✅\n{user.mention} has claimed the open slot for **{schedule.name}**! 🚂\n*Welcome aboard the train!*",
                                delete_after=20
                            )
                        
                        # Notify host that participant claimed the slot
                        await self.notify_host_of_readiness(schedule, notification, user, session)
                    
                    elif notification.stage == 1 and not is_assigned_participant and has_assigned_participants:
                        # SECURITY: This should already be blocked above, but adding extra safety
                        self.logger.warning(f"SECURITY: Blocked unauthorized Stage 1 reaction from {user.name} on {schedule.name} (has assigned participants)")
                        try:
                            await reaction.remove(user)
                            guild = self.bot.get_guild(notification.guild_id)
                            channel = guild.get_channel(notification.channel_id) if guild else None
                            if channel:
                                await channel.send(
                                    f"❌ {user.mention}, only the assigned rider can react to this notification. Wait for backup pings if needed.",
                                    delete_after=15
                                )
                        except:
                            pass
                        return
                    
                    elif notification.stage >= 2:
                        # Backup stage: Prioritize assigned participant, backups wait unless primary doesn't respond
                        if not notification.primary_confirmed:
                            # Check if this person is the originally assigned participant
                            if is_assigned_participant:
                                # Assigned participant is confirming - they get priority
                                session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                                    'primary_confirmed': True,
                                    'primary_user_id': user.id
                                })
                                session.commit()
                                
                                self.logger.info(f"Assigned rider {user.name} confirmed for schedule {notification.schedule_id}")
                                
                                # Send confirmation message
                                guild = self.bot.get_guild(notification.guild_id)
                                channel = guild.get_channel(notification.channel_id) if guild else None
                                if channel:
                                    await channel.send(
                                        f"✅ **PRIMARY RIDER CONFIRMED!**\n{user.mention} (assigned rider) is **ready** for **{schedule.name}**! 🚂\n*Great to see you're ready!*",
                                        delete_after=30
                                    )
                                    
                                    # If there's a backup waiting, notify them they're no longer needed as primary
                                    if notification.backup_confirmed and notification.backup_user_id:
                                        backup_user = guild.get_member(notification.backup_user_id)
                                        if backup_user:
                                            await channel.send(
                                                f"📢 **UPDATE** {backup_user.mention}\nThe assigned rider has confirmed! You're still marked as backup in case they need help. Thanks for volunteering! 🙏",
                                                delete_after=30
                                            )
                                
                                # Notify host that assigned participant is ready (stage 2+)
                                await self.notify_host_of_readiness(schedule, notification, user, session)
                            else:
                                # This is a backup volunteer - they DON'T take over, just mark as backup ready
                                session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                                    'backup_confirmed': True,
                                    'backup_user_id': user.id,
                                    'backup_pinged': True  # FIX: Mark as pinged so auto-assignment works
                                })
                                session.commit()
                                
                                self.logger.info(f"Backup rider {user.name} confirmed as backup for schedule {notification.schedule_id} (ready for auto-assignment)")
                                
                                # Send backup confirmation - let them know they're waiting for assigned rider
                                guild = self.bot.get_guild(notification.guild_id)
                                channel = guild.get_channel(notification.channel_id) if guild else None
                                if channel:
                                    # Get assigned rider info for message
                                    original_rider_id = None
                                    if schedule.participant_ids:
                                        original_rider_id = schedule.participant_ids[0] if schedule.participant_ids else None
                                    
                                    await channel.send(
                                        f"🔄 **BACKUP RIDER STANDING BY!** ✅\n{user.mention} is ready as **backup** for **{schedule.name}**! 🚂\n*Waiting to see if assigned rider responds. You'll take over if they don't confirm.*",
                                        delete_after=30
                                    )
                                    
                                    # Notify the assigned rider that backup is ready and waiting
                                    if original_rider_id and original_rider_id.strip():
                                        try:
                                            original_rider_id_clean = original_rider_id.strip()
                                            await channel.send(
                                                f"📢 **RIDER ALERT** <@{original_rider_id_clean}>\n"
                                                f"A backup is ready for **{schedule.name}**! 🚂\n"
                                                f"{user.mention} has volunteered. Please confirm if you're still able to stream!\n"
                                                f"*React with ✅ to keep your slot, or ❌ if you need the backup to take over.*",
                                                delete_after=60
                                            )
                                            self.logger.info(f"Notified assigned rider {original_rider_id_clean} that {user.name} is standing by as backup for {schedule.name}")
                                        except Exception as e:
                                            self.logger.error(f"Failed to notify assigned rider {original_rider_id}: {e}")
                        else:
                            # Primary already confirmed, this becomes additional backup
                            # Only update if this isn't already the backup
                            if not notification.backup_user_id or notification.backup_user_id != user.id:
                                session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                                    'backup_confirmed': True,
                                    'backup_user_id': user.id
                                })
                                session.commit()
                            
                            self.logger.info(f"Additional backup rider {user.name} confirmed for schedule {notification.schedule_id}")
                            
                            # Send backup confirmation message
                            guild = self.bot.get_guild(notification.guild_id)
                            channel = guild.get_channel(notification.channel_id) if guild else None
                            if channel:
                                await channel.send(
                                    f"🔄 **BACKUP RIDER READY!** ✅\n{user.mention} (backup rider) has confirmed they're **ready** for **{schedule.name}**! 🚂\n*Backup is standing by!*",
                                    delete_after=20
                                )

                    # Record attendance
                    from models import TrainAttendance
                    attendance = TrainAttendance(
                        notification_id=notification.id,
                        user_id=user.id,
                        status='ready'
                    )
                    session.add(attendance)
                    session.commit()
                    
                    # Refresh notification object after database update
                    session.refresh(notification)
                    
                    # Update message to show confirmation
                    await self.update_notification_message(notification)

                # Handle not ready emoji
                elif emoji == self.NOT_READY_EMOJI and added:
                    # Record attendance
                    from models import TrainAttendance
                    attendance = TrainAttendance(
                        notification_id=notification.id,
                        user_id=user.id,
                        status='not_ready'
                    )
                    session.add(attendance)
                    session.commit()
                    
                    # If this is the assigned participant saying they're not ready, promote backup to primary
                    if is_assigned_participant and notification.backup_confirmed and notification.backup_user_id:
                        backup_user = guild.get_member(notification.backup_user_id) if guild else None
                        
                        # Promote backup to primary
                        session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                            'primary_confirmed': True,
                            'primary_user_id': notification.backup_user_id
                        })
                        
                        # Update schedule to assign backup as primary
                        session.query(TrainSchedule).filter(TrainSchedule.id == schedule.id).update({
                            'participant_ids': [str(notification.backup_user_id)]
                        })
                        session.commit()
                        
                        self.logger.info(f"Assigned rider {user.name} declined - backup {notification.backup_user_id} promoted to primary for {schedule.name}")
                        
                        # Send messages
                        guild = self.bot.get_guild(notification.guild_id)
                        channel = guild.get_channel(notification.channel_id) if guild else None
                        if channel:
                            await channel.send(
                                f"📝 **RIDER UPDATE** ❌\n{user.mention} (assigned rider) has indicated they're **not ready** for **{schedule.name}**.\nThanks for letting us know!",
                                delete_after=20
                            )
                            
                            if backup_user:
                                await channel.send(
                                    f"🎯 **BACKUP PROMOTED!** ✅\n{backup_user.mention} you're now the **PRIMARY RIDER** for **{schedule.name}**! 🚂\n*The assigned rider declined, so you're taking over. Thanks for stepping up!*",
                                    delete_after=30
                                )
                    else:
                        # Send confirmation message
                        guild = self.bot.get_guild(notification.guild_id)
                        channel = guild.get_channel(notification.channel_id) if guild else None
                        if channel:
                            await channel.send(
                                f"📝 **RIDER UPDATE** ❌\n{user.mention} has indicated they're **not ready** for **{schedule.name}**.\nThanks for letting us know! We'll work on backup coverage. 🔄",
                                delete_after=20
                            )

                # Handle backup needed emoji
                elif emoji == self.BACKUP_EMOJI and added:
                    # Record attendance
                    from models import TrainAttendance
                    attendance = TrainAttendance(
                        notification_id=notification.id,
                        user_id=user.id,
                        status='backup_needed'
                    )
                    session.add(attendance)
                    session.commit()
                    
                    # Send confirmation message
                    guild = self.bot.get_guild(notification.guild_id)
                    channel = guild.get_channel(notification.channel_id) if guild else None
                    if channel:
                        await channel.send(
                            f"🔄 {user.mention} has requested a **backup** for **{schedule.name}**! We'll find someone to help! 🆘",
                            delete_after=15
                        )
                    
                # Handle not ready emoji removal (re-enable availability)
                elif emoji == self.READY_EMOJI and not added:
                    if notification.primary_user_id == user.id:
                        session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                            'primary_confirmed': False,
                            'primary_user_id': None
                        })
                        self.logger.info(f"Primary streamer {user.name} removed ready status for schedule {notification.schedule_id}")
                    elif notification.backup_user_id == user.id:
                        session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                            'backup_confirmed': False,
                            'backup_user_id': None
                        })
                        self.logger.info(f"Backup streamer {user.name} removed ready status for schedule {notification.schedule_id}")
                        
                    session.commit()
                    
                    # Update message to show change
                    await self.update_notification_message(notification)
                    
        except Exception as e:
            self.logger.error(f"Error processing notification reaction: {e}")
    
    async def update_notification_message(self, notification):
        """Update the notification message to show current status."""
        try:
            async with AsyncDatabaseSession() as session:
                # Refresh the notification object from database to get latest state
                notification = session.query(TrainNotification).filter_by(id=notification.id).first()
                if not notification:
                    return
                schedule = notification.schedule
                
                guild = self.bot.get_guild(notification.guild_id)
                if not guild:
                    return
                    
                channel = guild.get_channel(notification.channel_id)
                if not channel:
                    return
                    
                try:
                    message = await channel.fetch_message(notification.message_id)
                except:
                    return
                    
                # Create updated embed
                embed = discord.Embed(
                    title='🚂 Raid Train Starting Soon!',
                    description=f'**{schedule.name}** notification',
                    color=0x00ff00 if notification.primary_confirmed else 0x9146ff
                )
                
                # Add status information
                status_lines = []
                if notification.primary_confirmed:
                    primary_user = guild.get_member(notification.primary_user_id)
                    user_name = primary_user.display_name if primary_user else "Unknown"
                    status_lines.append(f"✅ **Primary:** {user_name} is ready!")
                else:
                    status_lines.append("⏰ **Primary:** Waiting for confirmation...")
                    
                if notification.backup_pinged:
                    if notification.backup_confirmed:
                        backup_user = guild.get_member(notification.backup_user_id)
                        user_name = backup_user.display_name if backup_user else "Unknown"
                        status_lines.append(f"✅ **Backup:** {user_name} is ready!")
                    else:
                        status_lines.append("🔄 **Backup:** Waiting for backup confirmation...")
                
                embed.add_field(
                    name='📊 Status',
                    value='\n'.join(status_lines),
                    inline=False
                )
                
                # Add time information based on stage
                time_info = self.get_time_info_for_stage(notification.stage)
                embed.add_field(
                    name='⏰ Timing',
                    value=time_info,
                    inline=False
                )
                
                embed.set_footer(text=f'{self.READY_EMOJI} Ready | {self.NOT_READY_EMOJI} Can\'t make it | {self.BACKUP_EMOJI} Need backup')
                
                await message.edit(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error updating notification message: {e}")
    
    def get_time_info_for_stage(self, stage):
        """Get appropriate time information for notification stage."""
        if stage == 1:
            return "Train starts in 1 hour"
        elif stage == 2:
            return "Train starts in 30 minutes"
        elif stage == 3:
            return "Train starts in 20 minutes - FINAL CALL"
        return "Train starting soon"
        
    async def check_and_send_notifications(self):
        """Check for upcoming trains and send multi-stage notifications."""
        self.logger.info("🔍 Entering check_and_send_notifications...")
        async with self.processing_lock:  # Prevent concurrent execution
            try:
                self.logger.info("🔓 Lock acquired, starting notification check...")
                # Add timeout protection to prevent database operations from hanging
                try:
                    await asyncio.wait_for(self._check_and_send_notifications_inner(), timeout=90.0)
                    self.logger.info("✅ Notification check completed successfully")
                except asyncio.TimeoutError:
                    self.logger.error("❌ TIMEOUT: Notification check took longer than 90 seconds - preventing health monitor crash")
                    # Update heartbeat even if database operations timed out
                    from utils.bot_monitor import update_heartbeat
                    update_heartbeat()
                    return
                    
            except Exception as e:
                self.logger.exception(f"Error in notification check: {e}")
                # Always update heartbeat to prevent crashes
                from utils.bot_monitor import update_heartbeat
                update_heartbeat()
                
    async def _check_and_send_notifications_inner(self):
        """Inner notification check method with database operations."""
        async with AsyncDatabaseSession() as session:
            uk_tz = pytz.timezone('Europe/London')
            now = datetime.now(uk_tz)
            current_day = now.weekday()  # 0=Monday
            
            self.logger.info(f"🕐 Notification check at {now.strftime('%A %I:%M %p %Z')} - checking day {current_day}")
            
            # Get schedules that match current UK day
            # Need to check both current day and next day (for early UTC times that are today in UK)
            next_day = (current_day + 1) % 7
            
            # Query both current day and next day schedules
            from datetime import date as date_type
            today = now.date()
            tomorrow = today + timedelta(days=1)

            current_day_schedules = session.query(TrainSchedule).filter_by(
                day_of_week=current_day,
                is_active=True
            ).all()

            next_day_schedules = session.query(TrainSchedule).filter_by(
                day_of_week=next_day,
                is_active=True
            ).all()

            # Filter next_day schedules to only those with early UTC times (< 5am)
            next_day_early_utc = [s for s in next_day_schedules if s.start_time.hour < 5]

            # Filter out one-time schedules that aren't for today or tomorrow
            def is_relevant(schedule):
                if schedule.specific_date is None:
                    return True  # Recurring — always relevant
                sd = schedule.specific_date
                if isinstance(sd, str):
                    from datetime import datetime as dt_class
                    sd = dt_class.strptime(sd, '%Y-%m-%d').date()
                return sd == today or sd == tomorrow

            schedules = [s for s in (current_day_schedules + next_day_early_utc) if is_relevant(s)]
            
            self.logger.info(f"Found {len(schedules)} schedules for UK day {current_day} ({len(current_day_schedules)} same-day, {len(next_day_early_utc)} next-day-UTC)")
            for schedule in schedules:
                try:
                    self.logger.info(f"Processing schedule: {schedule.name} at {schedule.start_time}")
                    await self.process_schedule_notifications(schedule, now, session)
                    
                    # Check if we should start Twitch chat monitoring for this train
                    await self._check_start_chat_monitoring(schedule, now)
                except Exception as e:
                    self.logger.exception(f"Error processing schedule {schedule.name}: {e}")
                    continue  # Continue processing other schedules
            
            # Also check all existing notifications for auto-assignment
            await self.check_all_notifications_for_auto_assignment(now, session)
            
            # Clean cache periodically (keep only last 100 entries)
            if len(self.sent_notifications_cache) > 100:
                # Keep only recent entries
                self.sent_notifications_cache = set(list(self.sent_notifications_cache)[-50:])
    
    def _compute_occurrence_datetime(self, schedule, now, uk_tz):
        """Compute the upcoming UK-localized start datetime + occurrence date for a schedule.

        Returns (start_datetime, occurrence_date) or (None, None) on failure.
        Mirrors the date logic in process_schedule_notifications but only needs the
        next occurrence for reminder/thread purposes.
        """
        try:
            today_date = now.date()
            specific_date = getattr(schedule, 'specific_date', None)
            if specific_date:
                if isinstance(specific_date, str):
                    from datetime import datetime as dt_class
                    specific_date = dt_class.strptime(specific_date, '%Y-%m-%d').date()
                schedule_date = specific_date
                if schedule.start_time.hour < 5:
                    schedule_date = schedule_date + timedelta(days=1)
            else:
                current_day = now.weekday()
                days_until = (schedule.day_of_week - current_day) % 7
                schedule_date = today_date + timedelta(days=days_until)
                try:
                    week_offset = self._get_week_offset(schedule)
                    if week_offset and week_offset > 0:
                        schedule_date = schedule_date + timedelta(weeks=week_offset)
                except Exception:
                    pass
            naive = datetime.combine(schedule_date, schedule.start_time)
            start_dt = uk_tz.localize(naive)
            return start_dt, schedule_date
        except Exception as e:
            self.logger.debug(f"Occurrence calc failed for {getattr(schedule, 'name', '?')}: {e}")
            return None, None

    async def check_train_reminders(self):
        """Beta: DM opted-in participants a configurable time before their own slot.

        Gated per-guild by the 'reminders' feature flag (default OFF). Deduplicated
        per (schedule, user, occurrence_date) via TrainReminderSent so each rider is
        DM'd at most once per occurrence.
        """
        try:
            from models import (
                UserTrainReminderPreference,
                TrainReminderSent,
            )
            perms = getattr(self.bot, 'permissions_manager', None)
            uk_tz = self.uk_tz
            now = datetime.now(uk_tz)

            async with AsyncDatabaseSession() as session:
                schedules = session.query(TrainSchedule).filter_by(is_active=True).all()
                for schedule in schedules:
                    guild_id = schedule.guild_id
                    # Per-guild beta gate
                    if perms and not await perms.is_feature_enabled(guild_id, 'reminders'):
                        continue

                    start_dt, occ_date = self._compute_occurrence_datetime(schedule, now, uk_tz)
                    if not start_dt:
                        continue
                    # Only upcoming occurrences matter for reminders
                    if (start_dt - now).total_seconds() <= 0:
                        continue

                    participants = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule.id,
                        is_active=True
                    ).all()
                    if not participants:
                        continue

                    for p in participants:
                        pref = session.query(UserTrainReminderPreference).filter_by(
                            guild_id=guild_id,
                            user_id=p.user_id,
                            enabled=True
                        ).first()
                        if not pref:
                            continue

                        lead = pref.lead_minutes or 30
                        reminder_time = start_dt - timedelta(minutes=lead)
                        delta = (now - reminder_time).total_seconds()
                        # Fire within a 90s window after the reminder time (loop runs every 60s)
                        if not (0 <= delta < 90):
                            continue

                        already = session.query(TrainReminderSent).filter_by(
                            schedule_id=schedule.id,
                            user_id=p.user_id,
                            occurrence_date=occ_date
                        ).first()
                        if already:
                            continue

                        # Record first to avoid duplicate sends even if DM is slow/raises.
                        # The DB unique constraint on (schedule_id,user_id,occurrence_date)
                        # is the source of truth: if a concurrent loop/restart already
                        # inserted this row, the commit raises and we skip sending.
                        session.add(TrainReminderSent(
                            schedule_id=schedule.id,
                            user_id=p.user_id,
                            guild_id=guild_id,
                            occurrence_date=occ_date
                        ))
                        from sqlalchemy.exc import IntegrityError
                        try:
                            session.commit()
                        except IntegrityError:
                            session.rollback()
                            continue

                        try:
                            user = self.bot.get_user(p.user_id)
                            if user is None:
                                user = await self.bot.fetch_user(p.user_id)
                            if user is None:
                                continue
                            guild = self.bot.get_guild(guild_id)
                            guild_name = guild.name if guild else "your server"
                            embed = discord.Embed(
                                title="⏰ Train Reminder",
                                description=(
                                    f"Your train **{schedule.name}** starts "
                                    f"<t:{int(start_dt.timestamp())}:R> "
                                    f"(<t:{int(start_dt.timestamp())}:t>)."
                                ),
                                color=discord.Color.orange()
                            )
                            embed.add_field(name="Server", value=guild_name, inline=True)
                            embed.set_footer(text="You opted in with /trainreminder • turn off any time")
                            await user.send(embed=embed)
                            self.logger.info(
                                f"⏰ Sent train reminder to {p.user_id} for '{schedule.name}' "
                                f"({lead}min lead)"
                            )
                        except discord.Forbidden:
                            self.logger.info(f"⏰ Could not DM reminder to {p.user_id} (DMs closed)")
                        except Exception as dm_err:
                            self.logger.warning(f"⏰ Reminder DM failed for {p.user_id}: {dm_err}")
        except Exception as e:
            self.logger.error(f"❌ Error in check_train_reminders: {e}", exc_info=True)

    @app_commands.command(
        name="trainreminder",
        description="[Beta] Opt in/out of DM reminders before your own train slots"
    )
    @app_commands.describe(
        action="Turn reminders on, off, or check your current setting",
        minutes="Minutes before your slot to be reminded (5-180, default 30)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="status", value="status"),
    ])
    async def trainreminder(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        minutes: int = 30
    ):
        """Manage per-user opt-in train reminder DMs."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.", ephemeral=True
            )
            return

        perms = getattr(self.bot, 'permissions_manager', None)
        if perms and not await perms.is_feature_enabled(interaction.guild.id, 'reminders'):
            await interaction.response.send_message(
                "❌ Train reminders aren't enabled in this server yet.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            from models import UserTrainReminderPreference
            guild_id = interaction.guild.id
            user_id = interaction.user.id
            choice = action.value

            async with AsyncDatabaseSession() as session:
                pref = session.query(UserTrainReminderPreference).filter_by(
                    guild_id=guild_id, user_id=user_id
                ).first()

                if choice == "status":
                    if pref and pref.enabled:
                        msg = (
                            f"⏰ Reminders are **ON** — you'll be DM'd "
                            f"**{pref.lead_minutes} minutes** before each slot you join."
                        )
                    else:
                        msg = "⏰ Reminders are **OFF**. Use `/trainreminder on` to enable them."
                    await interaction.followup.send(msg, ephemeral=True)
                    return

                if choice == "off":
                    if pref:
                        pref.enabled = False
                        session.commit()
                    await interaction.followup.send(
                        "✅ Train reminders turned **off**.", ephemeral=True
                    )
                    return

                # choice == "on"
                lead = max(5, min(180, int(minutes)))
                if pref:
                    pref.enabled = True
                    pref.lead_minutes = lead
                else:
                    pref = UserTrainReminderPreference(
                        guild_id=guild_id,
                        user_id=user_id,
                        enabled=True,
                        lead_minutes=lead
                    )
                    session.add(pref)
                session.commit()

            await interaction.followup.send(
                f"✅ Train reminders turned **on** — you'll get a DM **{lead} minutes** "
                f"before each slot you've joined.\n"
                f"*(Make sure your DMs are open so the bot can reach you.)*",
                ephemeral=True
            )
        except Exception as e:
            self.logger.error(f"Error in trainreminder command: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ Error updating your reminder setting: {e}", ephemeral=True
                )
            except Exception:
                pass

    async def process_schedule_notifications(self, schedule, now, session):
        """Process multi-stage notifications for a specific schedule."""
        try:
            uk_tz = pytz.timezone('Europe/London')
            today_date = now.date()
            
            # start_time is stored as UK wall clock time - localize against actual date for DST handling

            # Calculate the correct date for this schedule.
            # For specific-date (one-time) schedules use the stored date directly so that
            # midnight/early-morning slots are never confused with last week's occurrence.
            specific_date = getattr(schedule, 'specific_date', None)
            if specific_date:
                if isinstance(specific_date, str):
                    from datetime import datetime as dt_class
                    specific_date = dt_class.strptime(specific_date, '%Y-%m-%d').date()
                schedule_date = specific_date
                # Early-morning trains (< 5 am) on a specific date actually fire on the
                # *next* calendar day — e.g. a "Sunday midnight" slot with
                # specific_date=Jun 7 runs at Jun 8 00:00 BST, not Jun 7 00:00 BST.
                if schedule.start_time.hour < 5:
                    schedule_date = schedule_date + timedelta(days=1)
            else:
                # Recurring schedule: derive date from day_of_week
                current_day = now.weekday()
                schedule_day = schedule.day_of_week
                days_until_schedule = (schedule_day - current_day) % 7
                schedule_date = today_date + timedelta(days=days_until_schedule)
                week_offset = self._get_week_offset(schedule)
                if week_offset > 0:
                    schedule_date = schedule_date + timedelta(weeks=week_offset)

            naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
            start_datetime = uk_tz.localize(naive_uk_datetime)

            # Resolve current_day for checks below (may already be set in recurring branch)
            current_day = now.weekday()
            actual_uk_day = start_datetime.weekday()

            # If the converted UK day doesn't match current UK day, check if it's within
            # the notification window. If so, still process it (handles cross-midnight slots).
            notify_before_minutes = getattr(schedule, 'notify_before_minutes', 60)
            if actual_uk_day != current_day and start_datetime > now:
                time_until_start = (start_datetime - now).total_seconds()
                if time_until_start > notify_before_minutes * 60:
                    self.logger.debug(f"⏭️ Skipping {schedule.name} - belongs to UK day {actual_uk_day}, currently checking day {current_day}, will process on correct day")
                    return
                else:
                    self.logger.info(f"✅ {schedule.name} crosses midnight but notification window is active ({time_until_start/60:.0f}min until start)")

            # If the converted time is in the past handle it carefully.
            # If the train started within the last 2 hours keep this occurrence for stages 3/4.
            time_since_start_seconds = (now - start_datetime).total_seconds()
            within_active_window = 0 <= time_since_start_seconds <= 7200

            if start_datetime < now and not within_active_window:
                if specific_date:
                    # One-time schedule that is fully in the past — skip, never push to next week
                    self.logger.debug(f"⏭️ Skipping past specific-date schedule {schedule.name}")
                    return
                else:
                    # Recurring schedule — roll forward to next week's occurrence
                    days_until_schedule = (schedule.day_of_week - current_day) % 7
                    if actual_uk_day == current_day or days_until_schedule == 0:
                        schedule_date = schedule_date + timedelta(days=7)
                        naive_uk_datetime = datetime.combine(schedule_date, schedule.start_time)
                        start_datetime = uk_tz.localize(naive_uk_datetime)
                    else:
                        self.logger.debug(f"⏭️ Skipping past schedule {schedule.name} - belongs to different UK day")
                        return
            
            self.logger.info(f"✅ Schedule: {schedule.name} at {schedule.start_time} UK (day {schedule.day_of_week}) → {start_datetime.strftime('%I:%M %p %Z')}")
            
            # Multi-stage notifications for train session  
            # Use schedule's configured notification timing or default to 60 minutes
            notify_before_minutes = getattr(schedule, 'notify_before_minutes', 60)
            
            # Stage 1: Primary notification (user-configured minutes before, default 60)
            # Stage 2: 30 minutes before start (backup notification with @everyone if no response)  
            # Stage 3: 20 minutes before start (final reminder)
            stage_1_time = start_datetime - timedelta(minutes=notify_before_minutes)  # Primary
            stage_2_time = start_datetime - timedelta(minutes=30)  # 30 min before (backup)
            stage_3_time = start_datetime - timedelta(minutes=20)  # 20 min before (final reminder)
            
            self.logger.debug(f"Stage times for {schedule.name}: Stage1={stage_1_time}, Stage3={stage_3_time}")
            
            # Stage detection logic with comprehensive debugging
            current_stage = None
            target_time = None
            
            # Calculate time differences
            time_to_start = (start_datetime - now).total_seconds()
            time_since_start = (now - start_datetime).total_seconds()
            
            # DEBUG: Log full timing context
            self.logger.info(f"📊 Timing for {schedule.name}: now={now.strftime('%I:%M %p')}, start={start_datetime.strftime('%I:%M %p')}, time_to_start={time_to_start/60:.1f}min, time_since_start={time_since_start/60:.1f}min")
            
            # Determine stage based on time to/since start
            if time_to_start > notify_before_minutes * 60:
                # Too early for any notification
                current_stage = None
                self.logger.debug(f"⏰ {schedule.name}: Too early (>{notify_before_minutes} min before)")
            elif notify_before_minutes > 30 and time_to_start > 1800:
                # Stage 1: Primary notification (ONLY for schedules with notify_before > 30 min, before 30-min mark)
                current_stage = 1
                target_time = stage_1_time
                self.logger.info(f"🔔 {schedule.name}: STAGE 1 - Primary notification window ({notify_before_minutes} min before)")
            elif time_to_start > 1200:  # Between 20 min before and either Stage 1 or Stage 2
                # For schedules with notify_before <= 30 min: this is Stage 1
                # For schedules with notify_before > 30 min: this is Stage 2 (backup)
                if notify_before_minutes <= 30:
                    current_stage = 1
                    target_time = stage_1_time
                    self.logger.info(f"🔔 {schedule.name}: STAGE 1 - Primary notification window ({notify_before_minutes} min before)")
                else:
                    current_stage = 2
                    target_time = stage_2_time
                    self.logger.info(f"🔔 {schedule.name}: STAGE 2 - Backup notification window (30 min before)")
            elif time_to_start > 0 or time_since_start < 1200:  # 20 min before start to 20 min after start
                # Stage 3: Final reminder (fires 20 minutes before)
                current_stage = 3
                target_time = stage_3_time
                self.logger.info(f"🔔 {schedule.name}: STAGE 3 - Final reminder window (20 min before)")
            else:
                # Extended catch-up logic for any unsent notifications within 1 hour
                self.logger.debug(f"🔍 {schedule.name}: Checking catch-up logic")
                for stage_num, stage_time in [(1, stage_1_time), (2, stage_2_time), (3, stage_3_time)]:
                    time_since_stage = (now - stage_time).total_seconds()
                    if 0 <= time_since_stage <= 3600:  # Within 1 hour
                        existing = session.query(TrainNotification).filter_by(
                            schedule_id=schedule.id,
                            notification_date=today_date,
                            stage=stage_num
                        ).first()
                        
                        if not existing or not existing.is_sent:
                            current_stage = stage_num
                            target_time = stage_time
                            self.logger.info(f"🔄 {schedule.name}: CATCH-UP Stage {stage_num} (missed)")
                            break
            
            # Skip notification if not in proper time window  
            if current_stage is None:
                self.logger.debug(f"⏭️ Skipping {schedule.name} - not in notification time window")
                return  # Skip this schedule - not time to notify yet
            
            # Define today_date for duplicate prevention and database queries
            today_date = now.date()
            
            # CATCH-UP: Check if any EARLIER stages were missed and send them first
            # This prevents skipping notifications when the bot restarts
            if current_stage > 1:
                stage_times = {1: stage_1_time, 2: stage_2_time, 3: stage_3_time}
                for earlier_stage in range(1, current_stage):
                    earlier_stage_time = stage_times[earlier_stage]
                    time_since_earlier = (now - earlier_stage_time).total_seconds()
                    if 0 <= time_since_earlier <= 3600:
                        existing_earlier = session.query(TrainNotification).filter_by(
                            schedule_id=schedule.id,
                            notification_date=today_date,
                            stage=earlier_stage
                        ).first()
                        if not existing_earlier or not existing_earlier.is_sent:
                            self.logger.info(f"🔄 {schedule.name}: CATCH-UP - Stage {earlier_stage} was missed, sending it now before stage {current_stage}")
                            current_stage = earlier_stage
                            target_time = earlier_stage_time
                            break
            
            # CRITICAL: Check who should receive this train's reminder. With zero
            # signups we still notify the host so the slot can be claimed — a
            # scheduled train is never silently skipped unless it also has no host.
            participant_count = session.query(TrainParticipant).filter_by(
                schedule_id=schedule.id,
                is_active=True
            ).count()
            
            if participant_count == 0 and not schedule.host_user_id:
                self.logger.info(f"⏭️ Skipping {schedule.name} - no participants signed up and no host set")
                return  # Nobody to notify, don't send any notifications
            if participant_count == 0:
                self.logger.info(f"📣 {schedule.name} has no signups yet — sending reminder to host so the slot can be claimed")
            
            # Check if someone has already confirmed ready for this schedule today OR yesterday
            # (yesterday check prevents re-sending after midnight for late-night trains)
            yesterday_date = today_date - timedelta(days=1)
            confirmed_notification = session.query(TrainNotification).filter(
                TrainNotification.schedule_id == schedule.id,
                TrainNotification.notification_date.in_([today_date, yesterday_date]),
                TrainNotification.primary_confirmed == True
            ).first()
            
            if confirmed_notification:
                self.logger.info(f"Skipping Stage {current_stage} for {schedule.name} - participant already confirmed ready")
                return
            
            # CRITICAL: Check if ANY notification was manually marked complete - skip all future stages
            manually_completed = session.query(TrainNotification).filter(
                TrainNotification.schedule_id == schedule.id,
                TrainNotification.notification_date.in_([today_date, yesterday_date]),
                TrainNotification.is_completed == True
            ).first()
            
            if manually_completed:
                self.logger.info(f"Skipping Stage {current_stage} for {schedule.name} - train was manually marked complete")
                return
            
            self.logger.info(f"No confirmed participant for {schedule.name} - continuing with stage {current_stage}")
            
            # Check if notification for this stage already exists (check today and yesterday for midnight-crossing trains)
            existing_notification = session.query(TrainNotification).filter(
                TrainNotification.schedule_id == schedule.id,
                TrainNotification.notification_date.in_([today_date, yesterday_date]),
                TrainNotification.stage == current_stage
            ).first()
            
            if existing_notification:
                self.logger.info(f"Found existing notification for {schedule.name} stage {current_stage}: sent={existing_notification.is_sent}")
            else:
                self.logger.info(f"No existing notification found for {schedule.name} stage {current_stage}")
            
            # If notification exists and is already sent, check if we need to escalate
            if existing_notification:
                if existing_notification.is_sent:
                    self.logger.info(f"Existing notification already sent - checking escalation for {schedule.name}")
                    await self.check_notification_escalation(existing_notification, schedule, now, session)
                    return
                else:
                    self.logger.info(f"Found unsent existing notification for {schedule.name} - will retry sending")
                    # Don't return early, proceed to retry sending the notification
            
            # Check if it's time to send this stage (expanded window for catch-up notifications)
            time_diff = now - target_time
            time_diff_seconds = time_diff.total_seconds()
            self.logger.info(f"Time check for {schedule.name}: target_time={target_time}, now={now}, diff_seconds={time_diff_seconds}")
            
            # Allow notifications from target time to 60 minutes after (for catch-up) - NO EARLY FIRING
            if 0 <= time_diff_seconds <= 3600:  # 1 hour catch-up window, but NOT before target time
                self.logger.info(f"✅ Within time window for {schedule.name} - proceeding with notification")
                # Enhanced duplicate prevention with database check
                cache_key = f"{schedule.guild_id}_{schedule.id}_{today_date}_{current_stage}"
                
                # Double-check with database to prevent duplicates across bot restarts (check today+yesterday)
                existing_sent = session.query(TrainNotification).filter(
                    TrainNotification.schedule_id == schedule.id,
                    TrainNotification.notification_date.in_([today_date, yesterday_date]),
                    TrainNotification.stage == current_stage,
                    TrainNotification.is_sent == True
                ).first()
                
                if existing_sent:
                    self.logger.info(f"❌ Notification already sent in database: {cache_key}")
                    return
                
                self.logger.info(f"🔍 Checking cache for: {cache_key}")
                if cache_key not in self.sent_notifications_cache:
                    self.logger.info(f"✅ Cache check passed - acquiring lock for: {cache_key}")
                    # Direct notification sending - no lock needed (cache prevents duplicates)
                    self.logger.info(f"🚀 Starting notification process for {schedule.name}")
                    
                    # Prepare notification record in database
                    notification_id = await self.prepare_notification_record(schedule, current_stage, today_date, session)
                    if notification_id:
                        # Mark as processing to prevent duplicates
                        self.sent_notifications_cache.add(cache_key)
                        self.logger.info(f"✅ Notification record prepared, sending Discord message...")
                        
                        # Send Discord message
                        success = await self.send_discord_notification(schedule, current_stage, today_date, notification_id)
                        if success:
                            self.logger.info(f"✅ Notification sent successfully for {schedule.name}")
                        else:
                            self.logger.error(f"❌ Failed to send notification for {schedule.name}")
                            # Remove from cache on failure to allow retry
                            self.sent_notifications_cache.discard(cache_key)
                    else:
                        self.logger.debug(f"⏭️ No notification record prepared for {schedule.name} (no settings or guild not configured)")
                else:
                    self.logger.info(f"❌ Skipping duplicate notification: {cache_key}")
                
        except Exception as e:
            self.logger.error(f"Error processing schedule notifications: {e}")
    
    async def check_notification_escalation(self, notification, schedule, now, session):
        """Check if we need to escalate to backup streamers or auto-assign backup."""
        try:
            # Get notification settings to check timeout
            settings = session.query(NotificationSettings).filter_by(
                guild_id=schedule.guild_id
            ).first()
            
            ping_timeout_minutes = settings.ping_timeout_minutes if settings else 15
            
            # BACKUP PING TIMING - Wait 30 minutes before sending backup notification
            if not notification.backup_pinged:
                # If primary hasn't confirmed, check if 30 minutes have passed since original notification
                if not notification.primary_confirmed:
                    # Calculate time since original notification was sent
                    # Ensure both datetimes have same timezone handling
                    sent_at_aware = notification.sent_at
                    if sent_at_aware.tzinfo is None:
                        # If stored as naive, assume it's UK time and make it timezone-aware
                        sent_at_aware = self.uk_tz.localize(sent_at_aware)
                    
                    time_since_sent = (now - sent_at_aware).total_seconds() / 60  # in minutes
                    
                    # Only send backup ping after 30 minutes but within 3 hours (cap prevents
                    # old/cross-midnight notifications from firing backup pings the next day)
                    if 30 <= time_since_sent <= 180:
                        session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                            'backup_pinged': True
                        })
                        session.commit()
                        
                        await self.send_backup_notification(notification, schedule, session)
                        self.logger.info(f"🚨 30-MINUTE BACKUP PING: Escalated to backup notification for schedule {schedule.name} after {time_since_sent:.1f} minutes")
                    else:
                        # Still waiting for 30-minute timeout
                        minutes_remaining = 30 - time_since_sent
                        self.logger.info(f"⏰ Waiting for backup escalation for {schedule.name}: {minutes_remaining:.1f} minutes remaining")
            
            # Check if we've reached the timeout point and should auto-assign backup (separate check)
            await self.check_backup_auto_assignment(notification, schedule, now, session, ping_timeout_minutes)
                    
        except Exception as e:
            self.logger.error(f"Error checking notification escalation: {e}")
    
    async def prepare_notification_record(self, schedule, stage, notification_date, session):
        """Fast database preparation - creates notification record and returns ID."""
        try:
            from models import NotificationSettings
            
            self.logger.info(f"🔧 PREP: Starting notification prep for {schedule.name}")
            
            # Get notification settings
            settings = session.query(NotificationSettings).filter_by(
                guild_id=schedule.guild_id
            ).first()
            
            self.logger.info(f"🔧 PREP: Settings query complete - enabled: {settings.auto_ping_enabled if settings else 'No settings'}")
            
            if not settings or not settings.auto_ping_enabled:
                self.logger.warning(f"🔧 PREP: Auto-ping disabled for guild {schedule.guild_id}")
                return None
                
            # Verify guild and channel exist
            guild = self.bot.get_guild(schedule.guild_id)
            self.logger.info(f"🔧 PREP: Guild lookup complete - found: {guild.name if guild else 'None'}")
            
            if not guild:
                self.logger.error(f"🔧 PREP: Guild {schedule.guild_id} not found")
                return None
                
            channel = None
            if settings.raid_notification_channel_id:
                channel = guild.get_channel(settings.raid_notification_channel_id)
                self.logger.info(f"🔧 PREP: Using raid notification channel")
            
            self.logger.info(f"🔧 PREP: Channel lookup complete - found: {channel.name if channel else 'None'}")
            
            if not channel:
                self.logger.error(f"🔧 PREP: Channel {settings.raid_notification_channel_id} not found in guild {guild.name}")
                return None
            
            self.logger.info(f"🔧 PREP: Creating notification record...")
            
            # Create notification record (fast database operation)
            notification = TrainNotification(
                schedule_id=schedule.id,
                guild_id=schedule.guild_id,
                notification_date=notification_date,
                stage=stage,
                channel_id=channel.id
            )
            
            session.add(notification)
            self.logger.info(f"🔧 PREP: Committing to database...")
            session.commit()
            self.logger.info(f"🔧 PREP: Successfully created notification record ID: {notification.id}")
            return notification.id
            
        except Exception as e:
            self.logger.error(f"🔧 PREP: Error preparing notification record: {e}")
            return None
    
    async def send_discord_notification(self, schedule, stage, notification_date, notification_id):
        """Send Discord message - slow operation done outside lock."""
        try:
            # Notification tracking
            self.logger.info(f"Sending notification: {schedule.name} Stage {stage} (ID: {notification_id})")
            async with AsyncDatabaseSession() as session:
                # Get the notification record and settings
                notification = session.query(TrainNotification).filter_by(id=notification_id).first()
                if not notification:
                    return False
                    
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=schedule.guild_id
                ).first()
                
                # Get guild record for server description
                from models import Guild
                guild_record = session.query(Guild).filter_by(id=schedule.guild_id).first()
                server_description = guild_record.server_description if guild_record and guild_record.server_description else ""
                
                guild = self.bot.get_guild(schedule.guild_id)
                channel = guild.get_channel(notification.channel_id) if guild else None
                
                if not guild or not channel:
                    return False
                
                # Build Discord embed based on stage
                description_text = f'**{schedule.name}** - {self.get_time_info_for_stage(stage)}'
                if server_description:
                    description_text += f'\n📍 {server_description}'
                embed = discord.Embed(
                    title='🚂 Raid Train Starting Soon!',
                    description=description_text,
                    color=0x9146ff
                )
                
                # Stored as UK wall clock time - localize against actual schedule date
                uk_tz = pytz.timezone('Europe/London')
                _now_uk = datetime.now(uk_tz)
                _sd = schedule.specific_date
                if _sd and isinstance(_sd, str):
                    from datetime import datetime as _dtc
                    _sd = _dtc.strptime(_sd, '%Y-%m-%d').date()
                _disp_date = _sd if _sd else (_now_uk.date() + timedelta(days=(schedule.day_of_week - _now_uk.weekday()) % 7))
                uk_naive = datetime.combine(_disp_date, schedule.start_time)
                uk_time = uk_tz.localize(uk_naive)
                # Format with correct timezone abbreviation (GMT or BST)
                start_time = uk_time.strftime('%I:%M %p %Z')
                
                embed.add_field(
                    name='📅 Train Details',
                    value=f'**Start Time:** {start_time}\n**Duration:** {schedule.duration_minutes} minutes',
                    inline=False
                )
                
                embed.add_field(
                    name='📊 Status',
                    value="⏰ **Primary:** Waiting for confirmation...",
                    inline=False
                )
                
                embed.add_field(
                    name='🎯 Instructions',
                    value=f'{self.READY_EMOJI} = Ready to stream\n{self.NOT_READY_EMOJI} = Can\'t make it\n{self.BACKUP_EMOJI} = Need backup',
                    inline=False
                )
                
                current_uk = get_uk_time()
                embed.set_footer(text=f'Stage {stage} • {current_uk.strftime("%H:%M UK")}')
                
                # Build message content with pings
                message_content = ""
                if stage == 2:
                    message_content = "⏰ **30-MINUTE REMINDER:**"
                elif settings.raid_ping_role_id:
                    ping_role = guild.get_role(settings.raid_ping_role_id)
                    if ping_role:
                        message_content = ping_role.mention
                
                # Add participant ping - ALWAYS use TrainParticipant table as source of truth
                participant_found = False
                
                # Use TrainParticipant table (source of truth) - NEVER use stale participant_ids array
                from models import TrainParticipant
                signed_participants = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule.id,
                    guild_id=schedule.guild_id,
                    is_active=True
                ).order_by(TrainParticipant.signed_up_at.asc()).all()  # First signed up gets priority
                
                if signed_participants:
                    # Ping the first signed participant
                    first_participant = signed_participants[0]
                    message_content += f" 🎯 **TRAIN RIDER:** {self._resolve_participant_mention(first_participant, guild)}"
                    self.logger.info(f"Pinging active participant {first_participant.display_name} (ID: {first_participant.user_id}) for {schedule.name}")
                    participant_found = True
                
                if not participant_found:
                    # Empty slot — skip entirely. Do not fall back to the host;
                    # an unassigned train slot should fire no notification at all.
                    self.logger.info(f"⏭️  Skipping notification for '{schedule.name}' - slot has no active rider")
                    session.delete(notification)
                    session.commit()
                    return False
                
                # Send Discord message (slow operation) 
                try:
                    message = await channel.send(content=message_content, embed=embed)
                    
                    # Verify message was actually sent by checking if it exists
                    if not message or not message.id:
                        self.logger.error(f"❌ Discord API returned invalid message object for {schedule.name}")
                        return False
                    
                    # Only add reaction for confirmation stages (not attendance report)
                    if stage != 4:
                        await message.add_reaction(self.READY_EMOJI)
                        await message.add_reaction(self.NOT_READY_EMOJI)
                        await message.add_reaction(self.BACKUP_EMOJI)
                    
                    # ✅ FIX: Only mark as sent AFTER Discord confirms successful delivery
                    session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                        'message_id': message.id,
                        'is_sent': True,
                        'sent_at': get_uk_time()
                    })
                    session.commit()
                    
                    self.logger.info(f"✅ Discord message sent and confirmed for '{schedule.name}' in guild {guild.name}")
                    
                    return True
                    
                except discord.Forbidden as e:
                    self.logger.error(f"❌ Missing permissions to send notification for {schedule.name}: {e}")
                    return False
                except discord.HTTPException as e:
                    self.logger.error(f"❌ Discord API error sending notification for {schedule.name}: {e}")
                    return False
                
        except Exception as e:
            self.logger.error(f"Error sending Discord notification: {e}")
            return False
    
    async def get_current_stream_participants(self, schedule, session):
        """Get detailed attendance report showing Twitch chat presence only."""
        try:
            from models import TrainParticipant, TwitchChatAttendance
            
            # Get all participants who signed up for this train
            participants = session.query(TrainParticipant).filter_by(
                schedule_id=schedule.id,
                is_active=True
            ).all()
            
            # Get Twitch chat attendance data
            uk_date = get_uk_time().date()
            twitch_chat_attendance = session.query(TwitchChatAttendance).filter_by(
                schedule_id=schedule.id,
                train_date=uk_date
            ).all()
            
            if not participants and not twitch_chat_attendance:
                return "**No participants found for this train**"
            
            # Build Twitch chat attendance report
            report_lines = []
            
            # Process Twitch chat attendance data
            present_in_chat = []
            absent_from_chat = []
            no_twitch_link = []
            
            if twitch_chat_attendance:
                # Process database attendance records
                for record in twitch_chat_attendance:
                    # Find corresponding participant
                    participant = next((p for p in participants if p.user_id == record.user_id), None)
                    if participant:
                        if record.was_present_in_chat:
                            present_in_chat.append((participant.display_name, record.twitch_username))
                        else:
                            absent_from_chat.append((participant.display_name, record.twitch_username))
                
                # Check for participants without Twitch chat records
                tracked_user_ids = {r.user_id for r in twitch_chat_attendance}
                for participant in participants:
                    if participant.user_id not in tracked_user_ids:
                        if participant.twitch_username:
                            absent_from_chat.append((participant.display_name, participant.twitch_username))
                        else:
                            no_twitch_link.append((participant.display_name, None))
            else:
                # No chat data available, categorize based on Twitch links
                for participant in participants:
                    if not participant.twitch_username:
                        no_twitch_link.append((participant.display_name, None))
                    else:
                        absent_from_chat.append((participant.display_name, participant.twitch_username))
            
            # Show who was present in Twitch chat
            if present_in_chat:
                report_lines.append("**✅ Present in Twitch Chat:**")
                for i, (name, twitch) in enumerate(present_in_chat, 1):
                    report_lines.append(f"  {i}. **{name}** (Twitch: `{twitch}`)")
                report_lines.append("")  # Spacing
            
            # Show who was absent from Twitch chat
            if absent_from_chat:
                report_lines.append("**❌ Absent from Twitch Chat:**")
                for i, (name, twitch) in enumerate(absent_from_chat, 1):
                    report_lines.append(f"  {i}. **{name}** (Twitch: `{twitch}`)")
                report_lines.append("")  # Spacing
            
            # Show who doesn't have Twitch linked
            if no_twitch_link:
                report_lines.append("**⚠️ No Twitch Account Linked:**")
                for i, (name, _) in enumerate(no_twitch_link, 1):
                    report_lines.append(f"  {i}. **{name}** (Cannot track chat presence)")
                report_lines.append("")  # Spacing
            
            if not report_lines:
                return "**No Twitch chat attendance data available**\n\nRun `/twitchoauth` to enable Twitch chat monitoring."
            
            # Add explanation footer
            report_lines.append("**Note:** Chat presence is tracked automatically during raid trains via Twitch IRC monitoring")
            
            return "\n".join(report_lines)
            
        except Exception as e:
            self.logger.error(f"Error getting Twitch chat attendance: {e}")
            return f"Error retrieving attendance information: {str(e)}"

    async def send_stage_notification(self, schedule, stage, notification_date, session):
        """Send initial notification for a specific stage."""
        try:
            from models import TrainParticipant
            # Get notification settings for this guild
            settings = session.query(NotificationSettings).filter_by(
                guild_id=schedule.guild_id
            ).first()
            
            if not settings or not settings.auto_ping_enabled:
                return False
                
            guild = self.bot.get_guild(schedule.guild_id)
            if not guild:
                return False
                
            channel = None
            if settings.raid_notification_channel_id:
                channel = guild.get_channel(settings.raid_notification_channel_id)
            
            if not channel:
                self.logger.warning(f"No raid notification channel set for guild {guild.name}")
                return False
            
            # Create notification record
            notification = TrainNotification(
                schedule_id=schedule.id,
                guild_id=schedule.guild_id,
                notification_date=notification_date,
                stage=stage,
                channel_id=channel.id
            )
            
            session.add(notification)
            session.commit()  # Commit to get ID
            
            # Send the message
            embed = discord.Embed(
                title='🚂 Raid Train Starting Soon!',
                description=f'**{schedule.name}** - {self.get_time_info_for_stage(stage)}',
                color=0x9146ff
            )
            
            # Stored as UK wall clock time - localize against actual schedule date for DST-aware display
            uk_tz = pytz.timezone('Europe/London')
            now_uk = datetime.now(uk_tz)
            # Use specific_date if available, otherwise compute from day_of_week
            if schedule.specific_date:
                sd = schedule.specific_date
                if isinstance(sd, str):
                    from datetime import datetime as dt_cls
                    sd = dt_cls.strptime(sd, '%Y-%m-%d').date()
                display_date = sd
            else:
                current_day_idx = now_uk.weekday()
                days_ahead = (schedule.day_of_week - current_day_idx) % 7
                display_date = now_uk.date() + timedelta(days=days_ahead)
            uk_naive = datetime.combine(display_date, schedule.start_time)
            uk_time = uk_tz.localize(uk_naive)
            start_time = uk_time.strftime('%I:%M %p %Z')  # 12-hour format with timezone
            embed.add_field(
                name='📅 Train Details',
                value=f'**Start Time:** {start_time}\n**Duration:** {schedule.duration_minutes} minutes',
                inline=False
            )
            
            embed.add_field(
                name='📊 Status',
                value="⏰ **Primary:** Waiting for confirmation...",
                inline=False
            )
            
            embed.add_field(
                name='🎯 Instructions',
                value=f'{self.READY_EMOJI} = Ready to stream\n{self.NOT_READY_EMOJI} = Can\'t make it\n{self.BACKUP_EMOJI} = Need backup',
                inline=False
            )
            
            current_uk = get_uk_time()
            embed.set_footer(text=f'Stage {stage} • {current_uk.strftime("%H:%M UK")}')
            
            # Send with role ping for Stage 1 only; Stage 2 just re-pings participants (no role pings)
            message_content = ""
            if stage == 2:
                message_content = "🆘 **BACKUP NEEDED!**"
            else:
                ping_role = None
                if settings.raid_ping_role_id:
                    ping_role = guild.get_role(settings.raid_ping_role_id)
                message_content = ping_role.mention if ping_role else ""
            
            # Add participant ping for this train slot - ONLY currently signed up people
            participant_pings = []

            # Query full participant objects so we can fall back to name lookup
            # when user_id is 0 (manually-added participants).
            active_participants = session.query(TrainParticipant).filter_by(
                schedule_id=schedule.id, is_active=True
            ).all()
            self.logger.info(f"🔍 Retrieved {len(active_participants)} active participants from TrainParticipant table for {schedule.name}")

            # Empty slot — skip entirely so no channel message or role ping fires.
            if not active_participants:
                self.logger.info(f"⏭️  Skipping notification for '{schedule.name}' - slot has no active rider")
                session.delete(notification)
                session.commit()
                return False

            for participant in active_participants:
                mention = self._resolve_participant_mention(participant, guild)
                participant_pings.append(mention)

            if participant_pings:
                participant_text = " ".join(participant_pings)
                train_rider_text = f"🎯 **TRAIN RIDER:** {participant_text}"
                if message_content:
                    message_content += f"\n{train_rider_text}"
                else:
                    message_content = train_rider_text

                self.logger.info(f"✅ Pinging {len(participant_pings)} active riders for {schedule.name}: {participant_text}")
            
            message = await channel.send(content=message_content, embed=embed)
            
            # Add reaction options
            await message.add_reaction(self.READY_EMOJI)
            await message.add_reaction(self.NOT_READY_EMOJI)
            await message.add_reaction(self.BACKUP_EMOJI)
            
            # Update notification record with message ID
            session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                'message_id': message.id,
                'is_sent': True,
                'sent_at': get_uk_time()
            })
            session.commit()
            
            self.logger.info(f"Sent stage {stage} notification for '{schedule.name}' in guild {guild.name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error sending stage notification: {e}")
            return False
    
    async def notify_backup_no_longer_needed(self, notification, schedule, session, original_user):
        """Notify backup volunteers they're no longer needed because original streamer confirmed."""
        try:
            guild = self.bot.get_guild(notification.guild_id)
            channel = guild.get_channel(notification.channel_id) if guild else None
            
            if not guild or not channel:
                return
            
            # Check if there were any backup confirmations
            if notification.backup_confirmed and notification.backup_user_id:
                backup_user = guild.get_member(notification.backup_user_id)
                backup_mention = backup_user.mention if backup_user else "backup volunteer"
                
                # Create embed for backup dismissal
                embed = discord.Embed(
                    title='✅ Backup No Longer Needed',
                    description=f'**{schedule.name}** - Original streamer is now ready!',
                    color=0x00ff00
                )
                
                embed.add_field(
                    name='🎯 Original Streamer Confirmed',
                    value=f'{original_user.mention} has confirmed they\'re ready to stream',
                    inline=False
                )
                
                embed.add_field(
                    name='🙏 Thank You!',
                    value=f'{backup_mention}, thank you for volunteering as backup!\nYou\'re no longer needed for this slot.',
                    inline=False
                )
                
                # Stored as UK wall clock time - localize against actual schedule date
                uk_tz = pytz.timezone('Europe/London')
                _now_uk2 = datetime.now(uk_tz)
                _sd2 = schedule.specific_date
                if _sd2 and isinstance(_sd2, str):
                    from datetime import datetime as _dtc2
                    _sd2 = _dtc2.strptime(_sd2, '%Y-%m-%d').date()
                _disp_date2 = _sd2 if _sd2 else (_now_uk2.date() + timedelta(days=(schedule.day_of_week - _now_uk2.weekday()) % 7))
                uk_naive = datetime.combine(_disp_date2, schedule.start_time)
                uk_time = uk_tz.localize(uk_naive)
                start_time_display = uk_time.strftime('%I:%M %p %Z')
                
                embed.add_field(
                    name='📅 Train Details',
                    value=f'**Start Time:** {start_time_display}\n**Duration:** {schedule.duration_minutes} minutes',
                    inline=False
                )
                
                current_uk = get_uk_time()
                embed.set_footer(text=f'Backup Released • {current_uk.strftime("%H:%M UK")}')
                
                await channel.send(
                    content=f"📢 **UPDATE:** {backup_mention} - You're no longer needed as backup for **{schedule.name}**! Original streamer is ready! 🎉",
                    embed=embed
                )
                
                self.logger.info(f"Notified backup {backup_user.display_name if backup_user else notification.backup_user_id} they're no longer needed for {schedule.name}")
            
            # Cancel any pending delayed confirmation tasks (though this is tricky with asyncio.create_task)
            # For now, the delayed confirmation will still send but it's informational
            
        except Exception as e:
            self.logger.error(f"Error notifying backup no longer needed: {e}")

    async def check_next_day_raids(self):
        """Check for raid trains happening tomorrow and DM trusted users to activate the bot."""
        try:
            async with AsyncDatabaseSession() as session:
                current_time = get_uk_time()
                current_date = current_time.date()
                tomorrow_date = current_date + timedelta(days=1)
                tomorrow_day_of_week = tomorrow_date.weekday()  # Monday = 0, Sunday = 6
                
                # Check if we already sent DMs today (avoid spam)
                from models import SystemSettings
                daily_dm_key = f"daily_dm_sent_{current_date.strftime('%Y%m%d')}"
                dm_sent_today = session.query(SystemSettings).filter_by(
                    setting_key=daily_dm_key
                ).first()
                
                if dm_sent_today and dm_sent_today.is_enabled:
                    # Already sent DMs today
                    return
                
                # Get all active schedules for tomorrow. One-time (specific_date) schedules
                # must match tomorrow's actual date, not just the weekday, so an expired
                # one-time train never re-triggers "raid trains tomorrow" DMs on its weekday.
                candidate_schedules = session.query(TrainSchedule).filter(
                    TrainSchedule.is_active == True
                ).all()
                tomorrow_schedules = []
                for s in candidate_schedules:
                    sd = getattr(s, 'specific_date', None)
                    if sd:
                        if isinstance(sd, str):
                            from datetime import datetime as dt_class
                            sd = dt_class.strptime(sd, '%Y-%m-%d').date()
                        eff = sd + timedelta(days=1) if s.start_time.hour < 5 else sd
                        if eff == tomorrow_date:
                            tomorrow_schedules.append(s)
                    elif s.day_of_week == tomorrow_day_of_week:
                        tomorrow_schedules.append(s)
                
                if not tomorrow_schedules:
                    # No raid trains tomorrow
                    return
                
                # There are raid trains tomorrow - get all trusted users
                from models import TrustedUser
                trusted_users = session.query(TrustedUser).filter_by(is_active=True).all()
                
                if not trusted_users:
                    self.logger.info("No trusted users found to notify about tomorrow's raid trains")
                    return
                
                # Send DMs to trusted users
                dm_count = 0
                for trusted_user in trusted_users:
                    try:
                        user = self.bot.get_user(trusted_user.user_id)
                        if user:
                            await user.send(
                                "🚂 Heads up — there's a raid train scheduled for tomorrow! "
                                "Please run the ping command to wake the bot up so it's ready to go."
                            )
                            dm_count += 1
                            self.logger.info(f"Sent next-day raid notification DM to {trusted_user.username}")
                    except discord.Forbidden:
                        self.logger.warning(f"Cannot DM trusted user {trusted_user.username} - DMs disabled")
                    except Exception as e:
                        self.logger.error(f"Error sending DM to trusted user {trusted_user.username}: {e}")
                
                if dm_count > 0:
                    # Mark that we sent DMs today
                    new_setting = SystemSettings(
                        setting_key=daily_dm_key,
                        is_enabled=True,
                        setting_value=f"Sent DMs to {dm_count} trusted users"
                    )
                    session.add(new_setting)
                    session.commit()
                    
                    self.logger.info(f"✅ Sent DMs to {dm_count} trusted users about {len(tomorrow_schedules)} raid trains happening tomorrow")
                
        except Exception as e:
            self.logger.error(f"Error checking next day raids: {e}")

    async def send_delayed_backup_confirmation(self, user, schedule, guild, channel, delay_minutes=5):
        """Send a delayed confirmation message after backup reaction."""
        try:
            # Wait for the specified delay (5 minutes)
            await asyncio.sleep(delay_minutes * 60)
            
            # Verify guild and channel still exist
            if not guild or not channel:
                return
                
            # Send final confirmation message with backup person
            embed = discord.Embed(
                title='✅ Backup Confirmed - Final Status',
                description=f'**{schedule.name}** backup coverage confirmed!',
                color=0x00ff00
            )
            
            embed.add_field(
                name='🎯 Backup Rider',
                value=f'{user.mention} has confirmed as backup',
                inline=False
            )
            
            # Stored as UK wall clock time - localize for DST-aware display
            uk_tz = pytz.timezone('Europe/London')
            uk_naive = datetime.combine(datetime.now(uk_tz).date(), schedule.start_time)
            uk_time = uk_tz.localize(uk_naive)
            start_time_display = uk_time.strftime('%I:%M %p %Z')
            
            embed.add_field(
                name='📅 Train Details', 
                value=f'**Start Time:** {start_time_display}\n**Duration:** {schedule.duration_minutes} minutes',
                inline=False
            )
            
            embed.add_field(
                name='✅ Status',
                value='Backup coverage is active and ready!',
                inline=False
            )
            
            current_uk = get_uk_time()
            embed.set_footer(text=f'Final Confirmation • {current_uk.strftime("%H:%M UK")}')
            
            await channel.send(
                content=f"🎉 **FINAL CONFIRMATION** - {user.mention} is confirmed as backup rider for **{schedule.name}**!",
                embed=embed
            )
            
            self.logger.info(f"Sent delayed backup confirmation for {user.name} on schedule {schedule.name}")
            
        except Exception as e:
            self.logger.error(f"Error sending delayed backup confirmation: {e}")
    
    async def send_backup_notification(self, notification, schedule, session):
        """Send second ping to the same rider (30-minute backup ping)."""
        try:
            from models import TrainParticipant
            settings = session.query(NotificationSettings).filter_by(
                guild_id=schedule.guild_id
            ).first()
            
            if not settings:
                return
                
            guild = self.bot.get_guild(schedule.guild_id)
            if not guild:
                return
                
            channel = guild.get_channel(notification.channel_id)
            if not channel:
                return
            
            # Create second ping notification embed (30-minute reminder)
            embed = discord.Embed(
                title='🚨 URGENT: Train Starting in 30 Minutes!',
                description=f'**{schedule.name}** - Second ping reminder & backup call',
                color=0xff6b35  # Orange for urgency
            )
            
            # Stored as UK wall clock time - localize for DST-aware display
            uk_tz = pytz.timezone('Europe/London')
            uk_naive = datetime.combine(datetime.now(uk_tz).date(), schedule.start_time)
            uk_time = uk_tz.localize(uk_naive)
            start_time = uk_time.strftime('%I:%M %p %Z')
            embed.add_field(
                name='📅 Train Details',
                value=f'**Start Time:** {start_time}\n**Duration:** {schedule.duration_minutes} minutes',
                inline=False
            )
            
            embed.add_field(
                name='⚠️ Current Rider',
                value='This is your second notification - please confirm if you\'re ready to stream!',
                inline=False
            )
            
            embed.add_field(
                name='🔄 Backup Streamers',
                value='Primary streamer hasn\'t confirmed yet - backup streamers please respond if available!',
                inline=False
            )
            
            embed.add_field(
                name='🎯 Instructions', 
                value=f'{self.READY_EMOJI} = Ready to stream\n{self.NOT_READY_EMOJI} = Can\'t make it\n{self.BACKUP_EMOJI} = Need backup',
                inline=False
            )
            
            # Ping participants AND the backup role
            backup_message_content = []
            
            # Ping backup role first so it leads the message
            if settings.backup_ping_role_id:
                backup_ping_role = guild.get_role(settings.backup_ping_role_id)
                if backup_ping_role:
                    backup_message_content.append(f"{backup_ping_role.mention} 🚨 **BACKUP NEEDED** — primary rider hasn't confirmed!")
                    self.logger.info(f"Pinging backup role {backup_ping_role.name} for escalation - {schedule.name}")
            
            participants = session.query(TrainParticipant).filter_by(schedule_id=schedule.id, is_active=True).all()
            if participants:
                participant_pings = []
                for participant in participants:
                    participant_pings.append(self._resolve_participant_mention(participant, guild))

                if participant_pings:
                    participant_text = " ".join(participant_pings)
                    backup_message_content.append(f"{participant_text} — please confirm or let us know you can't make it!")
                    self.logger.info(f"Pinging {len(participant_pings)} registered riders for backup notification - {schedule.name}")
            
            final_message_content = "\n".join(backup_message_content) if backup_message_content else ""
            backup_message = await channel.send(content=final_message_content, embed=embed)
            await backup_message.add_reaction(self.READY_EMOJI)
            await backup_message.add_reaction(self.NOT_READY_EMOJI)
            await backup_message.add_reaction(self.BACKUP_EMOJI)
            
            self.logger.info(f"Sent backup notification for schedule {schedule.name}")
            
            # DM the bot owner privately about the backup escalation
            try:
                app_info = await self.bot.application_info()
                owner = app_info.owner
                if owner:
                    uk_tz = pytz.timezone('Europe/London')
                    now_uk = datetime.now(uk_tz)
                    owner_embed = discord.Embed(
                        title='⚠️ Backup Escalation Alert',
                        description=(
                            f"**{schedule.name}** - No response from primary rider after 30 minutes.\n\n"
                            f"**Guild:** {guild.name}\n"
                            f"**Channel:** #{channel.name}\n"
                            f"**Time:** {now_uk.strftime('%I:%M %p %Z')}"
                        ),
                        color=0xff6b35,
                        timestamp=datetime.utcnow()
                    )
                    await owner.send(embed=owner_embed)
                    self.logger.info(f"✅ Sent backup escalation DM to bot owner for {schedule.name}")
            except Exception as dm_err:
                self.logger.debug(f"Could not DM bot owner about backup escalation: {dm_err}")
            
        except Exception as e:
            self.logger.error(f"Error sending backup notification: {e}")
    
    async def auto_assign_backup_rider(self, notification, schedule, session):
        """Automatically assign backup rider when timeout is reached."""
        try:
            guild = self.bot.get_guild(schedule.guild_id)
            if not guild:
                return
                
            channel = guild.get_channel(notification.channel_id)
            if not channel:
                return
            
            # Get the backup user
            backup_user = guild.get_member(notification.backup_user_id)
            backup_name = backup_user.display_name if backup_user else "Unknown Backup Streamer"
            
            # Mark as completed and log the assignment
            session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                'is_completed': True,
                'primary_confirmed': True,  # Mark as confirmed since backup is taking over
                'primary_user_id': notification.backup_user_id  # Assign backup as primary
            })
            session.commit()
            
            # Send confirmation message
            embed = discord.Embed(
                title='✅ Backup Rider Assigned!',
                description=f'**{schedule.name}** attendance has been updated',
                color=0x00ff00
            )
            
            embed.add_field(
                name='🏆 Confirmed Streamer',
                value=f'{backup_name} has been automatically assigned as the primary streamer',
                inline=False
            )
            
            # Stored as UK wall clock time - localize for DST-aware display
            uk_tz = pytz.timezone('Europe/London')
            uk_naive = datetime.combine(datetime.now(uk_tz).date(), schedule.start_time)
            uk_time = uk_tz.localize(uk_naive)
            start_time_display = uk_time.strftime('%I:%M %p %Z')
            
            embed.add_field(
                name='📅 Train Details',
                value=f'**Start Time:** {start_time_display}\n**Duration:** {schedule.duration_minutes} minutes',
                inline=False
            )
            
            embed.set_footer(text=f'Auto-assigned at 15-minute mark • {datetime.now(pytz.timezone("Europe/London")).strftime("%H:%M UK")}')
            
            await channel.send(embed=embed)
            self.logger.info(f"Auto-assigned backup rider {backup_name} for schedule {schedule.name}")
            
        except Exception as e:
            self.logger.error(f"Error auto-assigning backup rider: {e}")
    
    async def check_backup_auto_assignment(self, notification, schedule, now, session, ping_timeout_minutes):
        """Check if backup should be auto-assigned at timeout."""
        try:
            # Only auto-assign if:
            # 1. Backup has been pinged and confirmed
            # 2. Primary still hasn't confirmed 
            # 3. We're within the timeout window
            # 4. Not already completed
            if (notification.backup_pinged and notification.backup_confirmed and 
                not notification.primary_confirmed and not notification.is_completed):
                
                import pytz
                uk_tz = pytz.timezone('Europe/London')
                train_start_naive = datetime.combine(notification.notification_date.date(), schedule.start_time)
                train_start_time = uk_tz.localize(train_start_naive)
                minutes_until_train = (train_start_time - now).total_seconds() / 60
                
                # If we're at or past the timeout threshold, auto-assign backup
                if minutes_until_train <= ping_timeout_minutes:
                    await self.auto_assign_backup_rider(notification, schedule, session)
                    
        except Exception as e:
            self.logger.error(f"Error checking backup auto-assignment: {e}")
    
    async def send_train_notification(self, schedule):
        """Send a notification for an upcoming train."""
        try:
            async with AsyncDatabaseSession() as session:
                # Get notification settings for this guild
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=schedule.guild_id
                ).first()
                
                if not settings or not settings.auto_ping_enabled:
                    return False
                
                # Get the guild and channel
                guild = self.bot.get_guild(schedule.guild_id)
                if not guild:
                    return False
                
                channel = None
                ping_role = None
                
                # Get raid notification channel and role
                if settings.raid_notification_channel_id:
                    channel = guild.get_channel(settings.raid_notification_channel_id)
                if settings.raid_ping_role_id:
                    ping_role = guild.get_role(settings.raid_ping_role_id)
                
                if not channel:
                    self.logger.warning(f"No raid notification channel set for guild {guild.name}")
                    return False
                
                # Create notification embed
                embed = discord.Embed(
                    title='🚂 Raid Train Starting Soon!',
                    description=f'**{schedule.name}** starts in {schedule.notify_before_minutes} minutes!',
                    color=0x9146ff
                )
                
                # Stored as UK wall clock time - localize for DST-aware display
                uk_tz = pytz.timezone('Europe/London')
                uk_naive = datetime.combine(datetime.now(uk_tz).date(), schedule.start_time)
                uk_time = uk_tz.localize(uk_naive)
                start_time = uk_time.strftime('%I:%M %p %Z')  # 12-hour format with timezone
                embed.add_field(
                    name='📅 Train Details',
                    value=f'**Start Time:** {start_time}\n**Duration:** {schedule.duration_minutes} minutes',
                    inline=False
                )
                
                embed.add_field(
                    name='🎯 Get Ready',
                    value='Prepare your stream for the raid train! Make sure you\'re live and ready to receive raids.',
                    inline=False
                )
                
                embed.set_footer(text=f'Scheduled notification • {datetime.now(pytz.timezone("Europe/London")).strftime("%Y-%m-%d %H:%M UK")}')
                
                # Send notification with optional role ping
                message_content = ping_role.mention if ping_role else ""
                await channel.send(content=message_content, embed=embed)
                
                self.logger.info(f"Sent train notification for '{schedule.name}' in guild {guild.name}")
                return True
                
        except Exception as e:
            self.logger.error(f"Error sending train notification: {e}")
            return False
    


    @commands.command(name='setupnotifications', help='Setup notification channels and roles for your server')
    @is_admin_or_trusted()
    async def setup_notifications(self, ctx):
        """Interactive setup for server notifications."""
        embed = discord.Embed(
            title='🔔 Notification Setup',
            description='Configure notification channels and roles for raid trains',
            color=0x00ff00
        )
        
        embed.add_field(
            name='📋 Available Setup Commands',
            value=(
                '`!setnotifchannel <type> <#channel>` - Set notification channel\n'
                '`!setpingrole <type> <@role>` - Set ping role\n'
                '`!notifstatus` - View current notification settings\n'
                '`!toggleautoping` - Enable/disable auto ping\n'
                '`!setpingtimeout <minutes>` - Set backup ping timeout'
            ),
            inline=False
        )
        
        embed.add_field(
            name='🎯 Notification Types',
            value=(
                '**raid** - Main raid train notifications\n'
                '**backup** - Backup streamer notifications\n'
                '**stream** - Stream status notifications\n'
                '**updates** - Bot update and changelog notifications'
            ),
            inline=False
        )
        
        embed.add_field(
            name='💡 Example Setup',
            value=(
                '`!setnotifchannel raid #raid-train`\n'
                '`!setpingrole raid @Raiders`\n'
                '`!setnotifchannel backup #backup-alerts`\n'
                '`!setpingrole backup @Backup-Streamers`\n'
                '`!setnotifchannel updates #announcements`\n'
                '`!setpingrole updates @Everyone`'
            ),
            inline=False
        )
        
        embed.set_footer(text='Use these commands to configure your server notifications')
        await ctx.send(embed=embed)
    
    @commands.command(name='setnotifchannel', help='Set a notification channel')
    @is_admin_or_trusted()
    async def set_notification_channel(self, ctx, notification_type: str, channel: discord.TextChannel):
        """Set notification channel for specific type."""
        valid_types = ['raid', 'backup', 'stream', 'updates']
        if notification_type.lower() not in valid_types:
            await ctx.send(f"❌ Invalid type. Use: {', '.join(valid_types)}")
            return
        
        try:
            async with AsyncDatabaseSession() as session:
                # Get or create notification settings for this guild
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                # Set the appropriate channel value
                channel_field = None
                if notification_type.lower() == 'raid':
                    channel_field = 'raid_notification_channel_id'
                elif notification_type.lower() == 'backup':
                    channel_field = 'backup_notification_channel_id'
                elif notification_type.lower() == 'stream':
                    channel_field = 'stream_notification_channel_id'
                elif notification_type.lower() == 'updates':
                    channel_field = 'updates_notification_channel_id'
                
                if not channel_field:
                    await ctx.send(f"❌ Invalid notification type: {notification_type}")
                    return
                
                if settings:  # Update existing record
                    update_data = {
                        channel_field: channel.id,
                        'updated_at': datetime.now(pytz.timezone('Europe/London'))
                    }
                    session.query(NotificationSettings).filter(NotificationSettings.id == settings.id).update(update_data)
                else:  # Create new record
                    settings_data = {
                        'guild_id': ctx.guild.id,
                        channel_field: channel.id,
                        'updated_at': datetime.now(pytz.timezone('Europe/London'))
                    }
                    settings = NotificationSettings(**settings_data)
                    session.add(settings)
                
                session.commit()
                
                embed = discord.Embed(
                    title='✅ Channel Set',
                    description=f'{notification_type.title()} notifications will be sent to {channel.mention}',
                    color=0x00ff00
                )
                await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error setting notification channel: {e}")
            await ctx.send("❌ Error setting notification channel. Please try again.")

    @commands.command(name='setpingrole', help='Set a ping role for notifications')
    @is_admin_or_trusted()
    async def set_ping_role(self, ctx, notification_type: str, role: discord.Role):
        """Set ping role for specific notification type."""
        valid_types = ['raid', 'backup', 'stream', 'updates']
        if notification_type.lower() not in valid_types:
            await ctx.send(f"❌ Invalid type. Use: {', '.join(valid_types)}")
            return
        
        try:
            async with AsyncDatabaseSession() as session:
                # Get or create notification settings for this guild
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                # Set the appropriate role value
                role_field = None
                if notification_type.lower() == 'raid':
                    role_field = 'raid_ping_role_id'
                elif notification_type.lower() == 'backup':
                    role_field = 'backup_ping_role_id'
                elif notification_type.lower() == 'stream':
                    role_field = 'stream_ping_role_id'
                elif notification_type.lower() == 'updates':
                    role_field = 'updates_ping_role_id'
                
                if not role_field:
                    await ctx.send(f"❌ Invalid notification type: {notification_type}")
                    return
                
                if settings:  # Update existing record
                    update_data = {
                        role_field: role.id,
                        'updated_at': datetime.now(pytz.timezone('Europe/London'))
                    }
                    session.query(NotificationSettings).filter(NotificationSettings.id == settings.id).update(update_data)
                else:  # Create new record
                    settings_data = {
                        'guild_id': ctx.guild.id,
                        role_field: role.id,
                        'updated_at': datetime.now(pytz.timezone('Europe/London'))
                    }
                    settings = NotificationSettings(**settings_data)
                    session.add(settings)
                
                session.commit()
                
                embed = discord.Embed(
                    title='✅ Role Set',
                    description=f'{notification_type.title()} notifications will ping {role.mention}',
                    color=0x00ff00
                )
                await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error setting ping role: {e}")
            await ctx.send("❌ Error setting ping role. Please try again.")
    
    
    @commands.command(name='notifstatus', help='View current notification settings')
    async def notification_status(self, ctx):
        """Show current notification configuration for this server."""
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
            
            embed = discord.Embed(
                title='🔔 Notification Settings',
                description=f'Current notification configuration for **{ctx.guild.name}**',
                color=0x0099ff
            )
            
            if not settings:
                embed.add_field(
                    name='⚠️ Not Configured',
                    value='Use `!setupnotifications` to configure notifications for your server.',
                    inline=False
                )
            else:
                # Raid notifications
                raid_channel = ctx.guild.get_channel(settings.raid_notification_channel_id) if settings.raid_notification_channel_id else None
                raid_role = ctx.guild.get_role(settings.raid_ping_role_id) if settings.raid_ping_role_id else None
                
                embed.add_field(
                    name='🎯 Raid Train Notifications',
                    value=(
                        f'**Channel:** {raid_channel.mention if raid_channel else "Not set"}\n'
                        f'**Ping Role:** {raid_role.mention if raid_role else "Not set"}'
                    ),
                    inline=False
                )
                
                # Backup notifications
                backup_channel = ctx.guild.get_channel(settings.backup_notification_channel_id) if settings.backup_notification_channel_id else None
                backup_role = ctx.guild.get_role(settings.backup_ping_role_id) if settings.backup_ping_role_id else None
                
                embed.add_field(
                    name='🔄 Backup Notifications',
                    value=(
                        f'**Channel:** {backup_channel.mention if backup_channel else "Not set"}\n'
                        f'**Ping Role:** {backup_role.mention if backup_role else "Not set"}'
                    ),
                    inline=False
                )
                
                # Stream notifications  
                stream_channel = ctx.guild.get_channel(settings.stream_notification_channel_id) if settings.stream_notification_channel_id else None
                stream_role = ctx.guild.get_role(settings.stream_ping_role_id) if settings.stream_ping_role_id else None
                
                embed.add_field(
                    name='📺 Stream Notifications',
                    value=(
                        f'**Channel:** {stream_channel.mention if stream_channel else "Not set"}\n'
                        f'**Ping Role:** {stream_role.mention if stream_role else "Not set"}'
                    ),
                    inline=False
                )
                
                # Updates notifications
                updates_channel = ctx.guild.get_channel(settings.updates_notification_channel_id) if settings.updates_notification_channel_id else None
                updates_role = ctx.guild.get_role(settings.updates_ping_role_id) if settings.updates_ping_role_id else None
                
                embed.add_field(
                    name='📢 Updates Notifications',
                    value=(
                        f'**Channel:** {updates_channel.mention if updates_channel else "Not set"}\n'
                        f'**Ping Role:** {updates_role.mention if updates_role else "Not set"}'
                    ),
                    inline=False
                )
                
                # Settings
                embed.add_field(
                    name='⚙️ General Settings',
                    value=(
                        f'**Auto Ping:** {"✅ Enabled" if settings.auto_ping_enabled else "❌ Disabled"}\n'
                        f'**Backup System:** {"✅ Enabled" if settings.backup_system_enabled else "❌ Disabled"}\n'
                        f'**Ping Timeout:** {settings.ping_timeout_minutes} minutes'
                    ),
                    inline=False
                )
            
                embed.set_footer(text='Use !setupnotifications for configuration help')
                await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error getting notification status: {e}")
            await ctx.send("❌ Error retrieving notification settings.")
    
    @commands.command(name='toggleautoping', help='Enable/disable automatic pings')
    @commands.has_permissions(manage_guild=True)
    async def toggle_auto_ping(self, ctx):
        """Toggle automatic ping system on/off."""
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                if not settings:
                    settings = NotificationSettings(guild_id=ctx.guild.id)
                    session.add(settings)
                
                # Toggle the setting
                new_value = not settings.auto_ping_enabled
                session.query(NotificationSettings).filter(
                    NotificationSettings.id == settings.id
                ).update({
                    'auto_ping_enabled': new_value,
                    'updated_at': datetime.now(pytz.timezone('Europe/London'))
                })
                session.commit()
                
                status = "enabled" if settings.auto_ping_enabled else "disabled"
                embed = discord.Embed(
                    title=f'🔔 Auto Ping {status.title()}',
                    description=f'Automatic ping system has been **{status}** for this server.',
                    color=0x00ff00 if settings.auto_ping_enabled else 0xff9900
                )
                await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error toggling auto ping: {e}")
            await ctx.send("❌ Error updating auto ping setting.")
    
    @commands.command(name='setpingtimeout', help='Set backup ping timeout in minutes')
    @is_admin_or_trusted()
    async def set_ping_timeout(self, ctx, minutes: int):
        """Set timeout before backup streamer is pinged."""
        if minutes < 1 or minutes > 60:
            await ctx.send("❌ Timeout must be between 1 and 60 minutes.")
            return
        
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                if not settings:
                    settings = NotificationSettings(guild_id=ctx.guild.id)
                    session.add(settings)
                
                session.query(NotificationSettings).filter(
                    NotificationSettings.id == settings.id
                ).update({
                    'ping_timeout_minutes': minutes,
                    'updated_at': datetime.now(pytz.timezone('Europe/London'))
                })
                session.commit()
                
                embed = discord.Embed(
                    title='⏰ Timeout Updated',
                    description=f'Backup streamers will be pinged after **{minutes} minutes** if no response.',
                    color=0x00ff00
                )
                await ctx.send(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Error setting ping timeout: {e}")
            await ctx.send("❌ Error updating ping timeout.")
    
    @commands.command(name='schedulenotif', help='Set notification timing for a train schedule')
    @commands.has_permissions(manage_guild=True)
    async def schedule_notification_timing(self, ctx, schedule_id: str = "", minutes: str = ""):
        """Set how many minutes before a train schedule to send notifications."""
        if not schedule_id or not minutes:
            await ctx.send('❌ Usage: `!schedulenotif <schedule_id> <minutes>`\nExample: `!schedulenotif 1 30`')
            return
        
        try:
            schedule_id_int = int(schedule_id)
            minutes_int = int(minutes)
            
            if minutes_int < 1 or minutes_int > 120:
                await ctx.send('❌ Notification time must be between 1 and 120 minutes.')
                return
            
            async with AsyncDatabaseSession() as session:
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id_int,
                    guild_id=ctx.guild.id
                ).first()
                
                if not schedule:
                    await ctx.send(f'❌ Train schedule with ID {schedule_id} not found.')
                    return
                
                old_timing = schedule.notify_before_minutes
                session.query(TrainSchedule).filter(
                    TrainSchedule.id == schedule.id
                ).update({
                    'notify_before_minutes': minutes_int,
                    'updated_at': datetime.now(pytz.timezone('Europe/London'))
                })
                session.commit()
                
                embed = discord.Embed(
                    title='🔔 Notification Timing Updated',
                    description=f'Updated notification timing for **{schedule.name}**',
                    color=0x00ff00
                )
                
                embed.add_field(
                    name='⏰ Timing Details',
                    value=f'**Previous:** {old_timing} minutes before\n**New:** {minutes_int} minutes before\n**Schedule ID:** {schedule_id}',
                    inline=False
                )
                
                embed.add_field(
                    name='📅 Next Notification',
                    value=f'Next automatic notification will be sent {minutes_int} minutes before the train starts',
                    inline=False
                )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Updated notification timing for schedule {schedule.name}: {old_timing} -> {minutes_int} minutes")
                
        except ValueError:
            await ctx.send('❌ Invalid input. Schedule ID and minutes must be numbers.')
        except Exception as e:
            self.logger.error(f"Error updating schedule notification timing: {e}")
            await ctx.send('❌ Error updating notification timing. Please try again.')
    
    
    async def check_all_notifications_for_auto_assignment(self, now, session):
        """Check all existing notifications for potential auto-assignment."""
        try:
            today_date = now.date()
            
            # OPTIMIZATION: Only check notifications from today within 2 hours of train start
            # This reduces database load significantly
            pending_notifications = session.query(TrainNotification).filter(
                TrainNotification.notification_date >= today_date,
                TrainNotification.is_completed == False,
                TrainNotification.backup_pinged == True,
                TrainNotification.backup_confirmed == True,
                TrainNotification.primary_confirmed == False
            ).limit(20).all()  # Limit to 20 most recent to reduce query load
            
            for notification in pending_notifications:
                if notification.schedule_id:  # Skip test notifications
                    schedule = session.query(TrainSchedule).filter_by(id=notification.schedule_id).first()
                    if schedule:
                        # Get notification settings for timeout
                        settings = session.query(NotificationSettings).filter_by(
                            guild_id=schedule.guild_id
                        ).first()
                        
                        ping_timeout_minutes = settings.ping_timeout_minutes if settings else 15
                        await self.check_backup_auto_assignment(notification, schedule, now, session, ping_timeout_minutes)
                        
        except Exception as e:
            self.logger.error(f"Error checking all notifications for auto-assignment: {e}")

    @commands.command(name='backupsignup', help='Sign up to become a backup streamer')
    async def backup_signup(self, ctx):
        """Allow users to self-assign the backup role."""
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                if not settings or not settings.backup_ping_role_id:
                    embed = discord.Embed(
                        title='❌ Backup Role Not Configured',
                        description='Server admins need to set up the backup role first.\nUse `!setpingrole backup @YourBackupRole`',
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                backup_role = ctx.guild.get_role(settings.backup_ping_role_id)
                if not backup_role:
                    embed = discord.Embed(
                        title='❌ Backup Role Not Found',
                        description='The configured backup role no longer exists. Please contact server admins.',
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Check if user already has the role
                if backup_role in ctx.author.roles:
                    embed = discord.Embed(
                        title='ℹ️ Already a Backup Streamer',
                        description=f'You already have the {backup_role.mention} role!',
                        color=0x0099ff
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Add the backup role to the user
                await ctx.author.add_roles(backup_role, reason="Self-assigned backup streamer role")
                
                embed = discord.Embed(
                    title='✅ Welcome Backup Streamer!',
                    description=f'You now have the {backup_role.mention} role!\nYou\'ll be pinged when backup streamers are needed.',
                    color=0x00ff00
                )
                embed.add_field(
                    name='🔔 What This Means',
                    value='• You\'ll get notifications when primary streamers are unavailable\n• React with ✅ to confirm you can stream as backup\n• Help keep the raid train running smoothly!',
                    inline=False
                )
                await ctx.send(embed=embed)
                
                self.logger.info(f"{ctx.author} signed up as backup streamer in {ctx.guild.name}")
                
        except Exception as e:
            self.logger.error(f"Error in backup signup: {e}")
            await ctx.send("❌ Error signing up for backup role. Please try again.")

    @is_admin_or_trusted()
    @commands.command(name='setbackupsignup', help='Configure a message for backup role reactions')
    async def set_backup_signup(self, ctx, message_or_url: str, emoji: str = "✅", remove_on_unreact: bool = True):
        """Configure an existing message for backup role signup via reactions."""
        try:
            # Parse message ID from URL or direct ID
            message_id = None
            if message_or_url.startswith('https://discord.com/channels/'):
                # Extract message ID from URL
                parts = message_or_url.split('/')
                if len(parts) >= 3:
                    message_id = int(parts[-1])
            else:
                # Direct message ID
                message_id = int(message_or_url)
            
            # Try to fetch the message
            try:
                message = await ctx.fetch_message(message_id)
            except:
                # Try to find it in other channels
                message = None
                for channel in ctx.guild.text_channels:
                    try:
                        message = await channel.fetch_message(message_id)
                        break
                    except:
                        continue
                        
                if not message:
                    await ctx.send(f"❌ Could not find message with ID {message_id}")
                    return
                    
            # Validate emoji
            if len(emoji) > 10:
                await ctx.send("❌ Emoji must be 10 characters or less")
                return
                
            async with AsyncDatabaseSession() as session:
                # Get or create notification settings
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=ctx.guild.id
                ).first()
                
                if not settings:
                    settings = NotificationSettings(guild_id=ctx.guild.id)
                    session.add(settings)
                
                # Check if backup role is configured
                if not settings.backup_ping_role_id:
                    await ctx.send("❌ Please configure a backup role first using `!setpingrole backup @YourRole`")
                    return
                
                # Update backup signup settings
                session.query(NotificationSettings).filter(
                    NotificationSettings.id == settings.id
                ).update({
                    'backup_signup_message_id': message_id,
                    'backup_signup_emoji': emoji,
                    'backup_remove_on_unreact': remove_on_unreact,
                    'updated_at': datetime.now(pytz.timezone('Europe/London'))
                })
                
                session.commit()
                
                # Add the configured emoji to the message
                try:
                    await message.add_reaction(emoji)
                except:
                    pass  # Reaction might already exist
                
                embed = discord.Embed(
                    title="✅ Backup Signup Message Configured",
                    description=f"Message configured for backup role reactions!\n\n**Message:** [Jump to message]({message.jump_url})\n**Emoji:** {emoji}\n**Remove on unreact:** {'Yes' if remove_on_unreact else 'No'}",
                    color=0x00ff00
                )
                embed.add_field(
                    name="📋 Next Steps",
                    value=f"• Users can now react with {emoji} to get the backup role\n• Use `!syncbackupsignup` to assign roles to current reactors",
                    inline=False
                )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Configured backup signup message {message_id} in {ctx.guild.name}")
                
        except ValueError:
            await ctx.send("❌ Invalid message ID or URL")
        except Exception as e:
            self.logger.error(f"Error setting backup signup message: {e}")
            await ctx.send("❌ Error configuring backup signup message. Please try again.")

    @is_admin_or_trusted()
    @commands.command(name='syncbackupsignup', help='Sync backup roles with current reactors')
    async def sync_backup_signup(self, ctx):
        """Sync backup roles with users who have already reacted to the signup message."""
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=ctx.guild.id
                ).first()
                
                if not settings or not settings.backup_signup_message_id:
                    await ctx.send("❌ No backup signup message configured. Use `!setbackupsignup` first.")
                    return
                
                if not settings.backup_ping_role_id:
                    await ctx.send("❌ No backup role configured. Use `!setpingrole backup @YourRole` first.")
                    return
                
                backup_role = ctx.guild.get_role(settings.backup_ping_role_id)
                if not backup_role:
                    await ctx.send("❌ Configured backup role no longer exists.")
                    return
                
                # Find the message
                message = None
                for channel in ctx.guild.text_channels:
                    try:
                        message = await channel.fetch_message(settings.backup_signup_message_id)
                        break
                    except:
                        continue
                        
                if not message:
                    await ctx.send(f"❌ Could not find backup signup message (ID: {settings.backup_signup_message_id})")
                    return
                
                # Find the target emoji reaction
                target_reaction = None
                for reaction in message.reactions:
                    if str(reaction.emoji) == settings.backup_signup_emoji:
                        target_reaction = reaction
                        break
                
                if not target_reaction:
                    await ctx.send(f"❌ No reactions found with emoji {settings.backup_signup_emoji}")
                    return
                
                # Get all users who reacted with the target emoji
                reactors = []
                async for user in target_reaction.users():
                    if not user.bot:  # Ignore bot reactions
                        reactors.append(user)
                
                # Process role assignments
                added_count = 0
                removed_count = 0
                
                # Add roles to reactors who don't have it
                for user in reactors:
                    if backup_role not in user.roles:
                        try:
                            await user.add_roles(backup_role, reason="Backup signup sync")
                            added_count += 1
                        except Exception as e:
                            self.logger.warning(f"Could not add backup role to {user.name}: {e}")
                
                # Remove roles from users who no longer react (if configured)
                if settings.backup_remove_on_unreact:
                    reactor_ids = [user.id for user in reactors]
                    for member in ctx.guild.members:
                        if (backup_role in member.roles and 
                            member.id not in reactor_ids and 
                            not member.bot):
                            try:
                                await member.remove_roles(backup_role, reason="Backup signup sync - no reaction")
                                removed_count += 1
                            except Exception as e:
                                self.logger.warning(f"Could not remove backup role from {member.name}: {e}")
                
                embed = discord.Embed(
                    title="🔄 Backup Signup Sync Complete",
                    description=f"Synced backup roles with message reactions",
                    color=0x00ff00
                )
                embed.add_field(
                    name="📊 Results",
                    value=f"• **Roles added:** {added_count}\n• **Roles removed:** {removed_count}\n• **Total reactors:** {len(reactors)}",
                    inline=False
                )
                
                if added_count > 0 or removed_count > 0:
                    embed.add_field(
                        name="ℹ️ What happened?",
                        value="Roles were automatically assigned/removed to match current reactions on the signup message.",
                        inline=False
                    )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Synced backup signup in {ctx.guild.name}: +{added_count}, -{removed_count}")
                
        except Exception as e:
            self.logger.error(f"Error syncing backup signup: {e}")
            await ctx.send("❌ Error syncing backup signup. Please try again.")

    @commands.command(name='backupstreamers', help='List all backup streamers')
    async def backup_list(self, ctx):
        """Display all users with the backup role."""
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                if not settings or not settings.backup_ping_role_id:
                    embed = discord.Embed(
                        title='❌ Backup Role Not Configured',
                        description='Server admins need to set up the backup role first.\nUse `!setpingrole backup @YourBackupRole`',
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                backup_role = ctx.guild.get_role(settings.backup_ping_role_id)
                if not backup_role:
                    embed = discord.Embed(
                        title='❌ Backup Role Not Found',
                        description='The configured backup role no longer exists. Please contact server admins.',
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Get all members with the backup role
                backup_members = [member for member in ctx.guild.members if backup_role in member.roles and not member.bot]
                
                embed = discord.Embed(
                    title='🔄 Backup Streamers',
                    description=f'Current backup streamers in **{ctx.guild.name}**',
                    color=0x9146ff
                )
                
                if backup_members:
                    member_list = []
                    for i, member in enumerate(backup_members, 1):
                        status_emoji = "🟢" if str(member.status) == "online" else "🟡" if str(member.status) == "idle" else "🔴" if str(member.status) == "dnd" else "⚪"
                        member_list.append(f"{i}. {status_emoji} {member.display_name}")
                    
                    # Split into chunks if too many members
                    chunk_size = 20
                    for i in range(0, len(member_list), chunk_size):
                        chunk = member_list[i:i + chunk_size]
                        field_name = f'👥 Backup Streamers ({i+1}-{min(i+chunk_size, len(member_list))})'
                        if i == 0:
                            field_name = f'👥 Backup Streamers ({len(backup_members)} total)'
                        embed.add_field(
                            name=field_name,
                            value='\n'.join(chunk),
                            inline=False
                        )
                else:
                    embed.add_field(
                        name='📭 No Backup Streamers',
                        value=f'No one has the {backup_role.mention} role yet.\nUse `!backupsignup` to become a backup streamer!',
                        inline=False
                    )
                
                embed.add_field(
                    name='🆘 Want to Help?',
                    value='Use `!backupsignup` to join the backup streamer team!',
                    inline=False
                )
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error listing backup streamers: {e}")
            await ctx.send("❌ Error retrieving backup streamers. Please try again.")

    @commands.command(name='addbackup', help='Add someone as a backup streamer (Trusted users only)')
    @is_admin_or_trusted()
    async def add_backup(self, ctx, member: discord.Member):
        """Add backup role to a specific member (trusted users only)."""
        # Prevent duplicate responses for hybrid commands
        if hasattr(ctx, 'interaction') and ctx.interaction:
            if ctx.interaction.response.is_done():
                return
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                if not settings or not settings.backup_ping_role_id:
                    embed = discord.Embed(
                        title='❌ Backup Role Not Configured',
                        description='Set up the backup role first using `!setpingrole backup @YourBackupRole`',
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                backup_role = ctx.guild.get_role(settings.backup_ping_role_id)
                if not backup_role:
                    embed = discord.Embed(
                        title='❌ Backup Role Not Found',
                        description='The configured backup role no longer exists.',
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Check if member already has the role
                if backup_role in member.roles:
                    embed = discord.Embed(
                        title='ℹ️ Already a Backup Streamer',
                        description=f'{member.mention} already has the {backup_role.mention} role!',
                        color=0x0099ff
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Add the backup role to the member
                await member.add_roles(backup_role, reason=f"Added as backup streamer by {ctx.author}")
                
                embed = discord.Embed(
                    title='✅ Backup Streamer Added',
                    description=f'{member.mention} has been added as a backup streamer!',
                    color=0x00ff00
                )
                embed.add_field(
                    name='🔔 Role Added',
                    value=f'{backup_role.mention} role assigned',
                    inline=False
                )
                await ctx.send(embed=embed)
                
                self.logger.info(f"{ctx.author} added {member} as backup streamer in {ctx.guild.name}")
                
        except Exception as e:
            self.logger.error(f"Error adding backup streamer: {e}")
            await ctx.send("❌ Error adding backup streamer. Please try again.")

    @commands.command(name='removebackup', help='Remove someone as a backup streamer (Trusted users only)')
    @is_admin_or_trusted()
    async def remove_backup(self, ctx, member: discord.Member):
        """Remove backup role from a specific member (trusted users only)."""
        # Prevent duplicate responses for hybrid commands
        if hasattr(ctx, 'interaction') and ctx.interaction:
            if ctx.interaction.response.is_done():
                return
        try:
            async with AsyncDatabaseSession() as session:
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == ctx.guild.id
                ).first()
                
                if not settings or not settings.backup_ping_role_id:
                    embed = discord.Embed(
                        title='❌ Backup Role Not Configured',
                        description='Set up the backup role first using `!setpingrole backup @YourBackupRole`',
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                backup_role = ctx.guild.get_role(settings.backup_ping_role_id)
                if not backup_role:
                    embed = discord.Embed(
                        title='❌ Backup Role Not Found',
                        description='The configured backup role no longer exists.',
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Check if member has the role
                if backup_role not in member.roles:
                    embed = discord.Embed(
                        title='ℹ️ Not a Backup Streamer',
                        description=f'{member.mention} doesn\'t have the {backup_role.mention} role.',
                        color=0x0099ff
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Remove the backup role from the member
                await member.remove_roles(backup_role, reason=f"Removed as backup streamer by {ctx.author}")
                
                embed = discord.Embed(
                    title='✅ Backup Streamer Removed',
                    description=f'{member.mention} has been removed as a backup streamer.',
                    color=0x00ff00
                )
                embed.add_field(
                    name='🔕 Role Removed',
                    value=f'{backup_role.mention} role removed',
                    inline=False
                )
                await ctx.send(embed=embed)
                
                self.logger.info(f"{ctx.author} removed {member} as backup streamer in {ctx.guild.name}")
                
        except Exception as e:
            self.logger.error(f"Error removing backup streamer: {e}")
            await ctx.send("❌ Error removing backup streamer. Please try again.")

    async def check_completed_sessions(self):
        """Check for completed train sessions and post slot-end notifications + attendance summaries."""
        try:
            async with self.processing_lock:
                async with AsyncDatabaseSession() as session:
                    from datetime import datetime, timedelta, date
                    import pytz
                    from models import TrainSlotCompletion, TrainParticipant, NotificationSettings
                    
                    uk_tz = pytz.timezone('Europe/London')
                    now_uk = datetime.now(uk_tz)
                    today_date = now_uk.date()
                    
                    # Look for schedules that have slots today (by weekday)
                    candidate_schedules = session.query(TrainSchedule).filter(
                        TrainSchedule.is_active == True,
                        TrainSchedule.day_of_week == today_date.weekday()
                    ).all()
                    
                    # Filter out one-time schedules not matching today's date
                    schedules = []
                    for s in candidate_schedules:
                        if s.specific_date is None:
                            schedules.append(s)  # Recurring — always relevant
                        else:
                            sd = s.specific_date
                            if isinstance(sd, str):
                                from datetime import datetime as _dtc
                                sd = _dtc.strptime(sd, '%Y-%m-%d').date()
                            if sd == today_date:
                                schedules.append(s)
                    
                    for schedule in schedules:
                        # Build timezone-aware slot_start and slot_end in UK time
                        start_time_value = schedule.start_time
                        duration_value = schedule.duration_minutes
                        slot_start_naive = datetime.combine(today_date, start_time_value)
                        slot_start = uk_tz.localize(slot_start_naive)
                        slot_end = slot_start + timedelta(minutes=duration_value)
                        
                        # Handle cross-midnight slots (rare edge case)
                        if slot_end < slot_start:
                            slot_end += timedelta(days=1)
                        
                        # Check for 5-minute warning before slot ends
                        minutes_until_end = (slot_end - now_uk).total_seconds() / 60
                        
                        if 4 <= minutes_until_end <= 6:  # 5-minute mark (±1 min window)
                            # Check if we've already sent 5-min warning for this schedule today (DATABASE CHECK)
                            existing_warning = session.query(TrainSlotCompletion).filter_by(
                                schedule_id=schedule.id,
                                completion_date=today_date,
                                warning_sent=True
                            ).first()
                            
                            if not existing_warning:
                                # Send warning and mark in database
                                await self.send_five_minute_warning(schedule, slot_start, slot_end, session)
                                
                                # Create or update completion record to track warning
                                completion_record = TrainSlotCompletion(
                                    schedule_id=schedule.id,
                                    guild_id=schedule.guild_id,
                                    completion_date=today_date,
                                    slot_start=slot_start_naive,
                                    slot_end=(slot_end.replace(tzinfo=None)),
                                    warning_sent=True,
                                    attendance_channel_id=None,
                                    participant_count=0
                                )
                                session.add(completion_record)
                                session.commit()
                        
                        # Check for slot-end notifications (0-5 minutes after slot ends)
                        minutes_since_end = (now_uk - slot_end).total_seconds() / 60
                        
                        if 0 <= minutes_since_end <= 5:
                            # Check if we've already sent slot-end notification for this schedule today
                            existing_completion = session.query(TrainSlotCompletion).filter_by(
                                schedule_id=schedule.id,
                                completion_date=today_date
                            ).first()
                            
                            # Only send if: no record exists, OR record exists but only has warning (not full completion)
                            if not existing_completion or (existing_completion and existing_completion.participant_count == 0):
                                # Send slot-end notification and record completion
                                await self.send_slot_end_notification(schedule, slot_start, slot_end, session)
                        
                        # Check for attendance summaries (5-30 minutes after slot ends)
                        elif 5 <= minutes_since_end <= 30:
                            # Memory cache as first-pass guard (prevents redundant DB queries)
                            attendance_posted_key = f"attendance_posted_{schedule.guild_id}_{schedule.id}_{today_date}"
                            
                            if attendance_posted_key not in self.sent_notifications_cache:
                                # DB-backed dedup: survive bot restarts within the window
                                completion_rec = session.query(TrainSlotCompletion).filter_by(
                                    schedule_id=schedule.id,
                                    completion_date=today_date
                                ).first()
                                
                                already_posted = completion_rec and completion_rec.attendance_posted
                                
                                if not already_posted:
                                    await self.post_session_attendance_summary(schedule, today_date, session)
                                    self.sent_notifications_cache.add(attendance_posted_key)
                                    # Persist so restarts don't re-post
                                    if completion_rec:
                                        session.query(TrainSlotCompletion).filter_by(
                                            id=completion_rec.id
                                        ).update({'attendance_posted': True})
                                        session.commit()
                                else:
                                    # Already posted in DB — keep cache warm too
                                    self.sent_notifications_cache.add(attendance_posted_key)
                                
        except Exception as e:
            self.logger.error(f"Error checking completed sessions: {e}")
    
    async def post_session_attendance_summary(self, schedule, session_date, session):
        """Post attendance summary showing everyone who was in Twitch chat during the train."""
        try:
            from models import TwitchChatAttendance, TrainParticipant
            
            # Get notification channel
            notification_settings = session.query(NotificationSettings).filter_by(
                guild_id=schedule.guild_id
            ).first()
            
            if not notification_settings or not notification_settings.attendance_channel_id:
                self.logger.info(f"No attendance channel set for guild {schedule.guild_id}")
                return
            
            # Get Discord channel
            guild = self.bot.get_guild(schedule.guild_id)
            if not guild:
                return
                
            channel = guild.get_channel(notification_settings.attendance_channel_id)
            if not channel:
                return
            
            # Get Twitch chat attendance data for this session - this shows EVERYONE in chat
            twitch_chat_attendance = session.query(TwitchChatAttendance).filter_by(
                schedule_id=schedule.id,
                train_date=session_date
            ).all()
            
            if not twitch_chat_attendance:
                self.logger.info(f"No Twitch chat attendance data for {schedule.name}")
                return  # No chat data to report
            
            # Get participants who signed up (for reference)
            participants = session.query(TrainParticipant).filter_by(
                schedule_id=schedule.id,
                is_active=True
            ).all()
            signup_user_ids = {p.user_id for p in participants}
            
            # Process ALL chat attendance records (not just signups)
            present_in_chat = []
            
            for record in twitch_chat_attendance:
                if record.was_present_in_chat:
                    # Check if this was a signed-up rider or just a viewer
                    was_signed_up = record.user_id in signup_user_ids
                    
                    present_in_chat.append({
                        'display_name': record.twitch_username,
                        'twitch_username': record.twitch_username,
                        'was_signed_up': was_signed_up,
                        'message_count': record.total_messages
                    })
            
            if not present_in_chat:
                self.logger.info(f"No one present in chat for {schedule.name}")
                return
            
            # Sort by message count (most active first)
            present_in_chat.sort(key=lambda x: x['message_count'], reverse=True)
            
            # Calculate statistics
            total_in_chat = len(present_in_chat)
            signed_up_present = len([p for p in present_in_chat if p['was_signed_up']])
            viewers_only = total_in_chat - signed_up_present
            total_messages = sum(p['message_count'] for p in present_in_chat)
            
            # Build embed
            import pytz
            uk_tz = pytz.timezone('Europe/London')
            uk_dt = uk_tz.localize(datetime.combine(datetime.now(uk_tz).date(), schedule.start_time))
            uk_time_str = uk_dt.strftime('%I:%M %p %Z').lstrip('0')
            
            embed = discord.Embed(
                title=f"📊 Twitch Chat Activity Report",
                description=f"**{schedule.name}** - {session_date.strftime('%B %d, %Y')}",
                color=0x9146FF,  # Twitch purple
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🚂 Session Info",
                value=f"**Time:** {uk_time_str}\n**Duration:** {schedule.duration_minutes} min",
                inline=True
            )
            
            embed.add_field(
                name="👥 Chat Participants",
                value=f"**Total in Chat:** {total_in_chat}\n**Riders:** {signed_up_present}\n**Viewers:** {viewers_only}\n**Total Messages:** {total_messages}",
                inline=True
            )
            
            embed.add_field(
                name="📊 Activity",
                value=f"**Avg Messages:** {round(total_messages / total_in_chat, 1) if total_in_chat > 0 else 0}",
                inline=True
            )
            
            # Show everyone who was in chat (limit to 20 to avoid embed size limits)
            if present_in_chat:
                chat_list = []
                for i, person in enumerate(present_in_chat[:20], 1):
                    rider_badge = "🚂" if person['was_signed_up'] else "👁️"
                    msg_count = f"({person['message_count']} msg)"
                    chat_list.append(f"{i}. {rider_badge} **{person['display_name']}** `{person['twitch_username']}` {msg_count}")
                
                embed.add_field(
                    name="💬 Chat Participants (sorted by activity)",
                    value='\n'.join(chat_list) + (f"\n_...and {len(present_in_chat) - 20} more_" if len(present_in_chat) > 20 else ""),
                    inline=False
                )
                
                embed.add_field(
                    name="Legend",
                    value="🚂 = Signed-up rider | 👁️ = Viewer/Supporter",
                    inline=False
                )
            
            embed.set_footer(text=f"Train ID: {schedule.id} • Tracked via Twitch IRC monitoring")
            
            # Send the summary
            await channel.send(embed=embed)
            self.logger.info(f"Posted attendance summary for {schedule.name} (ID: {schedule.id})")
            
        except Exception as e:
            self.logger.error(f"Error posting session attendance summary: {e}")

    async def send_five_minute_warning(self, schedule, slot_start, slot_end, session):
        """Send 5-minute warning to current rider when next rider is ready."""
        try:
            from models import TrainParticipant, NotificationSettings
            
            # Get notification settings
            notification_settings = session.query(NotificationSettings).filter_by(
                guild_id=schedule.guild_id
            ).first()
            
            if not notification_settings or not notification_settings.raid_notification_channel_id:
                self.logger.debug(f"No raid notification channel set for guild {schedule.guild_id}")
                return
            
            # Get Discord guild and channel
            guild = self.bot.get_guild(schedule.guild_id)
            if not guild:
                return
                
            channel = guild.get_channel(notification_settings.raid_notification_channel_id)
            if not channel:
                return
            
            # Get the current rider (participant for this schedule)
            current_participant = session.query(TrainParticipant).filter_by(
                schedule_id=schedule.id,
                is_active=True
            ).first()
            
            if not current_participant:
                self.logger.debug(f"No current participant for {schedule.name}, skipping 5-min warning")
                return
            
            # Try to find the next schedule (next time slot)
            all_schedules = session.query(TrainSchedule).filter_by(
                guild_id=schedule.guild_id,
                day_of_week=schedule.day_of_week,
                is_active=True
            ).order_by(TrainSchedule.start_time).all()
            
            # Find next schedule after current one and check if someone confirmed
            next_participant_display_name = None
            next_schedule_name = None
            current_found = False
            today_date = slot_start.date()
            
            for sched in all_schedules:
                if current_found:
                    # This is the next schedule - check if someone CONFIRMED (reacted)
                    from models import TrainNotification
                    confirmed_notification = session.query(TrainNotification).filter_by(
                        schedule_id=sched.id,
                        notification_date=today_date,
                        primary_confirmed=True  # Someone reacted with ✅
                    ).first()
                    
                    if confirmed_notification and confirmed_notification.primary_user_id:
                        # Get the user who confirmed
                        next_participant_user = session.query(TrainParticipant).filter_by(
                            schedule_id=sched.id,
                            user_id=confirmed_notification.primary_user_id,
                            is_active=True
                        ).first()
                        
                        if next_participant_user:
                            next_participant_display_name = next_participant_user.display_name
                            next_schedule_name = sched.name
                            break
                if sched.id == schedule.id:
                    current_found = True
            
            # Build the message
            import pytz
            uk_tz = pytz.timezone('Europe/London')
            slot_end_time = slot_end.strftime('%I:%M %p %Z').lstrip('0')
            
            embed = discord.Embed(
                title="⏰ 5 Minutes Remaining!",
                description=f"Your train slot is almost over, {self._resolve_participant_mention(current_participant, guild)}!",
                color=0xFFD700,  # Gold color
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="🚂 Current Slot",
                value=f"**{schedule.name}**\nEnds at **{slot_end_time}**",
                inline=True
            )
            
            if next_participant_display_name:
                embed.add_field(
                    name="🎯 Next Up",
                    value=f"{next_participant_display_name} is ready!\n*({next_schedule_name})*",
                    inline=True
                )
                message_text = f"🎉 **Thank you for being part of the train!**\n\n✅ **{next_participant_display_name}** confirmed they're ready for the next slot\n⏰ Please **raid out** when your time ends at **{slot_end_time}**"
            else:
                message_text = f"🎉 **Thank you for being part of the train!**\n\n⏰ Your slot ends at **{slot_end_time}**\n🚀 Please **raid out** when your time is up!"
            
            embed.add_field(
                name="💙 Thank You!",
                value=message_text,
                inline=False
            )
            
            embed.set_footer(text=f"5-minute warning • Schedule ID: {schedule.id}")
            
            # Send the message
            await channel.send(self._resolve_participant_mention(current_participant, guild), embed=embed)

            self.logger.info(f"✅ Sent 5-minute warning for '{schedule.name}' to {current_participant.display_name}")
            
        except Exception as e:
            self.logger.error(f"Error sending 5-minute warning: {e}")
            import traceback
            traceback.print_exc()

    async def send_slot_end_notification(self, schedule, slot_start, slot_end, session):
        """Send notification when a train slot ends."""
        try:
            from models import TrainSlotCompletion, TrainParticipant, NotificationSettings
            
            # Get attendance notification settings
            notification_settings = session.query(NotificationSettings).filter_by(
                guild_id=schedule.guild_id
            ).first()
            
            if not notification_settings or not notification_settings.attendance_channel_id:
                self.logger.debug(f"No attendance channel set for guild {schedule.guild_id}, skipping slot-end notification")
                return
            
            # Get Discord guild and channel
            guild = self.bot.get_guild(schedule.guild_id)
            if not guild:
                self.logger.warning(f"Guild {schedule.guild_id} not found")
                return
                
            channel = guild.get_channel(notification_settings.attendance_channel_id)
            if not channel:
                self.logger.warning(f"Attendance channel {notification_settings.attendance_channel_id} not found in guild {guild.name}")
                return
            
            # Get participants who were active in this slot
            participants = session.query(TrainParticipant).filter(
                TrainParticipant.schedule_id == schedule.id,
                TrainParticipant.is_active == True
            ).all()
            
            if not participants:
                self.logger.debug(f"No participants found for schedule {schedule.id}, skipping slot-end notification")
                return
            
            # Create the slot completion notification embed
            embed = discord.Embed(
                title="🚂 Train Slot Completed",
                description=f"**{schedule.name}** (#{schedule.id}) has ended",
                color=0x667eea,
                timestamp=slot_end
            )
            
            # Add slot timing information
            duration_str = f"{schedule.duration_minutes} minutes"
            embed.add_field(
                name="🕐 Slot Window",
                value=f"**Start:** {slot_start.strftime('%I:%M %p UK')}\n**End:** {slot_end.strftime('%I:%M %p UK')}\n**Duration:** {duration_str}",
                inline=True
            )
            
            # Add participant count
            embed.add_field(
                name="👥 Participants",
                value=f"**Total:** {len(participants)}",
                inline=True
            )
            
            # List participants with Twitch accounts (if any)
            participant_list = []
            for participant in participants[:15]:  # Limit to avoid embed limits
                # Get their Twitch account if linked (stored in User model)
                user_record = session.query(User).filter_by(id=participant.user_id).first()
                
                if user_record and user_record.twitch_login:
                    participant_list.append(f"<@{participant.user_id}> ({user_record.twitch_login})")
                else:
                    participant_list.append(f"<@{participant.user_id}>")
            
            if participant_list:
                participants_text = '\n'.join(participant_list)
                if len(participants) > 15:
                    participants_text += f"\n*... and {len(participants) - 15} more*"
                
                embed.add_field(
                    name="📋 Participant List",
                    value=participants_text,
                    inline=False
                )
            
            embed.set_footer(text=f"Schedule ID: {schedule.id} • Automatic slot completion notification")
            
            # Send the notification
            await channel.send(embed=embed)
            
            # Record this completion in database to prevent duplicates
            # Check if a record already exists (from 5-minute warning)
            existing_record = session.query(TrainSlotCompletion).filter_by(
                schedule_id=schedule.id,
                completion_date=slot_start.date()
            ).first()
            
            if existing_record:
                # Update existing record with completion data
                existing_record.attendance_channel_id = notification_settings.attendance_channel_id
                existing_record.participant_count = len(participants)
                existing_record.notified_at = get_uk_time()
            else:
                # Create new completion record
                completion = TrainSlotCompletion(
                    schedule_id=schedule.id,
                    guild_id=schedule.guild_id,
                    completion_date=slot_start.date(),
                    slot_start=slot_start,
                    slot_end=slot_end,
                    attendance_channel_id=notification_settings.attendance_channel_id,
                    participant_count=len(participants)
                )
                session.add(completion)
            
            session.commit()
            
            self.logger.info(f"✅ Sent slot-end notification for '{schedule.name}' (ID: {schedule.id}) - {len(participants)} participants")
            
        except Exception as e:
            self.logger.error(f"Error sending slot-end notification: {e}")
            import traceback
            traceback.print_exc()

    async def clear_saturday_participants_on_sunday(self):
        """Clear train participants from Saturday trains on Sunday for weekly reset."""
        try:
            async with AsyncDatabaseSession() as session:
                from models import TrainParticipant
                
                # Use proper Eastern timezone
                uk_tz = pytz.timezone('Europe/London')
                now = datetime.now(uk_tz)
                current_day = now.weekday()  # 0=Monday, 6=Sunday
                
                # Only run on Sunday (day 6)
                if current_day != 6:
                    return
                
                # Get all Saturday schedules (day 5)
                saturday_schedules = session.query(TrainSchedule).filter_by(
                    day_of_week=5,  # Saturday
                    is_active=True
                ).all()
                
                if not saturday_schedules:
                    self.logger.debug("No Saturday schedules found to clear participants")
                    return
                
                total_cleared = 0
                
                for schedule in saturday_schedules:
                    # Clear TrainParticipant records (set to inactive)
                    cleared_participants = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule.id,
                        is_active=True
                    ).update({
                        'is_active': False
                    })
                    
                    # Clear participant_ids array in schedule
                    if schedule.participant_ids:
                        schedule.participant_ids = []
                    
                    total_cleared += cleared_participants
                    
                    if cleared_participants > 0:
                        self.logger.info(f"🧹 Cleared {cleared_participants} participants from Saturday train '{schedule.name}' (ID: {schedule.id})")
                
                session.commit()
                
                if total_cleared > 0:
                    self.logger.info(f"✅ Sunday reset complete - Cleared {total_cleared} total participants from {len(saturday_schedules)} Saturday trains")
                else:
                    self.logger.debug("Sunday reset - No participants to clear from Saturday trains")
                    
        except Exception as e:
            self.logger.error(f"Error clearing Saturday participants on Sunday: {e}")
            import traceback
            traceback.print_exc()
    
    async def trigger_manual_backup_ping(self, guild_id, channel_id, schedule_name):
        """Trigger a backup ping for testing - called from HTTP endpoint."""
        try:
            async with AsyncDatabaseSession() as session:
                # Find the schedule
                schedule = session.query(TrainSchedule).filter(
                    TrainSchedule.name.like(f"%{schedule_name}%"),
                    TrainSchedule.is_active == True
                ).first()
                
                if not schedule:
                    self.logger.error(f"Schedule not found: {schedule_name}")
                    return False
                
                self.logger.info(f"📋 Triggering backup ping for {schedule.name}")
                
                # Get guild and channel
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    self.logger.error(f"Guild not found: {guild_id}")
                    return False
                
                channel = guild.get_channel(channel_id)
                if not channel:
                    self.logger.error(f"Channel not found: {channel_id}")
                    return False
                
                # Build backup notification embed
                uk_tz = pytz.timezone('Europe/London')
                now = datetime.now(uk_tz)

                # Create Stage 2 notification
                notification = TrainNotification(
                    schedule_id=schedule.id,
                    guild_id=guild_id,
                    notification_date=now.date(),
                    stage=2,  # Backup stage
                    channel_id=channel_id,
                    primary_confirmed=False,
                    backup_confirmed=False
                )
                
                session.add(notification)
                session.commit()
                
                # Get participant info from TrainParticipant table (source of truth)
                participant_mention = ""
                active_parts_s2 = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule.id, is_active=True
                ).order_by(TrainParticipant.signed_up_at.asc()).all()
                if active_parts_s2:
                    first_s2 = active_parts_s2[0]
                    participant_mention = self._resolve_participant_mention(first_s2, guild)
                    self.logger.info(f"🔍 Stage 2 backup ping mentioning active participant: {first_s2.username} ({first_s2.user_id})")
                
                embed = discord.Embed(
                    title=f"🔔 Stage 2: Backup Needed - {schedule.name}",
                    description=f"**Train starts soon!** We need backup confirmation.\n\n"
                                f"**Assigned Rider:** {participant_mention or 'None'}\n"
                                f"**Duration:** {schedule.duration_minutes} minutes\n\n"
                                f"⏰ **{schedule.name}** is coming up!",
                    color=0xFFA500,
                    timestamp=now
                )
                
                embed.add_field(
                    name="📋 Instructions",
                    value=f"✅ React if you're **READY** to be backup!\n"
                          f"❌ React if you're **NOT AVAILABLE**\n"
                          f"🔄 React if you **NEED HELP**",
                    inline=False
                )
                
                embed.set_footer(text=f"Stage 2 - Backup Notification | Manual Test")
                
                # Send message
                message = await channel.send(
                    content=f"🚨 **BACKUP NEEDED!** {participant_mention}\n"
                            f"**Anyone available to be backup for {schedule.name}?**",
                    embed=embed
                )
                
                # Add reactions
                await message.add_reaction(self.READY_EMOJI)
                await message.add_reaction(self.NOT_READY_EMOJI)
                await message.add_reaction(self.BACKUP_EMOJI)
                
                # Update notification with message ID
                session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                    'message_id': message.id,
                    'is_sent': True,
                    'sent_at': get_uk_time()
                })
                session.commit()
                
                self.logger.info(f"✅ Backup ping sent for {schedule.name} (message ID: {message.id})")
                return True
                
        except Exception as e:
            self.logger.error(f"Error triggering manual backup ping: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    @commands.command(name='testping', aliases=['pingtest', 'testnotif'])
    @commands.guild_only()
    async def test_ping(self, ctx, *, schedule_name: str = None):
        """
        [Owner/Trusted] Fire a test Stage 1 notification for an existing train schedule.
        Usage: !testping                   → uses first active schedule
               !testping Sunday Train - killafolkss
        """
        is_privileged = await self.bot.is_owner_or_trusted(ctx.author)
        if not is_privileged:
            await ctx.send("❌ This command is restricted to the bot owner and trusted users.")
            return

        async with ctx.typing():
            def _fetch():
                with DatabaseSession() as session:
                    q = session.query(TrainSchedule).filter(
                        TrainSchedule.guild_id == ctx.guild.id,
                        TrainSchedule.is_active == True
                    )
                    if schedule_name:
                        q = q.filter(TrainSchedule.name.ilike(f"%{schedule_name}%"))
                    schedule = q.order_by(TrainSchedule.id).first()
                    if not schedule:
                        return None, None, []
                    settings = session.query(NotificationSettings).filter_by(
                        guild_id=ctx.guild.id
                    ).first()
                    active_ids = self.get_active_participant_ids(session, schedule.id)
                    return (
                        {
                            'id': schedule.id,
                            'name': schedule.name,
                            'start_time': schedule.start_time,
                            'duration_minutes': schedule.duration_minutes,
                            'specific_date': schedule.specific_date,
                            'day_of_week': schedule.day_of_week,
                            'guild_id': schedule.guild_id,
                        },
                        {
                            'raid_notification_channel_id': settings.raid_notification_channel_id if settings else None,
                            'raid_ping_role_id': settings.raid_ping_role_id if settings else None,
                        } if settings else None,
                        list(active_ids)
                    )

            sched, settings, active_ids = await asyncio.to_thread(_fetch)

            if not sched:
                tip = f' matching `{schedule_name}`' if schedule_name else ''
                await ctx.send(f"❌ No active train schedule found{tip}.")
                return

            guild = ctx.guild
            channel = None
            if settings and settings['raid_notification_channel_id']:
                channel = guild.get_channel(settings['raid_notification_channel_id'])
            if not channel:
                channel = ctx.channel

            uk_tz = pytz.timezone('Europe/London')
            now_uk = datetime.now(uk_tz)
            if sched['specific_date']:
                sd = sched['specific_date']
                if isinstance(sd, str):
                    from datetime import datetime as dt_cls
                    sd = dt_cls.strptime(sd, '%Y-%m-%d').date()
                display_date = sd
            else:
                days_ahead = (sched['day_of_week'] - now_uk.weekday()) % 7
                display_date = now_uk.date() + timedelta(days=days_ahead)

            uk_naive = datetime.combine(display_date, sched['start_time'])
            uk_time = uk_tz.localize(uk_naive)
            start_time_str = uk_time.strftime('%I:%M %p %Z')

            embed = discord.Embed(
                title='🚂 Raid Train Starting Soon! (TEST)',
                description=f'**{sched["name"]}** — {self.get_time_info_for_stage(1)}',
                color=0x9146ff
            )
            embed.add_field(
                name='📅 Train Details',
                value=f'**Start Time:** {start_time_str}\n**Duration:** {sched["duration_minutes"]} minutes',
                inline=False
            )
            embed.add_field(
                name='📊 Status',
                value="⏰ **Primary:** Waiting for confirmation...",
                inline=False
            )
            embed.add_field(
                name='🎯 Instructions',
                value=f'{self.READY_EMOJI} = Ready to stream\n{self.NOT_READY_EMOJI} = Can\'t make it\n{self.BACKUP_EMOJI} = Need backup',
                inline=False
            )
            embed.set_footer(text=f'Stage 1 TEST • {now_uk.strftime("%H:%M UK")} • Schedule ID {sched["id"]}')

            ping_role = None
            if settings and settings['raid_ping_role_id']:
                ping_role = guild.get_role(settings['raid_ping_role_id'])

            message_content = ping_role.mention if ping_role else ""
            # Resolve each ID to a proper mention (handles user_id=0 via name lookup)
            participant_pings = []
            for uid in active_ids:
                if uid and str(uid).strip() and str(uid).strip() != '0':
                    member = guild.get_member(int(str(uid).strip()))
                    participant_pings.append(member.mention if member else f"<@{uid}>")
            if participant_pings:
                rider_text = "🎯 **TRAIN RIDER:** " + " ".join(participant_pings)
                message_content = (message_content + "\n" + rider_text).strip() if message_content else rider_text

            # If nothing to ping at all, fall back to the command author so the
            # test is still visually useful (no role set + no signed-up riders)
            if not message_content:
                message_content = f"{ctx.author.mention} *(no raid role or riders configured — showing owner ping as fallback)*"

            message = await channel.send(content=message_content, embed=embed)
            await message.add_reaction(self.READY_EMOJI)
            await message.add_reaction(self.NOT_READY_EMOJI)
            await message.add_reaction(self.BACKUP_EMOJI)

            if channel != ctx.channel:
                await ctx.send(
                    f"✅ Test ping sent to {channel.mention} for **{sched['name']}**.\n"
                    f"{'Role pinged: ' + ping_role.mention if ping_role else '⚠️ No raid ping role set — run `!setpingrole raid @Role` to fix.'}\n"
                    f"Riders pinged: {len(participant_pings)}"
                )
            self.logger.info(
                f"Test ping fired by {ctx.author} for schedule {sched['id']} ({sched['name']}) "
                f"in channel {channel.id} — {len(participant_pings)} rider(s) pinged"
            )

    @app_commands.command(name="postattendanceinfo", description="Post attendance tracking explanation to the attendance channel")
    @owner_only()
    async def post_attendance_info(self, interaction: discord.Interaction):
        """Post a detailed explanation of how attendance tracking works."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            async with AsyncDatabaseSession() as session:
                # Get attendance channel for this guild
                settings = session.query(NotificationSettings).filter_by(
                    guild_id=interaction.guild.id
                ).first()
                
                if not settings or not settings.attendance_channel_id:
                    await interaction.followup.send("❌ No attendance channel configured for this server.", ephemeral=True)
                    return
                
                attendance_channel = interaction.guild.get_channel(settings.attendance_channel_id)
                if not attendance_channel:
                    await interaction.followup.send("❌ Attendance channel not found.", ephemeral=True)
                    return
                
                # Create comprehensive explanation embed
                embed = discord.Embed(
                    title="📊 Automatic Attendance Tracking - How It Works",
                    description=(
                        "This bot automatically tracks train attendance by monitoring your Twitch chat when you go live!\n\n"
                        "**🆕 Enhanced Tracking:** Now uses IRC JOIN/PART events to detect ALL viewers with chat open "
                        "(including silent lurkers), plus total viewer count and engagement metrics!"
                    ),
                    color=0x9146FF,
                    timestamp=datetime.now(pytz.UTC)
                )
                
                embed.add_field(
                    name="🎮 Automatic Start",
                    value=(
                        "When you **go live on Twitch**, the bot automatically:\n"
                        "• Detects your stream (checks every 60 seconds)\n"
                        "• Finds any train scheduled within ±2 hours\n"
                        "• Starts monitoring your Twitch chat\n"
                        "• Sends attendance reports here every 25 minutes"
                    ),
                    inline=False
                )
                
                embed.add_field(
                    name="📋 What Gets Tracked",
                    value=(
                        "**Enhanced IRC Monitoring** - The bot uses Twitch IRC JOIN/PART events to track:\n"
                        "👁️ **ALL viewers with chat open** - Including silent lurkers (no account linking required)\n"
                        "💬 **Chat participation** - Active chatters and message activity\n"
                        "📊 **Total viewer count** - Everyone watching the stream\n"
                        "📈 **Engagement percentage** - Chat viewers vs. total viewers\n\n"
                        "**For Scheduled Trains (Roster Comparison):**\n"
                        "✅ **Present** - Train participants who appeared in chat\n"
                        "❌ **Absent** - Train participants not detected in chat\n"
                        "⚠️ **Not Linked** - Train participants without Twitch accounts linked"
                    ),
                    inline=False
                )
                
                embed.add_field(
                    name="🔗 How It Works",
                    value=(
                        "**📺 For Streamers:**\n"
                        "• Just go live on Twitch - tracking starts automatically!\n"
                        "• No setup needed - the bot monitors your chat\n\n"
                        "**👥 For Train Participants (Roster Tracking):**\n"
                        "• Sign up for the train using `/jointrain`\n"
                        "• Link your Twitch account using `/linktwitch` (so the bot knows your Twitch username)\n"
                        "• Join the streamer's Twitch chat during the train\n\n"
                        "**👁️ For Everyone Else:**\n"
                        "• No linking required! The bot tracks ALL viewers with chat open\n"
                        "• Your Twitch username will appear in attendance reports\n"
                        "• Works for supporters, viewers, and raiders from any community"
                    ),
                    inline=False
                )
                
                embed.add_field(
                    name="⏰ Timing & Duration",
                    value=(
                        "**With a train schedule:**\n"
                        "• Tracking starts when you go live within ±2 hours of train time\n"
                        "• Monitors for the scheduled train duration\n\n"
                        "**Without a schedule (ad-hoc):**\n"
                        "• Tracking starts whenever you go live\n"
                        "• Monitors for 2 hours by default\n"
                        "• Shows all chat participants (no roster comparison)"
                    ),
                    inline=False
                )
                
                embed.add_field(
                    name="📊 Attendance Reports",
                    value=(
                        f"Reports post automatically every **{getattr(settings, 'attendance_interval_minutes', 25)} minutes** during your stream, showing:\n"
                        "• **Identified chatters** - Who's present in chat ✅\n"
                        "• **Total viewer count** - Everyone watching your stream 👥\n"
                        "• **Engagement percentage** - Chat viewers vs. total viewers 📈\n"
                        "• **Absent participants** - Signed up but not in chat ❌\n"
                        "• **Unlinked users** - No Twitch account linked ⚠️\n"
                        "• **Real-time participation stats** - Live roster tracking"
                    ),
                    inline=False
                )
                
                embed.add_field(
                    name="💡 Tips & Privacy",
                    value=(
                        "**Account Management:**\n"
                        "• Use `/linktwitch` to link your Twitch account\n"
                        "• Use `/mydata` to view your tracked attendance data\n"
                        "• Use `/deletemydata` to permanently delete your attendance records\n\n"
                        "**Admin Controls:**\n"
                        "• Change report frequency with `/setattendanceinterval`\n"
                        "• Set attendance channel with `/setattendancechannel`\n\n"
                        "**Automation:**\n"
                        "• No manual commands needed - tracking starts automatically!\n"
                        "• Works for both scheduled trains and casual streams"
                    ),
                    inline=False
                )
                
                embed.set_footer(text="Attendance tracking is fully automated - just go live and it starts!")
                
                # Try to update existing message first, otherwise post new
                message_id = 1435054096448094289
                try:
                    existing_message = await attendance_channel.fetch_message(message_id)
                    await existing_message.edit(embed=embed)
                    await interaction.followup.send(f"✅ Updated existing attendance info message in {attendance_channel.mention}", ephemeral=True)
                    self.logger.info(f"Updated existing attendance info message (ID: {message_id}) in #{attendance_channel.name}")
                except discord.NotFound:
                    # Message not found, post new one
                    await attendance_channel.send(embed=embed)
                    await interaction.followup.send(f"✅ Posted new attendance info to {attendance_channel.mention} (original message not found)", ephemeral=True)
                    self.logger.info(f"Posted new attendance info to #{attendance_channel.name} in {interaction.guild.name}")
                except Exception as msg_error:
                    # Fallback to posting new message
                    self.logger.warning(f"Could not update existing message: {msg_error}, posting new one")
                    await attendance_channel.send(embed=embed)
                    await interaction.followup.send(f"✅ Posted attendance info to {attendance_channel.mention}", ephemeral=True)
                    self.logger.info(f"Posted attendance info to #{attendance_channel.name} in {interaction.guild.name}")
                
        except Exception as e:
            self.logger.error(f"Error posting attendance info: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error posting attendance info: {str(e)}", ephemeral=True)


    @commands.command(
        name='confirmrider',
        help='Manually confirm a rider as ready when they responded outside the bot notification. Usage: !confirmrider @member'
    )
    @commands.guild_only()
    async def confirm_rider(self, ctx, member: discord.Member = None):
        """Mark a rider as confirmed on today's notification when they replied manually.

        Restricted to admins and trusted users.
        Usage: !confirmrider @the1wholived237
        """
        is_privileged = (
            await ctx.bot.is_owner(ctx.author)
            or ctx.author.guild_permissions.manage_guild
            or await ctx.bot.is_trusted_user(ctx.author.id, ctx.guild.id)
        )
        if not is_privileged:
            await ctx.send("❌ This command is restricted to admins and trusted users.")
            return

        if not member:
            await ctx.send("❌ **Usage:** `!confirmrider @member`")
            return

        uk_tz = pytz.timezone('Europe/London')
        today = datetime.now(uk_tz).date()

        async with AsyncDatabaseSession() as session:
            # Find an active TrainParticipant for this member today (or this week's recurring slot)
            participant = session.query(TrainParticipant).filter_by(
                guild_id=ctx.guild.id,
                user_id=member.id,
                is_active=True
            ).first()

            if not participant:
                await ctx.send(f"❌ {member.mention} isn't assigned to any active train slot in this server.")
                return

            schedule_id = participant.schedule_id

            # Find today's notification for that slot (sent today or yesterday for midnight slots)
            from datetime import timedelta as _td
            notification = (
                session.query(TrainNotification)
                .filter(
                    TrainNotification.schedule_id == schedule_id,
                    TrainNotification.guild_id == ctx.guild.id,
                    TrainNotification.notification_date.in_([today, today - _td(days=1)]),
                    TrainNotification.is_sent == True,
                )
                .order_by(TrainNotification.id.desc())
                .first()
            )

            if not notification:
                await ctx.send(
                    f"⚠️ No sent notification found for {member.mention}'s slot today. "
                    f"The slot may not have been pinged yet, or the notification wasn't sent by the bot.\n"
                    f"Rider is **confirmed** in the logs regardless."
                )
                # Still log it even without a notification record
                self.logger.info(
                    f"Manual confirm (no notification record): {member} confirmed for schedule {schedule_id} "
                    f"by {ctx.author}"
                )
                return

            already = notification.primary_confirmed and notification.primary_user_id == member.id
            if already:
                await ctx.send(f"✅ {member.mention} was already marked as confirmed for this slot.")
                return

            # Mark confirmed
            schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
            backup_was_pinged = bool(notification.backup_pinged)
            session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                'primary_confirmed': True,
                'primary_user_id': member.id,
            })
            session.commit()

            self.logger.info(
                f"Manual confirm: {member} confirmed for schedule {schedule_id} "
                f"(notification {notification.id}) by {ctx.author} "
                f"(backup_was_pinged={backup_was_pinged})"
            )

            # Post to the notification channel
            notif_channel = ctx.guild.get_channel(notification.channel_id) if notification.channel_id else None
            schedule_name = schedule.name if schedule else f"Train #{schedule_id}"

            if backup_was_pinged:
                confirm_msg = (
                    f"🎯 **ORIGINAL RIDER NOW READY!** ✅\n"
                    f"{member.mention} (assigned rider) has confirmed they're **ready** for "
                    f"**{schedule_name}**! 🚂\n"
                    f"*Backup volunteers are no longer needed — thank you for standing by!*\n"
                    f"*(Manually confirmed by {ctx.author.mention})*"
                )
            else:
                confirm_msg = (
                    f"🎯 **TRAIN RIDER READY!** ✅\n"
                    f"{member.mention} (assigned rider) has confirmed they're **ready** for "
                    f"**{schedule_name}**! 🚂\n"
                    f"*(Manually confirmed by {ctx.author.mention})*"
                )

            if notif_channel and notif_channel != ctx.channel:
                await notif_channel.send(confirm_msg)

            await ctx.send(f"✅ {member.mention} marked as confirmed for **{schedule_name}**.")

            # If a backup was already pinged, tell them they're no longer needed
            if backup_was_pinged and schedule:
                try:
                    await self.notify_backup_no_longer_needed(notification, schedule, session, member)
                except Exception as e:
                    self.logger.warning(f"Could not notify backup volunteers: {e}")

            # Notify host
            if schedule:
                try:
                    await self.notify_host_of_readiness(schedule, notification, member, session)
                except Exception:
                    pass


    @commands.command(
        name='noshowrider',
        aliases=['cantmakeit'],
        help='Immediately mark a rider as not-ready and fire the stage-2 backup ping. Usage: !noshowrider @member'
    )
    @commands.guild_only()
    async def noshow_rider(self, ctx, member: discord.Member = None):
        """Mark a rider as unable to make it and immediately send the backup ping.

        Use when a rider tells an admin they can't make it outside of the bot,
        so the backup ping fires right away instead of waiting for the timeout.

        Restricted to admins and trusted users.
        Usage: !noshowrider @member  (alias: !cantmakeit)
        """
        is_privileged = (
            await ctx.bot.is_owner(ctx.author)
            or ctx.author.guild_permissions.manage_guild
            or await ctx.bot.is_trusted_user(ctx.author.id, ctx.guild.id)
        )
        if not is_privileged:
            await ctx.send("❌ This command is restricted to admins and trusted users.")
            return

        if not member:
            await ctx.send("❌ **Usage:** `!noshowrider @member`")
            return

        uk_tz = pytz.timezone('Europe/London')
        today = datetime.now(uk_tz).date()

        async with AsyncDatabaseSession() as session:
            # Find an active TrainParticipant for this member in this guild
            participant = session.query(TrainParticipant).filter_by(
                guild_id=ctx.guild.id,
                user_id=member.id,
                is_active=True
            ).first()

            if not participant:
                await ctx.send(f"❌ {member.mention} isn't assigned to any active train slot in this server.")
                return

            schedule_id = participant.schedule_id
            schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()

            # Find today's notification for that slot (sent today or yesterday for midnight slots)
            from datetime import timedelta as _td
            notification = (
                session.query(TrainNotification)
                .filter(
                    TrainNotification.schedule_id == schedule_id,
                    TrainNotification.guild_id == ctx.guild.id,
                    TrainNotification.notification_date.in_([today, today - _td(days=1)]),
                    TrainNotification.is_sent == True,
                )
                .order_by(TrainNotification.id.desc())
                .first()
            )

            if not notification:
                await ctx.send(
                    f"⚠️ No sent notification found for {member.mention}'s slot today. "
                    f"The slot may not have been pinged yet.\n"
                    f"*(Logged as a no-show anyway.)*"
                )
                self.logger.info(
                    f"Manual no-show (no notification record): {member} for schedule {schedule_id} "
                    f"by {ctx.author}"
                )
                return

            schedule_name = schedule.name if schedule else f"Train #{schedule_id}"

            if notification.backup_pinged:
                await ctx.send(
                    f"⚠️ The backup ping for **{schedule_name}** was already sent — no action taken."
                )
                return

            # Mark rider as not-ready and flag backup as pinged so the scheduler
            # doesn't fire a second time on its next tick.
            session.query(TrainNotification).filter(TrainNotification.id == notification.id).update({
                'primary_confirmed': False,
                'backup_pinged': True,
            })
            session.commit()

            self.logger.info(
                f"Manual no-show: {member} marked not-ready for schedule {schedule_id} "
                f"(notification {notification.id}) by {ctx.author} — firing backup ping now"
            )

            # Post a heads-up to the notification channel (if different from the command channel)
            notif_channel = ctx.guild.get_channel(notification.channel_id) if notification.channel_id else None
            noshow_msg = (
                f"🚫 **RIDER UNAVAILABLE** — backup needed!\n"
                f"{member.mention} is unable to make **{schedule_name}**.\n"
                f"*(Reported by {ctx.author.mention})*"
            )
            if notif_channel and notif_channel != ctx.channel:
                await notif_channel.send(noshow_msg)

            await ctx.send(
                f"✅ {member.mention} marked as unable to make **{schedule_name}**. "
                f"Firing the backup ping now…"
            )

            # Fire the stage-2 backup ping immediately
            if schedule:
                try:
                    # Re-fetch a fresh notification object so send_backup_notification
                    # sees the updated backup_pinged flag.
                    fresh_notification = session.query(TrainNotification).filter_by(
                        id=notification.id).first()
                    await self.send_backup_notification(fresh_notification, schedule, session)
                except Exception as e:
                    self.logger.error(f"Error firing backup ping via noshowrider: {e}", exc_info=True)
                    await ctx.send("⚠️ Could not send the backup ping. Check the bot logs.")


async def setup(bot):
    """Setup function to add the cog."""
    await bot.add_cog(NotificationCommands(bot))