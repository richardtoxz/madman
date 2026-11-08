from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
from engine.backend import SelfbotManager  # noqa: F401 - prepara o path do painel
from panel.web.routes.common import _stringify_snowflakes
from worker import run_selfbot


def _bot():
    bot = run_selfbot.MySelfbot.__new__(run_selfbot.MySelfbot)
    bot._dm_upsert_last = {}
    bot._dm_upsert_pending = {}
    bot.msg_manager = SimpleNamespace(_serialize_private_channel=lambda ch: {"id": str(ch.id), "n": ch.last_message_id})
    return bot


class SnowflakeStringifyTests(unittest.TestCase):
    def test_nested_ids_and_lists_become_strings(self):
        data = {
            "ok": True,
            "items": [{"id": 1234567890123456789, "user_id": 1234567890123456790, "members": 3}],
            "guild_ids": [1234567890123456791],
            "desired_channel_id": 1234567890123456792,
            "target_id": None,
        }
        out = _stringify_snowflakes(data)
        self.assertEqual(out["items"][0]["id"], "1234567890123456789")
        self.assertEqual(out["items"][0]["user_id"], "1234567890123456790")
        self.assertEqual(out["items"][0]["members"], 3)
        self.assertEqual(out["guild_ids"], ["1234567890123456791"])
        self.assertEqual(out["desired_channel_id"], "1234567890123456792")
        self.assertIsNone(out["target_id"])
        # o JSON ida e volta preserva o valor exato
        self.assertEqual(json.loads(json.dumps(out))["items"][0]["id"], "1234567890123456789")


class MessageRequestFlagTests(unittest.TestCase):
    def _serialize(self, **flags):
        from worker.core.config import ControlState
        from worker.messages.manager import MessageManager

        recipient = SimpleNamespace(id=1, name="x", global_name="X", display_avatar=None)
        channel = SimpleNamespace(
            id=2,
            recipient=recipient,
            last_message_id=2**40,
            read_state=None,
            is_message_request=lambda: flags["req"],
            is_spam=lambda: False,
            is_accepted=lambda: flags["accepted"],
        )
        mgr = MessageManager(SimpleNamespace(user=SimpleNamespace(id=9), private_channels=[]), ControlState())
        with patch("worker.messages.operations.discord.DMChannel", SimpleNamespace):
            return mgr._serialize_private_channel(channel)

    def test_accepted_request_is_not_pending(self):
        self.assertFalse(self._serialize(req=True, accepted=True)["is_request"])

    def test_pending_request_is_flagged(self):
        self.assertTrue(self._serialize(req=True, accepted=False)["is_request"])

    def test_regular_dm_is_not_request(self):
        self.assertFalse(self._serialize(req=False, accepted=True)["is_request"])


class DmUpsertDebounceTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_is_debounced_but_last_state_is_flushed(self):
        bot = _bot()
        channel = SimpleNamespace(id=10, last_message_id=1)
        lines: list[str] = []
        with patch.object(run_selfbot, "emit_line", side_effect=lines.append):
            bot._emit_dm_upsert(channel, debounce=True)  # envia na hora
            channel.last_message_id = 2
            bot._emit_dm_upsert(channel, debounce=True)  # dentro da janela
            channel.last_message_id = 3
            bot._emit_dm_upsert(channel, debounce=True)  # dentro da janela
            self.assertEqual(len(lines), 1)
            self.assertEqual(len(bot._dm_upsert_pending), 1)
            await asyncio.sleep(2.3)
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[-1].removeprefix("EVENT:DM_UPSERT:"))["n"], 3)
        self.assertFalse(bot._dm_upsert_pending)

    async def test_delete_cancels_pending_flush(self):
        bot = _bot()
        channel = SimpleNamespace(id=11, last_message_id=1)
        lines: list[str] = []
        with patch.object(run_selfbot, "emit_line", side_effect=lines.append):
            bot._emit_dm_upsert(channel, debounce=True)
            bot._emit_dm_upsert(channel, debounce=True)
            await bot.on_private_channel_delete(channel)
            await asyncio.sleep(2.3)
        kinds = [line.split(":", 2)[1] for line in lines]
        self.assertEqual(kinds, ["DM_UPSERT", "DM_REMOVE"])
        self.assertEqual(json.loads(lines[-1].removeprefix("EVENT:DM_REMOVE:"))["id"], "11")


if __name__ == "__main__":
    unittest.main()
