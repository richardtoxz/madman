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

    async def test_background_task_failure_is_logged(self):
        manager = SelfbotManager()

        async def boom():
            raise RuntimeError("falha-de-teste")

        with self.assertLogs("engine.backend", level="WARNING") as captured:
            task = asyncio.create_task(boom(), name="cleanup-test")
            manager._background_tasks.add(task)
            task.add_done_callback(manager._background_task_done)
            with self.assertRaises(RuntimeError):
                await task
            await asyncio.sleep(0)

        self.assertTrue(any("falha-de-teste" in line or "RuntimeError" in line for line in captured.output))
        self.assertNotIn(task, manager._background_tasks)


class ManagerTokenCleaningTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticate_cleans_pasted_token_before_starting_worker(self):
        manager = SelfbotManager()
        self.addAsyncCleanup(manager.shutdown)
        token = "x" * 40
        seen = []

        async def spawn(value):
            seen.append(value)
            return False, "falha-de-teste"

        manager._spawn_ready_process = spawn
        await manager.authenticate(123, f'"\u200b{token}\n"')
        self.assertEqual(seen, [token])


class ConnectFailureLimitTests(unittest.IsolatedAsyncioTestCase):
    """Cada tentativa abre um processo e um login no Discord pelo IP do servidor: falhas em s\u00e9rie param."""

    TOKEN = "t" * 40

    def setUp(self):
        self.now = 1000.0
        self.manager = SelfbotManager()
        self.manager._clock = lambda: self.now
        self.spawn = AsyncMock(return_value=(False, "Token inv\u00e1lido ou revogado."))
        self.manager._spawn_ready_process = self.spawn

    async def _fail(self, user_id: int, times: int) -> None:
        for _ in range(times):
            ok, _message = await self.manager.authenticate(user_id, self.TOKEN)
            self.assertFalse(ok)

    async def test_sixth_failed_attempt_in_ten_minutes_is_refused_without_a_worker(self):
        await self._fail(1, 5)

        ok, message = await self.manager.authenticate(1, self.TOKEN)

        self.assertFalse(ok)
        self.assertIn("Muitas tentativas", message)
        self.assertEqual(self.spawn.await_count, 5)

    async def test_other_accounts_keep_trying(self):
        await self._fail(1, 5)

        await self.manager.authenticate(2, self.TOKEN)

        self.assertEqual(self.spawn.await_count, 6)

    async def test_old_failures_expire_after_ten_minutes(self):
        await self._fail(1, 5)
        self.now += 601

        await self.manager.authenticate(1, self.TOKEN)

        self.assertEqual(self.spawn.await_count, 6)
