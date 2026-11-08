from __future__ import annotations

import asyncio
import gc
import unittest

from engine.backend import SelfbotManager
from panel.main import create_web_app
from panel.web.routes.common import JOB_STATE_KEY
from panel.web.routes.realtime import _start_streaming_job

UID = 606
JOB_NAME = "Limpeza de teste"


def _job_tasks() -> list[asyncio.Task]:
    return [task for task in asyncio.all_tasks() if task.get_name() == f"web-job-{UID}-{JOB_NAME}"]


async def _let_job_run() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


class StreamingJobTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = SelfbotManager()
        self.app = create_web_app(manager=self.manager, bot=None)
        self.addAsyncCleanup(self.manager.shutdown)
        self.addAsyncCleanup(self._cancel_job_tasks)

    async def _cancel_job_tasks(self):
        tasks = _job_tasks()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_running_job_survives_garbage_collection(self):
        async def runner(update_callback):
            # Só a própria task segura este future: sem outra referência, o ciclo
            # task <-> future vira lixo coletável.
            await asyncio.get_running_loop().create_future()

        self.assertTrue(_start_streaming_job(self.app, UID, JOB_NAME, runner)["ok"])
        await _let_job_run()
        self.assertEqual(len(_job_tasks()), 1)

        gc.collect()

        self.assertEqual(len(_job_tasks()), 1, "a task do job foi coletada pelo GC no meio da execução")
        self.assertTrue(self.app[JOB_STATE_KEY][UID]["running"])

    async def test_finished_job_does_not_keep_its_task(self):
        from panel.web.routes.common import BACKGROUND_TASKS_KEY

        async def runner(update_callback):
            return "ok"

        _start_streaming_job(self.app, UID, JOB_NAME, runner)
        await _let_job_run()

        self.assertFalse(self.app[JOB_STATE_KEY][UID]["running"])
        self.assertEqual(self.app[BACKGROUND_TASKS_KEY], set())


if __name__ == "__main__":
    unittest.main()
