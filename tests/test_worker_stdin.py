from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest


class WorkerStdinTests(unittest.TestCase):
    WORKER_CODE = textwrap.dedent("""
            import asyncio
            from worker.run_selfbot import MySelfbot
            from worker.core.router import register_handlers
            from worker.quest import QuestManager

            class Bot:
                _listen_stdin = MySelfbot._listen_stdin
                _closed = False

                def __init__(self):
                    self.quest_manager = QuestManager(self)
                    self._exact_handlers, self._prefix_handlers = register_handlers(self)

                def is_closed(self):
                    return self._closed

                async def close(self):
                    self._closed = True

            asyncio.run(Bot()._listen_stdin())
        """)

    def test_worker_reads_commands_from_inherited_stdin_pipe(self):
        from ipc_protocol import attach_request_id

        request_id = "a" * 32
        command = attach_request_id('QUEST_CONFIG:{"webhook_url":null}', request_id)
        result = subprocess.run(
            [sys.executable, "-c", self.WORKER_CODE],
            input=command + "\n",
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('QUEST:CONFIG_RESULT:{"ok":true', result.stdout)
        self.assertIn(request_id, result.stdout)

    def test_close_exits_even_while_parent_keeps_stdin_open(self):
        process = subprocess.Popen(
            [sys.executable, "-c", self.WORKER_CODE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            process.stdin.write(b"CLOSE\n")
            process.stdin.flush()
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr.read().decode("utf-8"))
            self.assertIn(b"STATUS:CLOSING", process.stdout.read())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_oversized_command_is_rejected_without_processing_its_tail(self):
        from ipc_protocol import MAX_IPC_LINE_LENGTH

        result = subprocess.run(
            [sys.executable, "-c", self.WORKER_CODE],
            input="x" * (MAX_IPC_LINE_LENGTH + 1) + '\nQUEST_CONFIG:{"webhook_url":null}\n',
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ERROR:Erro no comando: ValueError", result.stdout)
        self.assertNotIn("QUEST:CONFIG_RESULT:", result.stdout)
