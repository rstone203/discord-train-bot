"""
Google Sheets Two-Way Sync Utility for Train Schedules.

Uses the Replit Google Sheets connector to get an OAuth access token,
then calls the Google Sheets REST API directly to read/write data.

Sheet layout per guild:
  Tab "Schedules"  — one row per train schedule slot
  Tab "YYYY-MM Attendance" — monthly attendance grid (auto-created)
"""

import os
import logging
import asyncio
import aiohttp
import pytz
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)

UK_TZ = pytz.timezone("Europe/London")

# ─── Replit connector token fetch ──────────────────────────────────────────────

async def _get_access_token() -> str:
    """
    Fetch a fresh Google OAuth access token from the Replit connectors service.
    Never cache this – tokens expire.
    """
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    repl_identity = os.environ.get("REPL_IDENTITY")
    web_repl_renewal = os.environ.get("WEB_REPL_RENEWAL")

    if repl_identity:
        x_replit_token = f"repl {repl_identity}"
    elif web_repl_renewal:
        x_replit_token = f"depl {web_repl_renewal}"
    else:
        raise RuntimeError("No REPL_IDENTITY or WEB_REPL_RENEWAL env var found")

    if not hostname:
        raise RuntimeError("REPLIT_CONNECTORS_HOSTNAME env var not set")

    url = f"https://{hostname}/api/v2/connection?include_secrets=true&connector_names=google-sheet"
    headers = {
        "Accept": "application/json",
        "X-Replit-Token": x_replit_token,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()

    item = (data.get("items") or [None])[0]
    if not item:
        raise RuntimeError("Google Sheets not connected – no connector item returned")

    settings = item.get("settings", {})
    token = settings.get("access_token") or (settings.get("oauth", {}) or {}).get("credentials", {}).get("access_token")
    if not token:
        raise RuntimeError("Google Sheets connector returned no access token")
    return token


# ─── Sheets REST helpers ────────────────────────────────────────────────────────

SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_BASE  = "https://www.googleapis.com/drive/v3/files"


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def _sheets_get(token: str, path: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{SHEETS_BASE}{path}",
            headers=_auth_headers(token),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _sheets_post(token: str, path: str, body: dict) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{SHEETS_BASE}{path}",
            headers=_auth_headers(token),
            json=body,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _sheets_put(token: str, path: str, body: dict) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.put(
            f"{SHEETS_BASE}{path}",
            headers=_auth_headers(token),
            json=body,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _drive_post(token: str, body: dict) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            DRIVE_BASE,
            headers=_auth_headers(token),
            json=body,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


# ─── Spreadsheet creation ───────────────────────────────────────────────────────

async def create_spreadsheet(guild_name: str) -> dict:
    """
    Create a new Google Spreadsheet for this guild.
    Returns {"spreadsheet_id": ..., "spreadsheet_url": ...}
    """
    token = await _get_access_token()
    title = f"Train Schedule – {guild_name}"

    body = {
        "properties": {"title": title},
        "sheets": [
            {
                "properties": {
                    "sheetId": 0,
                    "title": "Schedules",
                    "gridProperties": {"frozenRowCount": 1},
                }
            }
        ],
    }

    result = await _sheets_post(token, "", body)
    spreadsheet_id = result["spreadsheetId"]
    spreadsheet_url = result["spreadsheetUrl"]

    # Write header row for Schedules sheet
    await _write_schedule_headers(token, spreadsheet_id)

    logger.info(f"Created spreadsheet {spreadsheet_id} for {guild_name}")
    return {"spreadsheet_id": spreadsheet_id, "spreadsheet_url": spreadsheet_url}


async def _write_schedule_headers(token: str, spreadsheet_id: str):
    """Write the header row on the Schedules tab."""
    headers = [
        [
            "Schedule ID", "Name / Slot", "Date (UK)", "Day",
            "Time (UK)", "Duration (min)", "Signed-Up Players",
            "Twitch Usernames", "Host", "Max Slots", "Notes",
        ]
    ]
    await _sheets_put(
        token,
        f"/{spreadsheet_id}/values/Schedules!A1:K1?valueInputOption=RAW",
        {"values": headers},
    )
    # Bold + freeze header via batchUpdate
    await _sheets_post(
        token,
        f"/{spreadsheet_id}:batchUpdate",
        {
            "requests": [
                {
                    "repeatCell": {
                        "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.5},
                                "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor,foregroundColor)",
                    }
                },
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": 0,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": 11,
                        }
                    }
                },
            ]
        },
    )


# ─── Schedule export (Discord → Sheet) ─────────────────────────────────────────

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _format_uk_time(schedule) -> str:
    """Return the schedule start time as a UK time string."""
    try:
        t = schedule.start_time  # datetime.time (stored as UK wall clock, Europe/London)
        return t.strftime("%H:%M")
    except Exception:
        return "?"


def _format_date(schedule) -> str:
    if schedule.specific_date:
        d = schedule.specific_date
        if isinstance(d, str):
            return d
        return d.strftime("%d/%m/%Y")
    return ""


def _participants_to_str(participants) -> tuple[str, str]:
    """Return (display_names_joined, twitch_usernames_joined)."""
    names   = []
    twitches = []
    for p in participants:
        if not p.is_active:
            continue
        names.append(p.display_name or p.username or "?")
        if p.twitch_username:
            twitches.append(p.twitch_username)
    return ", ".join(names), ", ".join(twitches)


def _schedule_to_row(schedule, participants) -> list:
    """Convert a TrainSchedule + its participants to a Sheets row."""
    display_names, twitch_names = _participants_to_str(participants)
    return [
        str(schedule.id),
        schedule.name or "",
        _format_date(schedule),
        DAY_NAMES[schedule.day_of_week] if schedule.day_of_week is not None else "",
        _format_uk_time(schedule),
        str(schedule.duration_minutes or ""),
        display_names,
        twitch_names,
        "",  # Host – filled separately if needed
        str(schedule.max_participants or ""),
        schedule.description or "",
    ]


async def export_schedules_to_sheet(spreadsheet_id: str, schedules_with_participants: list):
    """
    Overwrite the Schedules tab with fresh data from the database.
    schedules_with_participants: list of (TrainSchedule, [TrainParticipant, ...])
    """
    token = await _get_access_token()

    rows = [
        [
            "Schedule ID", "Name / Slot", "Date (UK)", "Day",
            "Time (UK)", "Duration (min)", "Signed-Up Players",
            "Twitch Usernames", "Host", "Max Slots", "Notes",
        ]
    ]
    for schedule, participants in schedules_with_participants:
        rows.append(_schedule_to_row(schedule, participants))

    # Clear the sheet then write fresh
    await _sheets_post(token, f"/{spreadsheet_id}/values/Schedules!A:K:clear", {})
    if len(rows) > 1:
        await _sheets_put(
            token,
            f"/{spreadsheet_id}/values/Schedules!A1:K{len(rows)}?valueInputOption=RAW",
            {"values": rows},
        )
    logger.info(f"Exported {len(rows)-1} schedules to sheet {spreadsheet_id}")
    return len(rows) - 1


# ─── Schedule import (Sheet → Discord DB) ──────────────────────────────────────

async def import_schedules_from_sheet(spreadsheet_id: str) -> list[dict]:
    """
    Read the Schedules tab and return a list of dicts describing rows
    that should be reflected in the database.

    Returns rows as dicts:
      {schedule_id, name, date_str, time_str, duration_minutes,
       participants_str, twitch_str, max_participants, notes}
    """
    token = await _get_access_token()
    result = await _sheets_get(token, f"/{spreadsheet_id}/values/Schedules!A2:K")
    values = result.get("values", [])

    rows = []
    for row in values:
        # Pad row to 11 columns
        row = row + [""] * (11 - len(row))
        rows.append({
            "schedule_id": row[0].strip(),
            "name":        row[1].strip(),
            "date_str":    row[2].strip(),
            "time_str":    row[4].strip(),
            "duration":    row[5].strip(),
            "participants_str": row[6].strip(),
            "twitch_str":      row[7].strip(),
            "max_participants": row[9].strip(),
            "notes":       row[10].strip(),
        })
    return rows


# ─── Monthly attendance sheet ───────────────────────────────────────────────────

async def ensure_monthly_attendance_sheet(spreadsheet_id: str, year: int, month: int) -> str:
    """
    Create (or verify existence of) a 'YYYY-MM Attendance' worksheet.
    Returns the sheet title.
    """
    token = await _get_access_token()
    tab_title = f"{year:04d}-{month:02d} Attendance"

    # Check existing sheets
    meta = await _sheets_get(token, f"/{spreadsheet_id}?fields=sheets.properties")
    existing = [s["properties"]["title"] for s in meta.get("sheets", [])]

    if tab_title not in existing:
        # Add the sheet
        await _sheets_post(
            token,
            f"/{spreadsheet_id}:batchUpdate",
            {"requests": [{"addSheet": {"properties": {"title": tab_title, "gridProperties": {"frozenRowCount": 2}}}}]},
        )
        logger.info(f"Created attendance tab '{tab_title}' in {spreadsheet_id}")

    return tab_title


async def export_monthly_attendance(
    spreadsheet_id: str,
    year: int,
    month: int,
    attendance_data: list[dict],
):
    """
    Write the monthly attendance grid.

    attendance_data: list of dicts:
      {
        "date": date object,
        "schedule_name": str,
        "participants": [{"discord_name": str, "twitch": str, "present": bool, "messages": int}, ...]
      }
    """
    token = await _get_access_token()
    tab_title = await ensure_monthly_attendance_sheet(spreadsheet_id, year, month)

    if not attendance_data:
        return

    # Collect all unique players across the month
    all_players: dict[str, str] = {}  # twitch_lower → display_name
    for day_data in attendance_data:
        for p in day_data.get("participants", []):
            key = (p.get("twitch") or p.get("discord_name") or "?").lower()
            all_players[key] = p.get("discord_name") or p.get("twitch") or "?"

    sorted_players = sorted(all_players.items(), key=lambda x: x[1].lower())

    # Build header rows
    # Row 1: Date headers
    # Row 2: Schedule name sub-headers
    # Row 3+: One row per player

    date_row   = ["Player", "Twitch"]
    sched_row  = ["", ""]
    col_map: dict[int, tuple[date, str]] = {}  # col_index → (date, sched_name)

    col = 2
    for day_data in attendance_data:
        d = day_data["date"]
        sname = day_data["schedule_name"]
        date_row.append(d.strftime("%d/%m/%Y"))
        sched_row.append(sname[:20])
        col_map[col] = (d, sname)
        col += 1

    # Add totals column
    date_row.append("Total Present")
    sched_row.append("")

    # Build player rows
    player_rows = []
    for twitch_key, display_name in sorted_players:
        row = [display_name, twitch_key]
        total_present = 0
        for ci in range(2, col):
            d, sname = col_map[ci]
            # Find this player in that day's data
            day_data = next((x for x in attendance_data if x["date"] == d and x["schedule_name"] == sname), None)
            if not day_data:
                row.append("")
                continue
            participant = next(
                (p for p in day_data["participants"]
                 if (p.get("twitch") or "").lower() == twitch_key
                 or (p.get("discord_name") or "").lower() == twitch_key),
                None,
            )
            if participant is None:
                row.append("—")
            elif participant.get("present"):
                msgs = participant.get("messages", 0)
                row.append(f"✅ ({msgs}msg)")
                total_present += 1
            else:
                row.append("❌")
        row.append(str(total_present))
        player_rows.append(row)

    all_rows = [date_row, sched_row] + player_rows
    num_cols = len(date_row)
    num_rows = len(all_rows)

    # Clear and write
    range_str = f"'{tab_title}'!A1:{_col_letter(num_cols)}{num_rows}"
    await _sheets_post(token, f"/{spreadsheet_id}/values/{tab_title}!A1:{_col_letter(num_cols)}{num_rows}:clear", {})
    await _sheets_put(
        token,
        f"/{spreadsheet_id}/values/{range_str}?valueInputOption=RAW",
        {"values": all_rows},
    )

    # Format header rows
    meta = await _sheets_get(token, f"/{spreadsheet_id}?fields=sheets.properties")
    sheet_id = next(
        (s["properties"]["sheetId"] for s in meta.get("sheets", []) if s["properties"]["title"] == tab_title),
        None,
    )
    if sheet_id is not None:
        await _sheets_post(
            token,
            f"/{spreadsheet_id}:batchUpdate",
            {
                "requests": [
                    {
                        "repeatCell": {
                            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 2},
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {"bold": True},
                                    "backgroundColor": {"red": 0.1, "green": 0.3, "blue": 0.5},
                                    "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                                }
                            },
                            "fields": "userEnteredFormat(textFormat,backgroundColor,foregroundColor)",
                        }
                    },
                    {
                        "autoResizeDimensions": {
                            "dimensions": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": 0,
                                "endIndex": num_cols,
                            }
                        }
                    },
                ]
            },
        )

    logger.info(f"Exported monthly attendance for {year:04d}-{month:02d}: {len(player_rows)} players, {col-2} sessions")


def _col_letter(n: int) -> str:
    """Convert 1-based column number to letter (1→A, 26→Z, 27→AA)."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


# ─── Convenience: get all data for a full month from DB ────────────────────────

def build_monthly_attendance_data(db_session, guild_id: int, year: int, month: int) -> list[dict]:
    """
    Pull all train attendance for a given month from the database and
    return it formatted for export_monthly_attendance().
    """
    from models import TrainSchedule, TrainParticipant, TwitchChatAttendance
    import calendar

    first_day = date(year, month, 1)
    last_day  = date(year, month, calendar.monthrange(year, month)[1])

    # All schedules in this month
    schedules = (
        db_session.query(TrainSchedule)
        .filter(
            TrainSchedule.guild_id == guild_id,
            TrainSchedule.specific_date >= first_day,
            TrainSchedule.specific_date <= last_day,
        )
        .order_by(TrainSchedule.specific_date, TrainSchedule.start_time)
        .all()
    )

    result = []
    for sched in schedules:
        # Participants who signed up
        participants = (
            db_session.query(TrainParticipant)
            .filter_by(schedule_id=sched.id, is_active=True)
            .all()
        )

        # Twitch chat attendance for this schedule
        attendance_records = (
            db_session.query(TwitchChatAttendance)
            .filter_by(schedule_id=sched.id, guild_id=guild_id)
            .all()
        )
        attendance_map = {a.twitch_username.lower(): a for a in attendance_records if a.twitch_username}

        day_participants = []
        for p in participants:
            twitch = (p.twitch_username or "").lower()
            att = attendance_map.get(twitch)
            day_participants.append({
                "discord_name": p.display_name or p.username or "?",
                "twitch": p.twitch_username or "",
                "present": att.was_present_in_chat if att else False,
                "messages": att.total_messages if att else 0,
            })

        result.append({
            "date": sched.specific_date,
            "schedule_name": sched.name or f"Train {sched.id}",
            "participants": day_participants,
        })

    return result
