from __future__ import annotations

import os
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from engine.backend import SelfbotManager


class ManagerSecurityTests(unittest.TestCase):
    def test_worker_environment_does_not_inherit_secrets(self):
        source = {
            "PATH": "/usr/bin",
            "LANG": "pt_BR.UTF-8",
            "TOKEN_BOT_CONTROLE": "secret-1",
            "SELFBOT_TOKEN": "secret-2",
            "SERVICE_API_KEY": "secret-3",
            "DATABASE_PASSWORD": "secret-4",
            "SESSION_COOKIE": "secret-5",
        }
        with patch.dict(os.environ, source, clear=True):
            environment = SelfbotManager._worker_environment()

        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["LANG"], "pt_BR.UTF-8")
        self.assertEqual(environment["PYTHONUNBUFFERED"], "1")
        self.assertNotIn("TOKEN_BOT_CONTROLE", environment)
        self.assertNotIn("SELFBOT_TOKEN", environment)
        self.assertNotIn("SERVICE_API_KEY", environment)
        self.assertNotIn("DATABASE_PASSWORD", environment)
        self.assertNotIn("SESSION_COOKIE", environment)


class ManagerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_worker_start_closes_partial_process(self):
        manager = SelfbotManager()
        entered_wait = asyncio.Event()
        release_wait = asyncio.Event()

        class FakeProcess:
            pass

        class FakeSelfbotProcess:
            instances = []

            def __init__(self, process, status, *, redactions=()):
                self.closed = False
                self.__class__.instances.append(self)

            def start_reader(self):
                return None

            async def send_startup_secret(self, secret):
                return True

            async def close(self):
                self.closed = True

        async def wait_ready(process, timeout):
            entered_wait.set()
            await release_wait.wait()
            return True

        manager._wait_ready = wait_ready
        with (
            patch("engine.backend.SelfbotProcess", FakeSelfbotProcess),
            patch(
                "engine.backend.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=FakeProcess()),
            ),
        ):
            task = asyncio.create_task(manager._spawn_ready_process("x" * 40))
            await asyncio.wait_for(entered_wait.wait(), timeout=1.0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(FakeSelfbotProcess.instances[0].closed)
