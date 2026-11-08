from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from worker.tracker.manager import TrackerManager
from worker.tracker.support import TrackerState
import discord


def custom(text=None, emoji_id=123, *, emoji_name="hana", animated=False):
    emoji = discord.PartialEmoji(name=emoji_name, id=emoji_id, animated=animated) if emoji_id else None
    return discord.CustomActivity(name=text, emoji=emoji)


def presence(*activities, status="online", desktop=None, guild_id=20):
    return NS(
        id=42,
        name="target",
        guild=NS(id=guild_id),
        status=status,
        activities=activities,
        desktop_status=desktop or status,
        mobile_status="offline",
        web_status="offline",
        embedded_status="offline",
    )


class TrackerPresenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tracker = TrackerManager(NS())
        self.tracker.enabled = True
        self.tracker.target_id = 42
        self.tracker._dispatch_log = AsyncMock()
        self.tracker.config.PRESENCE_COALESCE_DELAY = 0
        self.tracker.config.OFFLINE_DEBOUNCE = 0
        self.tracker.config.CUSTOM_STATUS_STABILITY_SECONDS = 0.02
        self.tracker.config.CUSTOM_STATUS_REMOVAL_SECONDS = 0.06
        self.addAsyncCleanup(self.tracker.shutdown)

    async def feed(self, subject):
        await self.tracker.on_presence_update(subject, subject)
        task = self.tracker._pending_presence_task or self.tracker._pending_offline_task
        if task:
            await task

    async def confirm(self):
        task = self.tracker._pending_custom_status_task
        if task:
            await task

    def logs(self):
        return [call.kwargs for call in self.tracker._dispatch_log.await_args_list]

    def custom_logs(self):
        return [log for log in self.logs() if any("Status personalizado" in name for name, _ in log.get("changes", []))]

    async def seed(self, activity=None):
        await self.tracker._dispatch_initial_state(presence(activity or custom()))
        self.tracker._dispatch_log.reset_mock()

    async def test_empty_payload_does_not_invent_balloon_or_a_custom_status(self):
        empty = custom(emoji_id=None)
        self.assertEqual(self.tracker._format_activity(empty), "")
        await self.feed(presence(empty))
        await self.confirm()
        self.assertEqual(self.custom_logs(), [])
        await self.feed(presence(custom()))
        await self.confirm()
        self.assertEqual(len(self.custom_logs()), 1)
        self.assertIn("hana", str(self.custom_logs()))
        self.assertNotIn("💭", str(self.logs()))

    async def test_blank_then_same_emoji_and_device_change_does_not_repeat_status(self):
        await self.seed()
        await self.feed(presence(custom(emoji_id=None)))
        await self.confirm()
        await self.feed(presence(custom(), status="idle", desktop="idle"))
        await self.confirm()
        self.assertEqual(self.custom_logs(), [])
        self.assertIn("Dispositivos", str(self.logs()))
        self.assertNotIn("hana", str([log.get("changes") for log in self.logs()]))

    async def test_same_status_from_different_guilds_and_emoji_rendering_is_silent(self):
        await self.seed()
        await self.feed(presence(custom(emoji_name="renamed", animated=True), guild_id=30))
        await self.confirm()
        await self.feed(presence(custom(), guild_id=20))
        await self.confirm()
        self.assertEqual(self.logs(), [])

    async def test_offline_and_online_keep_last_custom_status_without_repeating_it(self):
        await self.seed(custom("texto"))
        await self.feed(presence(status="offline"))
        self.assertEqual(self.tracker.state.custom_status.text, "texto")
        await self.feed(presence(custom("texto")))
        await self.confirm()
        self.assertEqual(self.custom_logs(), [])
        self.assertTrue(any("Offline" in str(log) for log in self.logs()))

    async def test_real_text_and_emoji_changes_are_reported_once_and_can_change_back(self):
        await self.seed(custom("antes"))
        for activity in (custom("depois"), custom("depois", 456, emoji_name="novo"), custom("antes")):
            await self.feed(presence(activity))
            await self.confirm()
            await self.feed(presence(activity))
            await self.confirm()
        self.assertEqual(len(self.custom_logs()), 3)
        self.assertIn("**Antes:**", str(self.custom_logs()))
        self.assertEqual(self.tracker.state.custom_status.text, "antes")

    async def test_temporary_emoji_loss_is_cancelled_but_sustained_removal_is_reported(self):
        await self.seed(custom("texto"))
        await self.feed(presence(custom("texto", emoji_id=None)))
        pending = self.tracker._pending_custom_status_task
        await self.feed(presence(custom("texto")))
        await pending
        self.assertEqual(self.custom_logs(), [])
        await self.feed(presence(custom("texto", emoji_id=None)))
        await self.confirm()
        self.assertEqual(len(self.custom_logs()), 1)
        self.assertEqual(self.tracker.state.custom_status.emoji_key, "")

    async def test_sustained_online_status_removal_and_later_readdition_are_reported(self):
        await self.seed()
        await self.feed(presence())
        await self.confirm()
        self.assertEqual(len(self.custom_logs()), 1)
        self.assertIn("Removido", str(self.custom_logs()))
        await self.feed(presence(custom()))
        await self.confirm()
        self.assertEqual(len(self.custom_logs()), 2)

    async def test_rapid_change_that_returns_to_last_status_is_suppressed(self):
        await self.seed()
        await self.feed(presence(custom(emoji_id=456)))
        pending = self.tracker._pending_custom_status_task
        await self.feed(presence(custom()))
        await pending
        self.assertEqual(self.custom_logs(), [])

    async def test_repeated_same_candidate_does_not_restart_confirmation_timer(self):
        await self.seed()
        await self.feed(presence(custom("novo")))
        pending = self.tracker._pending_custom_status_task
        await self.feed(presence(custom("novo"), status="idle"))
        self.assertIs(self.tracker._pending_custom_status_task, pending)
        await pending
        self.assertEqual(len(self.custom_logs()), 1)

    async def test_reconnect_does_not_repeat_initial_state_or_replace_confirmed_status(self):
        await self.seed()
        original = self.tracker.state.custom_status
        stale = presence(status="offline")

        async def resubscribe(run_id):
            await self.tracker._dispatch_initial_state(stale)

        self.tracker._subscribe_to_target = resubscribe
        for _ in range(3):
            await self.tracker.on_disconnect()
            await self.tracker.on_reconnect()
            await self.tracker._subscribe_task
            await self.feed(presence(custom()))
            await self.confirm()
        self.assertEqual(self.logs(), [])
        self.assertEqual(self.tracker.state.custom_status, original)
        self.assertEqual(self.tracker.state.status, "online")

    async def test_late_initial_snapshot_does_not_overwrite_newer_gateway_presence(self):
        await self.feed(presence(custom()))
        await self.confirm()
        self.tracker._dispatch_log.reset_mock()
        await self.tracker._dispatch_initial_state(presence(status="offline"))
        self.assertEqual(self.tracker.state.status, "online")
        self.assertEqual(len(self.logs()), 1)
        self.assertIn("Online", str(self.logs()))
        self.assertEqual(self.custom_logs(), [])

    async def test_resumed_does_not_erase_a_real_change_received_during_replay(self):
        await self.seed(custom("antes"))
        await self.tracker.on_disconnect()
        await self.feed(presence(custom("depois")))
        pending = self.tracker._pending_custom_status_task
        self.tracker._subscribe_to_target = AsyncMock()
        await self.tracker.on_reconnect()
        await self.tracker._subscribe_task
        self.assertIs(self.tracker._pending_custom_status_task, pending)
        await self.confirm()
        self.assertEqual(len(self.custom_logs()), 1)
        self.assertEqual(self.tracker.state.custom_status.text, "depois")

    async def test_fresh_member_cache_after_reconnect_is_compared_without_initial_notice(self):
        await self.seed(custom("antes"))
        await self.tracker.on_disconnect()
        member = presence(custom("depois"))
        self.tracker.client.guilds = [NS(get_member=lambda _: member)]
        self.tracker._ensure_guild_subscription = AsyncMock(return_value=member)
        await self.tracker._subscribe_to_target(self.tracker._run_id)
        await self.tracker._pending_presence_task
        await self.confirm()
        self.assertEqual(len(self.custom_logs()), 1)
        self.assertFalse(any("Estado Inicial" in log["title"] for log in self.logs()))

    async def test_activity_order_alone_does_not_generate_an_update(self):
        a = discord.Activity(name="A", type=discord.ActivityType.playing)
        b = discord.Activity(name="B", type=discord.ActivityType.playing)
        await self.feed(presence(a, custom(), b))
        await self.confirm()
        self.tracker._dispatch_log.reset_mock()
        await self.feed(presence(b, custom(), a))
        await self.confirm()
        self.assertEqual(self.logs(), [])

    async def test_shutdown_cancels_pending_status_and_a_new_state_allows_initial_again(self):
        await self.seed()
        await self.feed(presence(custom("novo")))
        pending = self.tracker._pending_custom_status_task
        await self.tracker.shutdown()
        self.assertTrue(pending.done())
        self.assertEqual(self.custom_logs(), [])
        self.assertFalse(self.tracker.state.initial_state_sent)
        self.assertEqual(self.tracker.state, TrackerState())
