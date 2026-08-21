from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterable, Iterable

import discord

from .ipc import emit_line


class MessageStateMixin:
    async def _reboot_state(self):
        current_task = asyncio.current_task()
        previous_task = self._current_task
        if (
            previous_task
            and previous_task is not current_task
            and not previous_task.done()
        ):
            # Primeiro solicita encerramento cooperativo para que a operacao antiga
            # consiga emitir seu resumo CANCELADO ao painel.
            self.cancelled = True
            self.control_state.cancelled = True
            try:
                await asyncio.wait_for(asyncio.shield(previous_task), timeout=5.0)
            except asyncio.TimeoutError:
                previous_task.cancel()
                try:
                    await previous_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                if current_task and current_task.cancelling():
                    raise
                pass
            except Exception:
                pass

        self.cancelled = False
        self.paused = False
        self.control_state.cancelled = False
        self.control_state.paused = False
        self.control_state.started = False
        self._unavailable_sources.clear()
        self._current_task = current_task

    async def _sleep_interruptibly(self, delay: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0.0, delay)
        while not self.cancelled:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return True
            await asyncio.sleep(min(remaining, 0.5))
        return False

    async def _wait_while_paused(self) -> bool:
        while self.paused and not self.cancelled:
            await asyncio.sleep(0.25)
        return not self.cancelled

    def _is_my_message(self, message: discord.Message) -> bool:
        author = getattr(message, "author", None)
        current_user = getattr(self.client, "user", None)
        return bool(
            author
            and current_user
            and getattr(author, "id", None) == getattr(current_user, "id", None)
        )

    def _is_deletable_message(self, message: discord.Message) -> bool:
        if not self._is_my_message(message):
            return False

        message_type = getattr(message, "type", None)
        is_deletable = getattr(message_type, "is_deletable", None)
        if callable(is_deletable):
            return bool(is_deletable())

        # Tipos novos/desconhecidos devem ser tentados. A API ainda e a fonte final
        # de verdade e as excecoes de delete sao tratadas individualmente.
        return True


class MessageDeleteMixin:
    async def _delete_one_message(
        self,
        message: discord.Message,
        prefix: str,
        request_id: str | None,
    ) -> str:
        if not self._is_deletable_message(message):
            return "skipped"

        transient_attempts = 0
        rate_limit_count = 0

        while not self.cancelled:
            if not await self._wait_while_paused():
                return "cancelled"

            try:
                await message.delete()
                return "deleted"
            except discord.NotFound:
                # Concorrencia normal: a mensagem ja desapareceu entre a busca e
                # a chamada DELETE. Isso nao transforma a limpeza em falha.
                return "missing"
            except discord.Forbidden:
                emit_line(f"{prefix}_FORBIDDEN:{message.id}", request_id)
                return "forbidden"
            except discord.CaptchaRequired:
                emit_line(f"{prefix}_ABORT_CAPTCHA", request_id)
                return "captcha"
            except discord.RateLimited as error:
                rate_limit_count += 1
                wait_time = max(float(error.retry_after), 1.0)
                emit_line(
                    f"{prefix}_RATELIMIT:{rate_limit_count}:{wait_time:.1f}",
                    request_id,
                )
                if getattr(error, "cloudflare", False):
                    return "ratelimited"
                if not await self._sleep_interruptibly(wait_time):
                    return "cancelled"
            except discord.HTTPException as error:
                status = int(getattr(error, "status", 0) or 0)

                # O cliente HTTP da biblioteca ja aguarda rate limits comuns.
                # Este ramo cobre apenas 429 nao tratavel/sub-rate-limit exposto.
                if status == 429:
                    rate_limit_count += 1
                    retry_after = getattr(error, "retry_after", None)
                    if retry_after is None:
                        retry_after = getattr(error, "json", {}).get("retry_after", 5.0)
                    wait_time = max(float(retry_after or 5.0), 1.0)
                    emit_line(
                        f"{prefix}_RATELIMIT:{rate_limit_count}:{wait_time:.1f}",
                        request_id,
                    )
                    if rate_limit_count >= self.config.MAX_429_CONSECUTIVE:
                        return "ratelimited"
                    if not await self._sleep_interruptibly(wait_time):
                        return "cancelled"
                    continue

                # A biblioteca faz as tentativas de transporte; uma pequena
                # segunda camada cobre indisponibilidades temporarias entre lotes.
                if status >= 500 and transient_attempts < 3:
                    transient_attempts += 1
                    wait_time = min(2**transient_attempts, 8)
                    emit_line(
                        f"{prefix}_RETRY:{status}:{transient_attempts}:{wait_time}",
                        request_id,
                    )
                    if not await self._sleep_interruptibly(wait_time):
                        return "cancelled"
                    continue

                emit_line(f"{prefix}_ERROR:{status or 'HTTP'}:{message.id}", request_id)
                return "failed"
            except asyncio.CancelledError:
                raise
            except Exception as error:
                emit_line(
                    f"{prefix}_ERROR:{type(error).__name__}:{message.id}", request_id
                )
                return "failed"

        return "cancelled"

    async def _delete_messages_adaptive(
        self,
        messages: AsyncIterable[discord.Message] | Iterable[discord.Message],
        prefix: str = "MSG_DEL",
        request_id: str | None = None,
        *,
        total_hint: int = 0,
    ) -> dict[str, int | float | bool]:
        stats: dict[str, int | float | bool] = {
            "deletadas": 0,
            "ausentes": 0,
            "ignoradas": 0,
            "proibidas": 0,
            "falhas": 0,
            "processadas": 0,
            "ratelimits": 0,
            "abortado": False,
            "tempo": 0.0,
        }
        start_time = asyncio.get_running_loop().time()
        current_delay = self.config.BASE_DELAY
        consecutive_ok = 0

        async def process(message: discord.Message) -> bool:
            nonlocal current_delay, consecutive_ok

            if self.cancelled:
                return False
            if not await self._wait_while_paused():
                return False

            processed = int(stats["processadas"])
            if processed and processed % self.config.BATCH_SIZE == 0:
                pause = random.uniform(
                    self.config.BATCH_PAUSE_MIN, self.config.BATCH_PAUSE_MAX
                )
                emit_line(
                    f"{prefix}_BATCH_PAUSE:{pause:.1f}:{processed}:{max(total_hint, processed)}",
                    request_id,
                )
                if not await self._sleep_interruptibly(pause):
                    return False

            outcome = await self._delete_one_message(message, prefix, request_id)
            stats["processadas"] = int(stats["processadas"]) + 1

            if outcome == "deleted":
                stats["deletadas"] = int(stats["deletadas"]) + 1
                consecutive_ok += 1
                if consecutive_ok >= self.config.COOLDOWN_AFTER:
                    current_delay = max(
                        self.config.MIN_DELAY,
                        current_delay - self.config.COOLDOWN_STEP,
                    )
                    consecutive_ok = 0
            elif outcome == "missing":
                stats["ausentes"] = int(stats["ausentes"]) + 1
            elif outcome == "skipped":
                stats["ignoradas"] = int(stats["ignoradas"]) + 1
            elif outcome == "forbidden":
                stats["proibidas"] = int(stats["proibidas"]) + 1
                consecutive_ok = 0
            elif outcome in {"captcha", "ratelimited"}:
                stats["falhas"] = int(stats["falhas"]) + 1
                stats["abortado"] = True
            elif outcome == "failed":
                stats["falhas"] = int(stats["falhas"]) + 1
                consecutive_ok = 0

            current = int(stats["processadas"])
            displayed_total = max(total_hint, current)
            if current == 1 or current % 5 == 0 or current >= displayed_total:
                emit_line(f"PROGRESS:{current}:{displayed_total}", request_id)

            if stats["abortado"] or outcome == "cancelled":
                return False
            return await self._sleep_interruptibly(current_delay)

        if isinstance(messages, AsyncIterable):
            async for message in messages:
                if not await process(message):
                    break
        else:
            for message in messages:
                if not await process(message):
                    break

        stats["tempo"] = asyncio.get_running_loop().time() - start_time
        return stats


class MessageLookupMixin:
    @staticmethod
    def _ipc_label(value, *, maximum: int = 160) -> str:
        text = " ".join(str(value).replace("\x00", "").splitlines()).strip()
        return text.replace(":", "：")[:maximum] or "Desconhecido"

    @staticmethod
    def _lookup_error_code(error: Exception) -> str:
        if isinstance(error, discord.Forbidden):
            return "FORBIDDEN"
        if isinstance(error, discord.NotFound):
            return "NOT_FOUND"
        if isinstance(error, discord.RateLimited):
            return "RATE_LIMITED"
        if isinstance(error, discord.HTTPException):
            return f"HTTP_{getattr(error, 'status', 0) or 'ERROR'}"
        return type(error).__name__

    def _mark_unavailable(self, kind: str, target_id: int, error: Exception) -> str:
        code = self._lookup_error_code(error)
        self._unavailable_sources.add((kind, int(target_id or 0), code))
        return code

    async def _count_search_messages(
        self, destination, request_id: str | None = None
    ) -> int | None:
        expected: int | None = None
        returned = 0
        deletable = 0
        indexing = False

        try:
            async for message in destination.search(
                authors=[self.client.user], limit=None, oldest_first=False
            ):
                if self.cancelled:
                    return deletable
                returned += 1
                if expected is None:
                    value = getattr(message, "total_results", None)
                    expected = int(value) if value is not None else None
                indexing = indexing or bool(
                    getattr(message, "doing_deep_historical_index", False)
                )
                if self._is_deletable_message(message):
                    deletable += 1
                if returned % 100 == 0:
                    emit_line(f"COUNT_PROGRESS:{deletable}", request_id)
        except (discord.HTTPException, discord.RateLimited, TypeError, ValueError) as error:
            emit_line(
                f"INFO:SEARCH_FALLBACK:{self._lookup_error_code(error)}", request_id
            )
            return None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            emit_line(
                f"INFO:SEARCH_FALLBACK:{type(error).__name__}", request_id
            )
            return None

        # Sem primeiro resultado nao ha como distinguir busca vazia de indice ainda
        # indisponivel. O historico completo faz essa verificacao sem suposicoes.
        if expected is None or indexing or returned < expected:
            reason = "INDEXING" if indexing else "INCOMPLETE"
            emit_line(f"INFO:SEARCH_FALLBACK:{reason}", request_id)
            return None
        return deletable

    async def _count_channel_history(
        self, channel, request_id: str | None = None
    ) -> int:
        count = 0
        try:
            async for message in channel.history(limit=None, oldest_first=False):
                if self.cancelled:
                    break
                if self._is_deletable_message(message):
                    count += 1
                    if count % 100 == 0:
                        emit_line(f"COUNT_PROGRESS:{count}", request_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, discord.RateLimited) as error:
            code = self._mark_unavailable(
                "history", getattr(channel, "id", 0), error
            )
            emit_line(
                f"INFO:HISTORY_UNAVAILABLE:{getattr(channel, 'id', 0)}:"
                f"{code}",
                request_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = self._mark_unavailable(
                "history", getattr(channel, "id", 0), error
            )
            emit_line(
                f"INFO:HISTORY_UNAVAILABLE:{getattr(channel, 'id', 0)}:{code}",
                request_id,
            )
        return count

    async def _base_guild_channels(self, guild, request_id: str | None = None):
        channels = list(getattr(guild, "channels", []))
        if channels:
            return channels
        try:
            return list(await guild.fetch_channels())
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, discord.RateLimited) as error:
            code = self._mark_unavailable(
                "guild_channels", getattr(guild, "id", 0), error
            )
            emit_line(
                f"INFO:GUILD_CHANNELS_UNAVAILABLE:{code}",
                request_id,
            )
            return []
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = self._mark_unavailable(
                "guild_channels", getattr(guild, "id", 0), error
            )
            emit_line(f"INFO:GUILD_CHANNELS_UNAVAILABLE:{code}", request_id)
            return []

    async def _iter_guild_targets(self, guild, request_id: str | None = None):
        seen_ids: set[int] = set()
        channels = await self._base_guild_channels(guild, request_id)

        for target in [*channels, *getattr(guild, "threads", [])]:
            target_id = getattr(target, "id", None)
            if not target_id or target_id in seen_ids or not hasattr(target, "history"):
                continue
            seen_ids.add(target_id)
            yield target

        # Guild.search normalmente cobre threads arquivadas. Esta enumeracao e o
        # fallback documentado para quando a indexacao de busca estiver incompleta.
        for parent in channels:
            archived_threads = getattr(parent, "archived_threads", None)
            if not callable(archived_threads):
                continue

            variants = [{}]
            if isinstance(parent, discord.TextChannel):
                variants.append({"private": True, "joined": True})

            for kwargs in variants:
                try:
                    async for thread in archived_threads(limit=None, **kwargs):
                        thread_id = getattr(thread, "id", None)
                        if not thread_id or thread_id in seen_ids:
                            continue
                        seen_ids.add(thread_id)
                        yield thread
                except (discord.Forbidden, discord.NotFound, discord.HTTPException, discord.RateLimited) as error:
                    code = self._mark_unavailable(
                        "archived_threads", getattr(parent, "id", 0), error
                    )
                    emit_line(
                        f"INFO:ARCHIVED_THREADS_UNAVAILABLE:{getattr(parent, 'id', 0)}:"
                        f"{code}",
                        request_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    code = self._mark_unavailable(
                        "archived_threads", getattr(parent, "id", 0), error
                    )
                    emit_line(
                        f"INFO:ARCHIVED_THREADS_UNAVAILABLE:"
                        f"{getattr(parent, 'id', 0)}:{code}",
                        request_id,
                    )

    async def _count_guild_history(
        self, guild, request_id: str | None = None
    ) -> int:
        count = 0
        async for target in self._iter_guild_targets(guild, request_id):
            count += await self._count_channel_history(target, request_id)
            if self.cancelled:
                break
        return count

    async def _count_destination_messages(
        self, destination, request_id: str | None = None, *, guild: bool = False
    ) -> int:
        count = await self._count_search_messages(destination, request_id)
        if count is not None:
            return count
        emit_line("STATUS:CONTANDO_HISTORY", request_id)
        if guild:
            return await self._count_guild_history(destination, request_id)
        return await self._count_channel_history(destination, request_id)

    async def _channel_history_has_message(
        self, channel, request_id: str | None = None
    ) -> bool:
        try:
            async for message in channel.history(limit=None, oldest_first=False):
                if self.cancelled:
                    return False
                if self._is_deletable_message(message):
                    return True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = self._mark_unavailable(
                "history", getattr(channel, "id", 0), error
            )
            emit_line(
                f"INFO:HISTORY_UNAVAILABLE:{getattr(channel, 'id', 0)}:{code}",
                request_id,
            )
        return False

    async def _has_destination_messages(
        self, destination, request_id: str | None = None, *, guild: bool = False
    ) -> bool:
        try:
            async for message in destination.search(
                authors=[self.client.user], limit=None, oldest_first=False
            ):
                if self._is_deletable_message(message):
                    return True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            emit_line(
                f"INFO:SEARCH_FALLBACK:{self._lookup_error_code(error)}", request_id
            )

        if guild:
            async for target in self._iter_guild_targets(destination, request_id):
                if await self._channel_history_has_message(target, request_id):
                    return True
                if self.cancelled:
                    return False
            return False
        return await self._channel_history_has_message(destination, request_id)

    async def _iter_search_messages(self, destination, request_id: str | None = None):
        try:
            async for message in destination.search(
                authors=[self.client.user], limit=None, oldest_first=False
            ):
                if self.cancelled:
                    return
                if self._is_deletable_message(message):
                    yield message
        except (discord.HTTPException, discord.RateLimited, TypeError, ValueError) as error:
            emit_line(
                f"INFO:SEARCH_DELETE_FALLBACK:{self._lookup_error_code(error)}",
                request_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            emit_line(
                f"INFO:SEARCH_DELETE_FALLBACK:{type(error).__name__}", request_id
            )

    async def _iter_channel_history_messages(
        self, channel, request_id: str | None = None
    ):
        try:
            async for message in channel.history(limit=None, oldest_first=False):
                if self.cancelled:
                    return
                if self._is_deletable_message(message):
                    yield message
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, discord.RateLimited) as error:
            code = self._mark_unavailable(
                "history", getattr(channel, "id", 0), error
            )
            emit_line(
                f"INFO:HISTORY_DELETE_UNAVAILABLE:{getattr(channel, 'id', 0)}:"
                f"{code}",
                request_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = self._mark_unavailable(
                "history", getattr(channel, "id", 0), error
            )
            emit_line(
                f"INFO:HISTORY_DELETE_UNAVAILABLE:"
                f"{getattr(channel, 'id', 0)}:{code}",
                request_id,
            )

    async def _iter_destination_messages(
        self, destination, request_id: str | None = None, *, guild: bool = False
    ):
        # Search encontra rapidamente mensagens antigas e de threads. Depois de
        # apaga-las, history(limit=None) percorre apenas o que restou e cobre
        # indexacao incompleta, anexos, stickers e qualquer conteudo/data.
        async for message in self._iter_search_messages(destination, request_id):
            yield message

        emit_line("STATUS:VERIFICANDO_HISTORY_COMPLETO", request_id)
        if guild:
            async for target in self._iter_guild_targets(destination, request_id):
                async for message in self._iter_channel_history_messages(
                    target, request_id
                ):
                    yield message
                if self.cancelled:
                    return
        else:
            async for message in self._iter_channel_history_messages(
                destination, request_id
            ):
                yield message

    async def _get_quick_count(
        self, channel, request_id: str | None = None
    ) -> int:
        return await self._count_destination_messages(channel, request_id)

    async def _resolve_channel(self, target_id: int):
        channel = self.client.get_channel(target_id)
        if channel:
            return channel, "channel", None

        try:
            channel = await self.client.fetch_channel(target_id)
            if channel:
                return channel, "channel", None
        except discord.NotFound:
            try:
                user = await self.client.fetch_user(target_id)
                channel = user.dm_channel or await user.create_dm()
                if channel:
                    return channel, "user", None
            except discord.NotFound:
                return None, None, "Canal ou usuario nao encontrado"
            except discord.Forbidden:
                return None, None, "Sem acesso ao usuario ou DM"
            except discord.RateLimited:
                return None, None, "Discord solicitou uma espera longa ao abrir a DM"
            except discord.HTTPException:
                return None, None, "Falha ao abrir a DM"
            except Exception as error:
                return None, None, f"Falha ao abrir a DM ({type(error).__name__})"
        except discord.Forbidden:
            return None, None, "Sem acesso ao canal"
        except discord.RateLimited:
            return None, None, "Discord solicitou uma espera longa ao buscar o canal"
        except discord.HTTPException:
            return None, None, "Falha ao buscar o canal"
        except Exception as error:
            return None, None, f"Falha ao buscar o canal ({type(error).__name__})"

        return None, None, "Canal ou usuario nao encontrado"

    def _channel_display_name(self, channel) -> str:
        if isinstance(channel, discord.DMChannel):
            value = f"DM com {channel.recipient}" if channel.recipient else "DM"
        elif isinstance(channel, discord.GroupChannel):
            value = f"Grupo: {channel}"
        elif isinstance(channel, discord.TextChannel):
            value = f"#{channel.name} em {channel.guild.name}"
        elif isinstance(channel, discord.Thread):
            value = f"Thread: {channel.name}"
        else:
            value = str(channel)
        return self._ipc_label(value)

    async def _verify_clean(
        self, destination, request_id: str | None = None, *, guild: bool = False
    ) -> int:
        return await self._count_destination_messages(
            destination, request_id, guild=guild
        )
