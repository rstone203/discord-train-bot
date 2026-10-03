---
name: Waitlist promotion model
description: How slot-waitlist seat promotions work and the single-owner rule for them
---

The slot waitlist reuses TrainParticipant rows: a waiting person is `is_waitlisted=True, is_active=False`; an active rider is `is_active=True`; both-False is inert. Queue rules live in `utils/waitlist.py` (shared by panel + detector) — do not reimplement them inline.

**Rule: the open-seat detector is the SINGLE owner of all waitlist promotions**, and only via the DM Accept/Decline offer flow (consent required). No other code path that frees a seat (admin remove, API delete, weekly reset, voluntary leave) may promote a waitlister itself — it must just leave the seat open for the detector.

**Why:** the confirmed design is consent-based DM offers. An earlier version also instant-promoted the next waitlister on leave, which bypassed consent and diverged in behavior (no trusted-role notice). Splitting promotion across two mechanisms caused inconsistency; consolidating on the detector fixed it.

**Scope:** the slot waitlist targets recurring weekly slots only — one-off/date-specific schedules are out of scope. Filter with `is_recurring_slot` / `recurring_schedules_query` everywhere this feature reads schedules (panel embed, join/leave picker, on-select guard, detector).

**Gating:** both the detector and the Accept/Decline handler must check the per-guild `waitlist` beta flag and fail closed. Throttling/expiry uses `offer_sent_at` (set before DM send to avoid per-tick spam; expires after a timeout, then move-to-back).
