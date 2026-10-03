---
name: Attendance pipeline routing
description: Why during-train attendance reports and day-after summaries can silently never post in this raid-train bot.
---

# Attendance pipeline routing

Two independent failure modes that both make attendance "look broken" with no errors in logs:

## 1. During-train reports only post for the schedule HOST
Only `_monitor_train_session` (started via `start_train_monitoring`) posts periodic + final
attendance reports. The ad-hoc path (`start_tracking_adhoc` -> `_monitor_adhoc_session`) just
sleeps and posts NOTHING.

`_auto_start_attendance_on_live` decides which path a live user takes. In raid trains each leg is
streamed by a PARTICIPANT (rider), not the single schedule host, so matching schedules on
`host_user_id` alone sends every rider to ad-hoc -> no reports.

**Rule:** live-detection must match schedules where the user is the host OR an active
`TrainParticipant`. When a user is active in multiple nearby legs, pick the NEAREST by time delta,
not the first match.

## 2. The comprehensive (day-after) report system can be dead code
`ComprehensiveReportManager` (generate_and_schedule_reports / send_pending_reports) only runs if
something calls it on a loop. It is easy for this to have ZERO callers, so reports sit in the DB
with `is_sent=False` forever and never arrive. It needs a `@tasks.loop` in the bot that calls
generate then send, started in setup_hook.

**Why:** these are wiring/routing bugs, not crashes — nothing logs an error, so "is attendance
working?" requires tracing which monitor path actually fires and confirming the report loop exists.

**How to apply:**
- Generation idempotency: the existing-report lookup must NOT filter on `is_sent`, or a frequent
  loop regenerates + resends duplicates after the first send.
- Always add a staleness guard when first wiring a never-run sender: suppress (mark sent) reports
  whose `scheduled_send_time` is older than a few days, else you dump months-old rows on startup.
- `active_monitors` keyed `train_{schedule_id}` dedupes monitors; reserve the slot before any
  awaits to avoid a duplicate-monitor race, and clean up the reservation on failure.
