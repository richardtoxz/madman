from __future__ import annotations

from typing import Any

from aiohttp import web

from engine.backend import SelfbotManager

from ..security import OAuthStateStore, RateLimiter, SessionStore, create_security_middleware
from .. import ui as _ui_module
from .common import (
    ACTIVITY_LOG_KEY,
    BACKGROUND_TASKS_KEY,
    BOT_KEY,
    EVENT_HOOKED_USERS_KEY,
    JOB_STATE_KEY,
    MANAGER_KEY,
    OAUTH_STATE_KEY,
    RATE_LIMITER_KEY,
    SESSION_STORE_KEY,
    WS_CLIENTS_KEY,
)
from .auth import handle_auth_session, handle_discord_callback, handle_discord_login, handle_logout
from .realtime import handle_ws
from .pages import handle_dev_version, handle_health, handle_index
from .demo import setup_demo_routes
from ..demo import DemoManager
from .worker import handle_worker_connect, handle_worker_disconnect, handle_worker_status
from .settings import handle_settings_save
from .protection import handle_protection_status, handle_protection_toggle, handle_protection_whitelist
from .presence import handle_rich_presence_set, handle_rich_presence_status
from .voice import handle_voice_action, handle_voice_status
from .account import (
    handle_account_ack_all,
    handle_account_mentions,
    handle_account_mentions_delete,
    handle_account_profile,
    handle_account_sessions,
    handle_account_status,
    handle_account_status_save,
    handle_account_unread,
)
from .cleanup import (
    handle_cleanup_control,
    handle_cleanup_dm_action,
    handle_cleanup_dms,
    handle_cleanup_execute,
    handle_cleanup_preview,
    handle_cleanup_scan_guilds,
)
from .social import handle_social_action, handle_social_overview, handle_social_protect_closest
from .guilds import handle_guilds_bulk, handle_guilds_list, handle_guilds_whitelist
from .tracker import handle_tracker_start, handle_tracker_status, handle_tracker_stop
from .quests import handle_quests_action, handle_quests_list


__all__ = [
    "ACTIVITY_LOG_KEY",
    "BACKGROUND_TASKS_KEY",
    "BOT_KEY",
    "EVENT_HOOKED_USERS_KEY",
    "JOB_STATE_KEY",
    "MANAGER_KEY",
    "OAUTH_STATE_KEY",
    "RATE_LIMITER_KEY",
    "SESSION_STORE_KEY",
    "WS_CLIENTS_KEY",
    "setup_web_routes",
]


def setup_web_routes(
    app: web.Application,
    manager: SelfbotManager | None = None,
    bot: Any = None,
) -> web.Application:
    app[MANAGER_KEY] = manager
    app[BOT_KEY] = bot
    session_store = SessionStore()
    app[SESSION_STORE_KEY] = session_store
    app[OAUTH_STATE_KEY] = OAuthStateStore(session_store._secret)
    app[RATE_LIMITER_KEY] = RateLimiter()
    app[WS_CLIENTS_KEY] = {}
    app[JOB_STATE_KEY] = {}
    app[ACTIVITY_LOG_KEY] = {}
    app[EVENT_HOOKED_USERS_KEY] = {}
    app[BACKGROUND_TASKS_KEY] = set()

    app.middlewares.append(create_security_middleware(SESSION_STORE_KEY))

    app.router.add_get("/", handle_index)
    app.router.add_get("/dashboard", handle_index)
    if _ui_module.dev_reload_enabled():
        app.router.add_get("/__dev/version", handle_dev_version)
    if isinstance(manager, DemoManager):
        setup_demo_routes(app)
    for tab_path in _ui_module.TAB_PATHS.values():
        app.router.add_get(tab_path, handle_index)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/auth/session", handle_auth_session)
    app.router.add_get("/api/auth/discord/login", handle_discord_login)
    app.router.add_get("/api/auth/discord/callback", handle_discord_callback)
    app.router.add_post("/api/auth/logout", handle_logout)

    app.router.add_get("/api/worker/status", handle_worker_status)
    app.router.add_post("/api/worker/connect", handle_worker_connect)
    app.router.add_post("/api/worker/disconnect", handle_worker_disconnect)

    app.router.add_post("/api/settings", handle_settings_save)
    app.router.add_get("/api/protection/status", handle_protection_status)
    app.router.add_post("/api/protection/toggle", handle_protection_toggle)
    app.router.add_post("/api/protection/whitelist", handle_protection_whitelist)
    app.router.add_get("/api/presence/rich", handle_rich_presence_status)
    app.router.add_post("/api/presence/rich", handle_rich_presence_set)

    app.router.add_get("/api/voice/status", handle_voice_status)
    app.router.add_post("/api/voice/action", handle_voice_action)

    app.router.add_get("/api/account/status", handle_account_status)
    app.router.add_post("/api/account/status", handle_account_status_save)
    app.router.add_get("/api/account/profile", handle_account_profile)
    app.router.add_get("/api/account/sessions", handle_account_sessions)
    app.router.add_get("/api/account/mentions", handle_account_mentions)
    app.router.add_post("/api/account/mentions/delete", handle_account_mentions_delete)
    app.router.add_get("/api/account/unread", handle_account_unread)
    app.router.add_post("/api/account/ack-all", handle_account_ack_all)

    app.router.add_get("/api/cleanup/dms", handle_cleanup_dms)
    app.router.add_post("/api/cleanup/dm-action", handle_cleanup_dm_action)
    app.router.add_post("/api/cleanup/scan-guilds", handle_cleanup_scan_guilds)
    app.router.add_post("/api/cleanup/preview", handle_cleanup_preview)
    app.router.add_post("/api/cleanup/execute", handle_cleanup_execute)
    app.router.add_post("/api/cleanup/control", handle_cleanup_control)

    app.router.add_get("/api/social/overview", handle_social_overview)
    app.router.add_post("/api/social/action", handle_social_action)
    app.router.add_post("/api/social/protect-closest", handle_social_protect_closest)

    app.router.add_get("/api/guilds", handle_guilds_list)
    app.router.add_post("/api/guilds/whitelist", handle_guilds_whitelist)
    app.router.add_post("/api/guilds/bulk", handle_guilds_bulk)

    app.router.add_get("/api/tracker/status", handle_tracker_status)
    app.router.add_post("/api/tracker/start", handle_tracker_start)
    app.router.add_post("/api/tracker/stop", handle_tracker_stop)

    app.router.add_get("/api/quests", handle_quests_list)
    app.router.add_post("/api/quests/action", handle_quests_action)

    app.router.add_get("/api/ws", handle_ws)
    return app
