from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any

from aiohttp import web

from engine.backend import SelfbotManager

from ..security import (
    OAuthStateStore,
    RateLimiter,
    SESSION_COOKIE_NAME,
    SESSION_REQUEST_KEY,
    SESSION_TTL_SECONDS,
    SessionStore,
    is_secure_request,
)


MANAGER_KEY: web.AppKey[SelfbotManager | None] = web.AppKey("manager", SelfbotManager | None)

BOT_KEY: web.AppKey[Any] = web.AppKey("bot", Any)

SESSION_STORE_KEY: web.AppKey[SessionStore] = web.AppKey("session_store", SessionStore)

OAUTH_STATE_KEY: web.AppKey[OAuthStateStore] = web.AppKey("oauth_state", OAuthStateStore)

RATE_LIMITER_KEY: web.AppKey[RateLimiter] = web.AppKey("rate_limiter", RateLimiter)

WS_CLIENTS_KEY: web.AppKey[dict[int, set[web.WebSocketResponse]]] = web.AppKey("ws_clients", dict)

JOB_STATE_KEY: web.AppKey[dict[int, dict[str, Any]]] = web.AppKey("job_state", dict)

ACTIVITY_LOG_KEY: web.AppKey[dict[int, deque[dict[str, Any]]]] = web.AppKey("activity_log", dict)

# Callback de eventos ao vivo de cada usuário. Guardar o callback, e não só o ID, deixa o painel
# reassiná-lo em toda conexão: o manager esquece as assinaturas quando a sessão cai por fora da web.
EVENT_HOOKED_USERS_KEY: web.AppKey[dict[int, Any]] = web.AppKey("event_hooked_users", dict)

# O loop só guarda referência fraca das tasks; sem esta, um job em andamento pode ser coletado.
BACKGROUND_TASKS_KEY: web.AppKey[set[asyncio.Task]] = web.AppKey("background_tasks", set)


def _with_login_identity(result: dict[str, Any], identity: dict[str, Any] | None) -> dict[str, Any]:
    """Completa o perfil com o que o login leu do Discord.

    Sem a conta conectada, o perfil do login é o que há. Com ela, o do worker vale; o do login
    só preenche o que faltar quando o worker não conseguiu buscar o perfil completo.
    """
    if not identity:
        return result
    if not result.get("ok"):
        return {
            "ok": True,
            "code": "PROFILE",
            "message": "Perfil lido no login.",
            "profile": dict(identity),
            "status": None,
            "text": "",
            "emoji": None,
            "devices": [],
        }
    profile = result.get("profile") or {}
    if profile.get("complete"):
        return result
    known = {key: value for key, value in profile.items() if value not in (None, "", [])}
    return {**result, "profile": {**identity, **known, "complete": False}}


_SNOWFLAKE_SINGLE_KEYS = frozenset(
    {
        "id",
        "user_id",
        "owner_id",
        "target_id",
        "channel_id",
        "guild_id",
        "last_message_id",
        "desired_channel_id",
    }
)

_SNOWFLAKE_LIST_KEYS = frozenset({"guild_ids", "owner_ids", "target_ids", "whitelist"})


def _stringify_snowflakes(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _SNOWFLAKE_SINGLE_KEYS and v is not None and not isinstance(v, (bool, dict, list)):
                out[k] = str(v)
            elif k in _SNOWFLAKE_LIST_KEYS and isinstance(v, (list, tuple)):
                out[k] = [str(item) for item in v if item is not None]
            else:
                out[k] = _stringify_snowflakes(v)
        return out
    if isinstance(value, list):
        return [_stringify_snowflakes(item) for item in value]
    return value


def _parse_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    raw = str(value if value is not None else "").strip()
    if not raw.isdigit():
        return None
    parsed = int(raw)
    return parsed if parsed > 0 else None


def _parse_non_negative_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    raw = str(value if value is not None else "").strip()
    if not raw.isdigit():
        return default
    return max(0, int(raw))


def _require_session(request: web.Request) -> dict[str, Any]:
    session = request.get(SESSION_REQUEST_KEY)
    if not session:
        raise web.HTTPUnauthorized(
            text=json.dumps(
                {
                    "ok": False,
                    "code": "UNAUTHORIZED",
                    "message": "Faça login para acessar este recurso.",
                }
            ),
            content_type="application/json",
        )
    return session


def _require_manager(request: web.Request) -> SelfbotManager:
    manager = request.app.get(MANAGER_KEY)
    if manager is None:
        raise web.HTTPServiceUnavailable(
            text=json.dumps(
                {
                    "ok": False,
                    "code": "MANAGER_UNAVAILABLE",
                    "message": "Motor de sessões indisponível.",
                }
            ),
            content_type="application/json",
        )
    return manager


def _require_user(request: web.Request) -> tuple[SelfbotManager, int]:
    session = _require_session(request)
    manager = _require_manager(request)
    return manager, int(session["user_id"])


async def _read_json(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _error_response(code: str, message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "code": code, "message": message}, status=status)


def _set_session_cookie(response: web.StreamResponse, request: web.Request, signed_session: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        signed_session,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=is_secure_request(request),
        path="/",
    )


def _record_activity(
    app: web.Application,
    user_id: int,
    category: str,
    title: str,
    detail: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logs = app[ACTIVITY_LOG_KEY].setdefault(int(user_id), deque(maxlen=80))
    entry = {
        "id": f"{int(time.time() * 1000)}-{len(logs)}",
        "timestamp": time.strftime("%H:%M:%S"),
        "category": category,
        "title": title,
        "detail": detail,
        "payload": payload or {},
    }
    logs.appendleft(entry)
    return entry


async def _broadcast_to_user(app: web.Application, user_id: int, event_type: str, data: dict[str, Any]) -> None:
    sockets = app[WS_CLIENTS_KEY].get(int(user_id))
    if not sockets:
        return
    message = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
    stale: list[web.WebSocketResponse] = []
    for ws in tuple(sockets):
        if ws.closed:
            stale.append(ws)
            continue
        try:
            await ws.send_str(message)
        except Exception:
            stale.append(ws)
    for ws in stale:
        sockets.discard(ws)


async def _log_activity(
    app: web.Application,
    user_id: int,
    category: str,
    title: str,
    detail: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    entry = _record_activity(app, user_id, category, title, detail, payload)
    await _broadcast_to_user(app, user_id, "activity", entry)
