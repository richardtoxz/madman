"""Reforços para o painel público: IP real atrás de proxy, CSP, limite de WebSockets e logs de segurança."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from engine.backend import SelfbotManager
from panel import main as panel_main
from panel.main import create_web_app
from panel.web import security
from panel.web.routes import SESSION_STORE_KEY
from panel.web.security import OAUTH_STATE_COOKIE_NAME, SESSION_COOKIE_NAME, get_client_ip
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

OAUTH_ENV = {
    "DISCORD_CLIENT_ID": "cid",
    "DISCORD_CLIENT_SECRET": "sec",
    "DISCORD_REDIRECT_URI": "https://painel.example/api/auth/discord/callback",
}
REAL_IP = "203.0.113.7"


class ClientIpTests(unittest.IsolatedAsyncioTestCase):
    """O cliente de teste conecta de 127.0.0.1, um proxy confiável por padrão (como o cloudflared)."""

    async def asyncSetUp(self):
        app = web.Application()

        async def whoami(request):
            return web.json_response({"ip": get_client_ip(request)})

        app.router.add_get("/ip", whoami)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def _ip(self, **headers) -> str:
        return (await (await self.client.get("/ip", headers=headers)).json())["ip"]

    async def test_forged_first_hop_is_ignored(self):
        # A Cloudflare acrescenta o IP real ao FIM do que o cliente mandou.
        self.assertEqual(await self._ip(**{"X-Forwarded-For": f"10.9.9.9, {REAL_IP}"}), REAL_IP)

    async def test_trusted_proxies_on_the_right_are_skipped(self):
        self.assertEqual(await self._ip(**{"X-Forwarded-For": f"10.9.9.9, {REAL_IP}, 127.0.0.1"}), REAL_IP)

    async def test_cloudflare_connecting_ip_wins(self):
        headers = {"X-Forwarded-For": "10.9.9.9, 198.51.100.1", "CF-Connecting-IP": REAL_IP}
        self.assertEqual(await self._ip(**headers), REAL_IP)

    async def test_invalid_rightmost_hop_falls_back_to_the_peer(self):
        self.assertEqual(await self._ip(**{"X-Forwarded-For": f"{REAL_IP}, nao-e-ip"}), "127.0.0.1")

    async def test_headers_from_an_untrusted_peer_are_ignored(self):
        with patch.dict(os.environ, {"TRUSTED_PROXY_IPS": "10.0.0.1"}):
            headers = {"X-Forwarded-For": REAL_IP, "CF-Connecting-IP": REAL_IP}
            self.assertEqual(await self._ip(**headers), "127.0.0.1")

    async def test_cidr_range_marks_the_peer_as_trusted(self):
        with patch.dict(os.environ, {"TRUSTED_PROXY_IPS": "127.0.0.0/8"}):
            self.assertEqual(await self._ip(**{"X-Forwarded-For": REAL_IP}), REAL_IP)

    async def test_cidr_range_skips_internal_proxy_hops(self):
        # No Render o proxy interno conecta de um IP privado que muda a cada deploy.
        with patch.dict(os.environ, {"TRUSTED_PROXY_IPS": "127.0.0.1,10.0.0.0/8"}):
            self.assertEqual(await self._ip(**{"X-Forwarded-For": f"{REAL_IP}, 10.214.3.9"}), REAL_IP)

    async def test_invalid_entries_in_the_trusted_list_are_ignored(self):
        with patch.dict(os.environ, {"TRUSTED_PROXY_IPS": "lixo, 127.0.0.0/8"}):
            self.assertEqual(await self._ip(**{"X-Forwarded-For": REAL_IP}), REAL_IP)


class SecureRequestBehindProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwarded_proto_from_a_cidr_proxy_counts_as_https(self):
        app = web.Application()

        async def secure(request):
            return web.json_response({"secure": security.is_secure_request(request)})

        app.router.add_get("/secure", secure)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        with patch.dict(os.environ, {"TRUSTED_PROXY_IPS": "127.0.0.0/8"}):
            os.environ.pop("WEB_FORCE_SECURE_COOKIE", None)
            resp = await client.get("/secure", headers={"X-Forwarded-Proto": "https"})
            self.assertTrue((await resp.json())["secure"])


class LoginRateLimitBehindProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_forged_forwarded_for_does_not_bypass_the_login_limit(self):
        with patch.dict(os.environ, OAUTH_ENV):
            client = TestClient(TestServer(create_web_app(manager=None, bot=None)))
            await client.start_server()
            self.addAsyncCleanup(client.close)
            statuses = []
            for i in range(20):
                resp = await client.get(
                    "/api/auth/discord/login",
                    allow_redirects=False,
                    headers={"Host": "painel.example", "X-Forwarded-For": f"10.9.0.{i}, {REAL_IP}"},
                )
                statuses.append(resp.status)
        self.assertEqual(statuses.count(302), 15)
        self.assertEqual(statuses.count(429), 5)


class _AppMixin:
    async def _client(self) -> TestClient:
        manager = SelfbotManager()
        client = TestClient(TestServer(create_web_app(manager=manager, bot=None)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        self.addAsyncCleanup(manager.shutdown)
        return client

    def _login(self, client: TestClient, user_id: int = 4242) -> dict:
        cookie, record = client.app[SESSION_STORE_KEY].create_session(user_id=user_id, username="U")
        client.session.cookie_jar.update_cookies({SESSION_COOKIE_NAME: cookie})
        return record


class CspTests(_AppMixin, unittest.IsolatedAsyncioTestCase):
    async def test_page_can_only_connect_back_to_its_own_origin(self):
        client = await self._client()
        csp = (await client.get("/")).headers["Content-Security-Policy"]
        directive = next(part.strip() for part in csp.split(";") if part.strip().startswith("connect-src"))
        self.assertEqual(directive, "connect-src 'self'")


class WebSocketLimitTests(_AppMixin, unittest.IsolatedAsyncioTestCase):
    async def test_extra_socket_closes_the_oldest_one(self):
        client = await self._client()
        self._login(client)
        limit = security.MAX_WS_PER_USER
        sockets = [await client.ws_connect("/api/ws") for _ in range(limit + 1)]
        self.addAsyncCleanup(lambda: [s.close() for s in sockets] and None)
        oldest = sockets[0]
        msg = await oldest.receive(timeout=2)
        self.assertEqual(oldest.close_code, security.WS_CLOSE_TOO_MANY)
        self.assertIsNotNone(msg)
        for ws in sockets[1:]:
            self.assertFalse(ws.closed)
        for ws in sockets:
            await ws.close()

    async def test_dashboard_does_not_reconnect_after_being_replaced(self):
        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        onclose = html[html.index("wsConnection.onclose") :]
        onclose = onclose[: onclose.index("};")]
        self.assertIn(str(security.WS_CLOSE_TOO_MANY), onclose)


class SecurityLogTests(_AppMixin, unittest.IsolatedAsyncioTestCase):
    async def test_csrf_and_origin_failures_are_logged_without_secrets(self):
        client = await self._client()
        record = self._login(client)
        with self.assertLogs("madman.security", "WARNING") as logs:
            await client.post("/api/settings", json={})
            await client.post(
                "/api/settings",
                json={},
                headers={"X-CSRF-Token": record["csrf_token"], "Origin": "https://evil.example"},
            )
        text = "\n".join(logs.output)
        self.assertIn("csrf_invalido", text)
        self.assertIn("origem_invalida", text)
        self.assertIn("usuario=4242", text)
        self.assertNotIn(record["csrf_token"], text)

    async def test_log_lines_cannot_be_forged_through_the_path(self):
        client = await self._client()
        self._login(client)
        with self.assertLogs("madman.security", "WARNING") as logs:
            await client.post("/api/x%0Aevento=falso%0D", json={})
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("\n", logs.output[0])
        self.assertNotIn("\r", logs.output[0])

    async def test_invalid_oauth_state_and_rate_limit_are_logged(self):
        with patch.dict(os.environ, OAUTH_ENV):
            client = await self._client()
            with self.assertLogs("madman.security", "WARNING") as logs:
                await client.get(
                    "/api/auth/discord/callback?state=x&code=y",
                    allow_redirects=False,
                    headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}=x.y"},
                )
                for _ in range(16):
                    await client.get(
                        "/api/auth/discord/login", allow_redirects=False, headers={"Host": "painel.example"}
                    )
        text = "\n".join(logs.output)
        self.assertIn("oauth_state_invalido", text)
        self.assertIn("limite_excedido", text)

    async def test_socket_limit_is_logged(self):
        client = await self._client()
        self._login(client)
        with self.assertLogs("madman.security", "WARNING") as logs:
            sockets = [await client.ws_connect("/api/ws") for _ in range(security.MAX_WS_PER_USER + 1)]
            await sockets[0].receive(timeout=2)
        for ws in sockets:
            await ws.close()
        self.assertIn("websockets_demais", "\n".join(logs.output))


class StartupWarningTests(unittest.TestCase):
    def test_missing_session_secret_is_reported(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEB_SESSION_SECRET", None)
            with self.assertLogs("madman.security", "WARNING") as logs:
                panel_main.warn_insecure_config()
        self.assertIn("WEB_SESSION_SECRET", "\n".join(logs.output))

    def test_short_session_secret_is_reported_as_ignored(self):
        with patch.dict(os.environ, {"WEB_SESSION_SECRET": "curto-demais"}):
            with self.assertLogs("madman.security", "WARNING") as logs:
                panel_main.warn_insecure_config()
        text = "\n".join(logs.output)
        self.assertIn("32", text)
        self.assertNotIn("curto-demais", text)

    def test_good_secret_is_quiet(self):
        with patch.dict(os.environ, {"WEB_SESSION_SECRET": "x" * 40}):
            with self.assertNoLogs("madman.security", "WARNING"):
                panel_main.warn_insecure_config()


if __name__ == "__main__":
    unittest.main()
