from __future__ import annotations

import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from worker.account import manager as account_module
from worker.account.manager import AccountManager
from worker.presence.manager import PresenceManager
from worker.voice.manager import VoiceManager


def _http_error(status: int, payload):
    return discord.HTTPException(SimpleNamespace(status=status, reason="erro"), payload)


def _not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Channel")


# ---------------------------------------------------------------- Farm Call em DM


def _dm(channel_id: int, name: str = "Amigo"):
    channel = MagicMock(spec=discord.DMChannel)
    channel.id = channel_id
    channel.guild = None
    channel.recipient = SimpleNamespace(global_name=name, name=name.lower())
    return channel


class _PrivateVoiceClient:
    """Cliente falso que confirma estados de voz fora de servidor, como o gateway."""

    def __init__(self, channels=()):
        self.user = SimpleNamespace(id=999, name="self")
        self.guilds = []
        self.presence_manager = SimpleNamespace(set_task=AsyncMock(), set_main=AsyncMock())
        self.channels = {c.id: c for c in channels}
        self.voice_calls = []
        self.users = {}
        self.manager = None
        states = {}
        self._states = states
        self._connection = SimpleNamespace(_voice_state_for=lambda uid: states.get(int(uid)))

    def get_channel(self, channel_id):
        return self.channels.get(int(channel_id))

    def get_guild(self, guild_id):
        return None

    async def fetch_channel(self, channel_id):
        raise _not_found()

    def get_user(self, user_id):
        return self.users.get(int(user_id))

    async def fetch_user(self, user_id):
        raise _not_found()

    async def change_voice_state(self, **kwargs):
        self.voice_calls.append(kwargs)
        before = self._states.pop(self.user.id, None)
        channel = kwargs.get("channel")
        after = SimpleNamespace(
            channel=channel,
            self_mute=bool(kwargs.get("self_mute")),
            self_deaf=bool(kwargs.get("self_deaf")),
        )
        if channel is not None:
            self._states[self.user.id] = after
        await self.manager.on_voice_state_update(self.user, before or SimpleNamespace(channel=None), after)


def _private_manager(*channels):
    client = _PrivateVoiceClient(channels)
    manager = VoiceManager(client)
    manager.STATE_TIMEOUT = 0.05
    client.manager = manager
    return manager, client


class PrivateCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_dm_call_joins_without_guild_muted_and_deafened(self):
        dm = _dm(50)
        manager, client = _private_manager(dm)

        with patch("worker.voice.manager.emit_line"):
            result = await manager.join_voice(dm.id)
            status = await manager.get_status()

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "JOINED")
        self.assertTrue(result["private"])
        self.assertEqual(result["call_kind"], "dm")
        self.assertIsNone(result["guild_id"])
        self.assertEqual(client.voice_calls[0]["channel"], dm)
        self.assertIs(client.voice_calls[0]["self_mute"], True)
        self.assertIs(client.voice_calls[0]["self_deaf"], True)
        self.assertEqual(status["mode"], "AFK")
        self.assertEqual(status["channel_id"], dm.id)

    async def test_user_id_opens_the_dm_for_the_call(self):
        dm = _dm(50)
        manager, client = _private_manager()
        client.users[777] = SimpleNamespace(id=777, create_dm=AsyncMock(return_value=dm))

        with patch("worker.voice.manager.emit_line"):
            result = await manager.join_voice(777)

        self.assertTrue(result["ok"])
        self.assertEqual(result["channel_id"], dm.id)
        client.users[777].create_dm.assert_awaited_once()

    async def test_unknown_id_is_reported_without_voice_change(self):
        manager, client = _private_manager()

        result = await manager.join_voice(12345)

        self.assertEqual(result["code"], "CHANNEL_NOT_FOUND")
        self.assertEqual(client.voice_calls, [])

    async def test_leaving_a_dm_call_uses_the_private_voice_state(self):
        dm = _dm(50)
        manager, client = _private_manager(dm)

        with patch("worker.voice.manager.emit_line"):
            await manager.join_voice(dm.id)
            result = await manager.leave_voice()

        self.assertEqual(result["code"], "LEFT")
        self.assertIsNone(client.voice_calls[-1]["channel"])
        self.assertFalse(manager.is_in_voice)

    async def test_public_presence_does_not_name_the_dm_partner(self):
        dm = _dm(50, name="Pessoa Secreta")
        manager, client = _private_manager(dm)

        with patch("worker.voice.manager.emit_line") as emit:
            await manager.join_voice(dm.id)

        name, detail = client.presence_manager.set_task.await_args.args
        self.assertNotIn("Secreta", f"{name} {detail}")
        self.assertTrue(all("Secreta" not in str(call.args[0]) for call in emit.call_args_list))

    async def test_dm_call_is_restored_after_reconnect(self):
        dm = _dm(50)
        manager, client = _private_manager(dm)

        with patch("worker.voice.manager.emit_line"):
            await manager.join_voice(dm.id)
            before = client._states.pop(client.user.id)
            await manager.on_voice_state_update(client.user, before, SimpleNamespace(channel=None))
            self.assertFalse(manager.is_in_voice)
            await manager.on_reconnect()

        self.assertTrue(manager.is_in_voice)
        self.assertEqual(manager.current_channel_id, dm.id)


# ---------------------------------------------------------------- Presença


def _presence_client(custom=None):
    return SimpleNamespace(
        settings=SimpleNamespace(custom_activity=custom),
        change_presence=AsyncMock(),
        http=SimpleNamespace(get_app_assets=AsyncMock(return_value=[])),
    )


class PresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_presence_keeps_custom_status_never_edits_settings_and_is_afk(self):
        custom = discord.CustomActivity("estudando", emoji="📚")
        client = _presence_client(custom)

        await PresenceManager(client).set_main()

        kwargs = client.change_presence.await_args.kwargs
        self.assertIs(kwargs["edit_settings"], False)
        self.assertIs(kwargs["afk"], True)
        self.assertIs(kwargs["activities"][0], custom)
        self.assertEqual(kwargs["activities"][1].name, "Madman")

    async def test_expired_custom_status_is_not_sent(self):
        custom = discord.CustomActivity("velho", expires_at=discord.utils.utcnow() - datetime.timedelta(minutes=1))
        client = _presence_client(custom)

        await PresenceManager(client).set_task("Limpando", "DM")

        activities = client.change_presence.await_args.kwargs["activities"]
        self.assertEqual(len(activities), 1)

    async def test_refresh_resends_last_activity(self):
        client = _presence_client()
        presence = PresenceManager(client)
        await presence.refresh()
        client.change_presence.assert_not_awaited()

        await presence.set_main()
        await presence.refresh()
        self.assertEqual(client.change_presence.await_count, 2)


# ---------------------------------------------------------------- Status personalizado


def _account_client(**extra):
    settings = SimpleNamespace(status=discord.Status.online, custom_activity=None, edit=AsyncMock())
    client = SimpleNamespace(
        settings=settings,
        user=SimpleNamespace(name="eu", global_name="Eu", display_avatar=None),
        presence_manager=SimpleNamespace(refresh=AsyncMock()),
        http=SimpleNamespace(request=AsyncMock(), ack_bulk=AsyncMock()),
        _connection=SimpleNamespace(auth_session_id="painelhash01"),
        private_channels=[],
        guilds=[],
    )
    for key, value in extra.items():
        setattr(client, key, value)
    return client


class CustomStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_saves_status_text_emoji_and_expiry_then_refreshes_presence(self):
        client = _account_client()
        expires = int((discord.utils.utcnow() + datetime.timedelta(hours=1)).timestamp() * 1000)

        result = await AccountManager(client).set_custom_status(
            {"status": "dnd", "text": "  focado   no  código ", "emoji": "💻", "expires_at_ms": expires}
        )

        self.assertTrue(result["ok"])
        changes = client.settings.edit.await_args.kwargs
        self.assertEqual(changes["status"], discord.Status.dnd)
        custom = changes["custom_activity"]
        self.assertEqual(custom.name, "focado no código")
        self.assertEqual(custom.emoji.name, "💻")
        self.assertIsNotNone(custom.expires_at)
        client.presence_manager.refresh.assert_awaited_once()

    async def test_empty_text_and_emoji_clears_the_custom_status(self):
        client = _account_client()
        await AccountManager(client).set_custom_status({"text": "", "emoji": None})
        self.assertIsNone(client.settings.edit.await_args.kwargs["custom_activity"])

    async def test_invalid_inputs_are_rejected_before_touching_settings(self):
        client = _account_client()
        manager = AccountManager(client)
        past = int((discord.utils.utcnow() - datetime.timedelta(minutes=5)).timestamp() * 1000)
        far = int((discord.utils.utcnow() + datetime.timedelta(days=90)).timestamp() * 1000)

        cases = [
            ({"status": "offline_forever"}, "INVALID_STATUS"),
            ({"text": "x" * 129}, "TEXT_TOO_LONG"),
            ({"text": "oi", "emoji": "abc"}, "INVALID_EMOJI"),
            ({"text": "oi", "emoji": {"id": "1", "name": "bad name!"}}, "INVALID_EMOJI"),
            ({"text": "oi", "expires_at_ms": past}, "INVALID_EXPIRY"),
            ({"text": "oi", "expires_at_ms": far}, "INVALID_EXPIRY"),
            ({}, "NOTHING_TO_CHANGE"),
        ]
        for payload, code in cases:
            with self.subTest(code=code, payload=payload):
                self.assertEqual((await manager.set_custom_status(payload))["code"], code)
        client.settings.edit.assert_not_awaited()

    async def test_custom_server_emoji_is_kept(self):
        client = _account_client()
        await AccountManager(client).set_custom_status(
            {"text": "oi", "emoji": {"id": "123456789", "name": "pepe", "animated": True}}
        )
        emoji = client.settings.edit.await_args.kwargs["custom_activity"].emoji
        self.assertEqual((emoji.id, emoji.name, emoji.animated), (123456789, "pepe", True))


# ---------------------------------------------------------------- Sessões


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_sessions_with_current_first(self):
        client = _account_client()
        client.http.request.return_value = {
            "user_sessions": [
                {
                    "id_hash": "outrohash01",
                    "approx_last_used_time": "2026-10-01T10:00:00+00:00",
                    "client_info": {"os": "Android", "platform": "Discord Android", "location": "São Paulo, Brazil"},
                },
                {
                    "id_hash": "painelhash01",
                    "approx_last_used_time": "2026-09-01T10:00:00+00:00",
                    "client_info": {"os": "Windows", "platform": "Chrome"},
                },
            ]
        }

        result = await AccountManager(client).list_sessions()

        self.assertEqual([s["id_hash"] for s in result["sessions"]], ["painelhash01", "outrohash01"])
        self.assertTrue(result["sessions"][0]["current"])
        self.assertEqual(result["sessions"][1]["location"], "São Paulo, Brazil")
        route = client.http.request.await_args.args[0]
        self.assertEqual((route.method, route.path), ("GET", "/auth/sessions"))

    async def test_manager_no_longer_ends_sessions(self):
        manager = AccountManager(_account_client())
        for name in ("logout_sessions", "finish_mfa", "_logout"):
            self.assertFalse(hasattr(manager, name), name)


# ---------------------------------------------------------------- Leitura


def _text_channel(channel_id: int, last_message_id: int | None, guild=None):
    return SimpleNamespace(
        id=channel_id,
        last_message_id=last_message_id,
        guild=guild,
        permissions_for=lambda me: SimpleNamespace(read_messages=True),
    )


class AckTests(unittest.IsolatedAsyncioTestCase):
    def _client(self):
        guild = SimpleNamespace(unavailable=False, me=object(), channels=[], threads=[])
        guild.channels = [
            _text_channel(10, 500, guild),  # não lido
            _text_channel(11, 400, guild),  # já lido
            _text_channel(12, None, guild),  # categoria/voz sem mensagens
        ]
        dm = _text_channel(20, 900)
        acked = {11: 400, 20: 100}
        badges = {20: 2}

        def get_read_state(channel_id, if_exists=False):
            if channel_id not in acked:
                return None
            return SimpleNamespace(last_acked_id=acked[channel_id], badge_count=badges.get(channel_id, 0))

        client = _account_client(private_channels=[dm], guilds=[guild])
        client._connection.get_read_state = get_read_state
        return client

    async def test_summary_counts_only_unread_channels(self):
        summary = AccountManager(self._client()).unread_summary()
        self.assertEqual((summary["channels"], summary["private"], summary["guild"], summary["mentions"]), (2, 1, 1, 2))

    async def test_ack_all_respects_scope(self):
        client = self._client()
        result = await AccountManager(client).ack_all("private")

        self.assertTrue(result["ok"])
        batch = client.http.ack_bulk.await_args.args[0]
        self.assertEqual(batch, [{"channel_id": "20", "message_id": "900", "read_state_type": 0}])

    async def test_ack_all_sends_batches_of_one_hundred(self):
        client = _account_client(private_channels=[_text_channel(i, 1000 + i) for i in range(1, 251)])
        client._connection.get_read_state = lambda channel_id, if_exists=False: None

        with patch.object(account_module, "ACK_PAUSE", 0):
            result = await AccountManager(client).ack_all("all")

        self.assertEqual(result["acked"], 250)
        self.assertEqual([len(c.args[0]) for c in client.http.ack_bulk.await_args_list], [100, 100, 50])


# ---------------------------------------------------------------- Rotas do site


class AccountRouteTests(unittest.IsolatedAsyncioTestCase):
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

    async def _post(self, path, body):
        return await self.client.post(path, json=body, headers=self.headers, cookies=self.cookies)

    async def test_account_routes_require_login(self):
        for path in ("/api/account/status", "/api/account/sessions", "/api/account/mentions", "/api/account/unread"):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 401, path)

    async def test_mutations_require_csrf(self):
        resp = await self.client.post("/api/account/ack-all", json={"scope": "all"}, cookies=self.cookies)
        self.assertEqual(resp.status, 403)

    async def test_status_save_forwards_only_known_fields_for_the_session_user(self):
        self.manager.account_status_set = AsyncMock(return_value={"ok": True, "text": "oi"})

        resp = await self._post(
            "/api/account/status",
            {"user_id": 2002, "status": "idle", "text": "oi", "emoji": "🔥", "expires_at_ms": None, "extra": 1},
        )

        self.assertEqual(resp.status, 200)
        uid, payload = self.manager.account_status_set.await_args.args
        self.assertEqual(uid, 1001)
        self.assertEqual(payload, {"status": "idle", "text": "oi", "emoji": "🔥", "expires_at_ms": None})

    async def test_invalid_inputs_are_rejected_before_the_worker(self):
        self.manager.account_ack_all = AsyncMock()
        self.manager.account_mentions_delete = AsyncMock()

        cases = [
            ("/api/account/ack-all", {"scope": "everything"}),
            ("/api/account/mentions/delete", {"ids": []}),
            ("/api/account/mentions/delete", {"ids": ["12", "abc"]}),
            ("/api/account/mentions/delete", {"ids": [str(i) for i in range(1, 102)]}),
        ]
        for path, body in cases:
            with self.subTest(path=path, body=body):
                resp = await self._post(path, body)
                self.assertEqual(resp.status, 400)
        for mock in (
            self.manager.account_ack_all,
            self.manager.account_mentions_delete,
        ):
            mock.assert_not_awaited()

    async def test_session_logout_and_mfa_routes_are_gone(self):
        for path in ("/api/account/sessions/logout", "/api/account/mfa"):
            with self.subTest(path=path):
                resp = await self._post(path, {})
                self.assertIn(resp.status, (404, 405))

    async def test_page_has_status_modal_and_a_read_only_device_list(self):
        from panel.web.ui import render_dashboard_html

        html = render_dashboard_html()
        self.assertIn('id="status-backdrop"', html)
        self.assertIn('name="status-choice" value="invisible"', html)
        self.assertIn('id="sessions-list"', html)
        for removed in ('id="mfa-backdrop"', "confirmLogoutSession", "logout-others-btn", "/api/account/mfa"):
            self.assertNotIn(removed, html)


if __name__ == "__main__":
    unittest.main()
