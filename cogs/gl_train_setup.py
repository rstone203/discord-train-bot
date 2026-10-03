"""
Game Lounge raid-train quick-setup command.

Usage:
    !setupgltrain @rider1 @rider2 ... @rider9

Creates 9 Sunday slots starting at 3 PM UK time (90 min each),
clears any existing Sunday schedules for the guild, and assigns
each mentioned member to their slot in order.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta

import discord
from discord.ext import commands

from database import DatabaseSession
from models import TrainSchedule, TrainParticipant, User, get_est_time
from utils.schedule_cache import invalidate_schedule_cache

# ── GL defaults ──────────────────────────────────────────────────────────────
GL_DAY_OF_WEEK   = 6          # Sunday
GL_DAY_NAME      = "Sunday"
GL_START_HOUR    = 15         # 3 PM UK wall-clock
GL_START_MINUTE  = 0
GL_DURATION      = 90         # minutes per slot
GL_SLOTS         = 9
GL_NOTIFY_BEFORE = 60         # minutes before slot start


class GLTrainSetup(commands.Cog):
    """One-shot command to rebuild the Game Lounge Sunday raid train."""

    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    # ── permission check ─────────────────────────────────────────────────────

    @staticmethod
    def _is_admin_or_trusted():
        async def predicate(ctx):
            if await ctx.bot.is_owner(ctx.author):
                return True
            if ctx.author.guild_permissions.manage_guild:
                return True
            guild_id = ctx.guild.id if ctx.guild else None
            return await ctx.bot.is_trusted_user(ctx.author.id, guild_id)
        return commands.check(predicate)

    # ── main command ─────────────────────────────────────────────────────────

    @_is_admin_or_trusted()
    @commands.guild_only()
    @commands.command(
        name='setupgltrain',
        help=(
            'Set up the Game Lounge Sunday raid train in one shot.\n'
            'Usage: !setupgltrain @rider1 @rider2 ... @rider9\n'
            'Mention riders in slot order (1 = 3 PM, 2 = 4:30 PM, …).\n'
            'Use - (hyphen) to leave a specific slot empty.\n'
            'You can provide fewer than 9 — trailing slots stay unassigned.'
        )
    )
    async def setup_gl_train(self, ctx, *args: str):
        """Wipe Sunday schedules, create 9 slots, assign riders in order.

        Each arg is either a member mention/ID or '-' to leave that slot empty.
        """

        if not args:
            await ctx.send(
                "❌ **Usage:** `!setupgltrain @rider1 @rider2 ... @rider9`\n\n"
                "Mention riders in slot order. Use `-` to skip a slot. "
                "Any trailing slots are left empty."
            )
            return

        if len(args) > GL_SLOTS:
            await ctx.send(
                f"❌ Too many arguments — the train has **{GL_SLOTS}** slots. "
                f"You provided {len(args)}."
            )
            return

        # Resolve each arg to a Member or None (for '-' / skip tokens).
        riders: list[discord.Member | None] = []
        seen: set[int] = set()
        errors: list[str] = []

        for raw in args:
            if raw.strip('-') == '' or raw.lower() in ('-', 'skip', 'empty', 'none'):
                riders.append(None)
                continue
            # Strip mention formatting <@123> or <@!123>
            cleaned = raw.strip('<@!> ')
            try:
                member = (
                    ctx.guild.get_member(int(cleaned))
                    or await ctx.guild.fetch_member(int(cleaned))
                )
            except (ValueError, discord.NotFound, discord.HTTPException):
                # Try by name as a fallback
                member = discord.utils.find(
                    lambda m, r=raw: m.mention == r or m.name == r or m.display_name == r,
                    ctx.guild.members,
                )
            if member is None:
                errors.append(raw)
                riders.append(None)
                continue
            if member.id in seen:
                riders.append(None)   # silently skip duplicate
            else:
                seen.add(member.id)
                riders.append(member)

        if errors:
            await ctx.send(
                f"⚠️ Could not resolve: {', '.join(errors)} — those slots will be left empty."
            )

        # ── progress message ──────────────────────────────────────────────
        progress = await ctx.send("🚂 Setting up the Game Lounge Sunday train…")

        try:
            created, assigned = await self._rebuild_schedule(ctx, riders)
        except Exception as exc:
            self.logger.exception("setupgltrain failed")
            await progress.edit(content=f"❌ Something went wrong: {exc}")
            return

        # ── kick off Twitch link reminders in the background ─────────────
        for member in riders:
            if member is None:
                continue
            try:
                from utils.auto_link_helper import send_link_reminder_if_needed
                asyncio.create_task(
                    send_link_reminder_if_needed(self.bot, member.id, ctx.guild.id)
                )
            except Exception:
                pass

        # ── summary embed ─────────────────────────────────────────────────
        embed = discord.Embed(
            title="✅ Game Lounge Sunday Train — Ready!",
            description=(
                f"**{GL_SLOTS} slots** created for **Sunday** starting at "
                f"**3:00 PM UK** · {GL_DURATION} min each"
            ),
            color=0x9146FF,
            timestamp=datetime.utcnow()
        )

        slots_lines = []
        for slot in created:
            time_str  = slot['time'].strftime('%I:%M %p').lstrip('0')
            rider_str = slot['rider'] or '*Empty*'
            slots_lines.append(f"**{slot['num']}.** {time_str} → {rider_str}")

        embed.add_field(
            name="🎟️ Slot Assignments",
            value="\n".join(slots_lines),
            inline=False
        )

        assigned_count = sum(1 for s in created if s['rider'])
        embed.add_field(name="👥 Riders assigned", value=str(assigned_count), inline=True)
        embed.add_field(name="📅 Day",             value="Sunday",            inline=True)
        embed.add_field(name="⏱️ Slot duration",   value="90 min",            inline=True)

        schedule_ids = ", ".join(str(s['id']) for s in created)
        embed.set_footer(text=f"Schedule IDs: {schedule_ids} · use !deleteschedule to wipe")

        await progress.edit(content=None, embed=embed)
        self.logger.info(
            f"setupgltrain: {GL_SLOTS} Sunday slots created by {ctx.author} "
            f"in {ctx.guild}, {assigned_count} riders assigned"
        )

    # ── assign-slot command ───────────────────────────────────────────────────

    @_is_admin_or_trusted()
    @commands.guild_only()
    @commands.command(
        name='assignslot',
        help=(
            'Assign or swap a single GL train slot without rebuilding the schedule.\n'
            'Usage: !assignslot <slot_number> @rider\n'
            'Slot numbers run 1–9 (1 = 3 PM, 2 = 4:30 PM, …).\n'
            'The current occupant (if any) is deactivated and the new rider added.\n'
            'History is preserved.'
        )
    )
    async def assign_slot(self, ctx, slot_number: int, *, member_raw: str = None):
        """Replace the participant in a single GL train slot.

        Usage: !assignslot <slot_number> @rider
        """
        # ── input validation ──────────────────────────────────────────────
        if slot_number < 1 or slot_number > GL_SLOTS:
            await ctx.send(
                f"❌ Slot number must be between **1** and **{GL_SLOTS}**. "
                f"You provided `{slot_number}`."
            )
            return

        if not member_raw:
            await ctx.send(
                "❌ **Usage:** `!assignslot <slot_number> @rider`\n"
                "Example: `!assignslot 3 @Username`"
            )
            return

        # Resolve member from mention or ID
        member: discord.Member | None = None
        if ctx.message.mentions:
            member = ctx.message.mentions[0]
        else:
            cleaned = member_raw.strip('<@!> ')
            try:
                member = (
                    ctx.guild.get_member(int(cleaned))
                    or await ctx.guild.fetch_member(int(cleaned))
                )
            except (ValueError, discord.NotFound, discord.HTTPException):
                member = discord.utils.find(
                    lambda m, r=member_raw: m.name == r or m.display_name == r,
                    ctx.guild.members,
                )

        if member is None:
            await ctx.send(f"❌ Could not find member `{member_raw}`. Please mention them or use their ID.")
            return

        # ── perform the swap ──────────────────────────────────────────────
        progress = await ctx.send(f"🔄 Assigning {member.mention} to slot **{slot_number}**…")

        try:
            result = await self._assign_slot_db(ctx, slot_number, member)
        except Exception as exc:
            self.logger.exception("assignslot failed")
            await progress.edit(content=f"❌ Something went wrong: {exc}")
            return

        if result is None:
            await progress.edit(
                content=(
                    f"❌ No active Sunday GL train slot found for position **{slot_number}**.\n"
                    "Make sure the schedule has been set up with `!setupgltrain` first."
                )
            )
            return

        # ── Twitch link reminder in the background ────────────────────────
        try:
            from utils.auto_link_helper import send_link_reminder_if_needed
            asyncio.create_task(
                send_link_reminder_if_needed(self.bot, member.id, ctx.guild.id)
            )
        except Exception:
            pass

        # ── confirmation embed ────────────────────────────────────────────
        time_str = result['slot_time'].strftime('%I:%M %p').lstrip('0')
        embed = discord.Embed(
            title="✅ Slot Updated",
            color=0x9146FF,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="🎟️ Slot", value=f"**{slot_number}** — {time_str} UK", inline=True)
        embed.add_field(name="👤 New Rider", value=member.mention, inline=True)
        if result['old_rider']:
            embed.add_field(name="🔄 Replaced", value=result['old_rider'], inline=True)
        else:
            embed.add_field(name="📋 Was", value="*Empty*", inline=True)
        embed.set_footer(text=f"Schedule ID: {result['schedule_id']} · use !assignslot to update again")

        await progress.edit(content=None, embed=embed)
        self.logger.info(
            f"assignslot: slot {slot_number} in {ctx.guild} assigned to {member} "
            f"by {ctx.author} (replaced: {result['old_rider']})"
        )

    async def _assign_slot_db(self, ctx, slot_number: int, member: discord.Member):
        """
        Finds the nth active Sunday slot (ordered by start_time), deactivates the
        current participant (if any), and inserts a new active participant.

        Returns a dict with slot info, or None if the slot doesn't exist.
        """
        guild_id = ctx.guild.id

        # GL schedules are unambiguously identified by:
        #   • guild_id, is_active=True, schedule_type='recurring'
        #   • name matching the pattern produced by !setupgltrain: "Sunday % Train"
        #   • day_of_week == GL_DAY_OF_WEEK (Sunday) or early-Monday overflow
        GL_NAME_PREFIX = f"{GL_DAY_NAME} "
        GL_NAME_SUFFIX = " Train"

        def _run():
            with DatabaseSession() as session:
                from datetime import time as _time

                sunday_slots = (
                    session.query(TrainSchedule)
                    .filter(
                        TrainSchedule.guild_id == guild_id,
                        TrainSchedule.is_active == True,
                        TrainSchedule.schedule_type == 'recurring',
                        TrainSchedule.day_of_week == GL_DAY_OF_WEEK,
                        TrainSchedule.name.like(f"{GL_NAME_PREFIX}%{GL_NAME_SUFFIX}"),
                    )
                    .order_by(TrainSchedule.start_time)
                    .all()
                )
                monday_overflow = (
                    session.query(TrainSchedule)
                    .filter(
                        TrainSchedule.guild_id == guild_id,
                        TrainSchedule.is_active == True,
                        TrainSchedule.schedule_type == 'recurring',
                        TrainSchedule.day_of_week == (GL_DAY_OF_WEEK + 1) % 7,
                        TrainSchedule.start_time < _time(5, 0),
                        TrainSchedule.name.like(f"{GL_NAME_PREFIX}%{GL_NAME_SUFFIX}"),
                    )
                    .order_by(TrainSchedule.start_time)
                    .all()
                )
                all_slots = sunday_slots + monday_overflow

                if slot_number > len(all_slots):
                    return None

                sched = all_slots[slot_number - 1]

                # Deactivate any existing active participant
                old_participant = (
                    session.query(TrainParticipant)
                    .filter_by(schedule_id=sched.id, is_active=True)
                    .first()
                )
                old_rider_str = None
                if old_participant:
                    old_rider_str = old_participant.display_name or old_participant.username
                    old_participant.is_active = False
                    session.flush()

                # Look up Twitch username for the new rider
                twitch_username = None
                try:
                    user_record = session.query(User).filter_by(
                        id=member.id, guild_id=guild_id
                    ).first()
                    if user_record and getattr(user_record, 'twitch_login', None):
                        twitch_username = user_record.twitch_login
                except Exception:
                    pass

                # Insert new participant
                new_participant = TrainParticipant(
                    schedule_id=sched.id,
                    guild_id=guild_id,
                    user_id=member.id,
                    username=member.name,
                    display_name=member.display_name,
                    twitch_username=twitch_username,
                    signed_up_at=get_est_time(),
                    is_active=True,
                    notes=f"Assigned via !assignslot by admin"
                )
                session.add(new_participant)
                session.commit()

                return {
                    'schedule_id': sched.id,
                    'slot_time': sched.start_time,
                    'old_rider': old_rider_str,
                }

        import asyncio as _asyncio
        result = await _asyncio.to_thread(_run)
        if result is not None:
            invalidate_schedule_cache(guild_id)
        return result

    # ── core logic ────────────────────────────────────────────────────────────

    async def _rebuild_schedule(self, ctx, riders):
        """
        1. Deactivate existing Sunday schedules (preserves attendance history).
        2. Create GL_SLOTS new recurring Sunday slots from 15:00 UK.
        3. Assign one rider per slot in the order provided.
        Returns (created_slots_list, assigned_count).
        """
        guild_id = ctx.guild.id

        with DatabaseSession() as session:

            # 1. Deactivate existing Sunday schedules for this guild
            existing = session.query(TrainSchedule).filter_by(
                guild_id=guild_id,
                day_of_week=GL_DAY_OF_WEEK
            ).all()
            # Also catch slots that rolled over into Monday (hour < 5)
            monday_overflow = session.query(TrainSchedule).filter_by(
                guild_id=guild_id,
                day_of_week=(GL_DAY_OF_WEEK + 1) % 7
            ).filter(TrainSchedule.start_time < time(5, 0)).all()

            for sched in existing + monday_overflow:
                setattr(sched, 'is_active', False)
            session.flush()

            # 2. Create new slots
            created = []
            base_minutes = GL_START_HOUR * 60 + GL_START_MINUTE

            for i in range(GL_SLOTS):
                total_minutes = base_minutes + GL_DURATION * i
                slot_hour    = (total_minutes // 60) % 24
                slot_minute  = total_minutes % 60
                slot_time    = time(slot_hour, slot_minute)

                # Handle midnight rollover
                extra_days   = total_minutes // 1440
                actual_day   = (GL_DAY_OF_WEEK + extra_days) % 7

                name = f"{GL_DAY_NAME} {slot_time.strftime('%I:%M %p')} Train"

                sched = TrainSchedule(
                    guild_id=guild_id,
                    name=name,
                    host_user_id=ctx.author.id,
                    day_of_week=actual_day,
                    start_time=slot_time,
                    duration_minutes=GL_DURATION,
                    notify_before_minutes=GL_NOTIFY_BEFORE,
                    max_participants=1,
                    schedule_type='recurring',
                    is_active=True,
                    created_at=get_est_time(),
                    updated_at=get_est_time()
                )
                session.add(sched)
                session.flush()   # get sched.id before adding participant

                # 3. Assign rider to this slot if provided (None = intentionally empty)
                rider_str = None
                member = riders[i] if i < len(riders) else None
                if member is not None:
                    # Try to find linked Twitch username
                    twitch_username = None
                    try:
                        user_record = session.query(User).filter_by(
                            id=member.id,
                            guild_id=guild_id
                        ).first()
                        if user_record and getattr(user_record, 'twitch_login', None):
                            twitch_username = user_record.twitch_login
                    except Exception:
                        pass

                    participant = TrainParticipant(
                        schedule_id=sched.id,
                        guild_id=guild_id,
                        user_id=member.id,
                        username=member.name,
                        display_name=member.display_name,
                        twitch_username=twitch_username,
                        signed_up_at=get_est_time(),
                        is_active=True,
                        notes="Added via !setupgltrain"
                    )
                    session.add(participant)
                    rider_str = member.mention

                created.append({
                    'num':   i + 1,
                    'id':    sched.id,
                    'time':  slot_time,
                    'rider': rider_str,
                })

            session.commit()

        invalidate_schedule_cache(guild_id)
        return created, sum(1 for s in created if s['rider'])


async def setup(bot):
    await bot.add_cog(GLTrainSetup(bot))
