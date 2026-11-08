from __future__ import annotations

from aiohttp import web

from tracker_protocol import normalize_webhook_url

from .common import _error_response, _log_activity, _read_json, _require_user


async def handle_settings_save(request: web.Request) -> web.Response:
    mgr, uid = _require_user(request)
    body = await _read_json(request)
    raw_webhook = str(body.get("webhook_url") or "").strip()

    if raw_webhook:
        normalized = normalize_webhook_url(raw_webhook)
        if not normalized:
            return _error_response(
                "INVALID_WEBHOOK", "URL de Webhook do Discord inválida (use https://discord.com/api/webhooks/...)."
            )
    else:
        normalized = None

    if mgr.is_authenticated(uid):
        quest_res = await mgr.quest_config(uid, webhook_url=normalized)
        if not quest_res.get("ok"):
            return web.json_response(quest_res, status=400)
    mgr.set_user_settings(uid, webhook_url=normalized)
    await _log_activity(
        request.app,
        uid,
        "sistema",
        "Configurações Salvas",
        "Webhook atualizado" if normalized else "Webhook removido",
    )
    return web.json_response(
        {
            "ok": True,
            "webhook_url": normalized or "",
            "message": "Configurações salvas com sucesso.",
        }
    )
