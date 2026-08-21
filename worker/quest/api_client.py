from __future__ import annotations

import asyncio
import re
from typing import Any

from ..lib.discord.http import Route


class QuestAPIClient:
    """Use the HTTP client already owned by discord.py-self.

    This keeps authentication, client properties, TLS impersonation,
    rate-limit buckets, and HTTP errors consistent with every other Discord
    request made by the worker.
    """

    REQUEST_TIMEOUT_SECONDS = 30.0

    def __init__(self, discord_client):
        self.client = discord_client

    @staticmethod
    def _route(method: str, path: str, parameters: dict[str, Any]) -> Route:
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
        ):
            raise ValueError("Quest API paths must be relative Discord routes")
        if "://" in path or not re.fullmatch(r"/[A-Za-z0-9_@./{}-]+", path):
            raise ValueError("Absolute URLs are not accepted by QuestAPIClient")
        return Route(method, path, **parameters)

    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        query: dict | None = None,
        headers: dict | None = None,
        **parameters,
    ):
        kwargs: dict[str, Any] = {}
        if payload is not None:
            kwargs["json"] = payload
        if query:
            kwargs["params"] = {
                key: value for key, value in query.items() if value is not None
            }
        if headers:
            kwargs["headers"] = headers

        # Validate and build the route before touching the HTTP client.  Apart
        # from failing fast, this makes it impossible for an absolute URL to
        # reach a custom/partially initialised client.
        route = self._route(method, path, parameters)
        request = self.client.http.request(route, **kwargs)
        return await asyncio.wait_for(request, timeout=self.REQUEST_TIMEOUT_SECONDS)

    async def get(
        self,
        path: str,
        *,
        headers: dict | None = None,
        query: dict | None = None,
        **parameters,
    ):
        return await self.request(
            "GET", path, headers=headers, query=query, **parameters
        )

    async def post(
        self,
        path: str,
        payload: dict,
        *,
        headers: dict | None = None,
        query: dict | None = None,
        **parameters,
    ):
        return await self.request(
            "POST",
            path,
            payload=payload,
            headers=headers,
            query=query,
            **parameters,
        )

    async def delete(self, path: str, **parameters):
        return await self.request("DELETE", path, **parameters)

    async def close(self) -> None:
        # The Discord client owns and closes its HTTP session.
        return None
