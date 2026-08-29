from __future__ import annotations

import random

import discord

from .ipc import emit_line


class MessageCleanupMixin:
    @staticmethod
    def _merge_stats(target: dict, source: dict) -> None:
        for key in (
            "deletadas",
            "ausentes",
            "ignoradas",
            "proibidas",
            "falhas",
            "processadas",
            "ratelimits",
        ):
            target[key] = int(target.get(key, 0)) + int(source.get(key, 0))
        target["abortado"] = bool(target.get("abortado")) or bool(
            source.get("abortado")
        )
        target["tempo"] = float(target.get("tempo", 0.0)) + float(
            source.get("tempo", 0.0)
        )

    @staticmethod
    def _empty_stats() -> dict:
        return {
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

    def _result_text(self, stats: dict, remaining: int, initial_total: int) -> str:
        failures = int(stats["falhas"]) + int(stats["proibidas"])
        ignored = int(stats["ignoradas"])
        unavailable = len(self._unavailable_sources)
        observed_total = max(
            initial_total - ignored,
            int(stats["deletadas"])
            + int(stats["ausentes"])
            + failures
            + remaining,
        )
        if self.cancelled:
            status = "CANCELADO"
        elif stats["abortado"]:
            status = "ABORTADO"
        elif remaining or failures or unavailable:
            status = "PARCIAL"
        else:
            status = "COMPLETADO"

        return (
            f"RESULT:{status}:Deletadas {stats['deletadas']} de {observed_total}. "
            f"Ja ausentes {stats['ausentes']}; ignoradas {ignored}; "
            f"falhas {failures}; "
            f"restantes {remaining}; fontes indisponiveis {unavailable}."
        )

    async def clean_channel(self, channel_id: int, request_id: str | None = None):
        await self._reboot_state()
        channel, target_type, error = await self._resolve_channel(channel_id)
        if error:
            emit_line(f"RESULT:ERRO:{error}", request_id)
            return

        emit_line(
            f"INFO:CANAL_DETECTADO:{self._channel_display_name(channel)}", request_id
        )
        emit_line(f"INFO:TARGET_TYPE:{target_type}", request_id)
        emit_line("STATUS:CONTANDO_SEARCH", request_id)

        # A confirmacao usa apenas a primeira pagina da busca. Se o indice ainda
        # estiver incompleto, a cobertura via history acontece depois do START.
        total = await self._get_quick_count(channel, request_id)
        emit_line(f"TOTAL:{total}", request_id)
        emit_line("STATUS:WAITING_START", request_id)
        if not await self._wait_for_start():
            emit_line("RESULT:CANCELADO:Limpeza nao iniciada.", request_id)
            return

        emit_line("STATUS:DELETANDO", request_id)
        stats = await self._delete_messages_adaptive(
            self._iter_destination_messages(channel, request_id),
            "MSG_CLEAN_CHANNEL",
            request_id=request_id,
            total_hint=total,
        )

        emit_line("STATUS:VERIFICACAO_FINAL", request_id)
        if self.cancelled:
            remaining = max(
                total - int(stats["deletadas"]) - int(stats["ausentes"]), 0
            )
        else:
            remaining = await self._verify_clean(channel, request_id)
        emit_line(self._result_text(stats, remaining, total), request_id)

    async def clean_all_dms(self, request_id: str | None = None):
        await self._reboot_state()

        try:
            private_channels = list(await self.client.fetch_private_channels())
        except (discord.HTTPException, discord.RateLimited) as error:
            emit_line(
                f"INFO:DM_FETCH_FALLBACK:{self._lookup_error_code(error)}", request_id
            )
            private_channels = list(self.client.private_channels)
        except Exception as error:
            emit_line(
                f"INFO:DM_FETCH_FALLBACK:{type(error).__name__}", request_id
            )
            private_channels = list(self.client.private_channels)

        dms = [
            channel
            for channel in private_channels
            if isinstance(channel, (discord.DMChannel, discord.GroupChannel))
        ]
        total_dms = len(dms)

        if total_dms == 0:
            emit_line(
                "MSG_CLEAN_ALL_DMS_RESULT:INFO:Nenhuma conversa privada aberta",
                request_id,
            )
            return

        emit_line(f"MSG_CLEAN_ALL_DMS_START:{total_dms}", request_id)
        grand_stats = self._empty_stats()
        grand_initial_total = 0
        grand_remaining = 0

        for index, dm in enumerate(dms, start=1):
            if self.cancelled:
                break

            recipient = getattr(dm, "recipient", None)
            name = self._ipc_label(recipient or str(dm) or f"Conversa {dm.id}")
            emit_line(
                f"MSG_CLEAN_ALL_DMS_CHANNEL:{index}:{total_dms}:{name}", request_id
            )

            count = await self._count_destination_messages(dm, request_id)
            grand_initial_total += count
            if count == 0:
                emit_line(f"MSG_CLEAN_ALL_DMS_SKIP:{name}:0", request_id)
                continue

            emit_line(f"MSG_CLEAN_ALL_DMS_FOUND:{name}:{count}", request_id)
            stats = await self._delete_messages_adaptive(
                self._iter_destination_messages(dm, request_id),
                "MSG_DEL_DM",
                request_id=request_id,
                total_hint=count,
            )
            self._merge_stats(grand_stats, stats)

            if self.cancelled:
                remaining = max(
                    count - int(stats["deletadas"]) - int(stats["ausentes"]), 0
                )
            else:
                remaining = await self._verify_clean(dm, request_id)
            grand_remaining += remaining
            if stats["abortado"]:
                break

            if index < total_dms:
                if not await self._sleep_interruptibly(random.uniform(1.0, 2.0)):
                    break

        failures = int(grand_stats["falhas"]) + int(grand_stats["proibidas"])
        ignored = int(grand_stats["ignoradas"])
        unavailable = len(self._unavailable_sources)
        observed_total = max(
            grand_initial_total - ignored,
            int(grand_stats["deletadas"])
            + int(grand_stats["ausentes"])
            + failures
            + grand_remaining,
        )
        if self.cancelled:
            status = "CANCELADO"
        elif grand_stats["abortado"]:
            status = "ABORTADO"
        elif grand_remaining or failures or unavailable:
            status = "PARCIAL"
        else:
            status = "COMPLETADO"

        emit_line(
            f"MSG_CLEAN_ALL_DMS_RESULT:{status}:Deletadas "
            f"{grand_stats['deletadas']} de {observed_total}; "
            f"ignoradas {ignored}; falhas {failures}; restantes {grand_remaining}; "
            f"fontes indisponiveis {unavailable}; conversas {total_dms}.",
            request_id,
        )

    async def clean_guild(self, guild_id: int, request_id: str | None = None):
        await self._reboot_state()

        guild = self.client.get_guild(guild_id)
        if not guild:
            try:
                guild = await self.client.fetch_guild(guild_id)
            except discord.NotFound:
                emit_line(
                    f"RESULT:ERRO:Servidor {guild_id} nao encontrado", request_id
                )
                return
            except discord.Forbidden:
                emit_line(f"RESULT:ERRO:Sem acesso ao servidor {guild_id}", request_id)
                return
            except discord.RateLimited:
                emit_line(
                    "RESULT:ERRO:Discord solicitou uma espera longa ao buscar o servidor",
                    request_id,
                )
                return
            except discord.HTTPException:
                emit_line("RESULT:ERRO:Falha ao buscar o servidor", request_id)
                return
            except Exception as error:
                emit_line(
                    f"RESULT:ERRO:Falha inesperada ao buscar o servidor "
                    f"({type(error).__name__})",
                    request_id,
                )
                return

        emit_line(
            f"INFO:CANAL_DETECTADO:Servidor： {self._ipc_label(guild.name)}", request_id
        )
        emit_line("INFO:TARGET_TYPE:guild", request_id)
        emit_line("STATUS:CONTANDO_SEARCH", request_id)

        total = await self._count_destination_messages(guild, request_id, guild=True)
        emit_line(f"TOTAL:{total}", request_id)
        emit_line("STATUS:WAITING_START", request_id)
        if not await self._wait_for_start():
            emit_line("RESULT:CANCELADO:Limpeza nao iniciada.", request_id)
            return

        emit_line("STATUS:DELETANDO", request_id)
        stats = await self._delete_messages_adaptive(
            self._iter_destination_messages(guild, request_id, guild=True),
            "MSG_CLEAN_GUILD",
            request_id=request_id,
            total_hint=total,
        )

        emit_line("STATUS:VERIFICACAO_FINAL", request_id)
        if self.cancelled:
            remaining = max(
                total - int(stats["deletadas"]) - int(stats["ausentes"]), 0
            )
        else:
            remaining = await self._verify_clean(guild, request_id, guild=True)
        emit_line(self._result_text(stats, remaining, total), request_id)
