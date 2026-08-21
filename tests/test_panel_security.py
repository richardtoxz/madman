from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import unittest
from types import SimpleNamespace

from panel.shared.tasks import cancel_panel_tasks, create_panel_task


class PanelSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_protection_callback_does_not_retain_token_interaction(self):
        # Panel and worker intentionally use incompatible `discord` packages;
        # inspect panel code in a fresh process just like production does.
        code = textwrap.dedent(
            """
            import asyncio
            from types import SimpleNamespace
            from panel.modals.token_modal import _subscribe_protection_events

            async def main():
                callbacks = []
                class Manager:
                    def subscribe_events(self, user_id, prefix, callback):
                        callbacks.append(callback)

                user = SimpleNamespace(id=123)
                interaction = SimpleNamespace(
                    user=user,
                    client=SimpleNamespace(user=SimpleNamespace(id=456)),
                    data={"components": [{"value": "sensitive-user-token"}]},
                )
                await _subscribe_protection_events(interaction, Manager(), user.id)
                captured = [cell.cell_contents for cell in (callbacks[0].__closure__ or ())]
                assert all(value is not interaction for value in captured)
                assert any(value is user for value in captured)

            asyncio.run(main())
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_panel_tasks_are_cancelled_on_shutdown(self):
        client = SimpleNamespace()
        blocker = asyncio.Event()
        task = create_panel_task(client, blocker.wait(), name="panel-test-task")
        await asyncio.sleep(0)

        await cancel_panel_tasks(client)

        self.assertTrue(task.cancelled())
        self.assertEqual(client._panel_background_tasks, set())
