from __future__ import annotations

import os
from typing import Callable

from .ipc import emit_line
from .lib.discord.errors import CaptchaRequired
from .providers.yescaptcha import YesCaptchaSolver

_yescaptcha_client: YesCaptchaSolver | None = None


def _resolve_api_key(client) -> str:
    runtime_key = getattr(client, "_quest_captcha_api_key", None)
    if runtime_key:
        return str(runtime_key).strip()
    return os.environ.get("YES_CAPTCHA_API_KEY", "").strip()


def _get_yescaptcha_client(api_key: str) -> YesCaptchaSolver | None:
    global _yescaptcha_client
    if not api_key:
        return None
    if _yescaptcha_client is None or _yescaptcha_client.api_key != api_key:
        _yescaptcha_client = YesCaptchaSolver(api_key)
    return _yescaptcha_client


async def solve_captcha(client, captcha: CaptchaRequired) -> str:
    api_key = _resolve_api_key(client)
    solver = _get_yescaptcha_client(api_key)
    if solver is None:
        raise RuntimeError(
            "YES_CAPTCHA_API_KEY nao configurada. "
            "Resgate manual necessario para este captcha."
        )

    user_agent = None
    try:
        user_agent = client.http.headers.user_agent
    except Exception:
        user_agent = None

    emit_line(
        f"CAPTCHA:SOLVE:service={captcha.service}:sitekey={captcha.sitekey}"
    )

    quest_manager = getattr(client, "quest_manager", None)
    if quest_manager:
        try:
            await quest_manager.dispatch_quest_captcha(
                service=captcha.service,
                sitekey=captcha.sitekey,
                status="solver-attempt",
            )
        except Exception:
            pass

    result = await solver.hcaptcha(
        captcha.sitekey,
        "https://discord.com",
        rqdata=captcha.rqdata,
        isInvisible=captcha.should_serve_invisible,
        userAgent=user_agent,
    )

    if not result.gRecaptchaResponse:
        if quest_manager:
            try:
                await quest_manager.dispatch_quest_captcha(
                    service=captcha.service,
                    sitekey=captcha.sitekey,
                    status="solver-empty-result",
                )
            except Exception:
                pass
        raise RuntimeError("YesCaptcha returned an empty solution")

    if quest_manager:
        try:
            await quest_manager.dispatch_quest_captcha(
                service=captcha.service,
                sitekey=captcha.sitekey,
                status="solver-success",
            )
        except Exception:
            pass

    return result.gRecaptchaResponse


def build_captcha_handler(client) -> Callable[[CaptchaRequired], object]:
    async def _handler(captcha: CaptchaRequired) -> str:
        return await solve_captcha(client, captcha)

    return _handler


async def close_captcha_solver() -> None:
    global _yescaptcha_client
    if _yescaptcha_client is not None:
        await _yescaptcha_client.close()
        _yescaptcha_client = None
