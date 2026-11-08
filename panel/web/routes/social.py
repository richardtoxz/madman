from __future__ import annotations

from aiohttp import web

from .common import (
    _error_response,
    _log_activity,
    _parse_non_negative_int,
    _parse_positive_int,
    _read_json,
    _require_user,
    _stringify_snowflakes,
)
from .realtime import _start_streaming_job


async def handle_social_overview(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    page = _parse_non_negative_int(request.query.get("page"), default=0)
    tab = request.query.get("tab", "friends")

    stats = await mgr.get_rel_stats(uid) or {}
    if tab == "pending":
        data = await mgr.get_pending_requests(uid, page=page, page_size=20)
    elif tab == "blocked":
        data = await mgr.get_blocked_list(uid, page=page, page_size=20)
    else:
        sort = request.query.get("sort", "status")
        if sort not in {"status", "interaction"}:
            sort = "status"
        data = await mgr.get_friends_list(uid, page=page, page_size=20, sort=sort)

    return web.json_response(
        {
            "ok": True,
            "stats": _stringify_snowflakes(stats),
            "data": _stringify_snowflakes(data),
            "whitelist": [str(x) for x in mgr.get_whitelist(uid)],
        }
    )


async def handle_social_protect_closest(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    count = _parse_positive_int(body.get("count"))
    if count not in {5, 10, 20}:
        return _error_response("INVALID_COUNT", "Escolha 5, 10 ou 20 amigos.")
    result = await mgr.get_closest_friends(uid, count)
    if not result.get("ok"):
        return web.json_response(_stringify_snowflakes(result))

    items = result.get("items") or []
    added = await mgr.add_to_whitelist(uid, [item["id"] for item in items])
    if added and mgr.is_authenticated(uid) and uid in mgr._protection_enabled_users:
        await mgr.protection_configure(uid, mgr.get_whitelist(uid))
    added_set = set(added)
    whitelist = mgr.get_whitelist(uid)
    skipped = sum(1 for item in items if item["id"] not in added_set and item["id"] not in whitelist)
    if added:
        await _log_activity(
            request.app,
            uid,
            "protecao",
            "Amigos mais próximos protegidos",
            f"{len(added)} amigo(s) adicionados à proteção.",
        )
    return web.json_response(
        {
            "ok": True,
            "found": len(items),
            "added": len(added),
            "already": len(items) - len(added) - skipped,
            "skipped": skipped,
            "names": [str(item.get("name") or "") for item in items if item["id"] in added_set],
            "whitelist": [str(x) for x in whitelist],
        }
    )


async def handle_social_action(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    action = str(body.get("action") or "").lower()

    if action == "add_friend":
        username = str(body.get("username") or "").strip()
        return web.json_response(await mgr.send_friend_request(uid, username))
    if action in {"nickname", "block", "unblock"}:
        tid = _parse_positive_int(body.get("target_id"))
        if tid is None:
            return _error_response("INVALID_TARGET_ID", "ID do usuário inválido.")
        if action == "nickname":
            nick = body.get("nickname")
            return web.json_response(await mgr.set_friend_nickname(uid, tid, nick))
        if action == "block":
            return web.json_response(await mgr.block_user(uid, tid))
        return web.json_response(await mgr.unblock_user(uid, tid))
    if action == "remove_all_friends":
        return web.json_response(
            _start_streaming_job(
                request.app,
                uid,
                "Remover Amigos (Exceto Whitelist)",
                lambda cb: mgr.start_rel_cleanup(uid, update_callback=cb),
            )
        )
    if action == "unblock_all":
        return web.json_response(
            _start_streaming_job(
                request.app,
                uid,
                "Desbloquear Todos",
                lambda cb: mgr.unblock_all(uid, update_callback=cb),
            )
        )
    if action == "accept_all":
        return web.json_response(
            _start_streaming_job(
                request.app,
                uid,
                "Aceitar Pedidos Pendentes",
                lambda cb: mgr.accept_all_requests(uid, update_callback=cb),
            )
        )
    if action == "deny_all":
        return web.json_response(
            _start_streaming_job(
                request.app,
                uid,
                "Recusar Pedidos Pendentes",
                lambda cb: mgr.deny_all_requests(uid, update_callback=cb),
            )
        )
    return _error_response("INVALID_ACTION", "Ação social inválida.")
