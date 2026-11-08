from __future__ import annotations

import json
import unittest

from engine.backend import SelfbotManager
from presence_protocol import validate_custom

CUSTOM = {"type": "playing", "name": "Jogo"}


class _PresenceProcess:
    is_alive = True

    def __init__(self, ok=True):
        self.commands = []
        self.ok = ok
        self.events = []

    async def send_and_wait_json(self, command, prefix, timeout=10.0):
        self.commands.append((command, prefix))
        if not self.ok:
            return {"ok": False, "code": "INVALID_RICH_PRESENCE", "message": "nao"}
        if command == "RICH_PRESENCE_STATUS":
            return {"ok": True, "enabled": True, "custom": None, "live": {"source": "default"}}
        if command.startswith("RICH_PRESENCE_SET_JSON:"):
            payload = json.loads(command.split(":", 1)[1])
            return {"ok": True, **payload}
        return None

    def emit_event(self, event):
        self.events.append(event)


class PresenceEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_validates_before_sending(self):
        manager = SelfbotManager()
        process = _PresenceProcess()
        manager.active_processes[101] = process
        result = await manager.rich_presence_set(101, True, {"type": "x", "name": "y"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "INVALID_RICH_PRESENCE")
        result = await manager.rich_presence_set(101, "sim", None)
        self.assertFalse(result["ok"])
        self.assertEqual(process.commands, [])

    async def test_set_sends_normalized_and_stores_on_ok_only(self):
        manager = SelfbotManager()
        process = _PresenceProcess()
        manager.active_processes[101] = process
        result = await manager.rich_presence_set(101, True, {"type": "playing", "name": " Jogo "})
        self.assertTrue(result["ok"])
        command, prefix = process.commands[0]
        self.assertEqual(prefix, "RICH_PRESENCE_RESULT:")
        sent = json.loads(command.split(":", 1)[1])
        self.assertEqual(sent["custom"]["name"], "Jogo")
        self.assertEqual(manager._rich_presence[101]["custom"]["name"], "Jogo")

        failing = _PresenceProcess(ok=False)
        manager.active_processes[202] = failing
        result = await manager.rich_presence_set(202, False, None)
        self.assertFalse(result["ok"])
        self.assertNotIn(202, manager._rich_presence)

    async def test_status_passthrough(self):
        manager = SelfbotManager()
        manager.active_processes[101] = _PresenceProcess()
        result = await manager.rich_presence_status(101)
        self.assertEqual(result["live"], {"source": "default"})

    async def test_not_connected(self):
        manager = SelfbotManager()
        self.assertEqual((await manager.rich_presence_status(9))["code"], "NOT_CONNECTED")
        self.assertEqual((await manager.rich_presence_set(9, True, None))["code"], "NOT_CONNECTED")

    async def test_no_response(self):
        manager = SelfbotManager()

        class Silent(_PresenceProcess):
            async def send_and_wait_json(self, command, prefix, timeout=10.0):
                return None

        manager.active_processes[101] = Silent()
        self.assertEqual((await manager.rich_presence_status(101))["code"], "NO_RESPONSE")
        self.assertEqual((await manager.rich_presence_set(101, True, None))["code"], "NO_RESPONSE")

    async def test_restore_sends_saved_config(self):
        manager = SelfbotManager()
        manager._rich_presence[101] = {"enabled": False, "custom": {"type": "playing", "name": "Jogo"}}
        process = _PresenceProcess()
        restored = await manager._restore_rich_presence_state(101, process)
        self.assertTrue(restored["ok"])
        sent = json.loads(process.commands[0][0].split(":", 1)[1])
        self.assertEqual(sent["enabled"], False)
        self.assertEqual(sent["custom"]["name"], "Jogo")

    async def test_restore_without_config_sends_nothing(self):
        manager = SelfbotManager()
        process = _PresenceProcess()
        self.assertIsNone(await manager._restore_rich_presence_state(101, process))
        self.assertEqual(process.commands, [])

    async def test_adopt_restarted_process_reapplies_and_reports_failure(self):
        manager = SelfbotManager()
        manager._rich_presence[101] = {"enabled": True, "custom": CUSTOM}
        process = _PresenceProcess(ok=False)
        process._event_subscriptions = {}
        manager._attach_event_subscriptions = lambda user_id, sp: None
        await manager._adopt_restarted_process(101, process)
        self.assertIn(
            (
                "RICH_PRESENCE_SET_JSON:"
                + json.dumps(
                    {
                        "enabled": True,
                        "custom": validate_custom(CUSTOM),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "RICH_PRESENCE_RESULT:",
            ),
            process.commands,
        )
        self.assertIn("PROCESS:STATE_RESTORE_FAILED:RICH_PRESENCE", process.events)

    async def test_manual_disconnect_drops_config(self):
        manager = SelfbotManager()
        manager._session_tokens[101] = "tok"
        manager._rich_presence[101] = {"enabled": True, "custom": None}

        async def noop(user_id):
            return None

        manager._stop_supervisor = noop
        manager._cleanup_process = noop
        ok, _ = await manager.disconnect(101)
        self.assertTrue(ok)
        self.assertNotIn(101, manager._rich_presence)

    async def test_forget_user_drops_config(self):
        manager = SelfbotManager()
        manager._rich_presence[101] = {"enabled": True, "custom": None}
        manager._forget_user(101)
        self.assertNotIn(101, manager._rich_presence)


if __name__ == "__main__":
    unittest.main()
