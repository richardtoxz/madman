from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest


class SettingsModalTests(unittest.TestCase):
    def _check(self, scenario):
        # Panel and worker have different discord packages and need separate processes.
        code = textwrap.dedent("""
            import asyncio
            import sys
            from types import SimpleNamespace as NS
            from unittest.mock import AsyncMock
            from panel.settings.modal import SettingsModal

            async def main():
                scenario = sys.argv[1]
                webhook = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40
                if scenario == "invalid":
                    webhook = webhook.replace("discord.com/", "discord.com:invalid/")
                if scenario == "clear":
                    webhook = ""
                interaction = NS(
                    response=NS(defer=AsyncMock(), send_message=AsyncMock()),
                    edit_original_response=AsyncMock(),
                    client=NS(_settings_state={123: {"webhook_url": "previous"}, 456: {"webhook_url": "other"}}),
                )

                async def configure(user_id, **kwargs):
                    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
                    assert interaction.client._settings_state[123]["webhook_url"] == "previous"
                    assert user_id == 123
                    assert kwargs["webhook_url"] == (webhook or None)
                    await asyncio.sleep(0)
                    return {"ok": scenario != "failure", "message": "O worker não confirmou a configuração."}

                manager = NS(quest_config=AsyncMock(side_effect=configure))
                modal = NS(manager=manager, admin_id=123, webhook_url=NS(value=webhook))
                await SettingsModal.on_submit(modal, interaction)
                assert interaction.client._settings_state[456]["webhook_url"] == "other"
                if scenario == "invalid":
                    manager.quest_config.assert_not_awaited()
                    interaction.response.send_message.assert_awaited_once()
                    interaction.response.defer.assert_not_awaited()
                else:
                    interaction.response.send_message.assert_not_awaited()
                    interaction.edit_original_response.assert_awaited_once()
                    if scenario == "failure":
                        assert interaction.client._settings_state[123]["webhook_url"] == "previous"
                        assert "não confirmou" in interaction.edit_original_response.await_args.kwargs["content"]
                    else:
                        assert interaction.client._settings_state[123]["webhook_url"] == (webhook or None)

            asyncio.run(main())
        """)
        result = subprocess.run(
            [sys.executable, "-c", code, scenario],
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_save_acknowledges_before_waiting_for_worker(self):
        self._check("success")

    def test_worker_failure_edits_deferred_response_and_keeps_previous_setting(self):
        self._check("failure")

    def test_clear_webhook_is_confirmed_before_updating_panel(self):
        self._check("clear")

    def test_invalid_port_returns_validation_error_without_contacting_worker(self):
        self._check("invalid")
