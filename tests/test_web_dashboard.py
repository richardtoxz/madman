from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine.backend import SelfbotManager
from panel.main import create_web_app
from panel.web.routes import SESSION_STORE_KEY
from panel.web.routes import auth as routes_auth
from panel.web.security import SESSION_COOKIE_NAME
from aiohttp.test_utils import TestClient, TestServer


class WebDashboardSecurityAndFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = SelfbotManager()
        self.app = create_web_app(manager=self.manager, bot=None)
        self.server = TestServer(self.app)
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addAsyncCleanup(self.manager.shutdown)

    async def test_dashboard_html_renders_easyui_components_and_security_headers(self):
        resp = await self.client.get("/")
        self.assertEqual(resp.status, 200)
        html = await resp.text()
        self.assertIn("Madman Bot is running!", html)
        self.assertIn("pill-nav", html)
        self.assertIn("spotlight-card", html)
        self.assertIn("lock-input", html)
        self.assertIn("liquid-toggle", html)
        self.assertIn('class="skip-link"', html)
        self.assertIn("color-scheme: dark", html)
        self.assertIn("prefers-reduced-motion: reduce", html)
        self.assertIn('role="alertdialog"', html)
        self.assertIn("chip-builder", html)
        self.assertNotIn("transition: all", html)
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("frame-ancestors 'none'", resp.headers.get("Content-Security-Policy", ""))

    async def test_each_tab_path_serves_the_same_page_with_security_headers(self):
        from panel.web.ui import TAB_PATHS, render_dashboard_html

        expected = render_dashboard_html()
        for path in TAB_PATHS.values():
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 200, path)
            self.assertEqual(await resp.text(), expected, path)
            self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY", path)
            self.assertIn("frame-ancestors 'none'", resp.headers.get("Content-Security-Policy", ""), path)
            self.assertNotIn("Set-Cookie", resp.headers, path)

    async def test_unknown_paths_are_not_served_the_dashboard(self):
        for path in ("/limpeza/extra", "/%2e%2e/etc/passwd", "/acompanhar.html", "/api/tracker"):
            resp = await self.client.get(path)
            self.assertIn(resp.status, (404, 405), path)

    async def test_dock_links_match_server_paths_and_mark_one_current_tab(self):
        import re
        from panel.web.ui import TAB_PATHS, render_dashboard_html

        html = render_dashboard_html()
        links = re.findall(
            r'<a href="([^"]+)" class="(?:dock-link|route-parent route-home)[^"]*" data-tab="([^"]+)"', html
        )
        self.assertEqual({tab: path for path, tab in links}, TAB_PATHS)
        # O Início abre pelo nome "Madman" no topo; o dock continua só com as ferramentas.
        dock_tabs = set(re.findall(r'class="dock-link[^"]*" data-tab="([^"]+)"', html))
        self.assertNotIn("home", dock_tabs)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertNotIn("__TAB_PATHS__", html)

    async def test_client_escaping_covers_attribute_quotes_and_inline_handlers(self):
        import re
        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        # Nomes, bios e atividades vêm de terceiros e entram em atributos entre aspas.
        esc_body = html[html.index("const ESC_MAP") : html.index("function safeId")]
        for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
            self.assertIn(entity, esc_body)
        # IDs em handlers inline passam por safeId; esc() não protege strings JS dentro de onclick.
        unsafe = re.findall(r"on(?:click|change)=\"[^\"]*\('\$\{esc\(", html)
        self.assertEqual(unsafe, [])

    def test_login_screen_only_offers_discord_oauth(self):
        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        login = html[html.index('id="login-view"') : html.index('id="main-stage"')]
        self.assertIn('href="/api/auth/discord/login"', login)
        self.assertNotIn("<input", login)
        self.assertNotIn("login-token-input", html)
        self.assertNotIn("token-login", html)

    def test_account_code_fields_have_paste_button_and_are_not_saved_by_password_managers(self):
        import re

        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        for input_id in ("home-token-input", "worker-token-input"):
            with self.subTest(input_id=input_id):
                field = re.search(r'<input[^>]*id="%s"[^>]*>' % input_id, html).group(0)
                self.assertIn('autocomplete="off"', field)
                self.assertNotIn("name=", field)
                self.assertIn('data-paste-for="%s"' % input_id, html)
                self.assertIn('data-token-form="%s"' % input_id, html)
        for marker in ("PasswordCredential", "current-password", 'autocomplete="username"', "data-token-username"):
            self.assertNotIn(marker, html)
        self.assertEqual(re.findall(r"<form[^>]*data-token-form", html), [])
        self.assertIn("function cleanToken(", html)
        self.assertIn("navigator.clipboard.readText", html)

    def test_skeleton_styles_exist_and_stop_with_reduced_motion(self):
        import re

        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        for cls in (".skeleton", ".sk-line", ".sk-circle", ".sk-row"):
            self.assertIn(cls, html)
        self.assertIn("@keyframes skeleton-shimmer", html)
        reduced = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", html, re.S)
        self.assertTrue(any(re.search(r"\.skeleton[^{]*\{[^}]*animation: none", block) for block in reduced))

    def test_app_opens_on_a_skeleton_instead_of_flashing_the_login(self):
        import re

        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        login = re.search(r'<section id="login-view" class="([^"]*)"', html).group(1)
        self.assertIn("hidden", login.split())
        boot = re.search(r'<div id="boot-view"[^>]*>', html).group(0)
        self.assertIn('aria-busy="true"', boot)
        self.assertNotIn("hidden", boot)
        body = html[html.index("async function bootstrapSession()") :]
        body = body[: body.index("\n    }\n")]
        self.assertIn('getElementById("boot-view")', body)
        for container in ("home-profile", "home-attention"):
            start = html.index(f'id="{container}"')
            self.assertIn("skeleton", html[start : html.index("</div>", start) + 400], container)
        self.assertNotIn(">Carregando…</p>", html)

    def test_list_loaders_show_skeleton_and_retry_on_failure(self):
        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        self.assertIn("function showSkeleton(", html)
        self.assertIn("function showLoadError(", html)

        def body(name):
            start = html.index(f"async function {name}(")
            return html[start : html.index("\n    }\n", start)]

        for name in (
            "loadOpenDms",
            "loadSocial",
            "loadGuilds",
            "loadTrackerState",
            "loadQuests",
            "loadSessions",
            "loadMentions",
        ):
            self.assertIn("showSkeleton(", body(name), name)
        for name in ("loadOpenDms", "loadSocial", "loadGuilds", "loadTrackerState", "loadQuests"):
            self.assertIn("showLoadError(", body(name), name)

    def test_privacy_tab_has_rich_presence_card_and_modal(self):
        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        privacy = html[html.index('id="tab-protection"') : html.index('id="tab-social"')]
        for element_id in ("rich-presence-card", "rich-presence-toggle-btn", "rich-presence-edit-btn"):
            self.assertIn(f'id="{element_id}"', privacy)
        for element_id in (
            "rich-presence-backdrop",
            "rp-preview-now",
            "rp-preview-next",
            "rp-changed-count",
            "rp-form",
            "rp-member-line-now",
            "rp-member-line-next",
            "rp-status-display",
            "rp-platform",
            "rp-stream-url",
            "rp-duration",
            "rp-details-url",
            "rp-state-url",
            "rp-large-url",
            "rp-small-url",
            "rp-party-current",
            "rp-party-max",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for function in (
            "openRichPresenceModal",
            "renderRichPreview",
            "saveRichPresence",
            "restoreRichPresenceDefault",
            "toggleRichPresence",
            "rpMemberListLine",
        ):
            self.assertIn(f"function {function}(", html)
        self.assertIn('"/api/presence/rich"', html)
        self.assertIn('<option value="streaming">Transmitindo</option>', html)
        self.assertIn('<option value="duration">', html)

    def test_dashboard_inlines_css_and_leaves_no_placeholders(self):
        from panel.web.ui import _load_css, render_dashboard_html

        html = render_dashboard_html()
        self.assertIn(_load_css(), html)
        self.assertNotIn("/*__MADMAN_CSS__*/", html)
        # Checkout com CRLF no Windows não pode vazar \r para a página servida.
        self.assertNotIn("\r", html)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))

    async def test_dev_reload_keeps_serving_the_dashboard(self):
        from panel.web.ui import render_dashboard_html

        with patch.dict("os.environ", {"WEB_DEV_RELOAD": "1"}):
            resp = await self.client.get("/")
            expected = render_dashboard_html()
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.text(), expected)

    async def test_session_enforces_csrf_and_same_origin(self):
        store = self.app[SESSION_STORE_KEY]
        signed_cookie, record = store.create_session(user_id=111222333, username="Alice")
        self.client.session.cookie_jar.update_cookies({SESSION_COOKIE_NAME: signed_cookie})
        csrf_token = record["csrf_token"]

        # Mutating call WITHOUT X-CSRF-Token must be blocked (403 CSRF_INVALID)
        no_csrf = await self.client.post(
            "/api/settings",
            json={"webhook_url": ""},
        )
        self.assertEqual(no_csrf.status, 403)
        self.assertEqual((await no_csrf.json())["code"], "CSRF_INVALID")

        # Mutating call WITH cross-site Origin must be blocked (403 ORIGIN_MISMATCH)
        bad_origin = await self.client.post(
            "/api/settings",
            json={"webhook_url": ""},
            headers={"X-CSRF-Token": csrf_token, "Origin": "https://attacker.example"},
        )
        self.assertEqual(bad_origin.status, 403)
        self.assertEqual((await bad_origin.json())["code"], "ORIGIN_MISMATCH")

        # Valid same-origin + valid X-CSRF-Token succeeds
        valid_webhook = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40
        self.manager.quest_config = AsyncMock(return_value={"ok": True})
        ok_save = await self.client.post(
            "/api/settings",
            json={"webhook_url": valid_webhook},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(ok_save.status, 200)
        self.assertEqual(self.manager.get_webhook_url(111222333), valid_webhook)
        self.assertIn("no-store", ok_save.headers.get("Cache-Control", ""))

    async def test_multi_tenant_isolation_ignores_spoofed_user_id_and_revokes_on_logout(self):
        store = self.app[SESSION_STORE_KEY]
        signed_cookie_a, record_a = store.create_session(user_id=1001, username="UserA")
        self.manager.set_user_settings(
            2002, webhook_url="https://discord.com/api/webhooks/222222222222222222/" + "b" * 40
        )

        # UserA tries to pass user_id=2002 in payload to overwrite UserB's whitelist
        resp = await self.client.post(
            "/api/protection/whitelist",
            json={"user_id": 2002, "target_id": 9999},
            headers={"X-CSRF-Token": record_a["csrf_token"]},
            cookies={SESSION_COOKIE_NAME: signed_cookie_a},
        )
        self.assertEqual(resp.status, 200)
        self.assertEqual(self.manager.get_whitelist(1001), [9999])
        self.assertEqual(self.manager.get_whitelist(2002), [])

        # Logout revokes the server-side session immediately
        logout = await self.client.post(
            "/api/auth/logout",
            json={},
            headers={"X-CSRF-Token": record_a["csrf_token"]},
            cookies={SESSION_COOKIE_NAME: signed_cookie_a},
        )
        self.assertEqual(logout.status, 200)

        replay = await self.client.get(
            "/api/worker/status",
            cookies={SESSION_COOKIE_NAME: signed_cookie_a},
        )
        self.assertEqual(replay.status, 401)

    async def test_two_phase_cleanup_and_websocket_live_progress(self):
        store = self.app[SESSION_STORE_KEY]
        signed_cookie, record = store.create_session(user_id=555, username="CleanerUser")

        self.manager.preview_clean = AsyncMock(
            return_value={
                "ok": True,
                "target_id": 777,
                "target_type": "text",
                "name": " #geral ",
                "estimated_total": 25,
            }
        )

        async def fake_execute(uid, target_id, *, guild=False, update_callback=None):
            if update_callback:
                await update_callback(10, 25, "CLEANING", "#geral")
            return "COMPLETADO:Deletadas 25 de 25."

        self.manager.execute_clean = fake_execute

        # 1. Preview
        prev_resp = await self.client.post(
            "/api/cleanup/preview",
            json={"target_id": "777", "guild": False},
            headers={"X-CSRF-Token": record["csrf_token"]},
            cookies={SESSION_COOKIE_NAME: signed_cookie},
        )
        self.assertEqual(prev_resp.status, 200)
        prev_data = await prev_resp.json()
        self.assertTrue(prev_data["ok"])
        self.assertEqual(prev_data["estimated_total"], 25)

        # 2. Connect WebSocket & Execute Cleanup
        async with self.client.ws_connect(
            "/api/ws",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={signed_cookie}"},
        ) as ws:
            exec_resp = await self.client.post(
                "/api/cleanup/execute",
                json={"mode": "channel", "target_id": "777"},
                headers={"X-CSRF-Token": record["csrf_token"]},
                cookies={SESSION_COOKIE_NAME: signed_cookie},
            )
            self.assertEqual(exec_resp.status, 200)

            received_types = []
            for _ in range(4):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                received_types.append(msg["type"])
                if msg["type"] == "job_result":
                    self.assertEqual(msg["data"]["result"]["deleted"], 25)
                    break

            self.assertIn("job_progress", received_types)
            self.assertIn("job_result", received_types)

    async def test_token_login_route_was_removed_and_never_opens_a_session(self):
        # Entrar no painel só pelo OAuth do Discord; o código da conta só é aceito já logado.
        resp = await self.client.post("/api/auth/token-login", json={"token": "x" * 45})
        self.assertIn(resp.status, (401, 403, 404, 405))
        self.assertNotIn(SESSION_COOKIE_NAME, resp.cookies)
        self.assertFalse(hasattr(routes_auth, "handle_token_login"))

    async def test_production_hardening_headers_proxy_health_and_input_validation(self):
        # 1. Unauthenticated /api/health does not leak active_sessions or bot_ready
        self.manager.active_processes[999] = SimpleNamespace(is_alive=True, close=AsyncMock())
        health_resp = await self.client.get("/api/health")
        self.assertEqual(health_resp.status, 200)
        health_data = await health_resp.json()
        self.assertEqual(health_data, {"ok": True, "service": "madman"})
        self.assertNotIn("active_sessions", health_data)

        # 2. Trusted proxy X-Forwarded-Proto: https sets HSTS & hardened headers
        https_resp = await self.client.get("/", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(https_resp.status, 200)
        self.assertIn("max-age=31536000", https_resp.headers.get("Strict-Transport-Security", ""))
        self.assertEqual(https_resp.headers.get("Cross-Origin-Opener-Policy"), "same-origin")
        self.assertIn("camera=()", https_resp.headers.get("Permissions-Policy", ""))

        # 3. Sec-Fetch-Site: cross-site is blocked even without Origin header
        store = self.app[SESSION_STORE_KEY]
        signed_cookie, record = store.create_session(user_id=707, username="SecUser")
        cross_site = await self.client.post(
            "/api/settings",
            json={"webhook_url": ""},
            headers={"X-CSRF-Token": record["csrf_token"], "Sec-Fetch-Site": "cross-site"},
            cookies={SESSION_COOKIE_NAME: signed_cookie},
        )
        self.assertEqual(cross_site.status, 403)
        self.assertEqual((await cross_site.json())["code"], "ORIGIN_MISMATCH")

        # 4. Malformed target_id returns 400 instead of 500
        bad_wl = await self.client.post(
            "/api/protection/whitelist",
            json={"target_id": "not_an_integer"},
            headers={"X-CSRF-Token": record["csrf_token"]},
            cookies={SESSION_COOKIE_NAME: signed_cookie},
        )
        self.assertEqual(bad_wl.status, 400)
        self.assertEqual((await bad_wl.json())["code"], "INVALID_TARGET_ID")

        # 5. Max active workers cap prevents OOM exhaustion
        self.manager.max_active_workers = 1
        ok, msg = await self.manager.authenticate(888, "z" * 45)
        self.assertFalse(ok)
        self.assertIn("Limite máximo", msg)

    async def test_snowflake_ids_serialized_as_strings_and_dock_tabs_rendered(self):
        resp = await self.client.get("/")
        html = await resp.text()
        for tab_id in (
            "tab-cleanup",
            "tab-protection",
            "tab-social",
            "tab-guilds",
            "tab-tracker",
            "tab-quests",
            "tab-history",
        ):
            self.assertIn(tab_id, html)
        self.assertIn("cmd-backdrop", html)

        store = self.app[SESSION_STORE_KEY]
        signed_cookie, _ = store.create_session(user_id=909, username="SnowflakeUser")

        huge_dm_id = 1234567890123456789
        huge_user_id = 1234567890123456790
        huge_guild_id = 1234567890123456791
        self.manager.list_open_dms = AsyncMock(
            return_value=[
                {
                    "id": huge_dm_id,
                    "type": "dm",
                    "name": "Fulano",
                    "user_id": huge_user_id,
                    "last_message_id": 1234567890123456792,
                    "unread": True,
                    "is_request": False,
                }
            ]
        )
        dms_resp = await self.client.get(
            "/api/cleanup/dms?refresh=1",
            cookies={SESSION_COOKIE_NAME: signed_cookie},
        )
        self.assertEqual(dms_resp.status, 200)
        self.manager.list_open_dms.assert_awaited_once_with(909, refresh=True)
        dms_data = await dms_resp.json()
        ch = dms_data["items"][0]
        self.assertIsInstance(ch["id"], str)
        self.assertEqual(ch["id"], str(huge_dm_id))
        self.assertEqual(ch["user_id"], str(huge_user_id))
        self.assertEqual(ch["last_message_id"], "1234567890123456792")

        self.manager.get_guild_stats = AsyncMock(
            return_value={"total": 1, "owners": 0, "muted": 0, "guild_ids": [huge_guild_id], "owner_ids": []}
        )
        self.manager.get_guilds_page = AsyncMock(
            return_value={
                "items": [{"id": huge_guild_id, "name": "Guild X", "owner_id": huge_user_id, "is_owner": False}],
                "total": 1,
                "page": 0,
                "pages": 1,
            }
        )
        guilds_resp = await self.client.get(
            "/api/guilds",
            cookies={SESSION_COOKIE_NAME: signed_cookie},
        )
        guilds_data = await guilds_resp.json()
        self.assertEqual(guilds_data["data"]["items"][0]["id"], str(huge_guild_id))
        self.assertEqual(guilds_data["data"]["items"][0]["owner_id"], str(huge_user_id))
        self.assertEqual(guilds_data["stats"]["guild_ids"], [str(huge_guild_id)])

    async def test_batch_cleanup_dm_actions_and_live_dm_websocket_events(self):
        from panel.web.routes import ACTIVITY_LOG_KEY

        store = self.app[SESSION_STORE_KEY]
        signed_cookie, record = store.create_session(user_id=606, username="BatchUser")

        event_callbacks = {}

        def fake_sub(prefix, cb):
            event_callbacks[prefix] = cb

        self.manager.active_processes[606] = SimpleNamespace(
            is_alive=True,
            username="BatchUser#0001",
            subscribe_event=fake_sub,
            unsubscribe_event=lambda *_: None,
            close=AsyncMock(),
        )

        async def fake_batch(uid, target_ids, update_callback=None):
            self.assertEqual(target_ids, [111, 222])
            if update_callback:
                await update_callback(5, 10, "CLEANING", "Conversa 1/2: Fulano")
            return {"ok": True, "deleted": 10, "failed": 0, "skipped_channels": 0, "channels": 2}

        self.manager.clean_channels_batch = fake_batch
        self.manager.close_single_dm = AsyncMock(return_value=(True, "DM fechada"))
        self.manager.leave_single_group_dm = AsyncMock(return_value=(True, "Saiu do grupo"))

        # Single DM close and group leave actions
        close_resp = await self.client.post(
            "/api/cleanup/dm-action",
            json={"action": "close", "channel_id": "111"},
            headers={"X-CSRF-Token": record["csrf_token"]},
            cookies={SESSION_COOKIE_NAME: signed_cookie},
        )
        self.assertEqual(close_resp.status, 200)
        self.manager.close_single_dm.assert_awaited_once_with(606, 111)

        leave_resp = await self.client.post(
            "/api/cleanup/dm-action",
            json={"action": "leave_group", "channel_id": "222"},
            headers={"X-CSRF-Token": record["csrf_token"]},
            cookies={SESSION_COOKIE_NAME: signed_cookie},
        )
        self.assertEqual(leave_resp.status, 200)
        self.manager.leave_single_group_dm.assert_awaited_once_with(606, 222)

        # Os hooks de eventos sao instalados no login/conexao do worker.
        from panel.web.routes.realtime import _ensure_worker_event_hooks

        _ensure_worker_event_hooks(self.app, self.manager, 606)

        # WebSocket batch execution + live DM_UPSERT / DM_REMOVE events
        async with self.client.ws_connect(
            "/api/ws",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={signed_cookie}"},
        ) as ws:
            # Emit live DM_UPSERT and DM_REMOVE from worker hook
            dm_cb = event_callbacks["EVENT:DM_"]
            await dm_cb('EVENT:DM_UPSERT:{"id":1234567890123456789,"type":"dm","name":"Beltrano","unread":true}')
            upsert_msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            self.assertEqual(upsert_msg["type"], "dm_upsert")
            self.assertEqual(upsert_msg["data"]["id"], "1234567890123456789")

            await dm_cb('EVENT:DM_REMOVE:{"id":"1234567890123456789"}')
            remove_msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            self.assertEqual(remove_msg["type"], "dm_remove")
            self.assertEqual(remove_msg["data"]["id"], "1234567890123456789")

            # Ensure DM events did NOT pollute ACTIVITY_LOG
            self.assertEqual(len(self.app[ACTIVITY_LOG_KEY].get(606, [])), 0)

            # Execute batch cleanup
            batch_resp = await self.client.post(
                "/api/cleanup/execute",
                json={"mode": "batch", "target_ids": ["111", "222"]},
                headers={"X-CSRF-Token": record["csrf_token"]},
                cookies={SESSION_COOKIE_NAME: signed_cookie},
            )
            self.assertEqual(batch_resp.status, 200)

            saw_result = False
            for _ in range(4):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                if msg["type"] == "job_result":
                    self.assertEqual(msg["data"]["result"]["deleted"], 10)
                    saw_result = True
                    break
            self.assertTrue(saw_result)

    async def test_worker_list_dms_gateway_cache_and_batch_cleanup(self):
        import json
        from worker.core.config import ControlState
        from worker.messages.manager import MessageManager

        user = SimpleNamespace(id=42)
        fake_recipient = SimpleNamespace(
            id=1234567890123456789,
            name="fulano",
            global_name="Fulano",
            display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatars/1.png"),
        )
        fake_dm = SimpleNamespace(
            id=9876543210987654321,
            recipient=fake_recipient,
            last_message_id=9876543210987654322,
            read_state=SimpleNamespace(badge_count=2, last_acked_id=100),
            is_message_request=lambda: True,
            is_spam=lambda: False,
        )
        client = SimpleNamespace(
            user=user,
            private_channels=[fake_dm],
            fetch_private_channels=AsyncMock(return_value=[]),
        )
        mgr = MessageManager(client, ControlState())

        emitted = []
        with (
            patch("worker.messages.operations.discord.DMChannel", SimpleNamespace),
            patch(
                "worker.messages.operations.emit_response",
                side_effect=lambda prefix, payload, rid: emitted.append((prefix, payload, rid)),
            ),
        ):
            await mgr.list_dms(request_id=None, refresh=False)

        client.fetch_private_channels.assert_not_awaited()
        self.assertEqual(len(emitted), 1)
        prefix, raw_json, _ = emitted[0]
        self.assertEqual(prefix, "DM_LIST_RESULT:")
        channels = json.loads(raw_json)
        ch = channels[0]
        self.assertEqual(ch["id"], "9876543210987654321")
        self.assertEqual(ch["user_id"], "1234567890123456789")
        self.assertEqual(ch["last_message_id"], "9876543210987654322")
        self.assertTrue(ch["unread"])
        self.assertEqual(ch["mention_count"], 2)
        self.assertTrue(ch["is_request"])
        self.assertGreater(ch["last_activity_ms"], 1420070400000)


if __name__ == "__main__":
    unittest.main()
