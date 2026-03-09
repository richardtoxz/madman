import asyncio
import random

import discord

from .ipc import emit_line


class MessageCleanupMixin:
	async def clean_channel(self, channel_id: int, request_id: str | None = None):
		await self._reboot_state()
		channel, target_type, error = await self._resolve_channel(channel_id)
		if error:
			emit_line(f"RESULT:ERRO: {error}", request_id)
			return

		channel_name = self._channel_display_name(channel)
		emit_line(f"INFO:CANAL_DETECTADO:{channel_name}", request_id)
		emit_line(f"INFO:TARGET_TYPE:{target_type}", request_id)

		emit_line("STATUS:CONTANDO", request_id)
		count = await self._get_quick_count(channel)
		emit_line(f"TOTAL:{count}", request_id)

		emit_line("STATUS:WAITING_START", request_id)
		if not await self._wait_for_start():
			return

		grand_total_deletadas = 0
		loop_count = 0
		max_loops = 3

		while loop_count < max_loops:
			loop_count += 1
			if loop_count > 1:
				emit_line(f"STATUS:RE_ESCANEO_{loop_count}", request_id)
				await asyncio.sleep(2)

			messages = await self._collect_messages(channel, request_id=request_id)
			if not messages:
				break

			last_found = len(messages)
			emit_line(f"TOTAL:LOOP_{loop_count}:{last_found}", request_id)
			emit_line(f"STATUS:DELETANDO_LOOP_{loop_count}", request_id)

			stats = await self._delete_messages_adaptive(
				messages, f"MSG_CLEAN_L{loop_count}", request_id=request_id
			)
			grand_total_deletadas += stats["deletadas"]

			if stats["deletadas"] == 0 or stats["abortado"] or self.cancelled:
				break

		await self._remove_own_reactions(channel, request_id=request_id)

		emit_line("STATUS:VERIFICACAO_FINAL", request_id)
		remanescente = await self._verify_clean(channel)

		status_final = "CANCELADO" if self.cancelled else "COMPLETADO"
		if remanescente > 0:
			emit_line(
				f"RESULT:{status_final}:Limpeza parcial. {remanescente} msgs ainda visíveis. Deletadas {grand_total_deletadas} total.",
				request_id,
			)
		else:
			emit_line(
				f"RESULT:{status_final}:Limpeza total concluída. Deletadas {grand_total_deletadas} total.",
				request_id,
			)

	async def clean_all_dms(self, request_id: str | None = None):
		await self._reboot_state()

		dms = [
			ch
			for ch in self.client.private_channels
			if isinstance(ch, discord.DMChannel)
		]
		total_dms = len(dms)

		if total_dms == 0:
			emit_line("MSG_CLEAN_ALL_DMS_RESULT:INFO:Nenhuma DM aberta", request_id)
			return

		emit_line(f"MSG_CLEAN_ALL_DMS_START:{total_dms}", request_id)
		grand_deleted = 0

		for idx, dm in enumerate(dms):
			if self.cancelled:
				break

			name = str(dm.recipient) if dm.recipient else f"DM {dm.id}"
			emit_line(f"INFO:CANAL_DETECTADO:DM com {name}", request_id)
			emit_line("INFO:TARGET_TYPE:user", request_id)
			emit_line(
				f"MSG_CLEAN_ALL_DMS_CHANNEL:{idx + 1}:{total_dms}:{name}", request_id
			)

			messages = await self._collect_messages_quiet(dm)
			if not messages:
				emit_line(f"MSG_CLEAN_ALL_DMS_SKIP:{name}:0", request_id)
				continue

			emit_line(f"MSG_CLEAN_ALL_DMS_FOUND:{name}:{len(messages)}", request_id)
			stats = await self._delete_messages_adaptive(
				messages, "MSG_DEL", request_id=request_id
			)
			grand_deleted += stats["deletadas"]

			if not stats["abortado"] and not self.cancelled and stats["deletadas"] > 0:
				emit_line(f"STATUS:RE_ESCANEO_DM:{name}", request_id)
				await asyncio.sleep(1)
				extra = await self._collect_messages_quiet(dm, limit=50)
				if extra:
					extra_stats = await self._delete_messages_adaptive(
						extra, "MSG_DEL_EXTRA", request_id=request_id
					)
					grand_deleted += extra_stats["deletadas"]

			await self._remove_own_reactions(dm, request_id=request_id)

			if stats["abortado"]:
				emit_line(
					f"MSG_CLEAN_ALL_DMS_RESULT:ABORTADO:Deletadas {grand_deleted} total.",
					request_id,
				)
				return

			if idx < total_dms - 1:
				await asyncio.sleep(random.uniform(2.0, 5.0))

		status = "CANCELADO" if self.cancelled else "COMPLETADO"
		emit_line(
			f"MSG_CLEAN_ALL_DMS_RESULT:{status}:Deletadas {grand_deleted} Mensagens em {total_dms} DMs",
			request_id,
		)

	async def clean_guild(self, guild_id: int, request_id: str | None = None):
		await self._reboot_state()

		guild = self.client.get_guild(guild_id)
		if not guild:
			try:
				guild = await self.client.fetch_guild(guild_id)
			except Exception:
				guild = None
		if not guild:
			emit_line(f"RESULT:ERRO:Guild {guild_id} nao encontrada", request_id)
			return

		emit_line(f"INFO:CANAL_DETECTADO:Guild: {guild.name}", request_id)
		emit_line("INFO:TARGET_TYPE:guild", request_id)
		emit_line("STATUS:CONTANDO_SEARCH", request_id)

		messages = []
		try:
			count = 0
			async for msg in guild.search(authors=[self.client.user], limit=None):
				if self.cancelled:
					break
				if not self._is_my_message(msg):
					continue
				messages.append(msg)
				count += 1
				if count % 25 == 0:
					emit_line(f"COUNT_PROGRESS:{count}", request_id)
					await asyncio.sleep(0.05)
		except Exception as e:
			emit_line(f"INFO:GUILD_SEARCH_FAIL:{e}", request_id)

		if messages:
			unique_messages = {}
			for msg in messages:
				unique_messages[msg.id] = msg
			messages = list(unique_messages.values())
			messages.sort(key=lambda item: item.id, reverse=True)

		channel_ids = {
			msg.channel.id
			for msg in messages
			if getattr(getattr(msg, "channel", None), "id", None)
		}

		if not messages:
			messages = await self._collect_guild_history_messages(
				guild,
				request_id=request_id,
				limit_per_channel=1200,
			)
			if not messages:
				emit_line(f"RESULT:INFO:Nenhuma mensagem sua em {guild.name}", request_id)
				return
			channel_ids = {
				msg.channel.id
				for msg in messages
				if getattr(getattr(msg, "channel", None), "id", None)
			}

		emit_line(f"TOTAL:{len(messages)}", request_id)
		emit_line("STATUS:WAITING_START", request_id)
		if not await self._wait_for_start():
			return

		emit_line("STATUS:DELETANDO", request_id)
		stats = await self._delete_messages_adaptive(
			messages, "MSG_CLEAN_GUILD", request_id=request_id
		)

		if not stats["abortado"] and not self.cancelled and stats["deletadas"] > 0:
			emit_line("STATUS:RE_ESCANEO_FINAL", request_id)
			await asyncio.sleep(3)
			extra_messages = await self._collect_guild_history_messages(
				guild,
				request_id=request_id,
				channel_ids=channel_ids or None,
				limit_per_channel=150,
			)
			if extra_messages:
				emit_line(f"INFO:DELETANDO_EXTRAS:{len(extra_messages)}", request_id)
				extra_stats = await self._delete_messages_adaptive(
					extra_messages,
					"MSG_CLEAN_GUILD_EXTRA",
					request_id=request_id,
				)
				stats["deletadas"] += extra_stats["deletadas"]

		status_final = (
			"CANCELADO"
			if self.cancelled
			else ("ABORTADO" if stats["abortado"] else "COMPLETADO")
		)
		emit_line(
			"RESULT:{}:Deletadas {} total.".format(status_final, stats["deletadas"]),
			request_id,
		)
