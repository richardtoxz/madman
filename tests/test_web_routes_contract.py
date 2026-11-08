"""Contrato HTTP do painel web: status, corpo, cookies e efeitos de cada rota.

Executa uma matriz de requisições (com e sem sessão, worker conectado ou não, sem manager,
tracker ativo, proteção desligada, OAuth configurado ou não) contra `setup_web_routes` com um
manager falso e compara com `fixtures/web_routes_contract.json`, gerado do comportamento
existente. Uma mudança intencional na API exige regenerar o arquivo:

    python -m tests.test_web_routes_contract --update
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import panel  # noqa: F401  (põe panel/lib no sys.path antes do aiohttp)
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import panel.web.routes as routes
from panel.web.security import SESSION_COOKIE_NAME

FIXTURE = Path(__file__).parent / "fixtures" / "web_routes_contract.json"
UID = 1001
OAUTH_ENV = ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "DISCORD_REDIRECT_URI")
CLEARED_ENV = (*OAUTH_ENV, "DISCORD_JOIN_GUILD_ID", "TOKEN_BOT_CONTROLE", "WEB_DEV_RELOAD", "ALLOWED_USER_IDS")
# Valores gerados a cada execução (horário, ids derivados do relógio, CSRF da sessão).
VOLATILE_KEYS = ("id", "timestamp", "started_at", "csrf_token")
LOG: list = []


class FakeManager:
    def __init__(self, *, authenticated=True, tracker=False, protection=True):
        self.authenticated = authenticated
        self.active_processes = {UID: NS(username="WorkerName", is_alive=True)} if authenticated else {}
        self._session_tokens = {UID: "t" * 40} if authenticated else {}
        self._protection_enabled_users = {UID} if protection else set()
        self._tracker_settings = {UID: {"target_id": 1, "target_ids": [1, 2], "webhook_url": "w"}} if tracker else {}
        self._settings = {"webhook_url": "https://discord.com/api/webhooks/1/abc"}

    def is_authenticated(self, user_id):
        return self.authenticated

    def get_whitelist(self, user_id):
        return [5, 6]

    def get_guild_whitelist(self, user_id):
        return [7]

    def get_user_settings(self, user_id):
        return dict(self._settings)

    def get_webhook_url(self, user_id):
        return self.get_user_settings(user_id).get("webhook_url")

    def set_user_settings(self, user_id, **kwargs):
        LOG.append(["set_user_settings", user_id, kwargs])
        return kwargs

    def subscribe_events(self, user_id, prefix, callback):
        LOG.append(["subscribe_events", user_id, prefix])

    async def authenticate(self, user_id, token):
        LOG.append(["authenticate", user_id, token])
        return token != "bad" * 20, "auth message"

    async def disconnect(self, user_id):
        LOG.append(["disconnect", user_id])
        return True, "disconnected"

    async def close_single_dm(self, user_id, channel_id):
        LOG.append(["close_single_dm", user_id, channel_id])
        return channel_id != 999, "closed msg"

    async def leave_single_group_dm(self, user_id, channel_id):
        LOG.append(["leave_single_group_dm", user_id, channel_id])
        return True, "left msg"

    async def toggle_whitelist(self, user_id, target_id):
        LOG.append(["toggle_whitelist", user_id, target_id])
        return True

    async def add_to_whitelist(self, user_id, ids):
        LOG.append(["add_to_whitelist", user_id, ids])
        return ids[:1]

    async def get_closest_friends(self, user_id, limit):
        LOG.append(["get_closest_friends", user_id, limit])
        if limit == 20:
            return {"ok": False, "code": "X", "message": "nope", "user_id": 123}
        return {"ok": True, "items": [{"id": 5, "name": "A"}, {"id": 9, "name": "B"}, {"id": 11, "name": None}]}

    async def control_clean(self, user_id, signal):
        LOG.append(["control_clean", user_id, signal])
        return True

    async def list_open_dms(self, user_id, refresh=False):
        LOG.append(["list_open_dms", user_id, refresh])
        return [{"id": 123456789012345678, "user_id": 42, "name": "dm"}]

    async def protection_status(self, user_id):
        return {"ok": True, "enabled": True, "target_id": 5}

    async def quest_config(self, user_id, webhook_url=None):
        LOG.append(["quest_config", user_id, webhook_url])
        return {"ok": webhook_url != "https://discord.com/api/webhooks/2/fail"}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def method(*args, **kwargs):
            callback = kwargs.pop("update_callback", None)
            LOG.append([name, list(args), sorted(kwargs.items())])
            if callback is not None:
                await callback(1, 3, "CLEANING", "detail")
                return "COMPLETADO:feito"
            return {
                "ok": True,
                "message": f"msg {name}",
                "text": "status text",
                "acked": 2,
                "id": 123456789012345678,
                "profile": {"id": 1, "complete": False, "username": "u"},
            }

        return method


def _normalize(value):
    if isinstance(value, dict):
        return {
            key: "<volatil>" if key in VOLATILE_KEYS and isinstance(item, (str, float)) else _normalize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


async def _snapshot(response) -> dict:
    text = await response.text()
    try:
        body = json.loads(text)
    except ValueError:
        body = text[:200]
    headers = {}
    for name in ("Location", "Content-Type"):
        if name in response.headers:
            headers[name] = re.sub(r"state=[^&]+", "state=<s>", response.headers[name])
    cookies = []
    for raw in response.headers.getall("Set-Cookie", []):
        attributes = sorted(part.strip().split("=")[0].lower() for part in raw.split(";")[1:])
        cookies.append([raw.split("=", 1)[0], attributes, re.findall(r"Max-Age=(\d+)", raw)])
    return {"status": response.status, "body": _normalize(body), "headers": headers, "cookies": sorted(cookies)}


POSTS = {
    "/api/worker/connect": [{}, {"token": "x"}, {"token": "bad" * 20}],
    "/api/worker/disconnect": [{}],
    "/api/settings": [
        {},
        {"webhook_url": "nope"},
        {"webhook_url": "https://discord.com/api/webhooks/1/abc"},
        {"webhook_url": "https://discord.com/api/webhooks/2/fail"},
    ],
    "/api/presence/rich": [{}, {"enabled": "x"}, {"enabled": True, "custom": None}, {"enabled": False}],
    "/api/protection/toggle": [{}, {"enabled": True}, {"enabled": False}],
    "/api/protection/whitelist": [
        {},
        {"target_id": "x"},
        {"target_id": True},
        {"target_id": "12"},
        {"whitelist": [1, "2"]},
    ],
    "/api/voice/action": [
        {},
        {"action": "join"},
        {"action": "JOIN", "channel_id": "55"},
        {"action": "leave"},
        {"action": "follow", "target_id": "a"},
        {"action": "follow", "target_id": 9},
        {"action": "unfollow"},
        {"action": "x"},
    ],
    "/api/account/status": [
        {},
        {"status": "idle"},
        {"clear": True, "text": "a"},
        {"text": "a" * 401},
        {"text": "hi", "emoji": "x", "expires_at_ms": 5},
    ],
    "/api/account/mentions/delete": [
        {},
        {"ids": []},
        {"ids": list(range(1, 102))},
        {"ids": [1, "x"]},
        {"ids": [1, "2"]},
    ],
    "/api/account/ack-all": [{}, {"scope": "bad"}, {"scope": "private"}, {"scope": "guilds"}],
    "/api/cleanup/dm-action": [
        {},
        {"channel_id": "0"},
        {"channel_id": 5, "action": "close"},
        {"target_id": 5, "action": "leave_group"},
        {"channel_id": 999, "action": "close"},
        {"channel_id": 5, "action": "zzz"},
    ],
    "/api/cleanup/scan-guilds": [{}],
    "/api/cleanup/preview": [{}, {"target_id": "x"}, {"target_id": "7", "guild": True}],
    "/api/cleanup/execute": [
        {"mode": "zzz"},
        {"target_id": "x"},
        {"mode": "guild", "target_id": "8"},
        {"mode": "batch"},
        {"mode": "batch", "target_ids": [1, "x"]},
        {"mode": "batch", "target_ids": [3, 3, "4"]},
        {"mode": "all_dms"},
        {"mode": "close_all_dms"},
        {"mode": "leave_groups"},
    ],
    "/api/cleanup/control": [{}, {"signal": "pause"}, {"signal": "nope"}],
    "/api/social/protect-closest": [{}, {"count": 3}, {"count": 5}, {"count": "20"}],
    "/api/social/action": [
        {},
        {"action": "add_friend", "username": " bob "},
        {"action": "block"},
        {"action": "block", "target_id": 4},
        {"action": "unblock", "target_id": "4"},
        {"action": "nickname", "target_id": 4, "nickname": "n"},
        {"action": "remove_all_friends"},
        {"action": "unblock_all"},
        {"action": "accept_all"},
        {"action": "deny_all"},
    ],
    "/api/guilds/whitelist": [
        {},
        {"clear": True},
        {"guild_id": "x"},
        {"guild_id": 9},
        {"visible_ids": [1], "selected_ids": [1]},
    ],
    "/api/guilds/bulk": [{}, {"action": "leave"}, {"action": "MUTE"}],
    "/api/tracker/start": [{}, {"target_id": 5, "webhook_url": "w"}, {"target_ids": [5]}],
    "/api/tracker/stop": [{}],
    "/api/quests/action": [{}, {"action": "start", "quest_id": " 9 "}, {"action": "auto"}, {"action": "cancel"}],
}
GETS = [
    "/",
    "/inicio",
    "/api/health",
    "/api/auth/session",
    "/api/worker/status",
    "/api/presence/rich",
    "/api/protection/status",
    "/api/voice/status",
    "/api/account/status",
    "/api/account/profile",
    "/api/account/profile?refresh=1",
    "/api/account/sessions",
    "/api/account/mentions",
    "/api/account/mentions?limit=0",
    "/api/account/mentions?limit=500&before=0",
    "/api/account/mentions?limit=7&before=99",
    "/api/account/mentions?limit=x&before=",
    "/api/account/unread",
    "/api/cleanup/dms",
    "/api/cleanup/dms?refresh=Yes",
    "/api/social/overview",
    "/api/social/overview?page=2&tab=pending",
    "/api/social/overview?tab=blocked&page=x",
    "/api/social/overview?sort=interaction&page=-1",
    "/api/social/overview?sort=bad",
    "/api/guilds",
    "/api/guilds?page=3",
    "/api/guilds?page=",
    "/api/tracker/status",
    "/api/quests",
    "/api/auth/discord/login",
    "/api/auth/discord/callback",
    "/api/auth/discord/callback?state=abc&code=x",
]


def _reset(client) -> None:
    LOG.clear()
    client.session.cookie_jar.clear()


async def _run_profile(manager_kind: str, with_session: bool, oauth: bool) -> dict:
    for key in OAUTH_ENV:
        os.environ.pop(key, None)
    if oauth:
        os.environ.update(
            DISCORD_CLIENT_ID="cid", DISCORD_CLIENT_SECRET="sec", DISCORD_REDIRECT_URI="http://127.0.0.1:9/cb"
        )
    manager = (
        None
        if manager_kind == "none"
        else FakeManager(
            authenticated=manager_kind != "unauth",
            tracker=manager_kind == "tracker",
            protection=manager_kind != "noprot",
        )
    )
    app = routes.setup_web_routes(web.Application(), manager=manager, bot=None)
    client = TestClient(TestServer(app))
    await client.start_server()
    out = {}
    try:
        cookies, headers = {}, {}
        if with_session:
            cookie, record = app[routes.SESSION_STORE_KEY].create_session(user_id=UID, username="UserA")
            cookies = {SESSION_COOKIE_NAME: cookie}
            headers = {"X-CSRF-Token": record["csrf_token"]}
        for path in GETS:
            _reset(client)
            snapshot = await _snapshot(await client.get(path, cookies=cookies, allow_redirects=False))
            if path in ("/", "/inicio") and isinstance(snapshot["body"], str):
                snapshot["body"] = len(snapshot["body"])
            out[f"GET {path}"] = {"resp": snapshot, "log": _normalize(LOG)}
        for path, bodies in POSTS.items():
            for index, body in enumerate(bodies):
                _reset(client)
                app[routes.JOB_STATE_KEY].clear()
                snapshot = await _snapshot(await client.post(path, json=body, headers=headers, cookies=cookies))
                for _ in range(10):
                    await asyncio.sleep(0)
                out[f"POST {path} #{index}"] = {
                    "resp": snapshot,
                    "log": _normalize(LOG),
                    "job": _normalize(app[routes.JOB_STATE_KEY].get(UID)),
                    "hooked": sorted(app[routes.EVENT_HOOKED_USERS_KEY]),
                }
        LOG.clear()
        response = await client.post(
            "/api/voice/action",
            data="{not json",
            headers={**headers, "Content-Type": "application/json"},
            cookies=cookies,
        )
        out["POST invalid json"] = {"resp": await _snapshot(response), "log": _normalize(LOG)}
        out["activity"] = _normalize({str(k): list(v) for k, v in app[routes.ACTIVITY_LOG_KEY].items()})
        for body in ({}, {"disconnect_worker": True}):
            LOG.clear()
            response = await client.post("/api/auth/logout", json=body, headers=headers, cookies=cookies)
            out[f"logout {body}"] = {"resp": await _snapshot(response), "log": _normalize(LOG)}
    finally:
        await client.close()
    return out


async def collect_contract() -> dict:
    result = {}
    with patch.dict(os.environ):
        for key in CLEARED_ENV:
            os.environ.pop(key, None)
        for kind in ("auth", "unauth", "none", "tracker", "noprot"):
            for with_session in (True, False):
                for oauth in (False, True):
                    result[f"{kind}/{with_session}/{oauth}"] = await _run_profile(kind, with_session, oauth)
    # Ida e volta por JSON: tuplas viram listas, como no arquivo gravado.
    return json.loads(json.dumps(result, sort_keys=True, default=str))


class WebRoutesContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_route_keeps_its_http_contract(self):
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        actual = await collect_contract()
        self.assertEqual(sorted(actual), sorted(expected))
        for profile in expected:
            self.assertEqual(sorted(actual[profile]), sorted(expected[profile]), profile)
            for case in expected[profile]:
                with self.subTest(profile=profile, case=case):
                    self.assertEqual(actual[profile][case], expected[profile][case])


if __name__ == "__main__":
    if "--update" in sys.argv:
        FIXTURE.parent.mkdir(exist_ok=True)
        data = asyncio.run(collect_contract())
        FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{sum(len(v) for v in data.values())} casos gravados em {FIXTURE}")
    else:
        unittest.main()
