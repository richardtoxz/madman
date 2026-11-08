from __future__ import annotations

from aiohttp import web

from .common import _error_response, _read_json, _require_user


async def handle_quests_list(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    return web.json_response(await mgr.quest_list(uid))


async def handle_quests_action(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    action = str(body.get("action") or "").lower()
    if action == "start":
        qid = str(body.get("quest_id") or "").strip()
        return web.json_response(await mgr.quest_start(uid, qid))
    if action == "auto":
        return web.json_response(await mgr.quest_auto(uid))
    if action == "cancel":
        return web.json_response(await mgr.quest_cancel(uid))
    return _error_response("INVALID_ACTION", "Ação de Quest inválida.")
