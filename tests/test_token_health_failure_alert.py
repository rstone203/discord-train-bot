"""
Tests for the Twitch token health-check failure fallback.

These cover the path that warns the bot owner when the token health check
*itself* can't run (e.g. the database is unreachable) or when tokens are fine.
This failure path is otherwise untested, so a future refactor could quietly
re-introduce the silent-failure behaviour it was added to prevent.

Run with: python -m unittest tests.test_token_health_failure_alert
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# database.py raises at import-time without DATABASE_URL; provide a dummy so the
# module under test can be imported. No real connection is ever opened here.
os.environ.setdefault("DATABASE_URL", "sqlite://")

from utils.twitch_chat_monitor import TwitchChatMonitor


def _make_monitor():
    """Build a TwitchChatMonitor without running __init__ (which starts loops)."""
    monitor = TwitchChatMonitor.__new__(TwitchChatMonitor)
    owner = MagicMock()
    owner.name = "owner#1"
    owner.send = AsyncMock()

    app_info = MagicMock()
    app_info.owner = owner

    bot = MagicMock()
    bot.application_info = AsyncMock(return_value=app_info)

    monitor.bot = bot
    return monitor, owner


class _FakeDBSession:
    """Context manager standing in for DatabaseSession()/AsyncDatabaseSession()
    with canned tokens. Supports both sync (`with`) and async (`async with`)
    usage."""

    def __init__(self, tokens):
        self._tokens = tokens

    def _make_session(self):
        sess = MagicMock()
        sess.query.return_value.filter_by.return_value.all.return_value = self._tokens
        return sess

    def __enter__(self):
        return self._make_session()

    def __exit__(self, *exc):
        return False

    async def __aenter__(self):
        return self._make_session()

    async def __aexit__(self, *exc):
        return False


class TokenHealthFailureAlertTests(unittest.IsolatedAsyncioTestCase):

    async def test_db_inspection_failure_warns_owner(self):
        """If the DB inspection raises, the owner is DM'd via the fallback."""
        monitor, owner = _make_monitor()

        # Spy on the fallback method while keeping its real behaviour.
        real_notify = monitor._notify_owner_health_check_failed
        spy = AsyncMock(side_effect=real_notify)
        monitor._notify_owner_health_check_failed = spy

        # DatabaseSession() raises before any token can be inspected.
        failing_db = MagicMock(side_effect=RuntimeError("DB unreachable"))

        with patch("utils.twitch_chat_monitor.DatabaseSession", failing_db), \
             patch("utils.twitch_chat_monitor.AsyncDatabaseSession", failing_db):
            with self.assertLogs("discord_bot.twitch_chat_monitor", level="ERROR") as logs:
                await monitor.check_token_health(reason="unit-test")

        spy.assert_awaited_once()
        owner.send.assert_awaited_once()
        # The loud log fires regardless of DM success.
        self.assertTrue(
            any("could NOT run its inspection" in m for m in logs.output),
            f"expected loud inspection-failure log, got: {logs.output}",
        )

    async def test_failure_cooldown_suppresses_second_dm_but_still_logs(self):
        """A second failure within 6h suppresses the DM but still logs loudly."""
        monitor, owner = _make_monitor()
        err = RuntimeError("DB unreachable")

        # First failure: DM goes out, cooldown timestamp recorded.
        await monitor._notify_owner_health_check_failed("first", err)
        self.assertEqual(owner.send.await_count, 1)
        self.assertIsNotNone(getattr(monitor, "_token_health_fail_last_notified", None))

        # Second failure inside the window: no new DM, but a loud log appears.
        with self.assertLogs("discord_bot.twitch_chat_monitor", level="WARNING") as logs:
            await monitor._notify_owner_health_check_failed("second", err)

        self.assertEqual(owner.send.await_count, 1, "DM should be suppressed within cooldown")
        self.assertTrue(
            any("within 6h cooldown" in m for m in logs.output),
            f"expected cooldown suppression log, got: {logs.output}",
        )

    async def test_cooldown_expired_sends_again(self):
        """Once the 6h window passes, a fresh failure DMs the owner again."""
        monitor, owner = _make_monitor()
        err = RuntimeError("DB unreachable")

        await monitor._notify_owner_health_check_failed("first", err)
        self.assertEqual(owner.send.await_count, 1)

        # Pretend the last failure DM was sent more than 6 hours ago.
        monitor._token_health_fail_last_notified = datetime.utcnow() - timedelta(hours=6, minutes=1)

        await monitor._notify_owner_health_check_failed("later", err)
        self.assertEqual(owner.send.await_count, 2)

    async def test_fallback_owner_lookup_raises_logs_loudly_no_dm(self):
        """If application_info() itself raises, the fallback logs loudly and no DM is sent."""
        monitor, owner = _make_monitor()
        # The owner lookup blows up before any DM can be attempted.
        monitor.bot.application_info = AsyncMock(side_effect=RuntimeError("Discord API down"))

        with self.assertLogs("discord_bot.twitch_chat_monitor", level="ERROR") as logs:
            await monitor._notify_owner_health_check_failed("unit-test", RuntimeError("DB unreachable"))

        owner.send.assert_not_awaited()
        self.assertTrue(
            any("could not be resolved" in m for m in logs.output),
            f"expected loud owner-unresolvable log, got: {logs.output}",
        )

    async def test_fallback_owner_none_logs_loudly_no_dm(self):
        """If application_info().owner is None, the fallback logs that no one will be warned."""
        monitor, owner = _make_monitor()
        # Owner lookup succeeds but resolves to nobody.
        app_info = MagicMock()
        app_info.owner = None
        monitor.bot.application_info = AsyncMock(return_value=app_info)

        with self.assertLogs("discord_bot.twitch_chat_monitor", level="ERROR") as logs:
            await monitor._notify_owner_health_check_failed("unit-test", RuntimeError("DB unreachable"))

        owner.send.assert_not_awaited()
        self.assertTrue(
            any("no one will be warned" in m for m in logs.output),
            f"expected owner-unknown log, got: {logs.output}",
        )

    async def test_check_token_health_owner_none_warns_loudly_no_dm(self):
        """Token problems found but owner resolves to None => loud log, no DM attempted."""
        monitor, owner = _make_monitor()
        app_info = MagicMock()
        app_info.owner = None
        monitor.bot.application_info = AsyncMock(return_value=app_info)

        spy = AsyncMock(side_effect=monitor._notify_owner_health_check_failed)
        monitor._notify_owner_health_check_failed = spy

        broken = MagicMock()
        broken.twitch_username = "bad_streamer"
        broken.twitch_user_id = "456"
        broken.scopes = []  # missing chat:read => counted as a problem
        broken.expires_at = datetime.now() + timedelta(days=1)
        broken.refresh_token = "refresh-xyz"

        with patch(
            "utils.twitch_chat_monitor.DatabaseSession",
            lambda: _FakeDBSession([broken]),
        ), patch(
            "utils.twitch_chat_monitor.AsyncDatabaseSession",
            lambda: _FakeDBSession([broken]),
        ):
            with self.assertLogs("discord_bot.twitch_chat_monitor", level="ERROR") as logs:
                await monitor.check_token_health(reason="unit-test")

        owner.send.assert_not_awaited()
        spy.assert_not_awaited()  # this is the in-line owner-None branch, not the fallback
        self.assertTrue(
            any("NO ONE will be warned" in m for m in logs.output),
            f"expected loud owner-unresolvable log, got: {logs.output}",
        )

    async def test_healthy_tokens_send_no_failure_dm(self):
        """Healthy tokens + resolvable owner => no failure fallback, no DM."""
        monitor, owner = _make_monitor()

        spy = AsyncMock(side_effect=monitor._notify_owner_health_check_failed)
        monitor._notify_owner_health_check_failed = spy

        healthy = MagicMock()
        healthy.twitch_username = "good_streamer"
        healthy.twitch_user_id = "123"
        healthy.scopes = ["chat:read"]
        healthy.expires_at = datetime.now() + timedelta(days=1)
        healthy.refresh_token = "refresh-abc"

        with patch(
            "utils.twitch_chat_monitor.DatabaseSession",
            lambda: _FakeDBSession([healthy]),
        ), patch(
            "utils.twitch_chat_monitor.AsyncDatabaseSession",
            lambda: _FakeDBSession([healthy]),
        ):
            await monitor.check_token_health(reason="unit-test")

        spy.assert_not_awaited()
        owner.send.assert_not_awaited()


class PerTokenWarningTests(unittest.IsolatedAsyncioTestCase):
    """Cover the common path: the check runs fine but finds specific bad tokens.

    These guard the per-token warning DM the owner relies on to learn that a
    streamer's attendance tracking / auto-raids will silently fail. A refactor
    that broke the DM, the token listing, or the cooldown would be caught here.
    """

    @staticmethod
    def _sent_embed(owner):
        """Pull the discord.Embed passed to the (single) owner.send call."""
        owner.send.assert_awaited_once()
        _, kwargs = owner.send.await_args
        return kwargs["embed"]

    async def test_missing_chat_read_warns_owner_and_lists_token(self):
        """A token without chat:read => owner gets the re-linking DM listing it."""
        monitor, owner = _make_monitor()

        spy = AsyncMock(side_effect=monitor._notify_owner_health_check_failed)
        monitor._notify_owner_health_check_failed = spy

        broken = MagicMock()
        broken.twitch_username = "bad_streamer"
        broken.twitch_user_id = "456"
        broken.scopes = []  # missing chat:read => counted as a problem
        broken.expires_at = datetime.now() + timedelta(days=1)
        broken.refresh_token = "refresh-xyz"

        with patch(
            "utils.twitch_chat_monitor.DatabaseSession",
            lambda: _FakeDBSession([broken]),
        ), patch(
            "utils.twitch_chat_monitor.AsyncDatabaseSession",
            lambda: _FakeDBSession([broken]),
        ):
            await monitor.check_token_health(reason="unit-test")

        # The per-token warning path was used, not the failure fallback.
        spy.assert_not_awaited()
        embed = self._sent_embed(owner)
        self.assertEqual(embed.title, "⚠️ Twitch Token Needs Re-Linking")
        self.assertIn("bad_streamer", embed.description)
        self.assertIn("chat:read", embed.description)
        # The cooldown timestamp is recorded after a successful DM.
        self.assertIsNotNone(getattr(monitor, "_token_health_last_notified", None))

    async def test_expired_without_refresh_token_is_included(self):
        """An expired token with no refresh token => included in the warning DM."""
        monitor, owner = _make_monitor()

        spy = AsyncMock(side_effect=monitor._notify_owner_health_check_failed)
        monitor._notify_owner_health_check_failed = spy

        expired = MagicMock()
        expired.twitch_username = "stale_streamer"
        expired.twitch_user_id = "789"
        expired.scopes = ["chat:read"]  # scope is fine
        expired.expires_at = datetime.now() - timedelta(hours=1)  # already expired
        expired.refresh_token = None  # can't auto-refresh

        with patch(
            "utils.twitch_chat_monitor.DatabaseSession",
            lambda: _FakeDBSession([expired]),
        ), patch(
            "utils.twitch_chat_monitor.AsyncDatabaseSession",
            lambda: _FakeDBSession([expired]),
        ):
            await monitor.check_token_health(reason="unit-test")

        spy.assert_not_awaited()
        embed = self._sent_embed(owner)
        self.assertIn("stale_streamer", embed.description)
        self.assertIn("expired", embed.description)

    async def test_cooldown_suppresses_repeat_warning_dm(self):
        """A second check within the 6h cooldown sends no repeat warning DM."""
        monitor, owner = _make_monitor()

        broken = MagicMock()
        broken.twitch_username = "bad_streamer"
        broken.twitch_user_id = "456"
        broken.scopes = []  # missing chat:read => counted as a problem
        broken.expires_at = datetime.now() + timedelta(days=1)
        broken.refresh_token = "refresh-xyz"

        with patch(
            "utils.twitch_chat_monitor.DatabaseSession",
            lambda: _FakeDBSession([broken]),
        ), patch(
            "utils.twitch_chat_monitor.AsyncDatabaseSession",
            lambda: _FakeDBSession([broken]),
        ):
            # First run: warning DM goes out, cooldown timestamp recorded.
            await monitor.check_token_health(reason="first")
            self.assertEqual(owner.send.await_count, 1)
            self.assertIsNotNone(getattr(monitor, "_token_health_last_notified", None))

            # Second run inside the 6h window: DM suppressed.
            await monitor.check_token_health(reason="second")
            self.assertEqual(
                owner.send.await_count, 1, "repeat warning DM should be suppressed within cooldown"
            )


if __name__ == "__main__":
    unittest.main()
