from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from ..core.bulk import run_bulk
from ..core.config import BulkConfig
from ..core.ipc import emit_line


class GuildManager:
    """Operacoes de servidor baseadas em uma fotografia REST atual da conta."""

    MAX_TRANSIENT_ATTEMPTS = 3

    def __init__(self, client):
        self.client = client
        self.cancelled = False
        self._bulk_lock = asyncio.Lock()

    @staticmethod
    def _error_code(error: Exception | None) -> str:
        if isinstance(error, discord.CaptchaRequired):
            return "CAPTCHA"
        if isinstance(error, discord.RateLimited):
            return "RATE_LIMIT_LONGO"
        if isinstance(error, discord.Forbidden):
            return "SEM_PERMISSAO"
        if isinstance(error, discord.NotFound):
            return "NAO_ENCONTRADO"
        if isinstance(error, discord.HTTPException):
            status = int(getattr(error, "status", 0) or 0)
            return f"HTTP_{status}" if status else "HTTP"
        return type(error).__name__.upper()[:40] if error else "DESCONHECIDO"

    @staticmethod
    def _warning_for(code: str | None) -> str | None:
        if not code:
            return None
        messages = {
            "CAPTCHA": "O Discord exigiu CAPTCHA ao atualizar os servidores.",
            "RATE_LIMIT_LONGO": "O Discord solicitou uma espera longa ao atualizar os servidores.",
            "SEM_PERMISSAO": "O Discord recusou a consulta atualizada dos servidores.",
        }
        return messages.get(code, "Nao foi possivel confirmar a lista atual; exibindo o cache local.")

    @staticmethod
    def _ipc_label(value: Any, limit: int = 80) -> str:
        text = " ".join(str(value or "Servidor desconhecido").split())
        text = text.replace("||REQ:", "REQ:").replace(":", "：")
        return text[:limit] or "Servidor desconhecido"

    @staticmethod
    def _page(items: list[Any], page: int, page_size: int) -> tuple[list[Any], int, int]:
        page_size = max(1, min(int(page_size), 25))
        pages = max(1, (len(items) + page_size - 1) // page_size)
        page = max(0, min(int(page), pages - 1))
        start = page * page_size
        return items[start : start + page_size], page, pages

    async def _sleep_interruptibly(self, seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0.0, seconds)
        while not self.cancelled:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return True
            await asyncio.sleep(min(remaining, 0.5))
        return False

    async def _guild_snapshot(self, *, allow_cache: bool = True) -> tuple[tuple[Any, ...], bool, str | None]:
        """Busca todos os UserGuild; cache e somente fallback para leitura."""

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                guilds = await self.client.fetch_guilds(with_counts=True)
                return tuple(guilds), True, None
            except asyncio.CancelledError:
                raise
            except (discord.CaptchaRequired, discord.RateLimited) as error:
                last_error = error
                break
            except discord.HTTPException as error:
                last_error = error
                status = int(getattr(error, "status", 0) or 0)
                if attempt == 0 and status >= 500:
                    await asyncio.sleep(1.0)
                    continue
                break
            except Exception as error:
                last_error = error
                break

        code = self._error_code(last_error)
        if not allow_cache:
            return (), False, code
        try:
            return tuple(self.client.guilds), False, code
        except Exception:
            return (), False, code

    def _cached_guild(self, guild_id: int):
        try:
            return self.client.get_guild(int(guild_id))
        except (AttributeError, TypeError, ValueError):
            return next(
                (
                    guild
                    for guild in getattr(self.client, "guilds", ())
                    if int(getattr(guild, "id", 0)) == int(guild_id)
                ),
                None,
            )

    def _is_owner(self, guild: Any, *, fresh: bool) -> bool:
        if fresh:
            return bool(getattr(guild, "owner", False))
        current_user_id = int(getattr(getattr(self.client, "user", None), "id", 0))
        return bool(current_user_id and int(getattr(guild, "owner_id", 0) or 0) == current_user_id)

    @staticmethod
    def _fully_muted(settings: Any) -> bool:
        try:
            return bool(settings.muted) and all(
                (
                    settings.level == discord.NotificationLevel.nothing,
                    bool(settings.suppress_everyone),
                    bool(settings.suppress_roles),
                    not bool(settings.mobile_push),
                    bool(settings.mute_scheduled_events),
                    settings.notify_highlights == discord.HighlightLevel.disabled,
                )
            )
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _icon_url(guild: Any) -> str | None:
        icon = getattr(guild, "icon", None)
        if not icon:
            return None
        try:
            return str(icon.with_size(64).url)
        except (AttributeError, TypeError, ValueError):
            return str(getattr(icon, "url", icon))

    def _guild_info(self, guild: Any, *, fresh: bool) -> dict[str, Any]:
        guild_id = int(guild.id)
        cached = self._cached_guild(guild_id)
        settings = getattr(cached, "notification_settings", None) if cached else None
        return {
            "id": guild_id,
            "name": str(getattr(guild, "name", None) or f"Servidor {guild_id}"),
            "owner": self._is_owner(guild, fresh=fresh),
            "muted": self._fully_muted(settings) if settings is not None else None,
            "unavailable": bool(getattr(cached, "unavailable", False)) if cached else False,
            "member_count": getattr(guild, "approximate_member_count", None),
            "presence_count": getattr(guild, "approximate_presence_count", None),
            "icon_url": self._icon_url(guild),
        }

    async def get_stats(self):
        guilds, fresh, error_code = await self._guild_snapshot()
        items = [self._guild_info(guild, fresh=fresh) for guild in guilds]
        return {
            "total": len(items),
            "owners": sum(item["owner"] for item in items),
            "muted": sum(item["muted"] is True for item in items),
            "mute_unknown": sum(item["muted"] is None for item in items),
            "unavailable": sum(item["unavailable"] for item in items),
            "guild_ids": [item["id"] for item in items],
            "owner_ids": [item["id"] for item in items if item["owner"]],
            "fresh": fresh,
            "warning": self._warning_for(error_code),
        }

    async def get_guilds_page(self, page: int = 0, page_size: int = 20):
        guilds, fresh, error_code = await self._guild_snapshot()
        items = [self._guild_info(guild, fresh=fresh) for guild in guilds]
        items.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        page_items, page, pages = self._page(items, page, page_size)
        return {
            "items": page_items,
            "total": len(items),
            "page": page,
            "pages": pages,
            "fresh": fresh,
            "warning": self._warning_for(error_code),
        }

    async def _resolve_guild(self, guild_id: int):
        cached = self._cached_guild(guild_id)
        if cached is not None and not bool(getattr(cached, "unavailable", False)):
            return cached

        fetched = await self.client.fetch_guild(int(guild_id), with_counts=False)
        if bool(getattr(fetched, "unavailable", False)):
            raise RuntimeError("servidor indisponivel")
        return fetched

    async def _mute_guild(self, guild: Any) -> str:
        guild_id = int(guild.id)
        try:
            resolved = await self._resolve_guild(guild_id)
        except discord.NotFound:
            # Se ele sumiu entre a fotografia e a acao, nao ha mais notificacao
            # para silenciar. Se ainda aparece na API, e uma falha real.
            current, fresh, _ = await self._guild_snapshot(allow_cache=False)
            if fresh and guild_id not in {int(item.id) for item in current}:
                return "already"
            raise RuntimeError("servidor ainda listado, mas nao pode ser acessado")

        settings = resolved.notification_settings
        if self._fully_muted(settings):
            return "already"

        updated = await settings.edit(
            muted_until=True,
            level=discord.NotificationLevel.nothing,
            suppress_everyone=True,
            suppress_roles=True,
            mobile_push=False,
            mute_scheduled_events=True,
            notify_highlights=discord.HighlightLevel.disabled,
        )
        if updated is None or not self._fully_muted(updated):
            raise RuntimeError("configuracao nao confirmada pelo Discord")
        return "success"

    async def _verify_left(self, target_ids: set[int]) -> tuple[int | None, str | None]:
        guilds, fresh, error_code = await self._guild_snapshot(allow_cache=False)
        if not fresh:
            return None, error_code
        current_ids = {int(guild.id) for guild in guilds}
        return len(target_ids & current_ids), None

    @staticmethod
    def _empty_result(status: str = "ABORTADO") -> dict[str, Any]:
        return {
            "status": status,
            "processed": 0,
            "total": 0,
            "success": 0,
            "already_done": 0,
            "failed": 0,
            "remaining": None,
            "rate_limits": 0,
            "duration": 0.0,
        }

    async def _run_bulk(
        self,
        *,
        guilds: list[Any],
        operation: Callable[[Any], Awaitable[str | None]],
        prefix: str,
        request_id: str | None,
        verify: Callable[[set[int]], Awaitable[tuple[int | None, str | None]]] | None,
        protected_owners: int,
        whitelisted: int,
        config: BulkConfig | None = None,
    ) -> dict[str, Any]:
        extra_details = []
        if protected_owners:
            extra_details.append(f"proprios protegidos {protected_owners}")
        if whitelisted:
            extra_details.append(f"whitelist {whitelisted}")

        return await run_bulk(
            self,
            guilds,
            prefix=prefix,
            request_id=request_id,
            config=config or BulkConfig(),
            emit=emit_line,
            operation=operation,
            item_id=lambda guild: int(guild.id),
            label=lambda guild: self._ipc_label(f"{getattr(guild, 'name', 'servidor')} ({guild.id})"),
            not_found_outcome="already",
            delay_after_success_only=True,
            # Sem verify, cada mute bem-sucedido ja foi validado pelo GuildSettings retornado.
            verify=verify,
            extra_details=tuple(extra_details),
        )

    async def _bulk_from_fresh_snapshot(
        self,
        *,
        action: str,
        whitelist: set[int],
        prefix: str,
        request_id: str | None,
    ) -> dict[str, Any]:
        if self._bulk_lock.locked():
            summary = "outra operacao de servidores ja esta em andamento nesta conta"
            emit_line(f"{prefix}_RESULT:ABORTADO:{summary}", request_id)
            return self._empty_result()

        async with self._bulk_lock:
            self.cancelled = False
            guilds, fresh, error_code = await self._guild_snapshot(allow_cache=False)
            if not fresh:
                summary = (
                    f"lista atualizada indisponivel; nenhuma alteracao foi realizada ({(error_code or 'erro').lower()})"
                )
                emit_line(f"{prefix}_RESULT:ABORTADO:{summary}", request_id)
                return self._empty_result()

            whitelisted = sum(int(guild.id) in whitelist for guild in guilds)
            protected_owners = 0
            selected = []
            for guild in guilds:
                if int(guild.id) in whitelist:
                    continue
                if action == "LEAVE" and self._is_owner(guild, fresh=True):
                    protected_owners += 1
                    continue
                selected.append(guild)

            operation = (lambda guild: guild.leave()) if action == "LEAVE" else self._mute_guild
            verify = self._verify_left if action == "LEAVE" else None
            return await self._run_bulk(
                guilds=selected,
                operation=operation,
                prefix=prefix,
                request_id=request_id,
                verify=verify,
                protected_owners=protected_owners,
                whitelisted=whitelisted,
            )

    async def leave_all_guilds(self, whitelist, request_id: str | None = None):
        return await self._bulk_from_fresh_snapshot(
            action="LEAVE",
            whitelist={int(guild_id) for guild_id in whitelist},
            prefix="GUILD_LEAVE",
            request_id=request_id,
        )

    async def mute_all_guilds(self, whitelist, request_id: str | None = None):
        return await self._bulk_from_fresh_snapshot(
            action="MUTE",
            whitelist={int(guild_id) for guild_id in whitelist},
            prefix="GUILD_MUTE",
            request_id=request_id,
        )
