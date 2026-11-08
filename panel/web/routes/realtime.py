from __future__ import annotations

import asyncio
import json
import time
import weakref
from typing import Any

import aiohttp
from aiohttp import web

from engine.backend import SelfbotManager
from ipc_protocol import parse_cleanup_result

from ..security import MAX_WS_PER_USER, WS_CLOSE_TOO_MANY, log_security_event, verify_same_origin
from .common import (
    BACKGROUND_TASKS_KEY,
    EVENT_HOOKED_USERS_KEY,
    JOB_STATE_KEY,
    WS_CLIENTS_KEY,
    _broadcast_to_user,
    _error_response,
    _log_activity,
    _record_activity,
    _require_session,
    _stringify_snowflakes,
)


# Hora em que cada WebSocket abriu, para saber qual é o mais antigo (não segura sockets fechados).
_WS_OPENED_AT: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _ensure_worker_event_hooks(app: web.Application, manager: SelfbotManager, user_id: int) -> None:
    uid = int(user_id)
    hooked = app[EVENT_HOOKED_USERS_KEY]
    callback = hooked.get(uid)
    if callback is None:
        callback = hooked[uid] = _worker_line_handler(app, uid)
    # Assinar de novo é idempotente e devolve o callback ao processo novo depois de uma
    # desconexão que não passou pela rota web.
    for prefix in (
        "EVENT:PROTECTION:",
        "EVENT:DM_",
        "EVENT:REL_",
        "EVENT:GUILD_",
        "TRACKER:",
        "PROCESS:",
    ):
        manager.subscribe_events(uid, prefix, callback)


def _worker_line_handler(app: web.Application, uid: int):
    async def _on_worker_line(line: str):
        for prefix, event_type in (("EVENT:DM_UPSERT:", "dm_upsert"), ("EVENT:DM_REMOVE:", "dm_remove")):
            if line.startswith(prefix):
                try:
                    dm_data = _stringify_snowflakes(json.loads(line.removeprefix(prefix).strip()))
                    if isinstance(dm_data, dict):
                        await _broadcast_to_user(app, uid, event_type, dm_data)
                except Exception:
                    pass
                return

        if line.startswith("EVENT:REL_"):
            await _broadcast_to_user(app, uid, "rel_event", {"raw": line})
            return

        if line.startswith("EVENT:GUILD_"):
            await _broadcast_to_user(app, uid, "guild_event", {"raw": line})
            return

        category = "sistema"
        title = "Evento do Worker"
        if line.startswith("EVENT:PROTECTION:"):
            category = "protecao"
            title = "Proteção Acionada"
        elif line.startswith("TRACKER:"):
            category = "tracker"
            title = "Alerta do Tracker"
        elif line.startswith("PROCESS:"):
            category = "sistema"
            title = "Supervisor do Processo"
        await _log_activity(app, uid, category, title, line, {"raw": line})

    return _on_worker_line


def _start_streaming_job(
    app: web.Application,
    user_id: int,
    job_name: str,
    runner_coro_factory,
) -> dict[str, Any]:
    uid = int(user_id)
    current = app[JOB_STATE_KEY].get(uid)
    if current and current.get("running"):
        return {
            "ok": False,
            "code": "JOB_ALREADY_RUNNING",
            "message": f"Já existe uma operação em andamento ({current.get('name')}).",
            "job": current,
        }

    job_state: dict[str, Any] = {
        "name": job_name,
        "running": True,
        "status": "STARTING",
        "current": 0,
        "total": 0,
        "detail": "Iniciando operação...",
        "result": None,
        "started_at": time.time(),
    }
    app[JOB_STATE_KEY][uid] = job_state

    async def update_callback(curr, total, status, detail):
        if curr is not None:
            job_state["current"] = int(curr)
        if total is not None:
            job_state["total"] = int(total)
        if status:
            job_state["status"] = str(status)
        if detail:
            job_state["detail"] = str(detail)
        await _broadcast_to_user(app, uid, "job_progress", dict(job_state))

    async def _run():
        await _log_activity(app, uid, "limpeza", f"Iniciado: {job_name}", "Operação em lote iniciada")
        try:
            raw_result = await runner_coro_factory(update_callback)
            parsed = parse_cleanup_result(raw_result, fallback_total=job_state.get("total", 0))
            job_state["running"] = False
            job_state["status"] = parsed.get("status", "COMPLETADO")
            job_state["result"] = parsed
            job_state["detail"] = parsed.get("text", str(raw_result))
            done_entry = _record_activity(
                app,
                uid,
                "limpeza",
                f"Concluído: {job_name}",
                job_state["detail"],
                parsed,
            )
            await _broadcast_to_user(app, uid, "job_result", dict(job_state))
            await _broadcast_to_user(app, uid, "activity", done_entry)
        except Exception as exc:
            job_state["running"] = False
            job_state["status"] = "ERRO"
            job_state["detail"] = f"Falha na execução: {type(exc).__name__}"
            await _broadcast_to_user(app, uid, "job_result", dict(job_state))

    tasks = app[BACKGROUND_TASKS_KEY]
    task = asyncio.create_task(_run(), name=f"web-job-{uid}-{job_name}")
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"ok": True, "job": dict(job_state)}


async def handle_ws(request: web.Request) -> web.StreamResponse:
    if not verify_same_origin(request):
        log_security_event("origem_invalida", request, origem=request.headers.get("Origin") or "-")
        return _error_response("ORIGIN_MISMATCH", "Origem inválida.", 403)
    session = _require_session(request)
    uid = int(session["user_id"])

    ws = web.WebSocketResponse(heartbeat=25.0)
    await ws.prepare(request)
    sockets = request.app[WS_CLIENTS_KEY].setdefault(uid, set())
    sockets.add(ws)
    _WS_OPENED_AT[ws] = time.monotonic()
    # Teto de abas por conta: a mais antiga cede o lugar (a aba em uso é a mais nova).
    # Sem teto, uma conta abriria conexões sem fim e esgotaria a memória do servidor.
    while len(sockets) > MAX_WS_PER_USER:
        oldest = min(sockets, key=lambda s: _WS_OPENED_AT.get(s, 0.0))
        sockets.discard(oldest)
        log_security_event("websockets_demais", request, limite=MAX_WS_PER_USER)
        await oldest.close(code=WS_CLOSE_TOO_MANY, message=b"Painel aberto em outra aba.")

    try:
        job = request.app[JOB_STATE_KEY].get(uid)
        if job:
            await ws.send_str(json.dumps({"type": "job_progress", "data": job}, ensure_ascii=False))
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT and msg.data == "ping":
                await ws.send_str(json.dumps({"type": "pong"}))
            elif msg.type == aiohttp.WSMsgType.ERROR:
                break
    finally:
        sockets.discard(ws)
    return ws
