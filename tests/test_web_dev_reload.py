from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.backend import SelfbotManager
from panel.main import create_web_app
from panel.web import ui
from aiohttp.test_utils import TestClient, TestServer


class DevReloadTests(unittest.IsolatedAsyncioTestCase):
    async def _client(self, env: dict[str, str]) -> TestClient:
        manager = SelfbotManager()
        with patch.dict("os.environ", env):
            app = create_web_app(manager=manager, bot=None)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        self.addAsyncCleanup(manager.shutdown)
        return client

    def test_snippet_is_only_injected_with_the_dev_flag(self):
        with patch.dict("os.environ", {"WEB_DEV_RELOAD": "0"}):
            self.assertNotIn("/__dev/version", ui.render_dashboard_html())
        with patch.dict("os.environ", {"WEB_DEV_RELOAD": "1"}):
            html = ui.render_dashboard_html()
        self.assertIn("/__dev/version", html)
        self.assertEqual(html.count("/__dev/version"), 1)
        self.assertLess(html.index("/__dev/version"), html.rindex("</body>"))

    async def test_version_route_does_not_exist_without_the_flag(self):
        client = await self._client({"WEB_DEV_RELOAD": "0"})
        resp = await client.get("/__dev/version")
        self.assertEqual(resp.status, 404)

    async def test_version_route_reports_a_stable_version_when_nothing_changes(self):
        client = await self._client({"WEB_DEV_RELOAD": "1"})
        with patch.dict("os.environ", {"WEB_DEV_RELOAD": "1"}):
            first = await (await client.get("/__dev/version")).json()
            second = await (await client.get("/__dev/version")).json()
        self.assertEqual(first, second)
        self.assertTrue(first["v"].isdigit())

    def test_version_changes_when_a_design_system_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bundle.css"
            source.write_text("a{}", encoding="utf-8")
            with (
                patch.object(ui, "_DESIGN_SYSTEM", Path(tmp)),
                patch.object(ui, "_rebuild_css_if_stale"),
            ):
                before = ui.dev_version()
                future = source.stat().st_mtime_ns + 5_000_000_000
                os.utime(source, ns=(future, future))
                after = ui.dev_version()
        self.assertNotEqual(before, after)

    def test_dev_rebuild_leaves_the_generated_css_matching_the_sources(self):
        spec = importlib.util.spec_from_file_location("_check_build_css", ui._BUILD_CSS_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict("os.environ", {"WEB_DEV_RELOAD": "1"}):
            ui.dev_version()
        self.assertEqual(ui._CSS_PATH.read_text(encoding="utf-8"), module.build())


if __name__ == "__main__":
    unittest.main()
