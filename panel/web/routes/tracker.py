from __future__ import annotations

from aiohttp import web

from .common import _error_response, _log_activity, _read_json, _require_user, _stringify_snowflakes


async def handle_tracker_status(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    status = await mgr.tracker_status(uid)
    status["default_webhook_url"] = mgr.get_webhook_url(uid) or ""
    return web.json_response(_stringify_snowflakes(status))


async def handle_tracker_start(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    targets = body.get("target_ids") or body.get("target_id") or ""
    webhook_url = str(body.get("webhook_url") or mgr.get_webhook_url(uid) or "").strip()
    if not webhook_url:
        return _error_response(
            "MISSING_WEBHOOK", "Configure o Webhook nas Configurações Base (Passo 2) ou informe-o aqui."
        )
    res = await mgr.tracker_start(uid, targets, webhook_url)
    if res.get("ok"):
        mgr.set_user_settings(uid, webhook_url=webhook_url)
        await _log_activity(
            request.app,
            uid,
            "tracker",
            "Tracker Iniciado",
            res.get("message", ""),
        )
    return web.json_response(res)


async def handle_tracker_stop(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    res = await mgr.tracker_stop(uid)
    if res.get("ok"):
        await _log_activity(request.app, uid, "tracker", "Tracker Desativado", res.get("message", ""))
    return web.json_response(res)
