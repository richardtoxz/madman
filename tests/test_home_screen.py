from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from worker.account import commands as account_commands
from worker.core import commands as ipc_commands
from worker.account.manager import AccountManager


def _badge(description: str, icon: str):
    return SimpleNamespace(description=description, url=f"https://cdn.discordapp.com/badge-icons/{icon}.png")


class _ProfileClient:
    def __init__(self, *, badges=(), error: Exception | None = None):
        self.user = SimpleNamespace(id=42, name="conta", global_name="Conta", display_avatar=None)
        self.settings = SimpleNamespace(status=discord.Status.idle, custom_activity=None)
        self.badges = list(badges)
        self.error = error
        self.calls = []

    async def fetch_user_profile(self, user_id, **kwargs):
        self.calls.append((user_id, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(
            name="conta",
            global_name="Conta",
            display_avatar=None,
            avatar_decoration=None,
            display_name_style=None,
            primary_guild=None,
            banner=None,
            accent_colour=None,
            metadata=SimpleNamespace(theme_colors=None, pronouns="ela/dela"),
            bio="oi",
            badges=self.badges,
        )


class AccountProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_real_badges_of_the_logged_account(self):
        client = _ProfileClient(badges=[_badge("Nitro", "nitro"), _badge("HypeSquad Bravery", "bravery")])
        manager = AccountManager(client)

        result = await manager.get_profile()

        self.assertTrue(result["ok"])
        self.assertEqual(
            [b["description"] for b in result["profile"]["badges"]],
            ["Nitro", "HypeSquad Bravery"],
        )
        self.assertTrue(result["profile"]["complete"])
        self.assertEqual(result["status"], "idle")
        self.assertEqual(client.calls[0][0], 42)
        self.assertFalse(client.calls[0][1]["with_mutual_guilds"])

    async def test_profile_is_cached_until_refresh(self):
        client = _ProfileClient(badges=[_badge("Nitro", "nitro")])
        manager = AccountManager(client)

        await manager.get_profile()
        await manager.get_profile()
        self.assertEqual(len(client.calls), 1)

        await manager.get_profile(refresh=True)
        self.assertEqual(len(client.calls), 2)

    async def test_failed_fetch_shows_no_badges_instead_of_made_up_ones(self):
        manager = AccountManager(_ProfileClient(error=RuntimeError("offline")))

        result = await manager.get_profile()

        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"]["badges"], [])
        self.assertFalse(result["profile"]["complete"])

    async def test_failed_refresh_keeps_the_last_good_profile(self):
        client = _ProfileClient(badges=[_badge("Nitro", "nitro")])
        manager = AccountManager(client)
        await manager.get_profile()
        client.error = RuntimeError("offline")

        result = await manager.get_profile(refresh=True)

        self.assertEqual(len(result["profile"]["badges"]), 1)

    async def test_devices_skip_the_panel_session_and_keep_the_best_status(self):
        client = _ProfileClient()
        client._connection = SimpleNamespace(session_id="painel")

        def session(sid, client_type, status, os_name="windows"):
            return SimpleNamespace(session_id=sid, client=client_type, status=status, os=SimpleNamespace(name=os_name))

        client.sessions = [
            session("all", "desktop", "online"),
            session("painel", "web", "online"),
            session("pc", "desktop", "idle"),
            session("pc2", "desktop", "online"),
            session("cel", "mobile", "dnd", "android"),
            session("ps5", "unknown", "online", "playstation"),
            session("misterio", "unknown", "online", "unknown"),
        ]

        result = await AccountManager(client).get_profile()

        self.assertEqual(
            result["devices"],
            [
                {"device": "desktop", "status": "online"},
                {"device": "mobile", "status": "dnd"},
                {"device": "embedded", "status": "online"},
            ],
        )

    async def test_router_forwards_refresh_flag(self):
        manager = SimpleNamespace(get_profile=AsyncMock(return_value={"ok": True}))
        bot = SimpleNamespace(account_manager=manager)
        spawned = []
        with (
            patch.object(account_commands, "_account", return_value=manager),
            patch.object(ipc_commands, "_spawn_command", side_effect=lambda bot, coro: spawned.append(coro)),
            patch.object(ipc_commands, "_request_id", return_value=None),
            patch.object(ipc_commands, "emit_response"),
        ):
            await account_commands.handle_account_profile(bot, "ACCOUNT_PROFILE")
            await account_commands.handle_account_profile(bot, "ACCOUNT_PROFILE_REFRESH")
            for coro in spawned:
                await coro

        self.assertEqual(
            [call.kwargs["refresh"] for call in manager.get_profile.await_args_list],
            [False, True],
        )


class HomeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer

        from engine.backend import SelfbotManager
        from panel.main import create_web_app
        from panel.web.routes import SESSION_STORE_KEY
        from panel.web.security import SESSION_COOKIE_NAME

        self.manager = SelfbotManager()
        self.app = create_web_app(manager=self.manager, bot=None)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addAsyncCleanup(self.manager.shutdown)
        cookie, record = self.app[SESSION_STORE_KEY].create_session(user_id=1001, username="UserA")
        self.cookies = {SESSION_COOKIE_NAME: cookie}
        self.headers = {"X-CSRF-Token": record["csrf_token"]}

    async def test_logout_with_disconnect_stops_only_the_session_users_account(self):
        self.manager.disconnect = AsyncMock(return_value=(True, "ok"))

        resp = await self.client.post(
            "/api/auth/logout", json={"disconnect_worker": True}, headers=self.headers, cookies=self.cookies
        )

        self.assertEqual(resp.status, 200)
        self.manager.disconnect.assert_awaited_once_with(1001)
        after = await self.client.get("/api/account/profile", cookies=self.cookies)
        self.assertEqual(after.status, 401)

    async def test_profile_requires_login(self):
        resp = await self.client.get("/api/account/profile")
        self.assertEqual(resp.status, 401)

    async def test_profile_is_for_the_session_user_and_forwards_refresh(self):
        self.manager.account_profile = AsyncMock(return_value={"ok": True, "profile": {"badges": []}})

        await self.client.get("/api/account/profile?refresh=1", cookies=self.cookies)
        await self.client.get("/api/account/profile?user_id=2002", cookies=self.cookies)

        calls = self.manager.account_profile.await_args_list
        self.assertEqual([c.args[0] for c in calls], [1001, 1001])
        self.assertEqual([c.kwargs["refresh"] for c in calls], [True, False])

    async def test_home_path_is_served(self):
        resp = await self.client.get("/inicio")
        self.assertEqual(resp.status, 200)


class HomePageMarkupTests(unittest.TestCase):
    def setUp(self):
        from panel.web.ui import render_dashboard_html

        self.html = render_dashboard_html()

    def test_home_is_the_default_tab_and_cleanup_starts_hidden(self):
        self.assertIn('const DEFAULT_TAB = "home";', self.html)
        self.assertRegex(self.html, r'id="tab-home" class="tab-panel stack"')
        self.assertRegex(self.html, r'id="tab-cleanup" class="tab-panel stack hidden"')

    def test_madman_in_the_header_opens_home(self):
        self.assertIn('<a href="/inicio" class="route-parent route-home" data-tab="home"', self.html)

    def test_dock_badges_are_not_hardcoded(self):
        dock = re.search(r'id="dock-badges"[^>]*>(.*?)</span>', self.html, re.S)
        self.assertIsNotNone(dock)
        self.assertEqual(dock.group(1).strip(), "")
        self.assertNotIn('title="Nitro"', self.html)
        self.assertNotIn('title="HypeSquad"', self.html)

    def test_losing_the_connection_goes_back_home(self):
        self.assertIn("wasConnected === true && !workerConnected", self.html)
        self.assertIn('switchTab("home")', self.html)

    def test_logout_lives_in_the_account_menu_and_disconnects_the_account(self):
        self.assertNotIn('id="logout-btn"', self.html)
        self.assertIn(
            'id="account-menu-btn" class="dock-account" aria-haspopup="menu" aria-expanded="false"', self.html
        )
        self.assertIn('data-menu-action="logout"', self.html)
        self.assertIn("body: { disconnect_worker: true }", self.html)
        self.assertNotIn("Sair do painel?", self.html)
        self.assertNotIn("account-chevron", self.html)

    def test_account_menu_offers_every_status_and_copy_id(self):
        for status in ("online", "idle", "dnd", "invisible"):
            self.assertIn(f'data-menu-action="set-status" data-status="{status}"', self.html)
        for action in ("status-toggle", "status-message", "connect", "account", "copy-id"):
            self.assertIn(f'data-menu-action="{action}"', self.html)
        # A troca de status usa a rota que já valida e só aceita os quatro valores.
        self.assertIn('api("/api/account/status", { method: "POST", body: { status } })', self.html)

    def test_home_has_reconnect_form_and_live_sections(self):
        for marker in ('id="home-connect"', 'id="home-token-input"', 'id="home-attention"', 'id="home-activity"'):
            self.assertIn(marker, self.html)


if __name__ == "__main__":
    unittest.main()
