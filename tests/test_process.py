from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

from engine.process import SelfbotProcess
from ipc_protocol import MAX_IPC_LINE_LENGTH


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAKE_WORKER = PROJECT_ROOT / "tests" / "fixtures" / "fake_worker.py"


class SelfbotProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.workers: list[SelfbotProcess] = []

    async def asyncTearDown(self):
        await asyncio.gather(
            *(worker.close() for worker in self.workers),
            return_exceptions=True,
        )

    async def start_worker(self, mode: str = "ready") -> SelfbotProcess:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(FAKE_WORKER),
            mode,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=PROJECT_ROOT,
            limit=MAX_IPC_LINE_LENGTH,
        )
        worker = SelfbotProcess(process, "test")
        worker.start_reader()
        self.workers.append(worker)
        return worker

    async def start_real_worker(self) -> SelfbotProcess:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "worker.run_selfbot",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=PROJECT_ROOT,
            limit=MAX_IPC_LINE_LENGTH,
        )
        worker = SelfbotProcess(process, "test-real")
        worker.start_reader()
        self.workers.append(worker)
        return worker

    async def test_ready_and_request_correlation(self):
        worker = await self.start_worker()
        self.assertTrue(await worker.wait_until_ready(2.0))
        self.assertEqual(worker.username, "fake:user")

        event_received = asyncio.Event()

        async def on_event(text: str):
            if text == "PONG:EVENT":
                event_received.set()

        worker.subscribe_event("PONG:", on_event)
        self.assertEqual(await worker.send_and_wait("PING", "PONG:"), "OK")
        await asyncio.wait_for(event_received.wait(), timeout=1.0)

    async def test_stream_finishes_on_result(self):
        worker = await self.start_worker()
        self.assertTrue(await worker.wait_until_ready(2.0))
        lines = [line async for line in worker.send_and_stream("STREAM", ["JOB_"])]
        self.assertEqual(lines, ["JOB_PROGRESS:1:2", "JOB_RESULT:OK"])

    async def test_process_exit_wakes_pending_request(self):
        worker = await self.start_worker()
        self.assertTrue(await worker.wait_until_ready(2.0))
        started = time.monotonic()
        result = await worker.send_and_wait("DIE", "NEVER:", timeout=5.0)
        self.assertEqual(result, "ERRO:PROCESS_EXITED")
        self.assertLess(time.monotonic() - started, 2.0)

    async def test_correlated_ipc_error_wakes_pending_request(self):
        worker = await self.start_worker()
        self.assertTrue(await worker.wait_until_ready(2.0))
        result = await worker.send_and_wait("FAIL", "EXPECTED:", timeout=5.0)
        self.assertEqual(result, "ERRO:IPC_ERROR:HANDLER_FAILED:Fake")

    async def test_command_injection_is_rejected(self):
        worker = await self.start_worker()
        self.assertTrue(await worker.wait_until_ready(2.0))
        self.assertFalse(await worker.send("PING\nCLOSE"))
        self.assertTrue(worker.is_alive)

    async def test_structured_startup_error(self):
        worker = await self.start_worker("error")
        self.assertFalse(await worker.wait_until_ready(2.0))
        self.assertEqual(worker.startup_error_code, "fake_error")
        self.assertEqual(worker.startup_error, "Falha controlada de teste.")

    async def test_real_worker_rejects_invalid_credential_without_network(self):
        worker = await self.start_real_worker()
        self.assertTrue(await worker.send_startup_secret("short"))
        self.assertFalse(await worker.wait_until_ready(3.0))
        self.assertEqual(worker.startup_error_code, "invalid_credential")

    async def test_close_is_idempotent(self):
        worker = await self.start_worker()
        self.assertTrue(await worker.wait_until_ready(2.0))
        await worker.close()
        await worker.close()
        self.assertFalse(worker.is_alive)
