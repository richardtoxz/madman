from __future__ import annotations

import importlib
import os

from aiohttp import web

from ..security import SESSION_REQUEST_KEY
from .. import ui as _ui_module
from ..demo import DemoManager
from .common import BOT_KEY, MANAGER_KEY


async def handle_index(request: web.Request) -> web.Response:
    if os.environ.get("WEB_DEV_RELOAD", "").strip() == "1":
        importlib.reload(_ui_module)
    demo = isinstance(request.app.get(MANAGER_KEY), DemoManager)
    return web.Response(
        text=_ui_module.render_dashboard_html(demo=demo),
        content_type="text/html",
        charset="utf-8",
    )


async def handle_dev_version(_request: web.Request) -> web.Response:
    """Versão dos arquivos do front para o recarregamento automático. Só é registrada com WEB_DEV_RELOAD=1."""
    return web.json_response({"v": _ui_module.dev_version()}, headers={"Cache-Control": "no-store"})


async def handle_health(request: web.Request) -> web.Response:
    session = request.get(SESSION_REQUEST_KEY)
    if not session:
        return web.json_response({"ok": True, "service": "madman"})
    mgr = request.app.get(MANAGER_KEY)
    ctrl_bot = request.app.get(BOT_KEY)
    processes = getattr(mgr, "active_processes", None) or {}
    active_sessions = sum(1 for proc in processes.values() if getattr(proc, "is_alive", False))
    return web.json_response(
        {
            "ok": True,
            "service": "madman",
            "bot_ready": bool(ctrl_bot and ctrl_bot.is_ready()),
            "active_sessions": active_sessions,
        }
    )
