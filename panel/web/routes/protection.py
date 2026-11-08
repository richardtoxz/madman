from __future__ import annotations

from aiohttp import web

from .common import (
    _error_response,
    _log_activity,
    _parse_positive_int,
    _read_json,
    _require_user,
    _stringify_snowflakes,
)


async def handle_protection_status(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    status = await mgr.protection_status(uid)
    status["whitelist"] = [str(x) for x in mgr.get_whitelist(uid)]
    return web.json_response(_stringify_snowflakes(status))


async def handle_protection_toggle(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    enable = bool(body.get("enabled"))
    result = await mgr.protection_enable(uid) if enable else await mgr.protection_disable(uid)
    if result.get("ok"):
        await _log_activity(
            request.app,
            uid,
            "protecao",
            "Proteção " + ("Ativada" if enable else "Desativada"),
            result.get("message", ""),
        )
    return web.json_response(result)


async def handle_protection_whitelist(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    if "target_id" in body:
        target_id = _parse_positive_int(body["target_id"])
        if target_id is None:
            return _error_response("INVALID_TARGET_ID", "ID do usuário inválido.")
        added = await mgr.toggle_whitelist(uid, target_id)
        if mgr.is_authenticated(uid) and uid in mgr._protection_enabled_users:
            await mgr.protection_configure(uid, mgr.get_whitelist(uid))
        return web.json_response(
            {
                "ok": True,
                "added": added,
                "whitelist": [str(x) for x in mgr.get_whitelist(uid)],
            }
        )
    raw_ids = body.get("whitelist") or []
    result = await mgr.protection_configure(uid, raw_ids)
    result["whitelist"] = [str(x) for x in mgr.get_whitelist(uid)]
    return web.json_response(result)
