"""Quem pode usar o painel: ALLOWED_USER_IDS vazio libera todos; preenchido, só os IDs listados."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from panel import access

ROOT = Path(__file__).resolve().parent.parent


class AllowedUsersTests(unittest.TestCase):
    def test_missing_list_allows_everyone(self):
        with patch.dict(os.environ):
            os.environ.pop("ALLOWED_USER_IDS", None)
            self.assertTrue(access.is_user_allowed(123))

    def test_listed_ids_are_the_only_ones_allowed(self):
        with patch.dict(os.environ, {"ALLOWED_USER_IDS": "111, 222;333\n444"}):
            for user_id in (111, 222, 333, 444):
                self.assertTrue(access.is_user_allowed(user_id), user_id)
            self.assertFalse(access.is_user_allowed(555))

    def test_invalid_entries_are_ignored(self):
        with patch.dict(os.environ, {"ALLOWED_USER_IDS": "abc, 111, -5"}):
            self.assertTrue(access.is_user_allowed(111))
            self.assertFalse(access.is_user_allowed(5))

    def test_list_without_any_valid_id_allows_nobody(self):
        # Um erro de digitação não pode abrir o painel para todo mundo.
        with patch.dict(os.environ, {"ALLOWED_USER_IDS": "abc"}):
            self.assertFalse(access.is_user_allowed(111))


class BotPanelAccessTests(unittest.TestCase):
    """O discord do painel não convive com o do worker: roda num processo só do painel."""

    def test_command_and_menus_refuse_users_outside_the_list(self):
        script = textwrap.dedent(
            """
            import asyncio
            from unittest.mock import AsyncMock, MagicMock

            import panel
            import discord
            from panel.access import ACCESS_DENIED_MESSAGE
            from panel.control_panel.cog import PanelCog
            from panel.control_panel.selects import BaseSelect


            def interaction(user_id):
                it = MagicMock()
                it.user.id = user_id
                it.response.send_message = AsyncMock()
                return it


            def assert_denied(it):
                it.response.send_message.assert_awaited_once_with(ACCESS_DENIED_MESSAGE, ephemeral=True)


            async def main():
                manager = MagicMock()
                select = BaseSelect(manager, "Menu", [discord.SelectOption(label="Entrar", value="auth")])
                denied = interaction(333)
                await select.callback(denied)
                assert_denied(denied)

                denied = interaction(333)
                await PanelCog.painel.callback(PanelCog(MagicMock(manager=manager)), denied)
                assert_denied(denied)


            asyncio.run(main())
            print("ok")
            """
        )
        env = {**os.environ, "ALLOWED_USER_IDS": "111"}
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, encoding="utf-8", cwd=ROOT, env=env, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
