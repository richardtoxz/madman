from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.backend import SelfbotManager
from panel import main as panel_main
from panel.main import create_web_app
from panel.web.demo import DEMO_USER_ID, DemoManager
from panel.web.routes import JOB_STATE_KEY
from panel.web.security import SESSION_COOKIE_NAME
from aiohttp.test_utils import TestClient, TestServer

ROUTES_DIR = Path(__file__).resolve().parents[1] / "panel" / "web" / "routes"


def _manager_names_used_by_routes() -> set[str]:
    names: set[str] = set()
    for path in ROUTES_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        names.update(re.findall(r"\b(?:mgr|manager)\.(\w+)", source))
    return names


class DemoManagerCoverageTests(unittest.TestCase):
    def test_demo_manager_implements_everything_the_routes_use(self):
        manager = DemoManager()
        missing = sorted(name for name in _manager_names_used_by_routes() if not hasattr(manager, name))
        self.assertEqual(missing, [])

    def test_demo_manager_has_no_catch_all(self):
        # Sem __getattr__: um método novo nas rotas precisa ser implementado de verdade no demo.
        self.assertNotIn("__getattr__", vars(DemoManager))


class DemoEntryPointTests(unittest.TestCase):
    def test_demo_flag_runs_only_the_web_panel_on_loopback(self):
        calls = []

        async def fake_run_web_only(manager, *, host, port=None):
            calls.append((manager, host, port))

        with (
            patch.object(panel_main, "run_web_only", fake_run_web_only),
            patch.object(panel_main.sys, "argv", ["panel.main", "--demo"]),
            patch.dict("os.environ", {"WEB_BIND_HOST": "0.0.0.0", "DEMO_PORT": "8095"}),
        ):
            panel_main.main()
        self.assertEqual(len(calls), 1)
        manager, host, port = calls[0]
        self.assertIsInstance(manager, DemoManager)
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8095)


class _ClientMixin:
    async def _client(self, manager) -> TestClient:
        app = create_web_app(manager=manager, bot=None)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        self.addAsyncCleanup(manager.shutdown)
        return client


class DemoDisabledTests(_ClientMixin, unittest.IsolatedAsyncioTestCase):
    async def test_real_manager_has_no_demo_routes_or_toolbar(self):
        client = await self._client(SelfbotManager())
        resp = await client.get("/__demo/entrar", allow_redirects=False)
        self.assertEqual(resp.status, 404)
        self.assertNotIn(SESSION_COOKIE_NAME, resp.cookies)
        html = await (await client.get("/")).text()
        self.assertNotIn("demo-toolbar", html)
        state = await client.get("/api/demo/state")
        self.assertIn(state.status, (401, 404))


class DemoFlowTests(_ClientMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = DemoManager(step_delay=0)
        self.client = await self._client(self.manager)
        resp = await self.client.get("/__demo/entrar", allow_redirects=False)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers["Location"], "/")
        session = await (await self.client.get("/api/auth/session")).json()
        self.assertTrue(session["authenticated"])
        self.assertTrue(session["worker_connected"])
        self.assertEqual(session["user"]["id"], str(DEMO_USER_ID))
        self.csrf = {"X-CSRF-Token": session["csrf_token"]}

    async def _get(self, path):
        resp = await self.client.get(path)
        self.assertEqual(resp.status, 200, path)
        return await resp.json()

    async def _set_state(self, **state):
        resp = await self.client.post("/api/demo/state", json=state, headers=self.csrf)
        self.assertEqual(resp.status, 200)
        return await resp.json()

    async def test_page_shows_the_demo_toolbar(self):
        html = await (await self.client.get("/")).text()
        self.assertIn('id="demo-toolbar"', html)

    async def test_every_screen_has_realistic_data(self):
        social = await self._get("/api/social/overview?tab=friends")
        self.assertGreater(social["stats"]["friends"], 10)
        friend = social["data"]["items"][0]
        self.assertTrue({"id", "name", "status"} <= set(friend))
        pending = await self._get("/api/social/overview?tab=pending")
        self.assertTrue(pending["data"]["incoming"])
        blocked = await self._get("/api/social/overview?tab=blocked")
        self.assertTrue(blocked["data"]["items"])

        guilds = await self._get("/api/guilds")
        self.assertTrue({"id", "name", "owner"} <= set(guilds["data"]["items"][0]))

        dms = (await self._get("/api/cleanup/dms"))["items"]
        self.assertTrue({"dm", "group"} <= {dm["type"] for dm in dms})
        self.assertTrue(all("last_activity_ms" in dm for dm in dms))

        profile = await self._get("/api/account/profile")
        self.assertTrue(profile["ok"])
        self.assertTrue(profile["profile"]["display_name"])

        self.assertTrue((await self._get("/api/quests"))["quests"])
        self.assertTrue((await self._get("/api/account/sessions"))["sessions"])
        self.assertTrue((await self._get("/api/account/mentions"))["mentions"])
        self.assertGreater((await self._get("/api/account/unread"))["channels"], 0)
        tracker = await self._get("/api/tracker/status")
        self.assertTrue(tracker["enabled"])
        self.assertTrue(tracker["targets"][0]["user"]["display_name"])
        self.assertTrue((await self._get("/api/voice/status"))["ok"])
        rich = await self._get("/api/presence/rich")
        self.assertTrue(rich["ok"] and rich["default"] and rich["seed"])
        self.assertTrue((await self._get("/api/account/status"))["ok"])

    async def test_actions_change_the_demo_data(self):
        dms = (await self._get("/api/cleanup/dms"))["items"]
        target = next(dm for dm in dms if dm["type"] == "dm")
        resp = await self.client.post(
            "/api/cleanup/dm-action", json={"action": "close", "channel_id": target["id"]}, headers=self.csrf
        )
        self.assertEqual(resp.status, 200)
        after = (await self._get("/api/cleanup/dms"))["items"]
        self.assertNotIn(target["id"], [dm["id"] for dm in after])

        rich = await self.client.post("/api/presence/rich", json={"enabled": False, "custom": None}, headers=self.csrf)
        self.assertTrue((await rich.json())["ok"])
        self.assertFalse((await self._get("/api/presence/rich"))["enabled"])

    async def test_bulk_job_reports_progress_and_finishes(self):
        resp = await self.client.post("/api/social/action", json={"action": "remove_all_friends"}, headers=self.csrf)
        self.assertTrue((await resp.json())["ok"])
        jobs = self.client.app[JOB_STATE_KEY]
        for _ in range(200):
            if not jobs[DEMO_USER_ID]["running"]:
                break
            await asyncio.sleep(0.01)
        job = jobs[DEMO_USER_ID]
        self.assertFalse(job["running"])
        self.assertEqual(job["status"], "COMPLETADO")
        self.assertGreater(job["total"], 0)

    async def test_empty_mode_empties_every_list(self):
        state = await self._set_state(mode="empty")
        self.assertEqual(state["mode"], "empty")
        social = await self._get("/api/social/overview?tab=friends")
        self.assertEqual(social["data"]["items"], [])
        self.assertEqual(social["stats"]["friends"], 0)
        self.assertEqual((await self._get("/api/cleanup/dms"))["items"], [])
        self.assertEqual((await self._get("/api/guilds"))["data"]["items"], [])
        self.assertEqual((await self._get("/api/quests"))["quests"], [])
        self.assertEqual((await self._get("/api/account/mentions"))["mentions"], [])
        self.assertEqual((await self._get("/api/account/unread"))["channels"], 0)

    async def test_error_mode_makes_worker_calls_fail(self):
        await self._set_state(mode="error")
        for path in ("/api/account/unread", "/api/quests", "/api/presence/rich", "/api/tracker/status"):
            data = await self._get(path)
            self.assertFalse(data["ok"], path)
            self.assertIn("simulado", data["message"].lower())
        # Como no login real, o perfil cai no que o login leu do Discord.
        profile = await self._get("/api/account/profile")
        self.assertEqual(profile["code"], "PROFILE")
        self.assertEqual(profile["profile"]["display_name"], "Madman Demo")

    async def test_latency_delays_worker_calls(self):
        await self._set_state(latency_ms=150)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await self._get("/api/account/unread")
        self.assertGreaterEqual(loop.time() - start, 0.14)

    async def test_disconnected_account_shows_the_connect_screen(self):
        await self._set_state(connected=False)
        session = await self._get("/api/auth/session")
        self.assertFalse(session["worker_connected"])
        status = await self._get("/api/worker/status")
        self.assertFalse(status["connected"])

    async def test_invalid_state_is_rejected(self):
        for body in ({"mode": "chaos"}, {"latency_ms": -1}, {"latency_ms": 60000}, {"connected": "sim"}):
            resp = await self.client.post("/api/demo/state", json=body, headers=self.csrf)
            self.assertEqual(resp.status, 400, body)

    async def test_state_change_needs_csrf_and_same_origin(self):
        no_csrf = await self.client.post("/api/demo/state", json={"mode": "empty"})
        self.assertEqual(no_csrf.status, 403)
        cross = await self.client.post(
            "/api/demo/state", json={"mode": "empty"}, headers={**self.csrf, "Origin": "https://evil.example"}
        )
        self.assertEqual(cross.status, 403)
        self.assertEqual(self.manager.state.mode, "normal")


if __name__ == "__main__":
    unittest.main()
