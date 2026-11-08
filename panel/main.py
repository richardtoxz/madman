from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable
from typing import Any

from aiohttp import web

from engine.backend import SelfbotManager
from .config import TOKEN_BOT_CONTROLE
from .web import setup_web_routes

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    """Logs da aplicação no stdout, onde o Render e o Docker leem. Nível em LOG_LEVEL (padrão INFO)."""
    level = logging.getLevelName(os.environ.get("LOG_LEVEL", "").strip().upper() or "INFO")
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stdout, force=True)


def install_shutdown_handler(loop: asyncio.AbstractEventLoop, callback: Callable[[], Any]) -> Callable[[], None]:
    """Chama `callback` no loop quando chega SIGTERM (o Render e o Docker mandam antes de parar o processo).

    Devolve uma função que desfaz a instalação.
    """
    try:
        loop.add_signal_handler(signal.SIGTERM, callback)
    except (NotImplementedError, RuntimeError):
        # Windows: o loop não tem add_signal_handler; o handler da stdlib repassa o pedido ao loop.
        try:
            previous = signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(callback))
        except ValueError:  # fora da thread principal: não há como receber o sinal
            return lambda: None
        return lambda: signal.signal(signal.SIGTERM, previous)
    return lambda: loop.remove_signal_handler(signal.SIGTERM)


def parse_web_port(raw_port: str | None = None) -> int | None:
    value = (raw_port if raw_port is not None else os.environ.get("PORT", "8080")).strip()
    try:
        port = int(value)
    except ValueError:
        print(f"⚠️ PORT inválida: {value!r}. Keep-alive desabilitado.")
        return None
    if not 1 <= port <= 65535:
        print(f"⚠️ PORT fora do intervalo: {port}. Keep-alive desabilitado.")
        return None
    return port


MAX_WEB_BODY_BYTES = 64 * 1024


def resolve_web_host(raw_host: str | None = None) -> str:
    if raw_host is not None and raw_host.strip():
        return raw_host.strip()
    if os.environ.get("WEB_LOCAL_ONLY", "").strip() == "1":
        return "127.0.0.1"
    configured = os.environ.get("WEB_BIND_HOST", "").strip()
    return configured or "0.0.0.0"


def warn_insecure_config() -> None:
    """Avisa no log de segurança sobre configuração fraca do painel (sem mostrar o valor do segredo)."""
    from .web.security import log_security_event

    secret = os.environ.get("WEB_SESSION_SECRET", "").strip()
    if not secret:
        log_security_event(
            "config_fraca",
            motivo="WEB_SESSION_SECRET ausente: usando um segredo aleatório, as sessões caem a cada reinício",
        )
    elif len(secret) < 32:
        log_security_event(
            "config_fraca",
            motivo="WEB_SESSION_SECRET ignorado: precisa de 32 caracteres ou mais; usando um aleatório",
        )


def create_web_app(manager: SelfbotManager | None = None, bot: Any = None) -> web.Application:
    app = web.Application(client_max_size=MAX_WEB_BODY_BYTES)
    setup_web_routes(app, manager=manager, bot=bot)
    return app


async def start_web_server(
    manager: SelfbotManager | None = None,
    bot: Any = None,
    *,
    host: str | None = None,
    port: int | None = None,
) -> web.AppRunner | None:
    resolved_port = port if port is not None else parse_web_port()
    if resolved_port is None:
        return None
    resolved_host = resolve_web_host(host)
    warn_insecure_config()
    app = create_web_app(manager=manager, bot=bot)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=resolved_host, port=resolved_port)
        await site.start()
    except OSError as error:
        await runner.cleanup()
        print(f"⚠️ Falha ao iniciar servidor web na porta {resolved_port}: {error}")
        return None
    print(f"🌍 Painel Web & Keep-alive iniciado em http://{resolved_host}:{resolved_port}")
    return runner


def keep_alive(manager: SelfbotManager | None = None, bot: Any = None):
    port = parse_web_port()
    if port is None:
        return None
    if bot is not None:
        bot._enable_web_server = True
        bot._web_port = port
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return loop.create_task(start_web_server(manager=manager, bot=bot, port=port))


async def run_web_only(manager: SelfbotManager | None = None, *, host: str = "0.0.0.0", port: int | None = None):
    mgr = manager or SelfbotManager()
    runner = await start_web_server(manager=mgr, bot=None, host=host, port=port)
    if runner is None:
        return
    stop_event = asyncio.Event()
    uninstall = install_shutdown_handler(asyncio.get_running_loop(), stop_event.set)
    try:
        await stop_event.wait()
    finally:
        uninstall()
        await mgr.shutdown()
        await runner.cleanup()


DEMO_HOST = "127.0.0.1"
DEFAULT_DEMO_PORT = 8090


def demo_port() -> int:
    raw = os.environ.get("DEMO_PORT", "").strip()
    return int(raw) if raw.isdigit() and 0 < int(raw) < 65536 else DEFAULT_DEMO_PORT


def run_demo() -> None:
    """Painel web com dados falsos para desenvolver o front. Só escuta no próprio computador."""
    from .web.demo import DemoManager

    port = demo_port()
    print(f"Modo demo: abra http://{DEMO_HOST}:{port}/__demo/entrar")
    asyncio.run(run_web_only(DemoManager(), host=DEMO_HOST, port=port))


def main():
    configure_logging()
    if "--demo" in sys.argv or os.environ.get("WEB_DEMO", "").strip() == "1":
        run_demo()
        return
    web_only = "--web-only" in sys.argv or os.environ.get("WEB_ONLY", "").strip() == "1"
    manager = SelfbotManager()

    if web_only:
        asyncio.run(run_web_only(manager))
        return

    if not TOKEN_BOT_CONTROLE:
        print("❌ Token do bot controle não configurado! (Dica: use --web-only para iniciar apenas o Painel Web)")
        sys.exit(1)

    from .bot import ControlBot

    bot = ControlBot(manager)
    keep_alive(manager=manager, bot=bot)
    # O logging já foi configurado acima; sem log_handler=None o discord.py instalaria um segundo handler.
    bot.run(TOKEN_BOT_CONTROLE, log_handler=None)


if __name__ == "__main__":
    main()
