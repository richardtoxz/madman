from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

SESSION_COOKIE_NAME = "madman_session"
OAUTH_STATE_COOKIE_NAME = "madman_oauth_state"
SESSION_TTL_SECONDS = 12 * 3600
OAUTH_STATE_TTL_SECONDS = 600
MAX_ACTIVE_SESSIONS = 2048
MAX_OAUTH_STATES = 2048
MAX_RATE_LIMIT_BUCKETS = 4096
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT_PATHS = {"/api/auth/discord/login"}
SESSION_REQUEST_KEY: web.RequestKey[dict[str, Any] | None] = web.RequestKey("session", dict[str, Any] | None)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
# Abas abertas ao mesmo tempo por conta; a mais antiga é fechada quando passa disso.
MAX_WS_PER_USER = 5
# Código de fechamento (faixa de aplicação 4000-4999): o painel não reconecta sozinho.
WS_CLOSE_TOO_MANY = 4008

# Eventos de segurança para quem mantém o site: tentativas barradas, limites e configuração fraca.
# Nunca registra tokens, cookies ou segredos.
security_log = logging.getLogger("madman.security")
_LOG_UNSAFE = re.compile(r"[\x00-\x1f\x7f]+")


def _log_value(value: Any, limit: int = 120) -> str:
    """Valor de log em uma linha só: sem caracteres de controle (\\n forjaria um evento falso)."""
    return _LOG_UNSAFE.sub("?", str(value))[:limit]


def log_security_event(event: str, request: web.Request | None = None, **fields: Any) -> None:
    parts = [f"evento={event}"]
    if request is not None:
        session = request.get(SESSION_REQUEST_KEY)
        parts.append(f"ip={_log_value(get_client_ip(request), 64)}")
        parts.append(f"caminho={_log_value(request.method)} {_log_value(request.path)}")
        if session:
            parts.append(f"usuario={int(session['user_id'])}")
    parts.extend(f"{key}={_log_value(value)}" for key, value in fields.items())
    security_log.warning(" ".join(parts))


def _load_or_generate_secret() -> bytes:
    configured = os.environ.get("WEB_SESSION_SECRET", "").strip()
    if configured and len(configured) >= 32:
        return configured.encode("utf-8")
    return secrets.token_bytes(32)


def _trusted_proxy_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """IPs ou faixas CIDR (ex.: 10.0.0.0/8) dos proxies confiáveis. Entradas inválidas são ignoradas.

    Faixas servem para proxies cujo IP muda, como o proxy interno do Render.
    """
    raw = os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1")
    networks = []
    for item in raw.split(","):
        try:
            networks.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted_proxy_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address.version == network.version and address in network for network in _trusted_proxy_networks())


def is_trusted_proxy_peer(request: web.Request) -> bool:
    return _is_trusted_proxy_ip(request.remote or "")


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def get_client_ip(request: web.Request) -> str:
    """IP real do cliente. Os cabeçalhos de proxy só valem quando a conexão vem de um proxy confiável.

    O CF-Connecting-IP é escrito pela Cloudflare e o cliente não consegue forjá-lo. No X-Forwarded-For,
    cada proxy ACRESCENTA quem o chamou ao final, então o começo do cabeçalho é do próprio cliente:
    lê da direita para a esquerda e fica com o primeiro IP que não é de um proxy confiável.
    """
    peer = (request.remote or "127.0.0.1").strip() or "127.0.0.1"
    if not is_trusted_proxy_peer(request):
        return peer
    cf_ip = _valid_ip(request.headers.get("CF-Connecting-IP") or "")
    if cf_ip:
        return cf_ip
    for hop in reversed((request.headers.get("X-Forwarded-For") or "").split(",")):
        hop = hop.strip()
        if not hop or _is_trusted_proxy_ip(hop):
            continue
        # Um valor inválido onde deveria estar o cliente: o cabeçalho não é confiável.
        return _valid_ip(hop) or peer
    return peer


def is_secure_request(request: web.Request) -> bool:
    if request.secure:
        return True
    if os.environ.get("WEB_FORCE_SECURE_COOKIE", "").strip() == "1":
        return True
    if is_trusted_proxy_peer(request):
        proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if proto == "https":
            return True
    return False


class SessionStore:
    """Server-side session store with HMAC-signed cookies, cap protection, and anti-fixation rotation."""

    def __init__(
        self,
        secret: bytes | None = None,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        max_sessions: int = MAX_ACTIVE_SESSIONS,
    ):
        self._secret = secret or _load_or_generate_secret()
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._sessions: dict[str, dict[str, Any]] = {}

    def _sign(self, session_id: str) -> str:
        digest = hmac.new(self._secret, session_id.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{session_id}.{digest}"

    def _unsign(self, cookie_value: str | None) -> str | None:
        if not cookie_value or "." not in cookie_value:
            return None
        session_id, _, signature = cookie_value.partition(".")
        if not session_id or not signature or len(session_id) > 128:
            return None
        expected = hmac.new(self._secret, session_id.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        return session_id

    def _prune_expired(self, now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        expired = [sid for sid, data in self._sessions.items() if data.get("expires_at", 0) <= ts]
        for sid in expired:
            self._sessions.pop(sid, None)
        while len(self._sessions) >= self._max_sessions:
            oldest_sid = next(iter(self._sessions))
            self._sessions.pop(oldest_sid, None)

    def create_session(
        self,
        *,
        user_id: int,
        username: str,
        avatar_url: str | None = None,
        auth_method: str = "discord_oauth",
        previous_cookie: str | None = None,
        identity: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if previous_cookie:
            old_sid = self._unsign(previous_cookie)
            if old_sid:
                self._sessions.pop(old_sid, None)
        self._prune_expired()

        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = time.time()
        record = {
            "session_id": session_id,
            "user_id": int(user_id),
            "username": str(username),
            "avatar_url": avatar_url,
            "auth_method": auth_method,
            # Perfil visual lido do /users/@me no login (avatar, banner, insígnias...).
            "identity": identity,
            "csrf_token": csrf_token,
            "created_at": now,
            "expires_at": now + self._ttl,
        }
        self._sessions[session_id] = record
        return self._sign(session_id), dict(record)

    def get_session(self, cookie_value: str | None) -> dict[str, Any] | None:
        session_id = self._unsign(cookie_value)
        if not session_id:
            return None
        record = self._sessions.get(session_id)
        if not record:
            return None
        now = time.time()
        if record.get("expires_at", 0) <= now:
            self._sessions.pop(session_id, None)
            return None
        return dict(record)

    def revoke_session(self, cookie_value: str | None) -> bool:
        session_id = self._unsign(cookie_value)
        if not session_id:
            return False
        return self._sessions.pop(session_id, None) is not None


class OAuthStateStore:
    """One-time state tokens bound to browser cookie to prevent OAuth Login CSRF."""

    def __init__(
        self,
        secret: bytes,
        ttl_seconds: int = OAUTH_STATE_TTL_SECONDS,
        max_states: int = MAX_OAUTH_STATES,
    ):
        self._secret = secret
        self._ttl = ttl_seconds
        self._max_states = max_states
        self._states: dict[str, float] = {}

    def issue(self) -> tuple[str, str]:
        now = time.time()
        expired = [st for st, exp in self._states.items() if exp <= now]
        for st in expired:
            self._states.pop(st, None)
        while len(self._states) >= self._max_states:
            oldest = next(iter(self._states))
            self._states.pop(oldest, None)
        state = secrets.token_urlsafe(32)
        self._states[state] = now + self._ttl
        sig = hmac.new(self._secret, state.encode("ascii"), hashlib.sha256).hexdigest()
        return state, f"{state}.{sig}"

    def consume(self, query_state: str | None, cookie_value: str | None) -> bool:
        if not query_state or not cookie_value or "." not in cookie_value:
            return False
        cookie_state, _, sig = cookie_value.partition(".")
        expected_sig = hmac.new(self._secret, cookie_state.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False
        if not hmac.compare_digest(query_state, cookie_state):
            return False
        expires_at = self._states.pop(cookie_state, None)
        if expires_at is None or expires_at <= time.time():
            return False
        return True


class RateLimiter:
    """Bounded sliding-window rate limiter per client IP or user key."""

    def __init__(self, max_buckets: int = MAX_RATE_LIMIT_BUCKETS):
        self._max_buckets = max_buckets
        self._buckets: dict[str, deque[float]] = {}

    def allow(self, key: str, *, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        bucket = self._buckets.get(key)
        if bucket is not None:
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                self._buckets.pop(key, None)
                bucket = None

        if bucket is None:
            if len(self._buckets) >= self._max_buckets:
                # Prune stale keys first; if still full, evict oldest key
                stale_keys = [k for k, q in self._buckets.items() if not q or q[-1] <= cutoff]
                for k in stale_keys:
                    self._buckets.pop(k, None)
                if len(self._buckets) >= self._max_buckets:
                    oldest_key = next(iter(self._buckets))
                    self._buckets.pop(oldest_key, None)
            bucket = deque()
            self._buckets[key] = bucket

        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def verify_same_origin(request: web.Request) -> bool:
    """Verifies that Origin/Referer and Sec-Fetch-Site match the target Host header."""
    host = (request.headers.get("Host") or "").strip().lower()
    if not host:
        return False

    sec_fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if sec_fetch_site and sec_fetch_site not in {"same-origin", "none"}:
        return False

    origin = (request.headers.get("Origin") or "").strip()
    if origin:
        if origin == "null":
            return False
        parsed = urlsplit(origin)
        return bool(parsed.netloc and parsed.netloc.lower() == host)

    referer = (request.headers.get("Referer") or "").strip()
    if referer:
        parsed = urlsplit(referer)
        return bool(parsed.netloc and parsed.netloc.lower() == host)

    if os.environ.get("WEB_REQUIRE_ORIGIN", "").strip() == "1":
        return False

    # Allow missing Origin/Referer only for local loopback test clients when Sec-Fetch-Site is also absent
    host_only = host.partition(":")[0]
    return host_only in _LOOPBACK_HOSTS


def create_security_middleware(session_store_key: web.AppKey[SessionStore]):
    @web.middleware
    async def security_middleware(request: web.Request, handler):
        store = request.app[session_store_key]
        raw_cookie = request.cookies.get(SESSION_COOKIE_NAME)
        session = store.get_session(raw_cookie)
        request[SESSION_REQUEST_KEY] = session

        path = request.path
        if path.startswith("/api/") and request.method in MUTATING_METHODS:
            if not verify_same_origin(request):
                log_security_event("origem_invalida", request, origem=request.headers.get("Origin") or "-")
                return web.json_response(
                    {
                        "ok": False,
                        "code": "ORIGIN_MISMATCH",
                        "message": "Origem da requisição inválida.",
                    },
                    status=403,
                )
            if path not in CSRF_EXEMPT_PATHS:
                if not session:
                    return web.json_response(
                        {
                            "ok": False,
                            "code": "UNAUTHORIZED",
                            "message": "Sessão expirada ou não autenticada.",
                        },
                        status=401,
                    )
                csrf_header = (request.headers.get("X-CSRF-Token") or "").strip()
                expected_csrf = session.get("csrf_token") or ""
                if not csrf_header or not hmac.compare_digest(csrf_header, expected_csrf):
                    log_security_event("csrf_invalido", request, enviado="sim" if csrf_header else "nao")
                    return web.json_response(
                        {
                            "ok": False,
                            "code": "CSRF_INVALID",
                            "message": "Token CSRF ausente ou inválido.",
                        },
                        status=403,
                    )

        response = await handler(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        if is_secure_request(request):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
        else:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self' https://cdn.discordapp.com https://fonts.googleapis.com https://fonts.gstatic.com; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "img-src 'self' data: https://cdn.discordapp.com https://media.discordapp.net; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
            )
        return response

    return security_middleware
