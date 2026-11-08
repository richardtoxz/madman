from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from engine.backend import SelfbotManager
from ipc_protocol import decode_json_payload, encode_json_payload, parse_cleanup_result
from panel.main import create_web_app, parse_web_port
from worker.messages.cleanup import MessageCleanupMixin
from worker.run_selfbot import MySelfbot
from worker.tracker.collection import TrackerCollection


class _DummyCleanupWorker(MessageCleanupMixin):
    def __init__(self, count: int = 7):
        self._count = count
        self.waited_for_start = False

    async def _reboot_state(self):
        return None

    async def _resolve_channel(self, channel_id: int):
        return SimpleNamespace(id=channel_id, name=f"ch-{channel_id}"), "text", None

    def _channel_display_name(self, channel) -> str:
        return channel.name

    async def _get_quick_count(self, channel, request_id=None) -> int:
        return self._count

    async def _wait_for_start(self):
        self.waited_for_start = True
        return True


class WebReadinessImprovementsTests(unittest.IsolatedAsyncioTestCase):
    def test_centralized_settings_and_protection_listener_deduplication(self):
        manager = SelfbotManager()
        subscribed = []
        unsubscribed = []
        manager.subscribe_events = lambda uid, prefix, cb: subscribed.append((uid, prefix, cb)) or True
        manager.unsubscribe_events = lambda uid, prefix, cb: unsubscribed.append((uid, prefix, cb)) or True

        cb1 = lambda line: None
        cb2 = lambda line: None
        manager.set_protection_listener(42, cb1)
        manager.set_protection_listener(42, cb2)

        self.assertEqual(len(subscribed), 2)
        self.assertEqual(len(unsubscribed), 1)
        self.assertIs(unsubscribed[0][2], cb1)

        settings = manager.set_user_settings(
            42,
            webhook_url="https://discord.com/api/webhooks/1/abc",
            voice_action="disconnect",
        )
        self.assertEqual(settings["webhook_url"], "https://discord.com/api/webhooks/1/abc")
        self.assertEqual(manager.get_webhook_url(42), "https://discord.com/api/webhooks/1/abc")
        self.assertEqual(manager.get_user_settings(42)["voice_action"], "disconnect")

    def test_ipc_json_codec_and_cleanup_result_parser(self):
        payload = {"status": "COMPLETADO", "deleted": 15, "total": 17, "failed": 2}
        encoded = encode_json_payload(payload)
        self.assertEqual(decode_json_payload(encoded), payload)

        parsed_json = parse_cleanup_result(f"RESULT_JSON:{encoded}")
        self.assertEqual(parsed_json["deleted"], 15)
        self.assertEqual(parsed_json["failed"], 2)
        self.assertEqual(parsed_json["outcome"], "complete")

        parsed_legacy = parse_cleanup_result("CANCELADO:Deletadas 4 de 10; falhas 1; restantes 5.")
        self.assertEqual(parsed_legacy["deleted"], 4)
        self.assertEqual(parsed_legacy["failed"], 1)
        self.assertEqual(parsed_legacy["outcome"], "cancelled")

    async def test_preview_clean_does_not_block_waiting_for_start(self):
        worker = _DummyCleanupWorker(count=12)
        emitted = []
        with patch(
            "worker.messages.cleanup.emit_response",
            side_effect=lambda prefix, payload, req_id=None: emitted.append(f"{prefix}{payload}"),
        ):
            await worker.preview_clean(10, guild=False)

        self.assertFalse(worker.waited_for_start)
        self.assertEqual(len(emitted), 1)
        self.assertTrue(emitted[0].startswith("MSG_PREVIEW_RESULT:"))
        decoded = decode_json_payload(emitted[0].split(":", 1)[1])
        self.assertTrue(decoded["ok"])
        self.assertEqual(decoded["estimated_total"], 12)
        self.assertEqual(decoded["target_type"], "text")

    async def test_per_user_bulk_lock_allows_parallel_users(self):
        manager = SelfbotManager()
        user1_entered = asyncio.Event()
        user2_entered = asyncio.Event()
        release = asyncio.Event()

        class _FakeProc:
            def __init__(self, uid: int):
                self.user_id = uid
                self.is_alive = True

            async def send_and_stream(self, command, prefixes, timeout):
                if self.user_id == 1:
                    user1_entered.set()
                    await release.wait()
                else:
                    user2_entered.set()
                yield f"{prefixes[0]}RESULT:COMPLETADO:Deletadas 1 de 1."

        manager.active_processes[1] = _FakeProc(1)
        manager.active_processes[2] = _FakeProc(2)

        task1 = asyncio.create_task(manager._run_bulk_stream(1, "CMD1", ["TEST_"]))
        await asyncio.wait_for(user1_entered.wait(), timeout=1.0)

        task2 = asyncio.create_task(manager._run_bulk_stream(2, "CMD2", ["TEST_"]))
        await asyncio.wait_for(user2_entered.wait(), timeout=1.0)

        self.assertTrue(user2_entered.is_set())
        release.set()
        results = await asyncio.gather(task1, task2)
        self.assertEqual(results, ["COMPLETADO:Deletadas 1 de 1.", "COMPLETADO:Deletadas 1 de 1."])

    async def test_tracker_collection_shares_cache_and_preserves_multi_target_capture_and_delete(self):
        room = SimpleNamespace(
            id=10,
            name="geral",
            guild=SimpleNamespace(id=20, name="Guild", me=object()),
            permissions_for=lambda _: SimpleNamespace(view_channel=True, read_message_history=True),
        )
        client = SimpleNamespace(get_channel=lambda _: room, guilds=[])
        collection = TrackerCollection(client)
        self.addAsyncCleanup(collection.shutdown)

        t1 = collection._build_tracker()
        t1.enabled = True
        t1.target_id = 42
        t1._webhook_queue = asyncio.Queue(maxsize=50)

        t2 = collection._build_tracker()
        t2.enabled = True
        t2.target_id = 77
        t2._webhook_queue = asyncio.Queue(maxsize=50)

        collection.trackers = {42: t1, 77: t2}
        self.assertIs(t1._msg_cache, t2._msg_cache)

        import discord

        msg_from_77 = SimpleNamespace(
            id=500,
            author=SimpleNamespace(
                id=77, name="user-77", display_avatar=SimpleNamespace(url="https://example.com/a.png")
            ),
            channel=room,
            guild=room.guild,
            content="segredo do 77",
            attachments=[],
            embeds=[],
            mentions=[],
            reference=None,
            created_at=discord.utils.utcnow(),
            jump_url="https://discord.com/channels/20/10/500",
        )

        # Even though t1 (target 42) processes on_message first and caches msg 500 as non-target,
        # t2 (target 77) must still capture it in full!
        await collection.on_message(msg_from_77)
        await t2.msg_buffer.flush(10)
        self.assertEqual(t2._stats["messages_seen"], 1)
        self.assertTrue(collection._shared_msg_cache[500].get("_captured"))

        # When msg 500 is deleted, t1 runs first and must not pop msg 500 before t2 logs the deletion
        await collection.on_raw_message_delete(SimpleNamespace(message_id=500, channel_id=10, cached_message=None))
        self.assertNotIn(500, collection._shared_msg_cache)
        events_t2 = []
        while not t2._webhook_queue.empty():
            events_t2.append(t2._webhook_queue.get_nowait())
        self.assertTrue(any(item[2] == "message_delete" for item in events_t2))

    async def test_relationship_update_suppresses_presence_only_noise(self):
        bot = MySelfbot.__new__(MySelfbot)
        before = SimpleNamespace(type="friend", nick="amigo", user=SimpleNamespace(id=99))
        after_same = SimpleNamespace(type="friend", nick="amigo", user=SimpleNamespace(id=99))
        after_changed = SimpleNamespace(type="friend", nick="novo", user=SimpleNamespace(id=99))
        emitted = []
        with patch("worker.run_selfbot.emit_line", side_effect=emitted.append):
            await bot.on_relationship_update(before, after_same)
            await bot.on_relationship_update(before, after_changed)
        self.assertEqual(emitted, ["EVENT:REL_UPDATE:99:nick:changed"])

    def test_web_app_routes_and_port_validation(self):
        self.assertEqual(parse_web_port("8080"), 8080)
        self.assertIsNone(parse_web_port("invalid"))
        self.assertIsNone(parse_web_port("70000"))

        manager = SelfbotManager()
        app = create_web_app(manager=manager, bot=None)
        routes = {resource.canonical for resource in app.router.resources()}
        self.assertIn("/", routes)
        self.assertIn("/api/health", routes)


if __name__ == "__main__":
    unittest.main()
