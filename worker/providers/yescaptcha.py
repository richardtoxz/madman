from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(slots=True)
class CaptchaSolution:
    gRecaptchaResponse: str
    userAgent: str | None = None
    respKey: str | None = None


class YesCaptchaSolver:
    base_url = "https://api.yescaptcha.com"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=130)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()
        async with session.post(
            f"{self.base_url}{endpoint}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
            },
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"YesCaptcha HTTP {response.status}: {data}")
            return data

    async def _create_task(self, task: dict[str, Any]) -> dict[str, Any]:
        data = await self._post_json(
            "/createTask",
            {
                "clientKey": self.api_key,
                "task": task,
            },
        )
        if data.get("errorId") == 1:
            raise RuntimeError(f"Error creating captcha task: {data}")
        return data

    async def _poll_task_result(self, task_id: str) -> dict[str, Any]:
        timeout_at = asyncio.get_running_loop().time() + 120.0
        while True:
            data = await self._post_json(
                "/getTaskResult",
                {
                    "clientKey": self.api_key,
                    "taskId": task_id,
                },
            )
            if data.get("errorId") == 1:
                raise RuntimeError(f"Error getting captcha result: {data}")
            if data.get("status") == "ready":
                return data
            if asyncio.get_running_loop().time() >= timeout_at:
                raise TimeoutError("Timeout while waiting for captcha result")
            await asyncio.sleep(3.0)

    async def hcaptcha(
        self,
        sitekey: str,
        url: str,
        *,
        rqdata: str | None = None,
        isInvisible: bool = False,
        userAgent: str | None = None,
    ) -> CaptchaSolution:
        task: dict[str, Any] = {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": url,
            "websiteKey": sitekey,
            "isInvisible": isInvisible,
        }
        if rqdata:
            task["rqdata"] = rqdata
        if userAgent:
            task["userAgent"] = userAgent

        create_result = await self._create_task(task)
        task_id = create_result["taskId"]
        result = await self._poll_task_result(task_id)
        solution = result.get("solution") or {}
        return CaptchaSolution(
            gRecaptchaResponse=solution.get("gRecaptchaResponse", ""),
            userAgent=solution.get("userAgent"),
            respKey=solution.get("respKey"),
        )
