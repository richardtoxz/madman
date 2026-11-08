from __future__ import annotations

from aiohttp import web

from .common import (
    ACTIVITY_LOG_KEY,
    EVENT_HOOKED_USERS_KEY,
    JOB_STATE_KEY,
    _error_response,
    _log_activity,
    _read_json,
    _require_user,
)
from .realtime import _ensure_worker_event_hooks


async def handle_worker_status(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    connected = mgr.is_authenticated(uid)
    proc = mgr.active_processes.get(uid)
    job = request.app[JOB_STATE_KEY].get(uid)
    logs = list(request.app[ACTIVITY_LOG_KEY].get(uid, ()))
    # O manager só guarda configuração enquanto o Tracker está ligado.
    tracker = mgr._tracker_settings.get(uid)
    return web.json_response(
        {
            "ok": True,
            "connected": connected,
            "worker_username": getattr(proc, "username", None) if connected else None,
            "settings": {
                "webhook_url": mgr.get_webhook_url(uid) or "",
            },
            "protection_enabled": uid in mgr._protection_enabled_users,
            "whitelist_count": len(mgr.get_whitelist(uid)),
            "guild_whitelist_count": len(mgr.get_guild_whitelist(uid)),
            "tracker_active": bool(tracker),
            "tracker_targets": [str(t) for t in tracker.get("target_ids", [])] if tracker else [],
            "job": job,
            "activity": logs[:30],
        }
    )


async def handle_worker_connect(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    token = str(body.get("token") or "").strip()
    if not token:
        return _error_response("MISSING_TOKEN", "Informe a credencial da conta.")
    ok, message = await mgr.authenticate(uid, token)
    if ok:
        _ensure_worker_event_hooks(request.app, mgr, uid)
        await _log_activity(request.app, uid, "sistema", "Worker Conectado", message)
    return web.json_response(
        {
            "ok": ok,
            "connected": mgr.is_authenticated(uid),
            "message": message,
        },
        status=200 if ok else 400,
    )


async def handle_worker_disconnect(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    ok, message = await mgr.disconnect(uid)
    request.app[EVENT_HOOKED_USERS_KEY].pop(uid, None)
    await _log_activity(request.app, uid, "sistema", "Worker Desconectado", message)
    return web.json_response({"ok": ok, "connected": False, "message": message})
