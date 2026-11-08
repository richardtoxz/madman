from __future__ import annotations

from aiohttp import web

from .common import _error_response, _log_activity, _read_json, _require_user


async def handle_rich_presence_status(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    return web.json_response(await mgr.rich_presence_status(uid))


async def handle_rich_presence_set(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return _error_response("INVALID_RICH_PRESENCE", "Informe se o Rich Presence fica ligado ou desligado.")
    result = await mgr.rich_presence_set(uid, enabled, body.get("custom"))
    if result.get("ok"):
        await _log_activity(
            request.app,
            uid,
            "presenca",
            "Rich Presence atualizado",
            "Ligado" if enabled else "Desligado",
        )
    return web.json_response(result)
