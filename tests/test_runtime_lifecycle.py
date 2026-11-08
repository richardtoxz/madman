"""Produção: logs da aplicação no stdout e desligamento limpo quando chega SIGTERM (Render, Docker)."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import signal
import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from panel import main as panel_main

ROOT = Path(__file__).resolve().parent.parent


class _RestoreRootLogger:
    def setUp(self):
        root = logging.getLogger()
        saved = (root.handlers[:], root.level)

        def restore():
            root.handlers[:] = saved[0]
            root.setLevel(saved[1])

        self.addCleanup(restore)


class LoggingSetupTests(_RestoreRootLogger, unittest.TestCase):
    def test_info_logs_from_the_app_reach_stdout(self):
        stream = io.StringIO()
        with patch.dict(os.environ, {"LOG_LEVEL": ""}), patch.object(sys, "stdout", stream):
            panel_main.configure_logging()
            logging.getLogger("engine.backend").info("selfbot autenticado")
        self.assertIn("INFO engine.backend: selfbot autenticado", stream.getvalue())

    def test_log_level_comes_from_the_environment(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "warning"}):
            panel_main.configure_logging()
        self.assertEqual(logging.getLogger().level, logging.WARNING)

    def test_unknown_level_falls_back_to_info(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "barulhento"}):
            panel_main.configure_logging()
        self.assertEqual(logging.getLogger().level, logging.INFO)

    def test_bot_does_not_install_a_second_log_handler(self):
        fake_bot = types.ModuleType("panel.bot")
        fake_bot.ControlBot = MagicMock()
        with (
            patch.dict(sys.modules, {"panel.bot": fake_bot}),
            patch.dict(os.environ, {"WEB_DEMO": "", "WEB_ONLY": "", "LOG_LEVEL": ""}),
            patch.object(sys, "argv", ["panel.main"]),
            patch.object(panel_main, "TOKEN_BOT_CONTROLE", "token-do-bot"),
            patch.object(panel_main, "SelfbotManager"),
            patch.object(panel_main, "keep_alive"),
        ):
            panel_main.main()
        fake_bot.ControlBot.return_value.run.assert_called_once_with("token-do-bot", log_handler=None)


class _SigtermSafety:
    """Sem tratamento, SIGTERM mataria o processo de testes: um handler neutro fica por baixo."""

    def setUp(self):
        previous = signal.signal(signal.SIGTERM, lambda *_: None)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)


class WebOnlyShutdownTests(_SigtermSafety, unittest.IsolatedAsyncioTestCase):
    async def test_sigterm_stops_the_server_and_shuts_the_manager_down(self):
        manager = MagicMock()
        manager.shutdown = AsyncMock()
        # O aviso de início tem emoji; o console do Windows com saída redirecionada não codifica.
        self.enterContext(patch("builtins.print"))
        task = asyncio.create_task(panel_main.run_web_only(manager, host="127.0.0.1", port=0))
        await asyncio.sleep(0.5)

        signal.raise_signal(signal.SIGTERM)
        await asyncio.wait_for(task, timeout=5)

        manager.shutdown.assert_awaited_once()


class BotShutdownTests(unittest.TestCase):
    """O discord do painel não convive com o do worker: roda num processo só do painel."""

    def test_sigterm_closes_the_bot(self):
        script = textwrap.dedent(
            """
            import asyncio
            import signal
            from unittest.mock import AsyncMock, MagicMock

            import panel
            from panel.bot import ControlBot


            async def main():
                bot = ControlBot(MagicMock())
                bot.close = AsyncMock()
                bot.load_extension = AsyncMock()
                bot.tree.sync = AsyncMock()
                await bot.setup_hook()

                signal.raise_signal(signal.SIGTERM)
                for _ in range(100):
                    if bot.close.await_count:
                        break
                    await asyncio.sleep(0.02)
                assert bot.close.await_count == 1, bot.close.await_count


            asyncio.run(main())
            print("ok")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, encoding="utf-8", cwd=ROOT, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
