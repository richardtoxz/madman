from __future__ import annotations

import json
import unittest

from engine.backend import SelfbotManager
from panel.main import create_web_app
from panel.web.routes.worker import handle_worker_status
from panel.web.security import SESSION_REQUEST_KEY
from aiohttp.test_utils import make_mocked_request

UID = 606


class WorkerStatusTrackerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = SelfbotManager()
        self.app = create_web_app(manager=self.manager, bot=None)
        self.addAsyncCleanup(self.manager.shutdown)

    async def _status(self) -> dict:
        request = make_mocked_request("GET", "/api/worker/status", app=self.app)
        request[SESSION_REQUEST_KEY] = {"user_id": UID}
        response = await handle_worker_status(request)
        return json.loads(response.text)

    async def test_tracker_is_inactive_without_saved_settings(self):
        body = await self._status()
        self.assertIs(body["tracker_active"], False)
        self.assertEqual(body["tracker_targets"], [])

    async def test_tracker_is_active_with_saved_settings(self):
        self.manager._tracker_settings[UID] = {"target_id": 1, "target_ids": [1, 2], "webhook_url": "w"}
        body = await self._status()
        self.assertIs(body["tracker_active"], True)
        self.assertEqual(body["tracker_targets"], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
