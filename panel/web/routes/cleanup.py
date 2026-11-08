from __future__ import annotations

from aiohttp import web

from .common import (
    _broadcast_to_user,
    _error_response,
    _parse_positive_int,
    _read_json,
    _require_user,
    _stringify_snowflakes,
)
from .realtime import _start_streaming_job


async def handle_cleanup_dms(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    refresh = request.query.get("refresh", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    try:
        dms = await mgr.list_open_dms(uid, refresh=refresh)
    except TypeError:
        dms = await mgr.list_open_dms(uid)
    return web.json_response({"ok": True, "items": _stringify_snowflakes(dms or [])})


async def handle_cleanup_dm_action(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    action = str(body.get("action") or "").strip().lower()
    cid = _parse_positive_int(body.get("channel_id") or body.get("target_id"))
    if cid is None:
        return _error_response("INVALID_CHANNEL_ID", "ID da conversa inválido.")

    if action == "close":
        ok, msg = await mgr.close_single_dm(uid, cid)
    elif action == "leave_group":
        leave_fn = getattr(mgr, "leave_single_group_dm", None)
        if callable(leave_fn):
            ok, msg = await leave_fn(uid, cid)
        else:
            ok, msg = await mgr.close_single_dm(uid, cid)
    else:
        return _error_response("INVALID_ACTION", "Ação de conversa inválida.")

    if ok:
        await _broadcast_to_user(request.app, uid, "dm_remove", {"id": str(cid)})
    return web.json_response(
        {"ok": bool(ok), "channel_id": str(cid), "message": msg},
        status=200 if ok else 400,
    )


async def handle_cleanup_scan_guilds(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    items = await mgr.scan_guilds_for_messages(uid)
    return web.json_response({"ok": True, "items": _stringify_snowflakes(items or [])})


async def handle_cleanup_preview(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    target_raw = str(body.get("target_id") or "").strip()
    is_guild = bool(body.get("guild", False))
    if not target_raw.isdigit():
        return _error_response("INVALID_ID", "Informe um ID numérico válido.")
    result = await mgr.preview_clean(uid, int(target_raw), guild=is_guild)
    return web.json_response(_stringify_snowflakes(result))


async def handle_cleanup_execute(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    mode = str(body.get("mode") or "channel").lower()

    if mode in {"channel", "guild"}:
        target_raw = str(body.get("target_id") or "").strip()
        if not target_raw.isdigit():
            return _error_response("INVALID_ID", "ID inválido.")
        tid = int(target_raw)
        is_guild = mode == "guild"
        label = f"Limpeza de {'Servidor' if is_guild else 'Canal/DM'} ({tid})"
        res = _start_streaming_job(
            request.app,
            uid,
            label,
            lambda cb: mgr.execute_clean(uid, tid, guild=is_guild, update_callback=cb),
        )
        return web.json_response(res)

    if mode == "batch":
        raw_ids = body.get("target_ids")
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
            return _error_response("INVALID_TARGET_IDS", "Selecione entre 1 e 100 conversas para limpar em fila.")
        parsed_ids: list[int] = []
        for item in raw_ids:
            pid = _parse_positive_int(item)
            if pid is None:
                return _error_response("INVALID_TARGET_IDS", "Um ou mais IDs selecionados são inválidos.")
            if pid not in parsed_ids:
                parsed_ids.append(pid)
        res = _start_streaming_job(
            request.app,
            uid,
            f"Limpeza em Fila ({len(parsed_ids)} conversas)",
            lambda cb: mgr.clean_channels_batch(uid, parsed_ids, update_callback=cb),
        )
        return web.json_response(res)

    if mode == "all_dms":
        res = _start_streaming_job(
            request.app,
            uid,
            "Limpar Todas as DMs",
            lambda cb: mgr.clean_all_dms(uid, update_callback=cb),
        )
        return web.json_response(res)

    if mode == "close_all_dms":
        res = _start_streaming_job(
            request.app,
            uid,
            "Fechar Todas as DMs",
            lambda cb: mgr.close_all_dms(uid, update_callback=cb),
        )
        return web.json_response(res)

    if mode == "leave_groups":
        res = _start_streaming_job(
            request.app,
            uid,
            "Sair de Todos os Grupos",
            lambda cb: mgr.leave_all_group_dms(uid, update_callback=cb),
        )
        return web.json_response(res)

    return _error_response("INVALID_MODE", "Modo de limpeza inválido.")


async def handle_cleanup_control(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    signal = str(body.get("signal") or "").upper()
    if signal not in {"PAUSE", "RESUME", "CANCEL", "START"}:
        return _error_response("INVALID_SIGNAL", "Sinal inválido.")
    sent = await mgr.control_clean(uid, signal)
    return web.json_response({"ok": sent, "signal": signal})
