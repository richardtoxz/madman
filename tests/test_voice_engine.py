from __future__ import annotations

import asyncio
import json
import unittest

from engine.backend import SelfbotManager


class _FakeVoiceProcess:
    is_alive = True

    def __init__(self, label: str):
        self.label = label
        self.commands = []

    async def send_and_wait_json(self, command, prefix, timeout=10.0):
        self.commands.append((command, prefix, timeout))
        if command == "VOICE_STATUS":
            return {
                "ok": True,
                "code": "STATUS",
                "mode": "IDLE",
                "in_voice": False,
                "following": False,
            }
        return {
            "ok": True,
            "code": "JOINED",
            "message": self.label,
        }


class VoiceEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_commands_use_validated_json_protocol(self):
        manager = SelfbotManager()
        process = _FakeVoiceProcess("conta")
        manager.active_processes[101] = process

        result = await manager.voice_join(101, 123456789012345678)
        follow = await manager.voice_follow(101, 987654321098765432)
        status = await manager.voice_status(101)

        join_command = process.commands[0][0]
        follow_command = process.commands[1][0]
        self.assertEqual(
            json.loads(join_command.removeprefix("VOICE_JOIN_JSON:")),
            {"channel_id": 123456789012345678},
        )
        self.assertEqual(
            json.loads(follow_command.removeprefix("VOICE_FOLLOW_JSON:")),
            {"target_id": 987654321098765432},
        )
        self.assertTrue(result["ok"])
        self.assertTrue(follow["ok"])
        self.assertEqual(status["code"], "STATUS")

    async def test_two_accounts_receive_independent_voice_commands(self):
        manager = SelfbotManager()
        first = _FakeVoiceProcess("conta-1")
        second = _FakeVoiceProcess("conta-2")
        manager.active_processes.update({101: first, 202: second})

        results = await asyncio.gather(
            manager.voice_join(101, 111111111111111111),
            manager.voice_join(202, 222222222222222222),
        )

        self.assertEqual([result["message"] for result in results], ["conta-1", "conta-2"])
        self.assertIn("111111111111111111", first.commands[0][0])
        self.assertIn("222222222222222222", second.commands[0][0])

    async def test_disconnected_account_returns_structured_error(self):
        manager = SelfbotManager()

        result = await manager.voice_leave(101)

        self.assertEqual(result["code"], "NOT_CONNECTED")
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
