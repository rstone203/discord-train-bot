---
name: Schedule date resolution across notification paths
description: How one-time (specific_date) vs recurring (day_of_week) trains must be resolved everywhere a schedule fires, to avoid pinging the wrong rider.
---

# Schedule date resolution: specific_date vs day_of_week

TrainSchedule rows can be **one-time** (have `specific_date` set) or **recurring**
(`specific_date` NULL, fire weekly on `day_of_week`). One-time rows ALSO carry a
`day_of_week` value matching their date's weekday, which is the trap.

**The rule:** any code that decides when a schedule fires, who to ping, or whether a train
is "today/tomorrow" MUST resolve the date from `specific_date` when it is set, and only fall
back to `day_of_week` for recurring rows. Resolving purely by `day_of_week` makes an expired
one-time train re-fire on the same weekday next week and ping last week's rider.

**Why:** A real "wrong person pinged" incident. The bug was fixed piecemeal — first the
Discord notification path, then the Twitch-chat ping paths — and each time MORE sites were
found still using `day_of_week` only. The lesson: this resolution logic is duplicated across
many independent surfaces, so a fix in one place is almost never complete.

**How to apply:**
- Canonical pattern: `if specific_date` → use it directly; if `start_time.hour < 5` add one
  day (early-morning slots belong to the PREVIOUS evening's session and roll to the next
  calendar day); `else` → weekday math. This `<5am` rollover must be applied consistently
  everywhere, including selection filters AND display/`upcoming` commands, or trains appear on
  the wrong day.
- A one-time train that is fully in the past must NEVER roll forward to next week — `return`/
  `continue` instead of adding 7 days, otherwise stale guards (e.g. `days_until_schedule == 0`)
  silently resurrect it.
- When ordering riders across a session that crosses midnight, sort by the FULL localized
  datetime, never time-of-day alone, or a 00:30 slot sorts before a 23:00 slot.
- Surfaces that need this: the rider-ping/warning paths, the live-role / attendance manager
  (selection, per-schedule date calc, and its past-handling), the "trains tomorrow" DM to
  trusted users, chat-monitoring start, the heartbeat status embed's "next upcoming train"
  selector, the comprehensive attendance-report generator, and the schedule-listing/`nexttrain`
  display commands. They share intent but not code — grep for `day_of_week ==` and weekday-offset
  date math whenever this class of bug resurfaces. Consider centralizing into one resolver if
  they drift. (Pure schedule-*creation* and full schedule-*listing* commands intentionally use
  weekday math and are out of scope.)

## Attendance report lookback window (comprehensive_report_manager)

The comprehensive report's attendance lookback must be bounded to the report's own coverage
span (number of consecutive train days), NOT a flat multi-day lookback.

**Why:** Attendance `train_date` is an **EST** date (`get_est_time().date()`), while the report's
`report_date` is derived in **UTC** — they can drift ~1 day at timezone boundaries, so the query
needs a small tolerance window rather than an exact-date match. But a flat 7-day lookback was too
wide: an expired one-time train stays `is_active` and keeps its `day_of_week`, so the same weekday
next week re-triggers report generation AND the 7-day window re-pulls last week's attendance,
emitting a duplicate report containing stale data.

**How to apply:** bound the lower edge to `report_date - len(train_days)` days (1-day report → 2-day
window, weekend → 3-day window). This preserves the EST/UTC tolerance and multi-day weekend reports
while excluding prior-week bleed-through. Don't switch to exact-date matching — the EST/UTC offset
would drop legitimate records. A cleaner long-term fix is deactivating one-time schedules once they
complete, which would remove most of this filtering burden across all surfaces above.
