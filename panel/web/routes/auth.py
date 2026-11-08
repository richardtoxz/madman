from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode, urlparse

import aiohttp
from aiohttp import web

from ..security import (
    OAUTH_STATE_COOKIE_NAME,
    OAUTH_STATE_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_REQUEST_KEY,
    get_client_ip,
    is_secure_request,
    log_security_event,
)
from ...access import ACCESS_DENIED_MESSAGE, is_user_allowed
from ..discord_identity import identity_profile
from .common import (
    MANAGER_KEY,
    OAUTH_STATE_KEY,
    RATE_LIMITER_KEY,
    SESSION_STORE_KEY,
    _error_response,
    _read_json,
    _record_activity,
    _require_session,
    _set_session_cookie,
)


log = logging.getLogger(__package__)  # mesmo logger de antes: panel.web.routes

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _discord_oauth_config() -> dict[str, str] | None:
    client_id = os.environ.get("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("DISCORD_REDIRECT_URI", "").strip()
    if client_id and client_secret and redirect_uri:
        config = {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "scope": "identify",
        }
        # guilds.join só é pedido quando há um servidor de destino e o bot que fará a entrada.
        join_guild_id = os.environ.get("DISCORD_JOIN_GUILD_ID", "").strip()
        bot_token = os.environ.get("TOKEN_BOT_CONTROLE", "").strip()
        if join_guild_id.isdigit() and int(join_guild_id) > 0 and bot_token:
            config["scope"] = "identify guilds.join"
            config["join_guild_id"] = join_guild_id
            config["bot_token"] = bot_token
        return config
    return None


async def _join_configured_guild(
    http: aiohttp.ClientSession, oauth_cfg: dict[str, str], user_id: int, token_data: dict[str, Any]
) -> bool:
    """Põe quem acabou de autorizar no servidor configurado. Falhas não impedem o login."""
    guild_id = oauth_cfg.get("join_guild_id")
    granted = str(token_data.get("scope") or "").split()
    if not guild_id or "guilds.join" not in granted:
        return False
    try:
        resp = await http.put(
            f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}",
            json={"access_token": token_data["access_token"]},
            headers={"Authorization": f"Bot {oauth_cfg['bot_token']}"},
        )
    except Exception as exc:
        log.warning("Falha ao adicionar %s ao servidor %s: %s", user_id, guild_id, type(exc).__name__)
        return False
    # 201 = entrou agora; 204 = já era membro.
    if resp.status not in (201, 204):
        log.warning("Discord recusou adicionar %s ao servidor %s (HTTP %s)", user_id, guild_id, resp.status)
        return False
    return resp.status == 201


async def handle_auth_session(request: web.Request) -> web.Response:
    session = request.get(SESSION_REQUEST_KEY)
    mgr = request.app.get(MANAGER_KEY)
    oauth_cfg = _discord_oauth_config()
    if not session:
        return web.json_response(
            {
                "ok": True,
                "authenticated": False,
                "oauth_configured": oauth_cfg is not None,
                "worker_connected": False,
            }
        )
    uid = int(session["user_id"])
    worker_connected = bool(mgr and mgr.is_authenticated(uid))
    proc = mgr.active_processes.get(uid) if mgr else None
    worker_username = getattr(proc, "username", None) or session["username"]
    return web.json_response(
        {
            "ok": True,
            "authenticated": True,
            "oauth_configured": oauth_cfg is not None,
            "csrf_token": session["csrf_token"],
            "user": {
                "id": str(uid),
                "username": session["username"],
                "avatar_url": session.get("avatar_url"),
                "auth_method": session.get("auth_method", "discord_oauth"),
            },
            "worker_connected": worker_connected,
            "worker_username": worker_username if worker_connected else None,
        }
    )


async def handle_discord_login(request: web.Request) -> web.Response:
    limiter = request.app[RATE_LIMITER_KEY]
    if not limiter.allow(f"oauth:{get_client_ip(request)}", limit=15, window_seconds=60):
        log_security_event("limite_excedido", request, limite="login 15/min")
        return _error_response("RATE_LIMITED", "Muitas tentativas. Aguarde 1 minuto.", 429)
    oauth_cfg = _discord_oauth_config()
    if not oauth_cfg:
        return _error_response(
            "OAUTH_NOT_CONFIGURED",
            "Configure DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET e DISCORD_REDIRECT_URI no .env.",
        )
    # O cookie de estado fica preso ao endereço aberto; se o Discord vai devolver para outro
    # (ex.: 127.0.0.1 x localhost), o callback não o encontraria. Recomeça no endereço do redirect.
    # Só para redirect local: atrás de proxy o Host pode ser reescrito e isso viraria um laço.
    redirect = urlparse(oauth_cfg["redirect_uri"])
    if redirect.hostname in _LOOPBACK_HOSTS and request.host.lower() != redirect.netloc.lower():
        return web.HTTPFound(f"{redirect.scheme}://{redirect.netloc}{request.path}")
    state, signed_cookie = request.app[OAUTH_STATE_KEY].issue()
    params = urlencode(
        {
            "client_id": oauth_cfg["client_id"],
            "redirect_uri": oauth_cfg["redirect_uri"],
            "response_type": "code",
            "scope": oauth_cfg["scope"],
            "state": state,
            "prompt": "none",
        }
    )
    response = web.HTTPFound(f"https://discord.com/oauth2/authorize?{params}")
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        signed_cookie,
        max_age=OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=is_secure_request(request),
        path="/",
    )
    return response


async def handle_discord_callback(request: web.Request) -> web.Response:
    oauth_cfg = _discord_oauth_config()
    if not oauth_cfg:
        return web.HTTPFound("/?error=oauth_not_configured")
    query_state = request.query.get("state")
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    if not request.app[OAUTH_STATE_KEY].consume(query_state, cookie_state):
        log_security_event("oauth_state_invalido", request, com_cookie="sim" if cookie_state else "nao")
        return _error_response("INVALID_OAUTH_STATE", "Estado OAuth2 inválido ou expirado.")
    code = (request.query.get("code") or "").strip()
    if not code:
        return web.HTTPFound("/?error=missing_code")

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            token_resp = await http.post(
                "https://discord.com/api/v10/oauth2/token",
                data={
                    "client_id": oauth_cfg["client_id"],
                    "client_secret": oauth_cfg["client_secret"],
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": oauth_cfg["redirect_uri"],
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_resp.status != 200:
                return web.HTTPFound("/?error=token_exchange_failed")
            token_data = await token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return web.HTTPFound("/?error=no_access_token")

            me_resp = await http.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me_resp.status != 200:
                return web.HTTPFound("/?error=profile_fetch_failed")
            profile = await me_resp.json()
            user_id = int(profile["id"])
            if not is_user_allowed(user_id):
                log_security_event("login_negado", request, conta=user_id)
                return _error_response("NOT_ALLOWED", ACCESS_DENIED_MESSAGE, 403)
            joined_guild = await _join_configured_guild(http, oauth_cfg, user_id, token_data)
    except Exception:
        return web.HTTPFound("/?error=oauth_network_error")

    username = profile.get("global_name") or profile.get("username") or str(user_id)
    identity = identity_profile(profile)
    signed_session, _ = request.app[SESSION_STORE_KEY].create_session(
        user_id=user_id,
        username=username,
        avatar_url=identity["avatar_url"] if identity else None,
        auth_method="discord_oauth",
        previous_cookie=request.cookies.get(SESSION_COOKIE_NAME),
        identity=identity,
    )
    if joined_guild:
        _record_activity(request.app, user_id, "sistema", "Servidor", "Você entrou no servidor do Madman.")
    response = web.HTTPFound("/")
    response.del_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
    _set_session_cookie(response, request, signed_session)
    return response


async def handle_logout(request: web.Request) -> web.Response:
    session = _require_session(request)
    body = await _read_json(request)
    if body.get("disconnect_worker"):
        mgr = request.app.get(MANAGER_KEY)
        if mgr:
            await mgr.disconnect(int(session["user_id"]))
    request.app[SESSION_STORE_KEY].revoke_session(request.cookies.get(SESSION_COOKIE_NAME))
    resp = web.json_response({"ok": True, "message": "Sessão encerrada."})
    resp.del_cookie(SESSION_COOKIE_NAME, path="/")
    return resp
