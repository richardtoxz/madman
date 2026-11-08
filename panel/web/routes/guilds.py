from __future__ import annotations

from aiohttp import web

from .common import (
    _error_response,
    _parse_non_negative_int,
    _parse_positive_int,
    _read_json,
    _require_user,
    _stringify_snowflakes,
)
from .realtime import _start_streaming_job


async def handle_guilds_list(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    page = _parse_non_negative_int(request.query.get("page"), default=0)
    stats = await mgr.get_guild_stats(uid)
    page_data = await mgr.get_guilds_page(uid, page=page, page_size=20)
    return web.json_response(
        {
            "ok": True,
            "stats": _stringify_snowflakes(stats),
            "data": _stringify_snowflakes(page_data),
            "whitelist": [str(x) for x in mgr.get_guild_whitelist(uid)],
        }
    )


async def handle_guilds_whitelist(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    if body.get("clear"):
        await mgr.clear_guild_whitelist(uid)
    elif "guild_id" in body:
        gid = _parse_positive_int(body["guild_id"])
        if gid is None:
            return _error_response("INVALID_GUILD_ID", "ID do servidor inválido.")
        await mgr.toggle_guild_whitelist(uid, gid)
    elif "visible_ids" in body and "selected_ids" in body:
        await mgr.update_guild_whitelist(uid, body["visible_ids"], body["selected_ids"])
    return web.json_response(
        {
            "ok": True,
            "whitelist": [str(x) for x in mgr.get_guild_whitelist(uid)],
        }
    )


async def handle_guilds_bulk(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    action = str(body.get("action") or "").upper()
    if action not in {"LEAVE", "MUTE"}:
        return _error_response("INVALID_ACTION", "Ação inválida.")
    label = "Sair de Servidores (Exceto Whitelist)" if action == "LEAVE" else "Silenciar Servidores (Exceto Whitelist)"
    return web.json_response(
        _start_streaming_job(
            request.app,
            uid,
            label,
            lambda cb: mgr.start_guild_cleanup(uid, action, update_callback=cb),
        )
    )
