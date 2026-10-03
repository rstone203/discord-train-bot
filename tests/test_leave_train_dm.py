"""
Tests for the admin-added DM 'Leave this train' feature
(cogs/train_participant_commands.py: _remove_train_participant, LeaveTrainButton).

Run with: python -m unittest tests.test_leave_train_dm
"""
import os
import unittest
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, ARRAY
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles


@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(element, compiler, **kw):  # pragma: no cover - trivial
    return "TEXT"


from models import Base, TrainSchedule, TrainParticipant, TrainNotification
from cogs.train_participant_commands import _remove_train_participant, LeaveTrainButton


class RemoveTrainParticipantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[TrainSchedule.__table__, TrainParticipant.__table__,
                    TrainNotification.__table__],
        )
        self.Session = sessionmaker(bind=self.engine)

        self.schedule = self._seed()

    def _seed(self):
        session = self.Session()
        schedule = TrainSchedule(
            guild_id=1,
            name="Test Train",
            day_of_week=2,
            start_time=time(20, 0),
            duration_minutes=60,
            schedule_type="recurring",
            specific_date=None,
            max_participants=5,
            is_active=True,
        )
        session.add(schedule)
        session.commit()

        participant = TrainParticipant(
            schedule_id=schedule.id,
            guild_id=1,
            user_id=42,
            username="tester",
            display_name="Tester",
            signed_up_at=datetime.utcnow(),
            is_active=True,
        )
        session.add(participant)
        session.commit()
        schedule_id = schedule.id
        session.close()
        return schedule_id

    def tearDown(self):
        Base.metadata.drop_all(self.engine)

    def _patched_run_db(self):
        Session = self.Session

        async def _fake_run_db(func, *args, **kwargs):
            session = Session()
            try:
                result = func(session, *args, **kwargs)
                session.commit()
                return result
            finally:
                session.close()
        return _fake_run_db

    async def test_remove_active_participant_succeeds(self):
        with patch("database.run_db", new=self._patched_run_db()):
            result = await _remove_train_participant(guild_id=1, schedule_id=self.schedule, user_id=42)

        self.assertTrue(result["ok"])
        self.assertEqual(result["schedule_name"], "Test Train")

        session = self.Session()
        participant = session.query(TrainParticipant).filter_by(
            schedule_id=self.schedule, user_id=42).first()
        self.assertFalse(participant.is_active)
        session.close()

    async def test_remove_not_signed_up_user_reports_reason(self):
        with patch("database.run_db", new=self._patched_run_db()):
            result = await _remove_train_participant(guild_id=1, schedule_id=self.schedule, user_id=999)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_signed_up")
        self.assertEqual(result["schedule_name"], "Test Train")

    async def test_remove_missing_schedule_reports_reason(self):
        with patch("database.run_db", new=self._patched_run_db()):
            result = await _remove_train_participant(guild_id=1, schedule_id=99999, user_id=42)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_schedule")
        self.assertIsNone(result["schedule_name"])


class LeaveTrainButtonTests(unittest.IsolatedAsyncioTestCase):
    def test_custom_id_template_matches_and_extracts_ids(self):
        button = LeaveTrainButton(schedule_id=7, user_id=12345)
        self.assertEqual(button.item.custom_id, "leave_train:7:12345")

    async def test_wrong_user_is_rejected_without_touching_db(self):
        button = LeaveTrainButton(schedule_id=7, user_id=12345)
        interaction = MagicMock()
        interaction.user.id = 999  # not the intended recipient
        interaction.response.send_message = AsyncMock()
        interaction.response.defer = AsyncMock()

        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        interaction.response.defer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
