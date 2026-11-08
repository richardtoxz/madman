from __future__ import annotations

import unittest
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from worker import run_selfbot


class GatewayMessageLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_ws_connect_receives_larger_message_limit_once(self):
        calls = []

        async def fake_ws_connect(self, url, **kwargs):
            calls.append(kwargs)
            return "ws"

        with patch.object(discord.http.HTTPClient, "ws_connect", fake_ws_connect):
            run_selfbot.raise_gateway_message_limit()
            run_selfbot.raise_gateway_message_limit()  # idempotente
            patched = discord.http.HTTPClient.ws_connect
            self.assertTrue(patched._madman_patched)
            self.assertEqual(await patched(object(), "wss://x"), "ws")
            await patched(object(), "wss://x", max_message_size=1)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["max_message_size"], run_selfbot.GATEWAY_MAX_MESSAGE_SIZE)
        self.assertEqual(calls[1]["max_message_size"], 1)


if __name__ == "__main__":
    unittest.main()
