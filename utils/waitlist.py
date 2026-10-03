"""Shared helpers for the slot waitlist (Task #38).

Waitlist entries reuse the TrainParticipant table: a "waiting" row is stored with
is_waitlisted=True and is_active=False, keyed by schedule_id (the recurring slot).
Active riders are is_active=True. A row with both False is inert (left/declined).

These helpers are used by both the panel (cogs/train_participant_commands.py) and
the open-seat offer detector (cogs/notification_commands.py) so the queue rules
stay in one place.
"""
from datetime import datetime

from sqlalchemy import func

from models import TrainParticipant, TrainSchedule

# How long an open-seat offer DM stays valid before the detector treats it as
# expired and advances to the next person in line.
OFFER_TIMEOUT_MINUTES = 30


def is_recurring_slot(schedule) -> bool:
    """True for recurring weekly slots only.

    The slot waitlist intentionally targets recurring slots (a standing "want this
    seat" queue). One-off/date-specific schedules are out of scope.
    """
    return (getattr(schedule, "schedule_type", None) == "recurring"
            and getattr(schedule, "specific_date", None) is None)


def recurring_schedules_query(session, guild_id=None):
    """Query for active recurring slots (optionally scoped to one guild)."""
    q = session.query(TrainSchedule).filter(
        TrainSchedule.is_active == True,  # noqa: E712
        TrainSchedule.schedule_type == "recurring",
        TrainSchedule.specific_date.is_(None),
    )
    if guild_id is not None:
        q = q.filter(TrainSchedule.guild_id == guild_id)
    return q


def slot_capacity(schedule: TrainSchedule) -> int:
    """Seats in a slot. Treat an unset max_participants as a single-rider slot."""
    cap = getattr(schedule, "max_participants", None)
    try:
        cap = int(cap) if cap is not None else 1
    except (TypeError, ValueError):
        cap = 1
    return cap if cap > 0 else 1


def active_rider_count(session, schedule_id: int) -> int:
    """Number of currently-assigned (active) riders for a slot."""
    return session.query(TrainParticipant).filter_by(
        schedule_id=schedule_id,
        is_active=True,
    ).count()


def waitlisted_rows(session, schedule_id: int):
    """All waiting rows for a slot, ordered by queue position then signup time."""
    return session.query(TrainParticipant).filter_by(
        schedule_id=schedule_id,
        is_waitlisted=True,
        is_active=False,
    ).order_by(
        TrainParticipant.waitlist_position.asc(),
        TrainParticipant.signed_up_at.asc(),
    ).all()


def first_waitlister(session, schedule_id: int):
    """The next person in line for a slot, or None."""
    return session.query(TrainParticipant).filter_by(
        schedule_id=schedule_id,
        is_waitlisted=True,
        is_active=False,
    ).order_by(
        TrainParticipant.waitlist_position.asc(),
        TrainParticipant.signed_up_at.asc(),
    ).first()


def renumber_waitlist(session, schedule_id: int) -> None:
    """Re-index remaining waiting rows to a contiguous 1..N sequence.

    Ordered by current position (falling back to signup time) to preserve queue
    order. Caller is responsible for committing.
    """
    for idx, entry in enumerate(waitlisted_rows(session, schedule_id), start=1):
        if entry.waitlist_position != idx:
            entry.waitlist_position = idx


def has_open_seat(session, schedule) -> bool:
    """True when a slot has fewer active riders than its capacity."""
    return active_rider_count(session, schedule.id) < slot_capacity(schedule)


def add_to_waitlist(session, schedule, user_id, username, display_name):
    """Put a user on a slot's waitlist.

    Returns a (status, position) tuple where status is one of:
      'active'     — already an assigned rider (not added)
      'waiting'    — already on the waitlist (position returned)
      'added'      — newly added (position returned)
    Caller commits.
    """
    schedule_id = schedule.id

    active = session.query(TrainParticipant).filter_by(
        schedule_id=schedule_id, user_id=user_id, is_active=True,
    ).first()
    if active:
        return "active", None

    waiting = session.query(TrainParticipant).filter_by(
        schedule_id=schedule_id, user_id=user_id,
        is_waitlisted=True, is_active=False,
    ).first()
    if waiting:
        return "waiting", waiting.waitlist_position

    max_pos = session.query(
        func.max(TrainParticipant.waitlist_position)
    ).filter_by(
        schedule_id=schedule_id, is_waitlisted=True, is_active=False,
    ).scalar()
    position = (max_pos or 0) + 1

    # Reuse an inert prior row for this user/slot if one exists, else create new.
    inert = session.query(TrainParticipant).filter_by(
        schedule_id=schedule_id, user_id=user_id,
        is_waitlisted=False, is_active=False,
    ).first()
    if inert:
        inert.is_waitlisted = True
        inert.waitlist_position = position
        inert.offer_sent_at = None
        inert.signed_up_at = datetime.utcnow()
    else:
        session.add(TrainParticipant(
            schedule_id=schedule_id,
            guild_id=schedule.guild_id,
            user_id=user_id,
            username=(username or "")[:32],
            display_name=(display_name or username or "")[:32],
            signed_up_at=datetime.utcnow(),
            is_active=False,
            is_waitlisted=True,
            waitlist_position=position,
            notes="Waitlist panel",
        ))
    return "added", position


def remove_from_waitlist(session, schedule_id, user_id) -> bool:
    """Take a user off a slot's waitlist and renumber. Returns True if removed.

    Caller commits.
    """
    waiting = session.query(TrainParticipant).filter_by(
        schedule_id=schedule_id, user_id=user_id,
        is_waitlisted=True, is_active=False,
    ).first()
    if not waiting:
        return False
    waiting.is_waitlisted = False
    waiting.waitlist_position = None
    waiting.offer_sent_at = None
    renumber_waitlist(session, schedule_id)
    return True


def move_to_back(session, schedule_id, entry) -> None:
    """Send a waiting row to the end of the queue (used on offer expiry)."""
    max_pos = session.query(
        func.max(TrainParticipant.waitlist_position)
    ).filter_by(
        schedule_id=schedule_id, is_waitlisted=True, is_active=False,
    ).scalar()
    entry.waitlist_position = (max_pos or 0) + 1
    entry.offer_sent_at = None
    renumber_waitlist(session, schedule_id)
