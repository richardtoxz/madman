from __future__ import annotations

import asyncio
import unittest

from engine.backend import SelfbotManager


class _FakeRelationshipProcess:
    is_alive = True

    def __init__(self, account_label: str):
        self.account_label = account_label
        self.commands = []

    async def send_and_stream(self, command, prefixes, timeout):
        self.commands.append((command, tuple(prefixes), timeout))
        prefix = prefixes[0]
        yield f"{prefix}START:1"
        await asyncio.sleep(0)
        yield f"{prefix}PROGRESS:1:1:{self.account_label}"
        yield f"{prefix}RESULT:COMPLETADO:processados 1/1; falhas 0"


class RelationshipEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_bulk_action_is_isolated_between_two_accounts(self):
        manager = SelfbotManager()
        first = _FakeRelationshipProcess("conta-1")
        second = _FakeRelationshipProcess("conta-2")
        manager.active_processes.update({101: first, 202: second})
        progress = {101: [], 202: []}

        def callback_for(user_id):
            async def callback(current, total, status, detail):
                progress[user_id].append((current, total, status, detail))

            return callback

        results = await asyncio.gather(
            manager.unblock_all(101, callback_for(101)),
            manager.unblock_all(202, callback_for(202)),
        )

        self.assertEqual(
            results,
            [
                "COMPLETADO:processados 1/1; falhas 0",
                "COMPLETADO:processados 1/1; falhas 0",
            ],
        )
        self.assertEqual(first.commands[0][0], "REL_UNBLOCK_ALL")
        self.assertEqual(second.commands[0][0], "REL_UNBLOCK_ALL")
        self.assertIn((1, 1, "CLEANING", "conta-1"), progress[101])
        self.assertIn((1, 1, "CLEANING", "conta-2"), progress[202])


if __name__ == "__main__":
    unittest.main()
