from __future__ import annotations

from typing import Any

from aiohttp import web

from .common import (
    _error_response,
    _log_activity,
    _parse_non_negative_int,
    _parse_positive_int,
    _read_json,
    _require_manager,
    _require_session,
    _require_user,
    _with_login_identity,
)


async def handle_account_status(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    return web.json_response(await mgr.account_status(uid))


async def handle_account_profile(request: web.Request) -> web.Response:
    session = _require_session(request)
    mgr = _require_manager(request)
    refresh = request.query.get("refresh") == "1"
    result = await mgr.account_profile(int(session["user_id"]), refresh=refresh)
    return web.json_response(_with_login_identity(result, session.get("identity")))


async def handle_account_status_save(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    payload: dict[str, Any] = {}
    if "status" in body:
        payload["status"] = str(body.get("status") or "")
    if body.get("clear"):
        payload["clear"] = True
    else:
        for key in ("text", "emoji", "expires_at_ms"):
            if key in body:
                payload[key] = body[key]
    if isinstance(payload.get("text"), str) and len(payload["text"]) > 400:
        return _error_response("TEXT_TOO_LONG", "Texto longo demais.")
    result = await mgr.account_status_set(uid, payload)
    if result.get("ok"):
        await _log_activity(request.app, uid, "sistema", "Status atualizado", result.get("text") or "")
    return web.json_response(result)


async def handle_account_sessions(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    return web.json_response(await mgr.account_sessions(uid))


async def handle_account_mentions(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    limit = min(50, _parse_non_negative_int(request.query.get("limit"), 25) or 25)
    before = _parse_positive_int(request.query.get("before"))
    return web.json_response(await mgr.account_mentions(uid, limit=limit, before=before))


async def handle_account_mentions_delete(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
        return _error_response("INVALID_IDS", "Selecione de 1 a 100 menções.")
    ids = [_parse_positive_int(value) for value in raw_ids]
    if any(value is None for value in ids):
        return _error_response("INVALID_IDS", "IDs de menção inválidos.")
    result = await mgr.account_mentions_delete(uid, ids)
    await _log_activity(request.app, uid, "limpeza", "Menções removidas", result.get("message", ""))
    return web.json_response(result)


async def handle_account_unread(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    return web.json_response(await mgr.account_unread(uid))


async def handle_account_ack_all(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    scope = str(body.get("scope") or "all")
    if scope not in ("all", "private", "guilds"):
        return _error_response("INVALID_SCOPE", "Escopo inválido.")
    result = await mgr.account_ack_all(uid, scope)
    if result.get("ok") and result.get("acked"):
        await _log_activity(request.app, uid, "limpeza", "Marcado como lido", result.get("message", ""))
    return web.json_response(result)
