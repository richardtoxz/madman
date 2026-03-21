from __future__ import annotations

import base64
import json
import re
import ssl
import uuid
from urllib.parse import urlencode

import aiohttp

from ..ipc import emit_line
from .constants import ANDROID_USER_AGENT, DESKTOP_USER_AGENT, TLS_CIPHERS


class QuestAPIClient:
    def __init__(self, discord_client):
        self.client = discord_client
        self._session: aiohttp.ClientSession | None = None
        self._build_number = 512062
        self._token: str | None = None

    def _get_token(self) -> str:
        if self._token:
            return self._token

        token = getattr(self.client, "token", None)
        if not token and hasattr(self.client, "http"):
            token = getattr(self.client.http, "token", None)

        if token:
            self._token = token.replace("Bot ", "")

        return self._token or ""

    async def _update_build_number(self) -> None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://discord.com/app",
                    headers={"User-Agent": DESKTOP_USER_AGENT},
                ) as response:
                    if response.status != 200:
                        return

                    html = await response.text()
                    scripts = re.findall(r"/assets/web\.([a-f0-9]+)\.js", html)
                    for script in scripts:
                        async with session.get(
                            f"https://discord.com/assets/web.{script}.js",
                            headers={"User-Agent": DESKTOP_USER_AGENT},
                        ) as script_response:
                            if script_response.status != 200:
                                continue

                            content = await script_response.text()
                            match = re.search(r'buildNumber["\\s:]+["\\s]*(\\d{5,7})', content)
                            if match:
                                self._build_number = int(match.group(1))
                                emit_line(
                                    f"QUEST:AUTO:Discord Build Atualizado: {self._build_number}"
                                )
                                return
        except Exception:
            return

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            await self._update_build_number()
            ssl_context = ssl.create_default_context()
            try:
                ssl_context.set_ciphers(TLS_CIPHERS)
            except Exception:
                pass
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def _make_headers(
        self, is_android: bool = False, custom_headers: dict | None = None
    ) -> dict:
        headers = {
            "Authorization": self._get_token(),
            "accept-language": "en-US,en;q=0.9",
            "x-debug-options": "bugReporterEnabled",
            "x-discord-locale": "en-US",
            "x-discord-timezone": "Asia/Saigon",
        }

        if is_android:
            headers["User-Agent"] = ANDROID_USER_AGENT
            props = {
                "os": "Android",
                "browser": "Discord Android",
                "device": "b0q",
                "system_locale": "en-US",
                "has_client_mods": False,
                "client_version": "316.11 - rn",
                "release_channel": "googleRelease",
                "device_vendor_id": uuid.uuid4().hex,
                "design_id": 2,
                "os_version": "28",
                "client_build_number": 5169,
                "client_event_source": None,
                "client_launch_id": uuid.uuid4().hex,
                "client_app_state": "active",
                "client_heartbeat_session_id": uuid.uuid4().hex,
            }
        else:
            headers["User-Agent"] = DESKTOP_USER_AGENT
            headers["origin"] = "https://discord.com"
            headers["referer"] = "https://discord.com/channels/@me"
            headers["sec-ch-ua"] = '"Not)A;Brand";v="8", "Chromium";v="138"'
            headers["sec-ch-ua-mobile"] = "?0"
            headers["sec-ch-ua-platform"] = '"Windows"'
            headers["sec-fetch-dest"] = "empty"
            headers["sec-fetch-mode"] = "cors"
            headers["sec-fetch-site"] = "same-origin"
            props = {
                "os": "Windows",
                "browser": "Discord Client",
                "release_channel": "stable",
                "client_version": "1.0.9228",
                "os_version": "10.0.19045",
                "os_arch": "x64",
                "app_arch": "x64",
                "system_locale": "en-US",
                "has_client_mods": False,
                "client_launch_id": uuid.uuid4().hex,
                "browser_user_agent": headers["User-Agent"],
                "browser_version": "37.6.0",
                "os_sdk_version": "19045",
                "client_build_number": self._build_number,
                "native_build_number": 77013,
                "client_event_source": None,
                "launch_signature": uuid.uuid4().hex,
                "client_heartbeat_session_id": uuid.uuid4().hex,
                "client_app_state": "focused",
            }

        headers["x-super-properties"] = base64.b64encode(
            json.dumps(props, separators=(",", ":")).encode()
        ).decode()

        if custom_headers:
            headers.update(custom_headers)

        return headers

    def _render_path(self, path: str, params: dict) -> str:
        rendered = path
        for key, value in params.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered

    def _with_query(
        self, path: str, query: dict | None = None, params: dict | None = None
    ) -> str:
        resolved_path = self._render_path(path, params or {})
        if not resolved_path.startswith("http"):
            resolved_path = "https://discord.com/api/v10" + resolved_path

        if not query:
            return resolved_path

        clean_query = {key: value for key, value in query.items() if value is not None}
        if not clean_query:
            return resolved_path

        return f"{resolved_path}?{urlencode(clean_query, doseq=True)}"

    async def get(
        self,
        path: str,
        *,
        headers: dict | None = None,
        query: dict | None = None,
        **params,
    ):
        is_android = headers.get("AndroidRequest") == "true" if headers else False
        final_headers = self._make_headers(is_android, headers)
        final_headers.pop("AndroidRequest", None)

        session = await self._get_session()
        async with session.get(
            self._with_query(path, query, params), headers=final_headers
        ) as response:
            return await response.json(content_type=None)

    async def post(
        self,
        path: str,
        payload: dict,
        *,
        headers: dict | None = None,
        query: dict | None = None,
        **params,
    ):
        is_android = headers.get("AndroidRequest") == "true" if headers else False
        final_headers = self._make_headers(is_android, headers)
        final_headers.pop("AndroidRequest", None)

        session = await self._get_session()
        async with session.post(
            self._with_query(path, query, params),
            headers=final_headers,
            json=payload,
        ) as response:
            body = await response.read()
            try:
                if body:
                    return json.loads(body)
            except Exception:
                pass
            return {}
