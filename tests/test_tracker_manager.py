from __future__ import annotations

import asyncio
import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.tracker.manager import TrackerManager
from worker.tracker.support import MessageBuffer


class TrackerManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_webhook_validation_rejects_lookalikes_queries_and_short_tokens(self):
        valid = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40
        self.assertEqual(TrackerManager.normalize_webhook_url(valid), valid)
        self.assertIsNone(
            TrackerManager.normalize_webhook_url(
                "https://discord.com.attacker.invalid/api/webhooks/123456789012345678/" + "a" * 40
            )
        )
        self.assertIsNone(TrackerManager.normalize_webhook_url(valid + "?wait=true"))
        self.assertIsNone(
            TrackerManager.normalize_webhook_url("https://discord.com/api/webhooks/123456789012345678/short")
        )
        self.assertIsNone(
            TrackerManager.normalize_webhook_url(
                "https://discord.com:invalid/api/webhooks/123456789012345678/" + "a" * 40
            )
        )

    async def test_message_buffer_flushes_full_batch_and_cancels_pending_work(self):
        flushed = []

        async def callback(channel_id, messages):
            flushed.append((channel_id, messages))

        buffer = MessageBuffer(callback)
        buffer.config.MESSAGE_BATCH_MAX = 2
        buffer.config.FLUSH_DELAY = 60
        buffer.add(10, {"content": "one"})
        buffer.add(10, {"content": "two"})
        await asyncio.sleep(0)

        self.assertEqual(len(flushed), 1)
        self.assertEqual(len(flushed[0][1]), 2)
        await buffer.clear()

    async def test_start_reuses_one_webhook_object_and_shutdown_cancels_tasks(self):
        class Client:
            guilds = []

            def get_user(self, user_id):
                return None

            def get_guild(self, guild_id):
                return None

            def is_ws_ratelimited(self):
                return False

            def is_ready(self):
                return True

            async def wait_until_ready(self):
                return None

            async def fetch_user_profile(self, user_id):
                return None

        class Webhook:
            def __init__(self):
                self.sent = 0

            async def send(self, **kwargs):
                self.sent += 1

        webhook = Webhook()
        manager = TrackerManager(Client())
        url = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40
        with patch("worker.tracker.manager.discord.Webhook.from_url", return_value=webhook) as factory:
            result = await manager.start_tracking(42, url)
            await asyncio.sleep(0.02)

            self.assertTrue(result["enabled"])
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(webhook.sent, 1)
            await manager.shutdown()

        self.assertFalse(manager.enabled)
        self.assertIsNone(manager._webhook_task)
        self.assertIsNone(manager._webhook_session)

    async def test_reconnect_waits_for_previous_subscription_to_finish(self):
        manager = TrackerManager(object())
        manager.enabled = True
        manager.target_id = 42
        previous_finished = asyncio.Event()
        replacement_started = asyncio.Event()

        async def previous_subscription():
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                previous_finished.set()

        async def replacement_subscription(run_id):
            self.assertTrue(previous_finished.is_set())
            replacement_started.set()

        previous_task = asyncio.create_task(previous_subscription())
        await asyncio.sleep(0)
        manager._subscribe_task = previous_task
        manager._subscribe_to_target = replacement_subscription

        await manager.on_reconnect()
        await manager._subscribe_task

        self.assertTrue(previous_task.cancelled())
        self.assertTrue(replacement_started.is_set())

    def test_panel_views_do_not_override_discords_sync_refresh_hook(self):
        project = Path(__file__).resolve().parents[1]
        for relative in (
            "panel/protection/modal.py",
            "panel/voice/modal.py",
            "panel/tracker/modal.py",
        ):
            tree = ast.parse((project / relative).read_text(encoding="utf-8"))
            async_refreshes = [
                node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_refresh"
            ]
            self.assertEqual(async_refreshes, [], relative)


if __name__ == "__main__":
    unittest.main()
