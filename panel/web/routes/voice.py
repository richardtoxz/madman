from __future__ import annotations

from aiohttp import web

from .common import _error_response, _read_json, _require_user, _stringify_snowflakes


async def handle_voice_status(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    return web.json_response(_stringify_snowflakes(await mgr.voice_status(uid)))


async def handle_voice_action(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    action = str(body.get("action") or "").lower()
    if action == "join":
        cid = str(body.get("channel_id") or "").strip()
        if not cid.isdigit():
            return _error_response("INVALID_CHANNEL", "ID do canal inválido.")
        res = await mgr.voice_join(uid, int(cid))
    elif action == "leave":
        res = await mgr.voice_leave(uid)
    elif action == "follow":
        tid = str(body.get("target_id") or "").strip()
        if not tid.isdigit():
            return _error_response("INVALID_TARGET", "ID do usuário inválido.")
        res = await mgr.voice_follow(uid, int(tid))
    elif action == "unfollow":
        res = await mgr.voice_unfollow(uid)
    else:
        return _error_response("INVALID_ACTION", "Ação de voz inválida.")
    return web.json_response(_stringify_snowflakes(res))
