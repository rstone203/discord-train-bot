"""
Tests for the Task #33 beta features.

Covers the two pieces most likely to silently regress:
  1. Feature-flag defaults — every beta feature must default OFF, while legacy
     features stay ON, regardless of whether a permissions row exists.
  2. The reminder/auto-thread occurrence-datetime helper — it must compute the
     correct upcoming UK-localized start time for recurring and specific-date
     schedules.

Run with: python -m unittest tests.test_beta_features
"""
import os
import unittest
from datetime import datetime, time, date
from unittest.mock import MagicMock, AsyncMock

import pytz

# database.py raises at import-time without DATABASE_URL; provide a dummy so the
# modules under test import cleanly. No real connection is ever opened here.
os.environ.setdefault("DATABASE_URL", "sqlite://")

from utils.server_permissions import ServerPermissionsManager, BETA_FEATURES
from cogs.notification_commands import NotificationCommands
from cogs.train_participant_commands import TrainParticipantCommands


LEGACY_FEATURES = ['trains', 'twitch', 'forwarding', 'admin', 'analytics', 'general']


class FeatureFlagDefaultsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mgr = ServerPermissionsManager()

    async def test_beta_features_off_when_no_row(self):
        """With no permissions row, every beta feature is disabled."""
        self.mgr.get_server_permissions = AsyncMock(return_value=None)
        for feature in BETA_FEATURES:
            self.assertFalse(
                await self.mgr.is_feature_enabled(123, feature),
                f"beta feature '{feature}' should default OFF with no row",
            )

    async def test_legacy_features_on_when_no_row(self):
        """With no permissions row, legacy features remain enabled."""
        self.mgr.get_server_permissions = AsyncMock(return_value=None)
        for feature in LEGACY_FEATURES:
            self.assertTrue(
                await self.mgr.is_feature_enabled(123, feature),
                f"legacy feature '{feature}' should default ON with no row",
            )

    async def test_beta_feature_respects_enabled_row(self):
        """When a row enables a beta column, the flag reads True."""
        perms = MagicMock()
        perms.trains_enabled = True
        perms.twitch_enabled = True
        perms.forwarding_enabled = True
        perms.admin_enabled = True
        perms.analytics_enabled = True
        perms.general_enabled = True
        perms.reminders_enabled = True
        perms.waitlist_enabled = False
        perms.stats_enabled = False
        self.mgr.get_server_permissions = AsyncMock(return_value=perms)

        self.assertTrue(await self.mgr.is_feature_enabled(1, 'reminders'))
        self.assertFalse(await self.mgr.is_feature_enabled(1, 'waitlist'))

    async def test_missing_beta_column_falls_back_to_off(self):
        """If the beta column is absent (pre-migration), it reads OFF, not error."""
        class _Bare:
            trains_enabled = True
            twitch_enabled = True
            forwarding_enabled = True
            admin_enabled = True
            analytics_enabled = True
            general_enabled = True
            # no beta columns at all

        self.mgr.get_server_permissions = AsyncMock(return_value=_Bare())
        for feature in BETA_FEATURES:
            self.assertFalse(await self.mgr.is_feature_enabled(1, feature))
        # legacy still works
        self.assertTrue(await self.mgr.is_feature_enabled(1, 'trains'))


class OccurrenceDatetimeTests(unittest.TestCase):
    def setUp(self):
        self.cog = NotificationCommands.__new__(NotificationCommands)
        self.cog.logger = MagicMock()
        self.cog._get_week_offset = lambda schedule: 0
        self.uk_tz = pytz.timezone('Europe/London')

    def _schedule(self, day_of_week, start_time, specific_date=None):
        s = MagicMock()
        s.name = "Test Train"
        s.day_of_week = day_of_week
        s.start_time = start_time
        s.specific_date = specific_date
        return s

    def test_recurring_same_day_future(self):
        """A recurring slot later today resolves to today's date."""
        now = self.uk_tz.localize(datetime(2026, 6, 24, 10, 0))  # Wednesday
        sched = self._schedule(day_of_week=2, start_time=time(20, 0))  # Wed 20:00
        start_dt, occ_date = self.cog._compute_occurrence_datetime(sched, now, self.uk_tz)
        self.assertEqual(occ_date, date(2026, 6, 24))
        self.assertEqual(start_dt.hour, 20)

    def test_recurring_next_week(self):
        """A recurring slot whose weekday is earlier than today rolls forward."""
        now = self.uk_tz.localize(datetime(2026, 6, 26, 10, 0))  # Friday
        sched = self._schedule(day_of_week=0, start_time=time(18, 0))  # Monday
        start_dt, occ_date = self.cog._compute_occurrence_datetime(sched, now, self.uk_tz)
        self.assertEqual(occ_date, date(2026, 6, 29))  # next Monday
        self.assertEqual(start_dt.weekday(), 0)

    def test_specific_date_used_directly(self):
        """A one-time specific-date schedule uses the stored date."""
        now = self.uk_tz.localize(datetime(2026, 6, 24, 10, 0))
        sched = self._schedule(
            day_of_week=4, start_time=time(19, 30), specific_date=date(2026, 7, 3)
        )
        start_dt, occ_date = self.cog._compute_occurrence_datetime(sched, now, self.uk_tz)
        self.assertEqual(occ_date, date(2026, 7, 3))
        self.assertEqual((start_dt.hour, start_dt.minute), (19, 30))

    def test_specific_date_early_morning_rolls_to_next_day(self):
        """An early-morning (<5am) specific-date slot fires the next calendar day."""
        now = self.uk_tz.localize(datetime(2026, 6, 24, 10, 0))
        sched = self._schedule(
            day_of_week=6, start_time=time(0, 30), specific_date=date(2026, 7, 5)
        )
        start_dt, occ_date = self.cog._compute_occurrence_datetime(sched, now, self.uk_tz)
        self.assertEqual(occ_date, date(2026, 7, 6))


class AttendanceStreakTests(unittest.TestCase):
    def _streak(self, server, attended):
        return TrainParticipantCommands._compute_attendance_streaks(server, set(attended))

    def test_no_sessions(self):
        self.assertEqual(self._streak([], []), (0, 0))

    def test_perfect_attendance(self):
        days = [date(2026, 6, d) for d in (1, 2, 3, 4)]
        self.assertEqual(self._streak(days, days), (4, 4))

    def test_current_streak_resets_on_recent_miss(self):
        days = [date(2026, 6, d) for d in (1, 2, 3, 4)]
        attended = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]  # missed the 4th
        self.assertEqual(self._streak(days, attended), (0, 3))

    def test_longest_vs_current(self):
        days = [date(2026, 6, d) for d in (1, 2, 3, 4, 5)]
        # attended 1,2,3 (run of 3), missed 4, attended 5 (current run of 1)
        attended = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 5)]
        self.assertEqual(self._streak(days, attended), (1, 3))


if __name__ == "__main__":
    unittest.main()
