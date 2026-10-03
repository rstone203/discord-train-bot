---
name: Train schedule start_time storage & timezone
description: How TrainSchedule.start_time is stored/interpreted and how to load an EST schedule image correctly
---

# Train schedule start_time is stored as UK (Europe/London) wall-clock

`TrainSchedule.start_time` is a naive TIME that the bot **localizes to Europe/London**
everywhere (notification scheduler, live_role_manager, twitch_chat_bot all do
`uk_tz.localize(datetime.combine(date, start_time))`). It is NOT stored as Eastern or UTC.

**Why:** the community/owner runs on US Eastern, but the system's canonical schedule
timezone is London. So an EST schedule image must be converted before storing.

**How to apply when loading an EST schedule image:**
- Convert each Eastern time to London wall-clock and store THAT as `start_time`.
  In summer (US EDT + UK BST) the offset is a clean **+5 hours**. (Offset briefly differs
  only during the ~3wk March / ~1wk Nov DST-misalignment windows.)
- Evening Eastern slots cross midnight in London: e.g. 7:00 PM ET = 00:00 London,
  8:30 PM ET = 01:30, 10:00 PM ET = 03:00. For those rolled-past-midnight slots set
  `day_of_week` to the **next day** (Sun=6 → Mon=0). Rule the code uses: if the stored
  hour < 5, the slot belongs to day_of_week+1. The display layer converts back to ET, so
  these still show as "Sunday X PM" to users.
- Verify by localizing stored time to Europe/London then `.astimezone(US/Eastern)` — it
  must reproduce the image's Eastern times exactly.

**Loader pattern:** see the `!loadsundayschedule` owner command (cogs/admin_commands.py,
`load_sunday_schedule`). For each slot create a TrainSchedule (duration 90,
schedule_type='one-time', specific_date=the Sunday date, max_participants=1) + one
TrainParticipant with `twitch_username` set (drives chat monitoring) and `user_id`=
the linked Discord id or **0 if unlinked** (0 is fine; monitoring uses twitch_username,
only per-user attendance mapping needs a real id). Then call
`invalidate_schedule_cache(guild_id)` and restart the bot.
day_of_week mapping: Mon=0 … Sun=6.
