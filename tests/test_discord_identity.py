from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from panel.web.discord_identity import identity_profile
from panel.web.routes.common import _with_login_identity

AVATAR = "a_" + "1" * 30
BANNER = "b" * 32
ME = {
    "id": "123456789012345678",
    "username": "conta",
    "global_name": "Conta",
    "avatar": AVATAR + "ab",
    "banner": BANNER,
    "accent_color": 0x5865F2,
    "avatar_decoration_data": {"asset": "a_" + "c" * 32, "sku_id": "1"},
    "public_flags": (1 << 6) | (1 << 22) | (1 << 9),
    "premium_type": 2,
    "primary_guild": {"identity_guild_id": "1", "identity_enabled": True, "tag": "MAD", "badge": "x"},
    "mfa_enabled": True,
    "locale": "pt-BR",
}


class IdentityProfileTests(unittest.TestCase):
    def test_maps_the_users_me_fields_to_the_panel_profile(self):
        p = identity_profile(ME)

        self.assertEqual(p["id"], ME["id"])
        self.assertEqual((p["username"], p["display_name"]), ("conta", "Conta"))
        self.assertEqual(p["avatar_url"], f"https://cdn.discordapp.com/avatars/{ME['id']}/{ME['avatar']}.gif?size=128")
        self.assertEqual(p["banner_url"], f"https://cdn.discordapp.com/banners/{ME['id']}/{BANNER}.png?size=480")
        self.assertTrue(p["decoration_url"].startswith("https://cdn.discordapp.com/avatar-decoration-presets/a_"))
        self.assertEqual(p["accent"], "#5865f2")
        self.assertEqual(p["guild_tag"], "MAD")
        self.assertFalse(p["complete"])
        self.assertNotIn("locale", p)
        self.assertNotIn("mfa_enabled", p)

    def test_badges_follow_the_flags_and_nitro_in_profile_order(self):
        names = [b["description"] for b in identity_profile(ME)["badges"]]
        self.assertEqual(names, ["HypeSquad Bravery", "Desenvolvedor ativo", "Assinante Nitro", "Apoiador inicial"])
        for badge in identity_profile(ME)["badges"]:
            self.assertTrue(badge["url"].startswith("https://cdn.discordapp.com/badge-icons/"))

    def test_plain_account_gets_default_avatar_and_nothing_made_up(self):
        p = identity_profile({"id": "123456789012345678", "username": "simples", "avatar": None})

        index = (123456789012345678 >> 22) % 6
        self.assertEqual(p["avatar_url"], f"https://cdn.discordapp.com/embed/avatars/{index}.png")
        self.assertEqual(p["badges"], [])
        self.assertIsNone(p["banner_url"])
        self.assertIsNone(p["decoration_url"])
        self.assertIsNone(p["guild_tag"])

    def test_rejects_bad_hashes_and_hidden_tags(self):
        p = identity_profile(
            {
                "id": "1",
                "avatar": "../../evil",
                "banner": "javascript:alert(1)",
                "primary_guild": {"identity_enabled": False, "tag": "OFF"},
                "accent_color": True,
            }
        )
        self.assertTrue(p["avatar_url"].startswith("https://cdn.discordapp.com/embed/avatars/"))
        self.assertIsNone(p["banner_url"])
        self.assertIsNone(p["guild_tag"])
        self.assertIsNone(p["accent"])

    def test_without_an_id_there_is_no_profile(self):
        self.assertIsNone(identity_profile({"username": "x"}))


class LoginIdentityMergeTests(unittest.TestCase):
    def setUp(self):
        self.identity = identity_profile(ME)

    def test_disconnected_account_uses_the_login_profile(self):
        result = _with_login_identity({"ok": False, "code": "NOT_CONNECTED"}, self.identity)

        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"]["banner_url"], self.identity["banner_url"])
        self.assertEqual(result["devices"], [])
        self.assertIsNone(result["status"])

    def test_complete_worker_profile_wins(self):
        worker = {"ok": True, "profile": {"complete": True, "badges": [{"description": "Boost", "url": "u"}]}}
        self.assertIs(_with_login_identity(worker, self.identity), worker)

    def test_incomplete_worker_profile_is_filled_by_the_login(self):
        worker = {"ok": True, "status": "idle", "profile": {"username": "novo", "badges": [], "complete": False}}

        merged = _with_login_identity(worker, self.identity)

        self.assertEqual(merged["profile"]["username"], "novo")
        self.assertEqual(len(merged["profile"]["badges"]), 4)
        self.assertEqual(merged["status"], "idle")

    def test_no_login_identity_keeps_the_worker_answer(self):
        result = {"ok": False, "code": "NOT_CONNECTED"}
        self.assertIs(_with_login_identity(result, None), result)


class ProfileRouteTests(unittest.IsolatedAsyncioTestCase):
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
        cookie, _ = self.app[SESSION_STORE_KEY].create_session(
            user_id=int(ME["id"]), username="Conta", identity=identity_profile(ME)
        )
        self.cookies = {SESSION_COOKIE_NAME: cookie}

    async def test_profile_without_the_account_connected_comes_from_the_login(self):
        self.manager.account_profile = AsyncMock(return_value={"ok": False, "code": "NOT_CONNECTED"})

        resp = await self.client.get("/api/account/profile", cookies=self.cookies)
        data = await resp.json()

        self.assertEqual(resp.status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["profile"]["guild_tag"], "MAD")
        self.assertEqual(len(data["profile"]["badges"]), 4)


if __name__ == "__main__":
    unittest.main()
