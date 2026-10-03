"""
Tests for the Task #38 slot waitlist helpers (utils/waitlist.py).

Covers the queue rules that both the panel and the open-seat offer detector
depend on, using an in-memory SQLite database:
  - capacity + active-rider counting
  - add/remove with contiguous renumbering
  - first-in-line ordering and offer expiry (move_to_back)
  - open-seat detection and the accept-promotion DB transition

Run with: python -m unittest tests.test_waitlist
"""
import os
import unittest
from datetime import datetime, timedelta, time

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, ARRAY
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles


# Some models use PostgreSQL ARRAY columns; teach SQLite to render them as TEXT
# so the in-memory test database can create the tables under test.
@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(element, compiler, **kw):  # pragma: no cover - trivial
    return "TEXT"


from models import Base, TrainSchedule, TrainParticipant
from utils import waitlist


class WaitlistHelpersTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        # Only create the tables under test; some other models use PG-specific
        # column types (ARRAY) that SQLite can't render.
        Base.metadata.create_all(
            self.engine,
            tables=[TrainSchedule.__table__, TrainParticipant.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.schedule = TrainSchedule(
            guild_id=1,
            name="Test Slot",
            day_of_week=2,
            start_time=time(20, 0),
            duration_minutes=60,
            schedule_type="recurring",
            specific_date=None,
            max_participants=1,
            is_active=True,
        )
        self.session.add(self.schedule)
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def _add(self, user_id):
        status, pos = waitlist.add_to_waitlist(
            self.session, self.schedule, user_id, f"user{user_id}", f"User {user_id}"
        )
        self.session.commit()
        return status, pos

    def _add_active_rider(self, user_id):
        self.session.add(TrainParticipant(
            schedule_id=self.schedule.id, guild_id=1, user_id=user_id,
            username=f"r{user_id}", display_name=f"R{user_id}", is_active=True,
        ))
        self.session.commit()

    # --- capacity / counting ---

    def test_slot_capacity_defaults_to_one(self):
        s = TrainSchedule(guild_id=1, name="x", day_of_week=0,
                          start_time=time(1, 0), max_participants=None)
        self.assertEqual(waitlist.slot_capacity(s), 1)

    def test_slot_capacity_reads_max_participants(self):
        self.schedule.max_participants = 3
        self.assertEqual(waitlist.slot_capacity(self.schedule), 3)

    def test_active_rider_count(self):
        self.assertEqual(waitlist.active_rider_count(self.session, self.schedule.id), 0)
        self._add_active_rider(100)
        self.assertEqual(waitlist.active_rider_count(self.session, self.schedule.id), 1)

    def test_has_open_seat(self):
        self.assertTrue(waitlist.has_open_seat(self.session, self.schedule))
        self._add_active_rider(100)
        self.assertFalse(waitlist.has_open_seat(self.session, self.schedule))

    # --- add / remove / renumber ---

    def test_add_assigns_contiguous_positions(self):
        self.assertEqual(self._add(11), ("added", 1))
        self.assertEqual(self._add(12), ("added", 2))
        self.assertEqual(self._add(13), ("added", 3))

    def test_add_existing_waitlister_returns_waiting(self):
        self._add(11)
        self.assertEqual(self._add(11), ("waiting", 1))

    def test_add_active_rider_returns_active(self):
        self._add_active_rider(11)
        self.assertEqual(self._add(11), ("active", None))

    def test_remove_renumbers_remaining(self):
        self._add(11)
        self._add(12)
        self._add(13)
        self.assertTrue(waitlist.remove_from_waitlist(self.session, self.schedule.id, 12))
        self.session.commit()
        rows = waitlist.waitlisted_rows(self.session, self.schedule.id)
        self.assertEqual([(r.user_id, r.waitlist_position) for r in rows],
                         [(11, 1), (13, 2)])

    def test_remove_nonexistent_returns_false(self):
        self.assertFalse(waitlist.remove_from_waitlist(self.session, self.schedule.id, 999))

    # --- ordering / offer expiry ---

    def test_first_waitlister_is_lowest_position(self):
        self._add(11)
        self._add(12)
        self.assertEqual(waitlist.first_waitlister(self.session, self.schedule.id).user_id, 11)

    def test_move_to_back_sends_to_end_and_clears_offer(self):
        self._add(11)
        self._add(12)
        first = waitlist.first_waitlister(self.session, self.schedule.id)
        first.offer_sent_at = datetime.utcnow()
        self.session.commit()
        waitlist.move_to_back(self.session, self.schedule.id, first)
        self.session.commit()
        rows = waitlist.waitlisted_rows(self.session, self.schedule.id)
        # 12 is now first, 11 moved to the back with offer cleared
        self.assertEqual([r.user_id for r in rows], [12, 11])
        self.assertIsNone(rows[1].offer_sent_at)

    # --- accept-promotion transition (mirrors the offer handler) ---

    # --- recurring-only scope ---

    def test_is_recurring_slot(self):
        from datetime import date
        recurring = TrainSchedule(guild_id=1, name="r", day_of_week=2,
                                  start_time=time(20, 0), duration_minutes=60,
                                  schedule_type="recurring", specific_date=None)
        one_off = TrainSchedule(guild_id=1, name="o", day_of_week=2,
                                start_time=time(20, 0), duration_minutes=60,
                                schedule_type="specific", specific_date=date(2026, 7, 3))
        self.assertTrue(waitlist.is_recurring_slot(recurring))
        self.assertFalse(waitlist.is_recurring_slot(one_off))

    def test_recurring_schedules_query_excludes_one_off(self):
        from datetime import date
        one_off = TrainSchedule(
            guild_id=1, name="One-time", day_of_week=4, start_time=time(19, 0),
            duration_minutes=60, schedule_type="specific",
            specific_date=date(2026, 7, 3), is_active=True,
        )
        self.session.add(one_off)
        self.session.commit()
        results = waitlist.recurring_schedules_query(self.session, 1).all()
        ids = {s.id for s in results}
        self.assertIn(self.schedule.id, ids)       # recurring slot included
        self.assertNotIn(one_off.id, ids)          # one-off excluded

    def test_leaving_active_rider_leaves_seat_open_for_detector(self):
        """When an active rider leaves, the seat stays open (no instant promotion).

        Mirrors the new _leave_train_internal behavior: the detector owns all
        waitlist promotions via the DM offer, so a waitlister must NOT be flipped
        to active just because a seat freed up.
        """
        self._add_active_rider(100)
        self._add(11)
        self.assertFalse(waitlist.has_open_seat(self.session, self.schedule))

        # Simulate the active rider leaving: deactivate, do NOT promote anyone.
        rider = self.session.query(TrainParticipant).filter_by(
            schedule_id=self.schedule.id, user_id=100, is_active=True).first()
        rider.is_active = False
        self.session.commit()

        # Seat is now open and the waitlister is still waiting (awaiting an offer).
        self.assertTrue(waitlist.has_open_seat(self.session, self.schedule))
        first = waitlist.first_waitlister(self.session, self.schedule.id)
        self.assertEqual(first.user_id, 11)
        self.assertEqual(waitlist.active_rider_count(self.session, self.schedule.id), 0)

    def test_accept_promotion_makes_active_and_renumbers(self):
        self._add(11)
        self._add(12)
        # Seat is open; simulate accepting the offer for user 11.
        self.assertTrue(waitlist.has_open_seat(self.session, self.schedule))
        row = waitlist.first_waitlister(self.session, self.schedule.id)
        row.is_active = True
        row.is_waitlisted = False
        row.waitlist_position = None
        row.offer_sent_at = None
        waitlist.renumber_waitlist(self.session, self.schedule.id)
        self.session.commit()

        self.assertEqual(waitlist.active_rider_count(self.session, self.schedule.id), 1)
        self.assertFalse(waitlist.has_open_seat(self.session, self.schedule))
        remaining = waitlist.waitlisted_rows(self.session, self.schedule.id)
        self.assertEqual([(r.user_id, r.waitlist_position) for r in remaining], [(12, 1)])


class _SessionCtx:
    """Context manager that hands the handler our in-memory session unchanged.

    Supports both sync (`with`) and async (`async with`) usage so it can stand
    in for either DatabaseSession or AsyncDatabaseSession in tests.
    """

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


class OfferHandlerStaleOfferTests(unittest.IsolatedAsyncioTestCase):
    """Directly exercise _handle_waitlist_offer_response for queue-order safety."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[TrainSchedule.__table__, TrainParticipant.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.schedule = TrainSchedule(
            guild_id=1, name="Test Slot", day_of_week=2, start_time=time(20, 0),
            duration_minutes=60, schedule_type="recurring", specific_date=None,
            max_participants=1, is_active=True,
        )
        self.session.add(self.schedule)
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def _waitlist(self, user_id, position, offer_sent_at=None):
        self.session.add(TrainParticipant(
            schedule_id=self.schedule.id, guild_id=1, user_id=user_id,
            username=f"u{user_id}", display_name=f"U{user_id}",
            is_active=False, is_waitlisted=True, waitlist_position=position,
            offer_sent_at=offer_sent_at,
        ))
        self.session.commit()

    def _interaction(self, user_id):
        from unittest.mock import AsyncMock, MagicMock
        interaction = MagicMock()
        interaction.user.id = user_id
        interaction.response.edit_message = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.is_done = MagicMock(return_value=False)
        bot = MagicMock()
        bot.permissions_manager.is_feature_enabled = AsyncMock(return_value=True)
        bot.get_guild = MagicMock(return_value=None)
        bot.get_cog = MagicMock(return_value=None)
        interaction.client = bot
        return interaction

    async def _run_accept(self, user_id):
        from unittest import mock
        from cogs import notification_commands as nc
        with mock.patch.object(nc, "DatabaseSession",
                               lambda: _SessionCtx(self.session)), \
             mock.patch.object(nc, "AsyncDatabaseSession",
                                lambda: _SessionCtx(self.session)):
            await nc._handle_waitlist_offer_response(
                self._interaction(user_id), "accept", self.schedule.id, user_id)

    async def test_stale_accepter_cannot_jump_queue(self):
        """After timeout moves user 11 to the back, an old Accept must be rejected."""
        # 11 was front and offered, then their offer expired and 12 became front.
        self._waitlist(11, 2, offer_sent_at=datetime.utcnow() - timedelta(minutes=45))
        self._waitlist(12, 1, offer_sent_at=datetime.utcnow())  # detector offered 12
        # 11 clicks their stale Accept button.
        await self._run_accept(11)
        row11 = self.session.query(TrainParticipant).filter_by(user_id=11).first()
        self.assertFalse(row11.is_active, "stale accepter must not be promoted")
        self.assertEqual(waitlist.active_rider_count(self.session, self.schedule.id), 0)

    async def test_current_front_with_valid_offer_is_promoted(self):
        """The current first-in-line with an unexpired offer can accept."""
        self._waitlist(12, 1, offer_sent_at=datetime.utcnow())
        await self._run_accept(12)
        row12 = self.session.query(TrainParticipant).filter_by(user_id=12).first()
        self.assertTrue(row12.is_active, "current front waitlister should be promoted")
        self.assertFalse(row12.is_waitlisted)
        self.assertEqual(waitlist.active_rider_count(self.session, self.schedule.id), 1)

    async def test_expired_offer_for_front_user_is_rejected(self):
        """Even the front user cannot accept once their offer has expired."""
        self._waitlist(12, 1, offer_sent_at=datetime.utcnow() - timedelta(minutes=45))
        await self._run_accept(12)
        row12 = self.session.query(TrainParticipant).filter_by(user_id=12).first()
        self.assertFalse(row12.is_active, "expired offer must not promote")


class ApplyWaitlistDeclineTests(unittest.IsolatedAsyncioTestCase):
    """Exercise apply_waitlist_decline, the single shared decline code path used
    by both the DM 'Decline' button and the /mywaitlists command.

    The detector is the sole owner of promotions, so decline must only remove the
    declining user and never promote anyone — even when a seat is open and someone
    is next in line. These tests pin that contract so the command path can never
    drift from the DM-button behavior.
    """

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[TrainSchedule.__table__, TrainParticipant.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.schedule = TrainSchedule(
            guild_id=1, name="Test Slot", day_of_week=2, start_time=time(20, 0),
            duration_minutes=60, schedule_type="recurring", specific_date=None,
            max_participants=1, is_active=True,
        )
        self.session.add(self.schedule)
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def _waitlist(self, user_id, position, offer_sent_at=None):
        self.session.add(TrainParticipant(
            schedule_id=self.schedule.id, guild_id=1, user_id=user_id,
            username=f"u{user_id}", display_name=f"U{user_id}",
            is_active=False, is_waitlisted=True, waitlist_position=position,
            offer_sent_at=offer_sent_at,
        ))
        self.session.commit()

    def _bot(self, flag_enabled=True):
        from unittest.mock import MagicMock, AsyncMock
        bot = MagicMock()
        bot.permissions_manager.is_feature_enabled = AsyncMock(
            return_value=flag_enabled)
        return bot

    async def _decline(self, user_id, flag_enabled=True):
        from unittest import mock
        from cogs import notification_commands as nc
        with mock.patch.object(nc, "DatabaseSession",
                               lambda: _SessionCtx(self.session)), \
             mock.patch.object(nc, "AsyncDatabaseSession",
                                lambda: _SessionCtx(self.session)):
            return await nc.apply_waitlist_decline(
                self._bot(flag_enabled), self.schedule.id, user_id)

    async def test_decline_removes_row_and_returns_declined(self):
        self._waitlist(11, 1, offer_sent_at=datetime.utcnow())
        status = await self._decline(11)
        self.assertEqual(status, "declined")
        # The waiting row is cleared (no longer waitlisted, no offer pending).
        self.assertEqual(waitlist.waitlisted_rows(self.session, self.schedule.id), [])
        row = self.session.query(TrainParticipant).filter_by(user_id=11).first()
        self.assertFalse(row.is_waitlisted)
        self.assertFalse(row.is_active, "decline must never promote the user")
        self.assertIsNone(row.waitlist_position)
        self.assertIsNone(row.offer_sent_at)

    async def test_decline_disabled_when_flag_off_no_mutation(self):
        self._waitlist(11, 1, offer_sent_at=datetime.utcnow())
        status = await self._decline(11, flag_enabled=False)
        self.assertEqual(status, "disabled")
        # Fail closed: the row must be untouched while the flag is off.
        row = self.session.query(TrainParticipant).filter_by(user_id=11).first()
        self.assertTrue(row.is_waitlisted)
        self.assertFalse(row.is_active)
        self.assertEqual(row.waitlist_position, 1)
        self.assertIsNotNone(row.offer_sent_at)

    async def test_decline_not_found_for_nonwaitlisted_user(self):
        # User 11 is on the waitlist; user 999 is not.
        self._waitlist(11, 1)
        status = await self._decline(999)
        self.assertEqual(status, "not_found")
        # The real waitlister is left undisturbed.
        self.assertEqual(
            [r.user_id for r in waitlist.waitlisted_rows(self.session, self.schedule.id)],
            [11])

    async def test_decline_not_found_for_inactive_schedule(self):
        self._waitlist(11, 1)
        self.schedule.is_active = False
        self.session.commit()
        status = await self._decline(11)
        self.assertEqual(status, "not_found")
        # No mutation — user remains waitlisted on the (now inactive) schedule.
        row = self.session.query(TrainParticipant).filter_by(user_id=11).first()
        self.assertTrue(row.is_waitlisted)

    async def test_decline_advances_queue_without_auto_promotion(self):
        """After 11 declines, 12 becomes front of queue but is NOT promoted.

        The decline path must hand off to the detector: the next person becomes
        first-in-line with no offer pending and no active seat, so the detector
        (not the decline path) is the one that will issue the next offer.
        """
        self._waitlist(11, 1, offer_sent_at=datetime.utcnow())
        self._waitlist(12, 2)
        status = await self._decline(11)
        self.assertEqual(status, "declined")

        rows = waitlist.waitlisted_rows(self.session, self.schedule.id)
        # 12 is now front of queue, renumbered to position 1.
        self.assertEqual([(r.user_id, r.waitlist_position) for r in rows], [(12, 1)])
        front = waitlist.first_waitlister(self.session, self.schedule.id)
        self.assertEqual(front.user_id, 12)
        # The decline path did NOT auto-promote or pre-issue an offer to 12.
        self.assertIsNone(front.offer_sent_at,
                          "decline must not issue the next offer; the detector does")
        self.assertEqual(waitlist.active_rider_count(self.session, self.schedule.id), 0)
        self.assertTrue(waitlist.has_open_seat(self.session, self.schedule))

    async def test_stale_offer_decline_removes_cleanly_without_affecting_promoted(self):
        """A stale/expired pending offer declined from /mywaitlists still removes
        the user cleanly and leaves an already-promoted rider untouched.
        """
        # Someone already took the seat (the detector promoted them earlier).
        self.session.add(TrainParticipant(
            schedule_id=self.schedule.id, guild_id=1, user_id=100,
            username="r100", display_name="R100", is_active=True,
            is_waitlisted=False, waitlist_position=None,
        ))
        # 11 still holds a stale, long-expired offer row behind them.
        self._waitlist(11, 1, offer_sent_at=datetime.utcnow() - timedelta(minutes=90))
        self.session.commit()

        status = await self._decline(11)
        self.assertEqual(status, "declined")

        # 11 is cleanly removed from the waitlist.
        self.assertEqual(waitlist.waitlisted_rows(self.session, self.schedule.id), [])
        row11 = self.session.query(TrainParticipant).filter_by(user_id=11).first()
        self.assertFalse(row11.is_waitlisted)
        self.assertFalse(row11.is_active)

        # The already-promoted rider is unaffected — still the active seat holder.
        row100 = self.session.query(TrainParticipant).filter_by(user_id=100).first()
        self.assertTrue(row100.is_active)
        self.assertFalse(row100.is_waitlisted)
        self.assertEqual(waitlist.active_rider_count(self.session, self.schedule.id), 1)


class DynamicItemRestartSafetyTests(unittest.IsolatedAsyncioTestCase):
    """Validate the restart-safe contract for the DM Accept/Decline buttons.

    On a bot restart the buttons are re-bound purely from their custom_id via
    discord.py's DynamicItem machinery: the registered template regex must match
    the custom_id the button emitted, and from_custom_id must reconstruct the
    same action/schedule/user. This is the actual mechanism behind "buttons
    survive a restart", so it can be verified without a live Discord restart.
    """

    async def test_custom_id_matches_template_and_round_trips(self):
        import re
        from cogs.notification_commands import WaitlistOfferButton, build_offer_view

        schedule_id, user_id = 1234, 567890
        for action in ("accept", "decline"):
            button = WaitlistOfferButton(action, schedule_id, user_id)
            custom_id = button.item.custom_id

            # The class-level template (what add_dynamic_items registers) must
            # fully match the emitted custom_id, or Discord won't route the click
            # back to this handler after a restart.
            pattern = WaitlistOfferButton.__discord_ui_compiled_template__
            match = pattern.fullmatch(custom_id)
            self.assertIsNotNone(
                match, f"custom_id {custom_id!r} did not match the registered template")

            # from_custom_id is what rebuilds the live button from the persisted id.
            rebuilt = await WaitlistOfferButton.from_custom_id(None, None, match)
            self.assertEqual(rebuilt.action, action)
            self.assertEqual(rebuilt.schedule_id, schedule_id)
            self.assertEqual(rebuilt.user_id, user_id)

    async def test_offer_view_carries_both_buttons_with_distinct_ids(self):
        from cogs.notification_commands import build_offer_view
        view = build_offer_view(42, 99)
        ids = sorted(child.custom_id for child in view.children)
        self.assertEqual(ids, [
            "waitlist_offer:accept:42:99",
            "waitlist_offer:decline:42:99",
        ])


class CheckWaitlistOffersGatingTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the live scheduler path check_waitlist_offers for flag gating."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[TrainSchedule.__table__, TrainParticipant.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.schedule = TrainSchedule(
            guild_id=1, name="Test Slot", day_of_week=2, start_time=time(20, 0),
            duration_minutes=60, schedule_type="recurring", specific_date=None,
            max_participants=1, is_active=True,
        )
        self.session.add(self.schedule)
        self.session.commit()
        # One waitlister, no active rider -> a seat is genuinely open.
        self.session.add(TrainParticipant(
            schedule_id=self.schedule.id, guild_id=1, user_id=11,
            username="u11", display_name="U11",
            is_active=False, is_waitlisted=True, waitlist_position=1,
            offer_sent_at=None,
        ))
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def _make_self(self, flag_enabled):
        from unittest.mock import AsyncMock, MagicMock
        fake = MagicMock()
        fake.logger = MagicMock()
        sent_user = MagicMock()
        sent_user.send = AsyncMock()
        fake.bot.get_user = MagicMock(return_value=sent_user)
        fake.bot.permissions_manager.is_feature_enabled = AsyncMock(
            return_value=flag_enabled)
        return fake, sent_user

    async def _run(self, flag_enabled):
        from unittest import mock
        from cogs import notification_commands as nc
        fake_self, sent_user = self._make_self(flag_enabled)
        with mock.patch.object(nc, "DatabaseSession",
                               lambda: _SessionCtx(self.session)), \
             mock.patch.object(nc, "AsyncDatabaseSession",
                                lambda: _SessionCtx(self.session)):
            await nc.NotificationCommands.check_waitlist_offers(fake_self)
        return sent_user

    async def test_no_offer_when_flag_off(self):
        sent_user = await self._run(flag_enabled=False)
        sent_user.send.assert_not_called()
        row = self.session.query(TrainParticipant).filter_by(user_id=11).first()
        self.assertIsNone(row.offer_sent_at, "no offer should be recorded while flag off")

    async def test_offer_sent_and_recorded_when_flag_on(self):
        sent_user = await self._run(flag_enabled=True)
        sent_user.send.assert_awaited_once()
        row = self.session.query(TrainParticipant).filter_by(user_id=11).first()
        self.assertIsNotNone(row.offer_sent_at, "offer should be timestamped when sent")


class PromotionNoticeChannelResolutionTests(unittest.IsolatedAsyncioTestCase):
    """Cover _post_waitlist_promotion_notice channel resolution + trusted pings.

    This is the "Waitlist Seat Filled" admin alert. Its channel is resolved with
    fallback logic (configured !setwaitlistlog channel -> waitlist_panel display
    channel) and it pings the active trusted role(s) so admins update the docs.
    None of that was covered before, so a regression could silently stop admins
    from being told a rider changed.
    """

    def setUp(self):
        from models import SystemSettings, TrustedRole, PersistentMessageDisplay
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[
                TrainSchedule.__table__,
                TrainParticipant.__table__,
                SystemSettings.__table__,
                TrustedRole.__table__,
                PersistentMessageDisplay.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.schedule = TrainSchedule(
            guild_id=1, name="Test Slot", day_of_week=2, start_time=time(20, 0),
            duration_minutes=60, schedule_type="recurring", specific_date=None,
            max_participants=1, is_active=True,
        )
        self.session.add(self.schedule)
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def _set_log_channel(self, channel_id):
        from models import SystemSettings
        self.session.add(SystemSettings(
            setting_key="waitlist_log_channel_1",
            setting_value=str(channel_id)))
        self.session.commit()

    def _set_panel_channel(self, channel_id):
        from models import PersistentMessageDisplay
        self.session.add(PersistentMessageDisplay(
            guild_id=1, channel_id=channel_id, display_type='waitlist_panel'))
        self.session.commit()

    def _add_trusted_role(self, role_id, is_active=True):
        from models import TrustedRole
        self.session.add(TrustedRole(
            guild_id=1, role_id=role_id, role_name=f"Role {role_id}",
            granted_by=999, is_active=is_active))
        self.session.commit()

    def _make_guild(self, channels=None, member_id=None):
        from unittest.mock import AsyncMock, MagicMock
        guild = MagicMock()
        guild.id = 1
        channel_map = {}
        for cid in (channels or []):
            chan = MagicMock()
            chan.send = AsyncMock()
            channel_map[cid] = chan
        guild.get_channel = MagicMock(side_effect=lambda cid: channel_map.get(cid))
        guild.get_member = MagicMock(return_value=None)
        return guild, channel_map

    async def _run(self, guild, member_id=4242):
        from unittest import mock
        from cogs import notification_commands as nc
        with mock.patch.object(nc, "DatabaseSession",
                               lambda: _SessionCtx(self.session)), \
             mock.patch.object(nc, "AsyncDatabaseSession",
                                lambda: _SessionCtx(self.session)):
            await nc._post_waitlist_promotion_notice(
                None, guild, self.schedule, member_id)

    async def test_uses_configured_log_channel(self):
        self._set_log_channel(555)
        self._set_panel_channel(777)  # present but must be ignored
        guild, channels = self._make_guild(channels=[555, 777])
        await self._run(guild)
        channels[555].send.assert_awaited_once()
        channels[777].send.assert_not_called()

    async def test_falls_back_to_panel_channel(self):
        # No !setwaitlistlog configured -> use the waitlist panel's channel.
        self._set_panel_channel(777)
        guild, channels = self._make_guild(channels=[777])
        await self._run(guild)
        channels[777].send.assert_awaited_once()

    async def test_pings_active_trusted_roles(self):
        self._set_log_channel(555)
        self._add_trusted_role(111)
        self._add_trusted_role(222)
        self._add_trusted_role(333, is_active=False)  # inactive -> not pinged
        guild, channels = self._make_guild(channels=[555])
        await self._run(guild)
        _, kwargs = channels[555].send.call_args
        content = kwargs.get("content") or ""
        self.assertIn("<@&111>", content)
        self.assertIn("<@&222>", content)
        self.assertNotIn("<@&333>", content)

    async def test_no_op_when_no_channel_configured(self):
        # Neither a log channel nor a panel channel exists -> graceful no-op.
        guild, channels = self._make_guild(channels=[555, 777])
        await self._run(guild)
        guild.get_channel.assert_not_called()
        for chan in channels.values():
            chan.send.assert_not_called()

    async def test_no_op_when_channel_missing_from_guild(self):
        # Configured channel id no longer resolves (deleted channel) -> no crash.
        self._set_log_channel(555)
        guild, channels = self._make_guild(channels=[])  # get_channel returns None
        await self._run(guild)  # must not raise
        guild.get_channel.assert_called_once_with(555)


if __name__ == "__main__":
    unittest.main()
