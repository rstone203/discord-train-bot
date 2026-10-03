"""
Google Sheets Two-Way Sync Commands for Train Schedules.

Slash commands:
  /sheetsync setup        — create a new spreadsheet and link it to this server
  /sheetsync export       — push current schedules from Discord → Sheet
  /sheetsync import       — pull edits from Sheet → Discord DB
  /sheetsync status       — show sync status and spreadsheet link
  /sheetsync attendance   — export this month's (or a chosen month's) attendance
  /sheetsync unsync       — unlink the spreadsheet from this server

Prefix commands (admin convenience):
  !sheetexport            — instant export
  !sheetimport            — instant import
  !sheetattendance [YYYY-MM] — export monthly attendance
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, date
import asyncio
import pytz

from database import DatabaseSession
from models import TrainSchedule, TrainParticipant

logger = logging.getLogger(__name__)
UK_TZ = pytz.timezone("Europe/London")


def _get_google_sheet_sync_record(session, guild_id: int):
    """Get the google_sheet_syncs row for a guild, or None."""
    from sqlalchemy import text
    row = session.execute(
        text("SELECT * FROM google_sheet_syncs WHERE guild_id = :gid ORDER BY id LIMIT 1"),
        {"gid": guild_id},
    ).fetchone()
    return row


def _upsert_sync_record(session, guild_id: int, spreadsheet_id: str, spreadsheet_url: str):
    """Insert or update the google_sheet_syncs row for a guild."""
    from sqlalchemy import text
    existing = _get_google_sheet_sync_record(session, guild_id)
    if existing:
        session.execute(
            text("""
                UPDATE google_sheet_syncs
                SET spreadsheet_id = :sid,
                    spreadsheet_url = :url,
                    sync_enabled = true,
                    auto_export = true,
                    auto_import = true,
                    updated_at = NOW()
                WHERE guild_id = :gid
            """),
            {"sid": spreadsheet_id, "url": spreadsheet_url, "gid": guild_id},
        )
    else:
        session.execute(
            text("""
                INSERT INTO google_sheet_syncs
                  (guild_id, spreadsheet_id, spreadsheet_url, sync_enabled,
                   auto_export, auto_import, sync_interval_minutes, created_at, updated_at)
                VALUES
                  (:gid, :sid, :url, true, true, true, 5, NOW(), NOW())
            """),
            {"gid": guild_id, "sid": spreadsheet_id, "url": spreadsheet_url},
        )
    session.commit()


def _update_last_export(session, guild_id: int):
    from sqlalchemy import text
    session.execute(
        text("UPDATE google_sheet_syncs SET last_export_at = NOW(), updated_at = NOW() WHERE guild_id = :gid"),
        {"gid": guild_id},
    )
    session.commit()


def _update_last_import(session, guild_id: int):
    from sqlalchemy import text
    session.execute(
        text("UPDATE google_sheet_syncs SET last_import_at = NOW(), updated_at = NOW() WHERE guild_id = :gid"),
        {"gid": guild_id},
    )
    session.commit()


def _set_sync_error(session, guild_id: int, error: str):
    from sqlalchemy import text
    session.execute(
        text("UPDATE google_sheet_syncs SET last_sync_error = :err, updated_at = NOW() WHERE guild_id = :gid"),
        {"err": error[:500], "gid": guild_id},
    )
    session.commit()


class GoogleSheetsCommands(commands.Cog):
    """Google Sheets two-way sync for train schedules."""

    def __init__(self, bot):
        self.bot = bot
        self.auto_sync_task.start()

    def cog_unload(self):
        self.auto_sync_task.cancel()

    # ── Permission helpers ────────────────────────────────────────────────────

    async def _is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if interaction.user.guild_permissions.manage_guild:
            return True
        guild_id = interaction.guild.id if interaction.guild else None
        return await self.bot.is_trusted_user(interaction.user.id, guild_id)

    def _require_admin(self, interaction: discord.Interaction) -> bool:
        """For prefix commands."""
        return (
            interaction.author.guild_permissions.manage_guild
            or interaction.author.guild_permissions.administrator
        )

    # ── Auto-sync background task ─────────────────────────────────────────────

    @tasks.loop(minutes=5)
    async def auto_sync_task(self):
        """Auto-export schedules every 5 minutes for all guilds with sync enabled."""
        await asyncio.sleep(0)  # yield
        try:
            with DatabaseSession() as session:
                from sqlalchemy import text
                rows = session.execute(
                    text("SELECT guild_id, spreadsheet_id FROM google_sheet_syncs WHERE sync_enabled = true AND auto_export = true")
                ).fetchall()

            for row in rows:
                guild_id    = row[0]
                sheet_id    = row[1]
                try:
                    await self._do_export(guild_id, sheet_id)
                except Exception as e:
                    logger.warning(f"Auto-sync export failed for guild {guild_id}: {e}")
                    with DatabaseSession() as session:
                        _set_sync_error(session, guild_id, str(e))
        except Exception as e:
            logger.error(f"auto_sync_task error: {e}")

    @auto_sync_task.before_loop
    async def before_auto_sync(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)  # small initial delay

    # ── Core export / import logic ────────────────────────────────────────────

    async def _do_export(self, guild_id: int, spreadsheet_id: str) -> int:
        """Export all active schedules for guild to the Sheet. Returns row count."""
        from utils.google_sheets_sync import export_schedules_to_sheet

        with DatabaseSession() as session:
            schedules = (
                session.query(TrainSchedule)
                .filter_by(guild_id=guild_id, is_active=True)
                .order_by(TrainSchedule.specific_date, TrainSchedule.start_time)
                .all()
            )
            data = []
            for sched in schedules:
                parts = (
                    session.query(TrainParticipant)
                    .filter_by(schedule_id=sched.id, is_active=True)
                    .all()
                )
                data.append((sched, parts))

        count = await export_schedules_to_sheet(spreadsheet_id, data)

        with DatabaseSession() as session:
            _update_last_export(session, guild_id)

        return count

    async def _do_import(self, guild_id: int, spreadsheet_id: str) -> dict:
        """Import changes from the Sheet back into the database. Returns summary dict."""
        from utils.google_sheets_sync import import_schedules_from_sheet

        rows = await import_schedules_from_sheet(spreadsheet_id)

        updated = 0
        skipped = 0

        with DatabaseSession() as session:
            for row in rows:
                try:
                    sid_str = row.get("schedule_id", "").strip()
                    if not sid_str.isdigit():
                        skipped += 1
                        continue
                    sid = int(sid_str)

                    sched = session.query(TrainSchedule).filter_by(id=sid, guild_id=guild_id).first()
                    if not sched:
                        skipped += 1
                        continue

                    # Apply sheet changes to schedule name / description
                    changed = False
                    new_name = row.get("name", "").strip()
                    if new_name and new_name != sched.name:
                        sched.name = new_name
                        changed = True

                    new_notes = row.get("notes", "").strip()
                    if new_notes != (sched.description or ""):
                        sched.description = new_notes
                        changed = True

                    if changed:
                        session.add(sched)
                        updated += 1
                except Exception as e:
                    logger.warning(f"Import row error: {e}")
                    skipped += 1

            session.commit()
            _update_last_import(session, guild_id)

        return {"updated": updated, "skipped": skipped, "total": len(rows)}

    # ── Slash command group ───────────────────────────────────────────────────

    sheetsync = app_commands.Group(name="sheetsync", description="Google Sheets sync for train schedules")

    @sheetsync.command(name="setup", description="Create a new Google Spreadsheet and link it to this server")
    async def sheetsync_setup(self, interaction: discord.Interaction):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False, thinking=True)

        try:
            # Check if already set up
            with DatabaseSession() as session:
                existing = _get_google_sheet_sync_record(session, interaction.guild_id)

            if existing and existing.spreadsheet_id:
                embed = discord.Embed(
                    title="ℹ️ Already Linked",
                    description=(
                        f"This server already has a spreadsheet linked.\n"
                        f"[Open Spreadsheet]({existing.spreadsheet_url})\n\n"
                        "Use `/sheetsync export` to refresh it, or `/sheetsync unsync` to unlink first."
                    ),
                    color=0x3498db,
                )
                await interaction.followup.send(embed=embed)
                return

            from utils.google_sheets_sync import create_spreadsheet
            result = await create_spreadsheet(interaction.guild.name)
            spreadsheet_id  = result["spreadsheet_id"]
            spreadsheet_url = result["spreadsheet_url"]

            with DatabaseSession() as session:
                _upsert_sync_record(session, interaction.guild_id, spreadsheet_id, spreadsheet_url)

            # Initial export
            count = await self._do_export(interaction.guild_id, spreadsheet_id)

            embed = discord.Embed(
                title="✅ Google Sheets Linked!",
                description=(
                    f"A new spreadsheet has been created and linked to **{interaction.guild.name}**.\n\n"
                    f"📊 [Open Spreadsheet]({spreadsheet_url})\n\n"
                    f"**{count}** schedule slot(s) exported on setup.\n\n"
                    "The sheet will auto-update every 5 minutes.\n"
                    "Use `/sheetsync import` to pull edits from the sheet back to Discord."
                ),
                color=0x2ecc71,
            )
            embed.set_footer(text="Two-way sync active · /sheetsync status for details")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"sheetsync setup error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Setup failed: {e}")

    @sheetsync.command(name="export", description="Push current Discord schedules to the Google Sheet")
    async def sheetsync_export(self, interaction: discord.Interaction):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        with DatabaseSession() as session:
            rec = _get_google_sheet_sync_record(session, interaction.guild_id)

        if not rec or not rec.spreadsheet_id:
            await interaction.followup.send("❌ No spreadsheet linked. Run `/sheetsync setup` first.")
            return

        try:
            count = await self._do_export(interaction.guild_id, rec.spreadsheet_id)
            await interaction.followup.send(
                f"✅ Exported **{count}** schedule slot(s) to [Google Sheets]({rec.spreadsheet_url})."
            )
        except Exception as e:
            logger.error(f"sheetsync export error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Export failed: {e}")

    @sheetsync.command(name="import", description="Pull edits from the Google Sheet back to Discord")
    async def sheetsync_import(self, interaction: discord.Interaction):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        with DatabaseSession() as session:
            rec = _get_google_sheet_sync_record(session, interaction.guild_id)

        if not rec or not rec.spreadsheet_id:
            await interaction.followup.send("❌ No spreadsheet linked. Run `/sheetsync setup` first.")
            return

        try:
            summary = await self._do_import(interaction.guild_id, rec.spreadsheet_id)
            await interaction.followup.send(
                f"✅ Import complete — **{summary['updated']}** schedule(s) updated, "
                f"{summary['skipped']} row(s) skipped."
            )
        except Exception as e:
            logger.error(f"sheetsync import error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Import failed: {e}")

    @sheetsync.command(name="status", description="Show Google Sheets sync status and spreadsheet link")
    async def sheetsync_status(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        with DatabaseSession() as session:
            rec = _get_google_sheet_sync_record(session, interaction.guild_id)

        if not rec or not rec.spreadsheet_id:
            await interaction.followup.send("❌ No spreadsheet linked. Run `/sheetsync setup` first.")
            return

        last_export = rec.last_export_at.strftime("%d/%m/%Y %H:%M") if rec.last_export_at else "Never"
        last_import = rec.last_import_at.strftime("%d/%m/%Y %H:%M") if rec.last_import_at else "Never"
        error_text  = f"\n⚠️ Last error: `{rec.last_sync_error}`" if rec.last_sync_error else ""

        embed = discord.Embed(
            title="📊 Google Sheets Sync Status",
            color=0x2ecc71 if rec.sync_enabled else 0xe74c3c,
        )
        embed.add_field(name="Spreadsheet", value=f"[Open Sheet]({rec.spreadsheet_url})", inline=False)
        embed.add_field(name="Sync Enabled", value="✅ Yes" if rec.sync_enabled else "❌ No", inline=True)
        embed.add_field(name="Auto Export", value="✅ Yes" if rec.auto_export else "❌ No", inline=True)
        embed.add_field(name="Auto Import", value="✅ Yes" if rec.auto_import else "❌ No", inline=True)
        embed.add_field(name="Last Export (UK)", value=last_export, inline=True)
        embed.add_field(name="Last Import (UK)", value=last_import, inline=True)
        if error_text:
            embed.add_field(name="Errors", value=error_text, inline=False)
        embed.set_footer(text="Auto-sync runs every 5 minutes")
        await interaction.followup.send(embed=embed)

    @sheetsync.command(name="attendance", description="Export this month's attendance to the Google Sheet")
    @app_commands.describe(month="Month to export in YYYY-MM format (default: current month)")
    async def sheetsync_attendance(self, interaction: discord.Interaction, month: str = None):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        with DatabaseSession() as session:
            rec = _get_google_sheet_sync_record(session, interaction.guild_id)

        if not rec or not rec.spreadsheet_id:
            await interaction.followup.send("❌ No spreadsheet linked. Run `/sheetsync setup` first.")
            return

        # Parse month
        now = datetime.now(UK_TZ)
        if month:
            try:
                target = datetime.strptime(month, "%Y-%m")
                year, mo = target.year, target.month
            except ValueError:
                await interaction.followup.send("❌ Month format must be `YYYY-MM` e.g. `2025-06`.")
                return
        else:
            year, mo = now.year, now.month

        try:
            from utils.google_sheets_sync import export_monthly_attendance, build_monthly_attendance_data

            with DatabaseSession() as session:
                att_data = build_monthly_attendance_data(session, interaction.guild_id, year, mo)

            if not att_data:
                await interaction.followup.send(
                    f"ℹ️ No attendance data found for **{year:04d}-{mo:02d}**. "
                    "Make sure there are schedules with specific dates in that month."
                )
                return

            await export_monthly_attendance(rec.spreadsheet_id, year, mo, att_data)

            sessions_count = len(att_data)
            tab_name = f"{year:04d}-{mo:02d} Attendance"
            await interaction.followup.send(
                f"✅ Monthly attendance for **{year:04d}-{mo:02d}** exported to the sheet "
                f"({sessions_count} session(s)).\n"
                f"[Open Spreadsheet]({rec.spreadsheet_url}) → look for the **{tab_name}** tab."
            )
        except Exception as e:
            logger.error(f"sheetsync attendance error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Attendance export failed: {e}")

    @sheetsync.command(name="unsync", description="Unlink the Google Sheet from this server")
    async def sheetsync_unsync(self, interaction: discord.Interaction):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        with DatabaseSession() as session:
            rec = _get_google_sheet_sync_record(session, interaction.guild_id)
            if not rec:
                await interaction.followup.send("No spreadsheet is linked to this server.")
                return
            from sqlalchemy import text
            session.execute(
                text("UPDATE google_sheet_syncs SET sync_enabled = false, updated_at = NOW() WHERE guild_id = :gid"),
                {"gid": interaction.guild_id},
            )
            session.commit()

        await interaction.followup.send("✅ Google Sheets sync has been disabled for this server.")

    # ── Prefix command shortcuts ──────────────────────────────────────────────

    @commands.command(name="sheetexport")
    @commands.has_permissions(manage_guild=True)
    async def prefix_sheetexport(self, ctx):
        """Immediately export schedules to Google Sheets."""
        with DatabaseSession() as session:
            rec = _get_google_sheet_sync_record(session, ctx.guild.id)

        if not rec or not rec.spreadsheet_id:
            await ctx.send("❌ No spreadsheet linked. Use `/sheetsync setup` first.")
            return

        msg = await ctx.send("⏳ Exporting to Google Sheets...")
        try:
            count = await self._do_export(ctx.guild.id, rec.spreadsheet_id)
            await msg.edit(content=f"✅ Exported **{count}** schedule(s) to [Google Sheets]({rec.spreadsheet_url}).")
        except Exception as e:
            await msg.edit(content=f"❌ Export failed: {e}")

    @commands.command(name="sheetimport")
    @commands.has_permissions(manage_guild=True)
    async def prefix_sheetimport(self, ctx):
        """Immediately import schedule changes from Google Sheets."""
        with DatabaseSession() as session:
            rec = _get_google_sheet_sync_record(session, ctx.guild.id)

        if not rec or not rec.spreadsheet_id:
            await ctx.send("❌ No spreadsheet linked. Use `/sheetsync setup` first.")
            return

        msg = await ctx.send("⏳ Importing from Google Sheets...")
        try:
            summary = await self._do_import(ctx.guild.id, rec.spreadsheet_id)
            await msg.edit(
                content=f"✅ Import complete — **{summary['updated']}** updated, {summary['skipped']} skipped."
            )
        except Exception as e:
            await msg.edit(content=f"❌ Import failed: {e}")

    @commands.command(name="sheetattendance")
    @commands.has_permissions(manage_guild=True)
    async def prefix_sheetattendance(self, ctx, month: str = None):
        """Export monthly attendance. Usage: !sheetattendance [YYYY-MM]"""
        with DatabaseSession() as session:
            rec = _get_google_sheet_sync_record(session, ctx.guild.id)

        if not rec or not rec.spreadsheet_id:
            await ctx.send("❌ No spreadsheet linked. Use `/sheetsync setup` first.")
            return

        now = datetime.now(UK_TZ)
        if month:
            try:
                target = datetime.strptime(month, "%Y-%m")
                year, mo = target.year, target.month
            except ValueError:
                await ctx.send("❌ Format: `!sheetattendance 2025-06`")
                return
        else:
            year, mo = now.year, now.month

        msg = await ctx.send(f"⏳ Exporting attendance for {year:04d}-{mo:02d}...")
        try:
            from utils.google_sheets_sync import export_monthly_attendance, build_monthly_attendance_data

            with DatabaseSession() as session:
                att_data = build_monthly_attendance_data(session, ctx.guild.id, year, mo)

            if not att_data:
                await msg.edit(content=f"ℹ️ No attendance data for {year:04d}-{mo:02d}.")
                return

            await export_monthly_attendance(rec.spreadsheet_id, year, mo, att_data)
            await msg.edit(
                content=f"✅ Attendance for {year:04d}-{mo:02d} exported — {len(att_data)} session(s).\n"
                        f"[Open Spreadsheet]({rec.spreadsheet_url})"
            )
        except Exception as e:
            await msg.edit(content=f"❌ Attendance export failed: {e}")


async def setup(bot):
    await bot.add_cog(GoogleSheetsCommands(bot))
