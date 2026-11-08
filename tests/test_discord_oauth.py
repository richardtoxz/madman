from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from panel.web.routes import auth as routes

BASE_ENV = {
    "DISCORD_CLIENT_ID": "123",
    "DISCORD_CLIENT_SECRET": "segredo",
    "DISCORD_REDIRECT_URI": "http://localhost:8080/api/auth/discord/callback",
    "TOKEN_BOT_CONTROLE": "bot-token",
}


class _Resp:
    def __init__(self, status: int):
        self.status = status


class _Http:
    def __init__(self, status: int = 201, error: Exception | None = None):
        self.status = status
        self.error = error
        self.calls = []

    async def put(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return _Resp(self.status)


class OAuthConfigTests(unittest.TestCase):
    def test_only_identify_without_a_target_server(self):
        with patch.dict(os.environ, {**BASE_ENV, "DISCORD_JOIN_GUILD_ID": ""}, clear=True):
            cfg = routes._discord_oauth_config()
        self.assertEqual(cfg["scope"], "identify")
        self.assertNotIn("join_guild_id", cfg)

    def test_asks_guilds_join_when_a_server_is_set(self):
        with patch.dict(os.environ, {**BASE_ENV, "DISCORD_JOIN_GUILD_ID": "999"}, clear=True):
            cfg = routes._discord_oauth_config()
        self.assertEqual(cfg["scope"], "identify guilds.join")
        self.assertEqual(cfg["join_guild_id"], "999")

    def test_invalid_server_or_missing_bot_keeps_identify(self):
        for extra in ({"DISCORD_JOIN_GUILD_ID": "abc"}, {"DISCORD_JOIN_GUILD_ID": "999", "TOKEN_BOT_CONTROLE": ""}):
            with patch.dict(os.environ, {**BASE_ENV, **extra}, clear=True):
                self.assertEqual(routes._discord_oauth_config()["scope"], "identify")


class JoinGuildTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = {"join_guild_id": "999", "bot_token": "bot-token"}
        self.token = {"access_token": "acesso", "scope": "identify guilds.join"}

    async def test_adds_the_user_with_the_bot_and_the_access_token(self):
        http = _Http(201)

        joined = await routes._join_configured_guild(http, self.cfg, 42, self.token)

        self.assertTrue(joined)
        url, kwargs = http.calls[0]
        self.assertEqual(url, "https://discord.com/api/v10/guilds/999/members/42")
        self.assertEqual(kwargs["json"], {"access_token": "acesso"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot bot-token")

    async def test_already_a_member_is_not_reported_as_joined(self):
        self.assertFalse(await routes._join_configured_guild(_Http(204), self.cfg, 42, self.token))

    async def test_skips_when_the_user_did_not_grant_guilds_join(self):
        http = _Http(201)
        token = {"access_token": "acesso", "scope": "identify"}

        self.assertFalse(await routes._join_configured_guild(http, self.cfg, 42, token))
        self.assertEqual(http.calls, [])

    async def test_failures_do_not_break_the_login(self):
        self.assertFalse(await routes._join_configured_guild(_Http(403), self.cfg, 42, self.token))
        self.assertFalse(await routes._join_configured_guild(_Http(error=OSError("rede")), self.cfg, 42, self.token))


class LoginRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer

        from panel.main import create_web_app

        self.client = TestClient(TestServer(create_web_app(manager=None, bot=None)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def _env(self, redirect_uri: str) -> dict[str, str]:
        return {**BASE_ENV, "DISCORD_JOIN_GUILD_ID": "999", "DISCORD_REDIRECT_URI": redirect_uri}

    async def test_login_url_carries_the_configured_scopes(self):
        redirect_uri = str(self.client.make_url("/api/auth/discord/callback"))
        with patch.dict(os.environ, self._env(redirect_uri), clear=True):
            resp = await self.client.get("/api/auth/discord/login", allow_redirects=False)

        self.assertEqual(resp.status, 302)
        location = urlparse(resp.headers["Location"])
        self.assertEqual(location.netloc, "discord.com")
        query = parse_qs(location.query)
        self.assertEqual(query["scope"], ["identify guilds.join"])
        self.assertEqual(query["redirect_uri"], [redirect_uri])
        self.assertIn("madman_oauth_state", resp.cookies)

    async def test_login_from_another_local_address_restarts_on_the_redirect_address(self):
        # O painel aberto em 127.0.0.1 com redirect em localhost perderia o cookie de estado.
        with patch.dict(os.environ, self._env("http://localhost:8080/api/auth/discord/callback"), clear=True):
            resp = await self.client.get("/api/auth/discord/login", allow_redirects=False)

        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.headers["Location"], "http://localhost:8080/api/auth/discord/login")
        self.assertNotIn("madman_oauth_state", resp.cookies)

    async def test_public_redirect_never_bounces(self):
        with patch.dict(os.environ, self._env("https://painel.exemplo.com/api/auth/discord/callback"), clear=True):
            resp = await self.client.get("/api/auth/discord/login", allow_redirects=False)

        self.assertEqual(urlparse(resp.headers["Location"]).netloc, "discord.com")


class _JsonResp:
    def __init__(self, status: int, data: dict):
        self.status = status
        self._data = data

    async def json(self):
        return self._data


class _DiscordApi:
    """Discord falso para o callback: troca o code e devolve o /users/@me do usuário dado."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        return _JsonResp(200, {"access_token": "acesso", "scope": "identify"})

    async def get(self, url, **kwargs):
        return _JsonResp(200, {"id": self.user_id, "username": "alguem"})


class AllowedUsersLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer

        from panel.main import create_web_app

        self.client = TestClient(TestServer(create_web_app(manager=None, bot=None)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def _login_as(self, user_id: str):
        env = {
            **BASE_ENV,
            "DISCORD_REDIRECT_URI": "https://painel.exemplo.com/api/auth/discord/callback",
            "ALLOWED_USER_IDS": "111",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(routes.aiohttp, "ClientSession", _DiscordApi(user_id)),
        ):
            login = await self.client.get("/api/auth/discord/login", allow_redirects=False)
            state = parse_qs(urlparse(login.headers["Location"]).query)["state"][0]
            return await self.client.get(f"/api/auth/discord/callback?state={state}&code=abc", allow_redirects=False)

    async def test_user_outside_the_list_gets_no_session(self):
        resp = await self._login_as("333")

        self.assertEqual(resp.status, 403)
        self.assertEqual((await resp.json())["code"], "NOT_ALLOWED")
        self.assertNotIn("madman_session", resp.cookies)

    async def test_listed_user_logs_in(self):
        resp = await self._login_as("111")

        self.assertEqual(resp.status, 302)
        self.assertIn("madman_session", resp.cookies)


if __name__ == "__main__":
    unittest.main()
