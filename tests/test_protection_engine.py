from __future__ import annotations

import json
import unittest

from engine.backend import SelfbotManager


class _ProtectionProcess:
    is_alive = True

    def __init__(self):
        self.commands = []
        self.enabled = False
        self.whitelist = []

    async def send_and_wait_json(self, command, prefix, timeout=10.0):
        self.commands.append((command, prefix, timeout))
        if command.startswith("PROTECTION_ENABLE_JSON:"):
            self.whitelist = json.loads(command.split(":", 1)[1])["whitelist"]
            self.enabled = True
            return {"ok": True, "code": "ENABLED", "enabled": True}
        if command.startswith("PROTECTION_CONFIG_JSON:"):
            self.whitelist = json.loads(command.split(":", 1)[1])["whitelist"]
            return {"ok": True, "code": "CONFIGURED"}
        if command == "PROTECTION_DISABLE":
            self.enabled = False
            return {"ok": True, "code": "DISABLED"}
        if command == "PROTECTION_STATUS":
            return {
                "ok": True,
                "code": "STATUS",
                "enabled": self.enabled,
                "whitelist": self.whitelist,
            }
        return None


class ProtectionEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_sends_current_whitelist_and_tracks_desired_state(self):
        manager = SelfbotManager()
        process = _ProtectionProcess()
        manager.active_processes[101] = process
        manager.user_whitelists[101] = [30, 20, 30]

        result = await manager.protection_enable(101)

        self.assertTrue(result["ok"])
        self.assertEqual(process.whitelist, [20, 30])
        self.assertIn(101, manager._protection_enabled_users)

    async def test_configurations_are_isolated_between_accounts(self):
        manager = SelfbotManager()
        first = _ProtectionProcess()
        second = _ProtectionProcess()
        manager.active_processes.update({101: first, 202: second})

        first_result = await manager.protection_configure(101, [1, 2])
        second_result = await manager.protection_configure(202, [3])

        self.assertTrue(first_result["ok"])
        self.assertTrue(second_result["ok"])
        self.assertEqual(manager.get_whitelist(101), [1, 2])
        self.assertEqual(manager.get_whitelist(202), [3])
        self.assertEqual(first.whitelist, [1, 2])
        self.assertEqual(second.whitelist, [3])

    async def test_invalid_whitelist_does_not_reach_worker(self):
        manager = SelfbotManager()
        process = _ProtectionProcess()
        manager.active_processes[101] = process

        result = await manager.protection_configure(101, [0])

        self.assertFalse(result["ok"])
        self.assertEqual(process.commands, [])

    async def test_disable_clears_desired_state(self):
        manager = SelfbotManager()
        process = _ProtectionProcess()
        manager.active_processes[101] = process
        await manager.protection_enable(101)

        result = await manager.protection_disable(101)

        self.assertTrue(result["ok"])
        self.assertNotIn(101, manager._protection_enabled_users)

    async def test_enabled_protection_can_be_restored_after_worker_restart(self):
        manager = SelfbotManager()
        manager._protection_enabled_users.add(101)
        manager.user_whitelists[101] = [9, 8]
        restarted = _ProtectionProcess()

        result = await manager._restore_protection_state(101, restarted)

        self.assertTrue(result["ok"])
        self.assertTrue(restarted.enabled)
        self.assertEqual(restarted.whitelist, [8, 9])

    async def test_status_has_structured_disconnected_error(self):
        manager = SelfbotManager()

        result = await manager.protection_status(404)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NOT_CONNECTED")


if __name__ == "__main__":
    unittest.main()
