from __future__ import annotations

import json
import unittest

from engine.backend import SelfbotManager


class _QuestProcess:
    is_alive = True

    def __init__(self):
        self.commands = []
        self.webhook_url = None

    async def send_and_wait_json(self, command, prefix, timeout=10.0):
        self.commands.append((command, prefix, timeout))
        if command.startswith("QUEST_CONFIG:"):
            payload = json.loads(command.split(":", 1)[1])
            self.webhook_url = payload["webhook_url"]
            return {
                "ok": True,
                "code": "CONFIG_UPDATED",
                "webhook": bool(self.webhook_url),
            }
        return None


class QuestEngineTests(unittest.IsolatedAsyncioTestCase):
    WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40

    async def test_config_is_confirmed_and_isolated_between_accounts(self):
        manager = SelfbotManager()
        first = _QuestProcess()
        second = _QuestProcess()
        manager.active_processes.update({101: first, 202: second})

        first_result = await manager.quest_config(101, webhook_url=self.WEBHOOK)
        second_result = await manager.quest_config(202, webhook_url=None)

        self.assertTrue(first_result["ok"])
        self.assertTrue(second_result["ok"])
        self.assertIn(101, manager._quest_settings)
        self.assertNotIn(202, manager._quest_settings)
        self.assertEqual(first.webhook_url, self.WEBHOOK)
        self.assertIsNone(second.webhook_url)

    async def test_config_is_restored_after_worker_restart(self):
        manager = SelfbotManager()
        manager._quest_settings[101] = {"webhook_url": self.WEBHOOK}
        restarted = _QuestProcess()

        result = await manager._restore_quest_state(101, restarted)

        self.assertTrue(result["ok"])
        self.assertEqual(restarted.webhook_url, self.WEBHOOK)

    async def test_clearing_config_removes_only_calling_accounts_state(self):
        manager = SelfbotManager()
        first = _QuestProcess()
        second = _QuestProcess()
        manager.active_processes.update({101: first, 202: second})
        await manager.quest_config(101, webhook_url=self.WEBHOOK)
        await manager.quest_config(202, webhook_url=self.WEBHOOK)

        result = await manager.quest_config(101, webhook_url=None)

        self.assertTrue(result["ok"])
        self.assertNotIn(101, manager._quest_settings)
        self.assertIn(202, manager._quest_settings)

    async def test_disconnected_config_returns_structured_error(self):
        result = await SelfbotManager().quest_config(404, webhook_url=self.WEBHOOK)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NOT_CONNECTED")


if __name__ == "__main__":
    unittest.main()
