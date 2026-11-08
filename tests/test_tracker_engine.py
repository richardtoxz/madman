from __future__ import annotations

import json
import unittest

from engine.backend import SelfbotManager


class _TrackerProcess:
    is_alive = True

    def __init__(self):
        self.commands = []
        self.enabled = False
        self.target_id = None

    async def send_and_wait_json(self, command, prefix, timeout=10.0):
        self.commands.append((command, prefix, timeout))
        if command.startswith("TRACKER_START_JSON:"):
            payload = json.loads(command.split(":", 1)[1])
            self.enabled = True
            self.target_id = payload["target_id"]
            return {
                "ok": True,
                "code": "STARTED",
                "enabled": True,
                "target_id": self.target_id,
            }
        if command == "TRACKER_STOP":
            self.enabled = False
            return {"ok": True, "code": "STOPPED", "enabled": False}
        if command == "TRACKER_STATUS":
            return {
                "ok": True,
                "code": "STATUS",
                "enabled": self.enabled,
                "target_id": self.target_id if self.enabled else None,
            }
        return None


class TrackerEngineTests(unittest.IsolatedAsyncioTestCase):
    WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40

    async def test_start_is_confirmed_and_isolated_between_accounts(self):
        manager = SelfbotManager()
        first = _TrackerProcess()
        second = _TrackerProcess()
        manager.active_processes.update({101: first, 202: second})

        first_result = await manager.tracker_start(101, 11, self.WEBHOOK)
        second_result = await manager.tracker_start(202, 22, self.WEBHOOK)

        self.assertTrue(first_result["ok"])
        self.assertTrue(second_result["ok"])
        self.assertEqual(first.target_id, 11)
        self.assertEqual(second.target_id, 22)
        self.assertEqual(manager._tracker_settings[101]["target_id"], 11)
        self.assertEqual(manager._tracker_settings[202]["target_id"], 22)

    async def test_tracker_is_restored_after_worker_restart(self):
        manager = SelfbotManager()
        manager._tracker_settings[101] = {
            "target_id": 77,
            "webhook_url": self.WEBHOOK,
        }
        restarted = _TrackerProcess()

        result = await manager._restore_tracker_state(101, restarted)

        self.assertTrue(result["ok"])
        self.assertTrue(restarted.enabled)
        self.assertEqual(restarted.target_id, 77)

    async def test_stop_clears_only_the_calling_accounts_desired_state(self):
        manager = SelfbotManager()
        first = _TrackerProcess()
        second = _TrackerProcess()
        manager.active_processes.update({101: first, 202: second})
        await manager.tracker_start(101, 11, self.WEBHOOK)
        await manager.tracker_start(202, 22, self.WEBHOOK)

        result = await manager.tracker_stop(101)

        self.assertTrue(result["ok"])
        self.assertNotIn(101, manager._tracker_settings)
        self.assertIn(202, manager._tracker_settings)

    async def test_disconnected_status_is_structured(self):
        result = await SelfbotManager().tracker_status(404)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NOT_CONNECTED")

    async def test_multiple_ids_are_restored_and_failed_edit_keeps_desired_state(self):
        manager = SelfbotManager()
        process = _TrackerProcess()
        manager.active_processes[101] = process
        await manager.tracker_start(101, [42, 77], self.WEBHOOK)
        restarted = _TrackerProcess()
        await manager._restore_tracker_state(101, restarted)
        payload = json.loads(restarted.commands[0][0].split(":", 1)[1])
        self.assertEqual(payload["target_ids"], [42, 77])
        invalid = await manager.tracker_start(101, [True], self.WEBHOOK)
        self.assertFalse(invalid["ok"])
        self.assertEqual(manager._tracker_settings[101]["target_ids"], [42, 77])


if __name__ == "__main__":
    unittest.main()
