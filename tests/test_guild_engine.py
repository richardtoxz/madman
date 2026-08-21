from __future__ import annotations

import asyncio
import json
import unittest

from engine.backend import SelfbotManager


class _FakeGuildProcess:
    is_alive = True

    def __init__(self, account_label: str):
        self.account_label = account_label
        self.commands = []

    async def send_and_wait_json(self, command, prefix, timeout=10.0):
        self.commands.append((command, prefix, timeout))
        return {
            "items": [{"id": 1, "name": self.account_label}],
            "total": 1,
            "page": 0,
            "pages": 1,
            "fresh": True,
            "warning": None,
        }

    async def send_and_stream(self, command, prefixes, timeout):
        self.commands.append((command, tuple(prefixes), timeout))
        prefix = prefixes[0]
        yield f"{prefix}START:1"
        await asyncio.sleep(0)
        yield f"{prefix}PROGRESS:1:1:{self.account_label}"
        yield f"{prefix}RESULT:COMPLETADO:processados 1/1; falhas 0; restantes 0"


class GuildEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_guild_page_uses_bounded_json_command(self):
        manager = SelfbotManager()
        process = _FakeGuildProcess("conta")
        manager.active_processes[101] = process

        result = await manager.get_guilds_page(101, page=2, page_size=20)

        command, prefix, timeout = process.commands[0]
        self.assertTrue(command.startswith("GUILD_LIST_PAGE_JSON:"))
        self.assertEqual(
            json.loads(command.removeprefix("GUILD_LIST_PAGE_JSON:")),
            {"page": 2, "page_size": 20},
        )
        self.assertEqual(prefix, "GUILD_LIST_RESULT:")
        self.assertEqual(timeout, 30.0)
        self.assertEqual(result["total"], 1)

    async def test_same_action_is_isolated_between_two_accounts(self):
        manager = SelfbotManager()
        first = _FakeGuildProcess("conta-1")
        second = _FakeGuildProcess("conta-2")
        manager.active_processes.update({101: first, 202: second})
        manager.user_guild_whitelists.update({101: [33, 22, 33], 202: [44]})
        progress = {101: [], 202: []}

        def callback_for(user_id):
            async def callback(current, total, status, detail):
                progress[user_id].append((current, total, status, detail))

            return callback

        results = await asyncio.gather(
            manager.start_guild_cleanup(101, "LEAVE", callback_for(101)),
            manager.start_guild_cleanup(202, "LEAVE", callback_for(202)),
        )

        self.assertEqual(
            results,
            [
                "COMPLETADO:processados 1/1; falhas 0; restantes 0",
                "COMPLETADO:processados 1/1; falhas 0; restantes 0",
            ],
        )
        first_payload = json.loads(
            first.commands[0][0].removeprefix("GUILD_LEAVE_ALL_JSON:")
        )
        second_payload = json.loads(
            second.commands[0][0].removeprefix("GUILD_LEAVE_ALL_JSON:")
        )
        self.assertEqual(first_payload, {"whitelist": [22, 33]})
        self.assertEqual(second_payload, {"whitelist": [44]})
        self.assertIn((1, 1, "CLEANING", "conta-1"), progress[101])
        self.assertIn((1, 1, "CLEANING", "conta-2"), progress[202])

    async def test_page_whitelist_update_does_not_touch_other_pages(self):
        manager = SelfbotManager()
        manager.user_guild_whitelists[101] = [1, 2, 30]

        result = await manager.update_guild_whitelist(101, [1, 2, 3], [2, 3])

        self.assertEqual(result, [2, 3, 30])
        self.assertEqual(manager.get_guild_whitelist(101), [2, 3, 30])
        removed = await manager.clear_guild_whitelist(101)
        self.assertEqual(removed, 3)
        self.assertEqual(manager.get_guild_whitelist(101), [])

    async def test_invalid_action_is_rejected_without_contacting_worker(self):
        manager = SelfbotManager()
        process = _FakeGuildProcess("conta")
        manager.active_processes[101] = process

        result = await manager.start_guild_cleanup(101, "DELETE")

        self.assertEqual(result, "ABORTADO:acao de servidor invalida")
        self.assertEqual(process.commands, [])


if __name__ == "__main__":
    unittest.main()
