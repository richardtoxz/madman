"""Rotas do modo demo. Só são registradas quando o manager é um DemoManager (`--demo`)."""

from __future__ import annotations

from aiohttp import web

from ..demo import DEMO_MODES, DEMO_USER_ID, DEMO_USERNAME, MAX_LATENCY_MS, DemoManager
from ..security import SESSION_COOKIE_NAME
from .common import MANAGER_KEY, SESSION_STORE_KEY, _error_response, _read_json, _require_session, _set_session_cookie


def _demo_manager(request: web.Request) -> DemoManager:
    manager = request.app.get(MANAGER_KEY)
    if not isinstance(manager, DemoManager):
        raise web.HTTPNotFound()
    return manager


async def handle_demo_enter(request: web.Request) -> web.Response:
    """Abre uma sessão da conta demo e manda para o painel, sem passar pelo Discord."""
    _demo_manager(request)
    signed_session, _record = request.app[SESSION_STORE_KEY].create_session(
        user_id=DEMO_USER_ID,
        username="Madman Demo",
        auth_method="demo",
        previous_cookie=request.cookies.get(SESSION_COOKIE_NAME),
        identity={"username": DEMO_USERNAME, "display_name": "Madman Demo", "avatar_url": None},
    )
    response = web.HTTPFound("/")
    _set_session_cookie(response, request, signed_session)
    return response


async def handle_demo_state(request: web.Request) -> web.Response:
    manager = _demo_manager(request)
    _require_session(request)
    return web.json_response(manager.state.as_dict())


async def handle_demo_state_save(request: web.Request) -> web.Response:
    manager = _demo_manager(request)
    _require_session(request)
    body = await _read_json(request)
    mode = body.get("mode")
    latency = body.get("latency_ms")
    connected = body.get("connected")
    if mode is not None and mode not in DEMO_MODES:
        return _error_response("INVALID_DEMO_STATE", "Modo inválido: use normal, empty ou error.")
    if latency is not None and (
        isinstance(latency, bool) or not isinstance(latency, int) or not 0 <= latency <= MAX_LATENCY_MS
    ):
        return _error_response("INVALID_DEMO_STATE", f"Latência precisa ser de 0 a {MAX_LATENCY_MS} ms.")
    if connected is not None and not isinstance(connected, bool):
        return _error_response("INVALID_DEMO_STATE", "Conectado precisa ser verdadeiro ou falso.")
    return web.json_response(manager.set_state(mode=mode, latency_ms=latency, connected=connected))


def setup_demo_routes(app: web.Application) -> None:
    app.router.add_get("/__demo/entrar", handle_demo_enter)
    app.router.add_get("/api/demo/state", handle_demo_state)
    app.router.add_post("/api/demo/state", handle_demo_state_save)
