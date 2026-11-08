from __future__ import annotations

import asyncio
import contextlib
import io
import json
import unittest
import uuid

from ipc_protocol import REQUEST_ID_MARKER
from worker.core.router import dispatch
from worker.protection.commands import handle_protection_enable
from worker.quest.commands import handle_quest_config
from worker.relationships.commands import handle_rel_nickname
from worker.tracker.commands import handle_tracker_start
from worker.voice.commands import handle_voice_join


class _Bot:
    _current_request_id = None


class CommandRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_command_returns_correlated_error(self):
        request_id = uuid.uuid4().hex
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            await dispatch(
                _Bot(),
                {},
                {},
                f"UNKNOWN{REQUEST_ID_MARKER}{request_id}\n",
            )

        self.assertEqual(
            output.getvalue().strip(),
            f"IPC_ERROR:UNKNOWN_COMMAND{REQUEST_ID_MARKER}{request_id}",
        )

    async def test_quest_config_rejects_non_object_with_structured_response(self):
        bot = _Bot()
        bot.quest_manager = None
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            await handle_quest_config(bot, "QUEST_CONFIG:[]")

        prefix = "QUEST:CONFIG_RESULT:"
        self.assertTrue(output.getvalue().startswith(prefix))
        result = json.loads(output.getvalue()[len(prefix) :])
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "INVALID_PAYLOAD")

    async def test_relationship_json_command_preserves_nickname_safely(self):
        received = {}

        class RelationshipManager:
            async def set_friend_nickname(self, target_id, nickname):
                received.update(target_id=target_id, nickname=nickname)
                return {"ok": True, "code": "UPDATED", "message": "ok"}

        bot = _Bot()
        bot.rel_manager = RelationshipManager()
        command = "REL_NICKNAME_JSON:" + json.dumps(
            {"target_id": 123, "nickname": "nome:com\nquebra"},
            separators=(",", ":"),
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            await handle_rel_nickname(bot, command)

        prefix = "REL_NICKNAME_RESULT:"
        self.assertEqual(received, {"target_id": 123, "nickname": "nome:com\nquebra"})
        self.assertTrue(output.getvalue().startswith(prefix))
        self.assertTrue(json.loads(output.getvalue()[len(prefix) :])["ok"])

    async def test_voice_json_command_is_validated_and_returns_structured_result(self):
        received = {}

        class VoiceManager:
            async def join_voice(self, channel_id):
                received["channel_id"] = channel_id
                return {"ok": True, "code": "JOINED", "message": "ok"}

        bot = _Bot()
        bot.voice_manager = VoiceManager()
        command = "VOICE_JOIN_JSON:" + json.dumps({"channel_id": 123456789012345678}, separators=(",", ":"))

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            await handle_voice_join(bot, command)
            await asyncio.sleep(0)

        prefix = "VOICE_RESULT:"
        self.assertEqual(received, {"channel_id": 123456789012345678})
        self.assertTrue(output.getvalue().startswith(prefix))
        self.assertEqual(json.loads(output.getvalue()[len(prefix) :])["code"], "JOINED")

    async def test_protection_enable_uses_validated_json_whitelist(self):
        received = {}

        class ProtectionManager:
            def enable(self, whitelist):
                received["whitelist"] = whitelist
                return {"ok": True, "code": "ENABLED", "message": "ok"}

        class PresenceManager:
            async def set_protection(self, enabled):
                received["presence"] = enabled

        bot = _Bot()
        bot.protection_manager = ProtectionManager()
        bot.presence_manager = PresenceManager()
        command = "PROTECTION_ENABLE_JSON:" + json.dumps({"whitelist": [30, 20, 30]}, separators=(",", ":"))

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            await handle_protection_enable(bot, command)

        prefix = "PROTECTION_RESULT:"
        self.assertEqual(received, {"whitelist": [20, 30], "presence": True})
        self.assertTrue(output.getvalue().startswith(prefix))
        self.assertEqual(json.loads(output.getvalue()[len(prefix) :])["code"], "ENABLED")

    async def test_tracker_start_uses_json_and_returns_worker_confirmation(self):
        received = {}

        class TrackerManager:
            async def start_tracking(self, target_id, webhook_url):
                received.update(target_id=target_id, webhook_url=webhook_url)
                return {
                    "ok": True,
                    "code": "STARTED",
                    "enabled": True,
                    "target_id": target_id,
                }

        bot = _Bot()
        bot.tracker_manager = TrackerManager()
        webhook = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40
        command = "TRACKER_START_JSON:" + json.dumps({"target_id": 42, "webhook_url": webhook}, separators=(",", ":"))

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            await handle_tracker_start(bot, command)

        prefix = "TRACKER_RESULT:"
        self.assertEqual(received, {"target_id": 42, "webhook_url": webhook})
        self.assertTrue(output.getvalue().startswith(prefix))
        self.assertTrue(json.loads(output.getvalue()[len(prefix) :])["enabled"])
