import discord
import asyncio
import time
import random
import json


from config import MessageConfig


class MessageManager:
    def __init__(self, client: discord.Client, control_flags: dict):
        self.client = client
        self.control_flags = control_flags
        self.cancelled = False
        self.paused = False
        self._current_task = None
        self.config = MessageConfig()

    async def _reboot_state(self):
        if self._current_task and not self._current_task.done():
            self.cancelled = True
            self.control_flags["cancelled"] = True
            self._current_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._current_task), timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass

        self.cancelled = False
        self.paused = False
        self.control_flags["cancelled"] = False
        self.control_flags["started"] = False
        self._current_task = asyncio.current_task()

    def _is_my_message(self, msg: discord.Message) -> bool:
        if msg.author.id == self.client.user.id:
            return True
        try:
            if hasattr(msg, "interaction") and msg.interaction and msg.interaction.user:
                if msg.interaction.user.id == self.client.user.id:
                    return True
        except Exception:
            pass
        return False

    async def _adaptive_delete(
        self, message, current_delay, consecutive_ok, consecutive_429, prefix
    ):
        try:
            await message.delete()
            consecutive_ok += 1
            consecutive_429 = 0
            if consecutive_ok >= self.config.COOLDOWN_AFTER:
                current_delay = max(
                    self.config.MIN_DELAY, current_delay - self.config.COOLDOWN_STEP
                )
                consecutive_ok = 0
            return True, current_delay, consecutive_ok, consecutive_429, False

        except discord.NotFound:
            return True, current_delay, consecutive_ok, 0, False

        except discord.HTTPException as e:
            if e.status == 429:
                consecutive_429 += 1
                consecutive_ok = 0
                retry_after = getattr(e, "retry_after", current_delay * 2)
                wait_time = max(retry_after, 1.0) + random.uniform(0.5, 2.0)
                current_delay = min(
                    current_delay * self.config.BACKOFF_MULTIPLIER,
                    self.config.MAX_DELAY,
                )
                print(
                    f"{prefix}_RATELIMIT:{consecutive_429}:{self.config.MAX_429_CONSECUTIVE}:{wait_time:.1f}:{current_delay:.2f}",
                    flush=True,
                )
                if consecutive_429 >= self.config.MAX_429_CONSECUTIVE:
                    return False, current_delay, 0, consecutive_429, True
                await asyncio.sleep(wait_time)
                try:
                    await message.delete()
                    return True, current_delay, 0, 0, False
                except discord.NotFound:
                    return True, current_delay, 0, 0, False
                except Exception:
                    return False, current_delay, 0, consecutive_429, False
            elif e.status == 403:
                print(f"{prefix}_ERROR:403:{message.id}", flush=True)
                return False, current_delay, consecutive_ok, 0, False
            else:
                print(f"{prefix}_ERROR:{e.status}:{message.id}:{e.text}", flush=True)
                await asyncio.sleep(2)
                return False, current_delay, consecutive_ok, 0, False

        except Exception as e:
            if "captcha" in str(e).lower():
                print(f"{prefix}_ABORT_CAPTCHA", flush=True)
                return False, current_delay, 0, 0, True
            print(f"{prefix}_ERROR:UNKNOWN:{message.id}:{e}", flush=True)
            return False, current_delay, consecutive_ok, 0, False

    async def _delete_messages_adaptive(self, messages, prefix="MSG_DEL"):
        stats = {
            "deletadas": 0,
            "falhas": 0,
            "ratelimits": 0,
            "abortado": False,
            "tempo": 0.0,
        }
        start_time = time.time()
        total = len(messages)
        current_delay = self.config.BASE_DELAY
        print(f"DEBUG:DELAY_INICIAL:{current_delay:.2f}", flush=True)
        consecutive_ok = 0
        consecutive_429 = 0

        if total == 0:
            return stats

        for i, msg in enumerate(messages):
            if self.cancelled:
                break

            while self.paused and not self.cancelled:
                await asyncio.sleep(0.5)

            if i > 0 and i % self.config.BATCH_SIZE == 0:
                pause = random.uniform(
                    self.config.BATCH_PAUSE_MIN, self.config.BATCH_PAUSE_MAX
                )
                print(
                    f"{prefix}_BATCH_PAUSE:{pause:.0f}:{stats['deletadas']}:{total}",
                    flush=True,
                )
                await asyncio.sleep(pause)

            (
                success,
                current_delay,
                consecutive_ok,
                consecutive_429,
                should_abort,
            ) = await self._adaptive_delete(
                msg, current_delay, consecutive_ok, consecutive_429, prefix
            )

            if should_abort:
                stats["abortado"] = True
                stats["ratelimits"] = consecutive_429
                break

            if success:
                stats["deletadas"] += 1
            else:
                stats["falhas"] += 1

            print(f"PROGRESS:{stats['deletadas']}:{total}", flush=True)

            if i < total - 1 and not self.cancelled:
                await asyncio.sleep(current_delay)

        stats["tempo"] = time.time() - start_time
        return stats

    async def _remove_own_reactions(self, channel):
        removed = 0
        try:
            async for msg in channel.history(limit=500):
                if not hasattr(msg, "reactions") or not msg.reactions:
                    continue
                for reaction in msg.reactions:
                    if getattr(reaction, "me", False):
                        try:
                            await msg.remove_reaction(reaction.emoji, self.client.user)
                            removed += 1
                            await asyncio.sleep(0.5)
                        except Exception:
                            pass
        except Exception:
            pass
        if removed:
            print(f"REACTIONS_REMOVED:{removed}", flush=True)

    async def clean_channel(self, channel_id: int):
        await self._reboot_state()
        channel, target_type, error = await self._resolve_channel(channel_id)
        if error:
            print(f"RESULT:ERRO: {error}", flush=True)
            return

        channel_name = self._channel_display_name(channel)
        print(f"INFO:CANAL_DETECTADO:{channel_name}", flush=True)
        print(f"INFO:TARGET_TYPE:{target_type}", flush=True)

        print("STATUS:CONTANDO", flush=True)
        count = await self._get_quick_count(channel)
        print(f"TOTAL:{count}", flush=True)

        print("STATUS:WAITING_START", flush=True)
        if not await self._wait_for_start():
            return

        grand_total_deletadas = 0
        loop_count = 0
        max_loops = 3

        while loop_count < max_loops:
            loop_count += 1
            if loop_count > 1:
                print(f"STATUS:RE_ESCANEO_{loop_count}", flush=True)
                await asyncio.sleep(2)

            messages = await self._collect_messages(channel)
            if not messages:
                break

            last_found = len(messages)
            print(f"TOTAL:LOOP_{loop_count}:{last_found}", flush=True)
            print(f"STATUS:DELETANDO_LOOP_{loop_count}", flush=True)

            stats = await self._delete_messages_adaptive(
                messages, f"MSG_CLEAN_L{loop_count}"
            )
            grand_total_deletadas += stats["deletadas"]

            if stats["deletadas"] == 0 or stats["abortado"] or self.cancelled:
                break

        await self._remove_own_reactions(channel)

        print("STATUS:VERIFICACAO_FINAL", flush=True)
        remanescente = await self._verify_clean(channel)

        status_final = "CANCELADO" if self.cancelled else "COMPLETADO"
        if remanescente > 0:
            print(
                f"RESULT:{status_final}:Limpeza parcial. {remanescente} msgs ainda visíveis. Deletadas {grand_total_deletadas} total.",
                flush=True,
            )
        else:
            print(
                f"RESULT:{status_final}:Limpeza total concluída. Deletadas {grand_total_deletadas} total.",
                flush=True,
            )

    async def clean_all_dms(self):
        await self._reboot_state()

        dms = [
            ch
            for ch in self.client.private_channels
            if isinstance(ch, discord.DMChannel)
        ]
        total_dms = len(dms)

        if total_dms == 0:
            print("MSG_CLEAN_ALL_DMS_RESULT:INFO:Nenhuma DM aberta", flush=True)
            return

        print(f"MSG_CLEAN_ALL_DMS_START:{total_dms}", flush=True)
        grand_deleted = 0
        grand_failed = 0

        for idx, dm in enumerate(dms):
            if self.cancelled:
                break

            name = str(dm.recipient) if dm.recipient else f"DM {dm.id}"
            print(f"INFO:CANAL_DETECTADO:DM com {name}", flush=True)
            print("INFO:TARGET_TYPE:user", flush=True)
            print(f"MSG_CLEAN_ALL_DMS_CHANNEL:{idx + 1}:{total_dms}:{name}", flush=True)

            messages = await self._collect_messages_quiet(dm)
            if not messages:
                print(f"MSG_CLEAN_ALL_DMS_SKIP:{name}:0", flush=True)
                continue

            print(f"MSG_CLEAN_ALL_DMS_FOUND:{name}:{len(messages)}", flush=True)
            stats = await self._delete_messages_adaptive(messages, "MSG_DEL")
            grand_deleted += stats["deletadas"]
            grand_failed += stats["falhas"]

            if not stats["abortado"] and not self.cancelled and stats["deletadas"] > 0:
                print(f"STATUS:RE_ESCANEO_DM:{name}", flush=True)
                await asyncio.sleep(1)
                extra = await self._collect_messages_quiet(dm, limit=50)
                if extra:
                    extra_stats = await self._delete_messages_adaptive(
                        extra, "MSG_DEL_EXTRA"
                    )
                    grand_deleted += extra_stats["deletadas"]

            await self._remove_own_reactions(dm)

            if stats["abortado"]:
                print(
                    f"MSG_CLEAN_ALL_DMS_RESULT:ABORTADO:Deletadas {grand_deleted} total.",
                    flush=True,
                )
                return

            if idx < total_dms - 1:
                await asyncio.sleep(random.uniform(2.0, 5.0))

        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        print(
            f"MSG_CLEAN_ALL_DMS_RESULT:{status}:Deletadas {grand_deleted} Mensagens em {total_dms} DMs",
            flush=True,
        )

    async def clean_guild(self, guild_id: int):
        await self._reboot_state()

        guild = self.client.get_guild(guild_id)
        if not guild:
            print(f"RESULT:ERRO:Guild {guild_id} nao encontrada", flush=True)
            return

        print(f"INFO:CANAL_DETECTADO:Guild: {guild.name}", flush=True)
        print("INFO:TARGET_TYPE:channel", flush=True)
        print("STATUS:CONTANDO_SEARCH", flush=True)

        messages = []
        try:
            count = 0
            async for msg in guild.search(authors=[self.client.user], limit=None):
                if self.cancelled:
                    break
                messages.append(msg)
                count += 1
                if count % 25 == 0:
                    print(f"COUNT_PROGRESS:{count}", flush=True)
                    await asyncio.sleep(0.05)
        except Exception as e:
            print(f"RESULT:ERRO:Falha no search: {e}", flush=True)
            return

        if not messages:
            print(f"RESULT:INFO:Nenhuma mensagem sua em {guild.name}", flush=True)
            return

        print(f"TOTAL:{len(messages)}", flush=True)
        print("STATUS:WAITING_START", flush=True)
        if not await self._wait_for_start():
            return

        print("STATUS:DELETANDO", flush=True)
        stats = await self._delete_messages_adaptive(messages, "MSG_CLEAN_GUILD")

        if not stats["abortado"] and not self.cancelled and stats["deletadas"] > 0:
            print("STATUS:RE_ESCANEO_FINAL", flush=True)
            await asyncio.sleep(3)
            extra_messages = []
            try:
                async for msg in guild.search(authors=[self.client.user], limit=50):
                    extra_messages.append(msg)
            except Exception:
                pass
            if extra_messages:
                print(f"INFO:DELETANDO_EXTRAS:{len(extra_messages)}", flush=True)
                extra_stats = await self._delete_messages_adaptive(
                    extra_messages, "MSG_CLEAN_GUILD_EXTRA"
                )
                stats["deletadas"] += extra_stats["deletadas"]

        status_final = (
            "CANCELADO"
            if self.cancelled
            else ("ABORTADO" if stats["abortado"] else "COMPLETADO")
        )
        print(
            "RESULT:{}:Deletadas {} total.".format(status_final, stats["deletadas"]),
            flush=True,
        )

    async def close_dm(self, channel_id: int):
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                channel = await self.client.fetch_channel(channel_id)
            if isinstance(channel, discord.DMChannel):
                await channel.close()
                print(f"DM_CLOSE_RESULT:OK:{channel_id}", flush=True)
            elif isinstance(channel, discord.GroupChannel):
                await channel.leave(silent=True)
                print(f"DM_CLOSE_RESULT:OK:{channel_id}", flush=True)
            else:
                print("DM_CLOSE_RESULT:FAIL:Nao e DM", flush=True)
        except Exception as e:
            print(f"DM_CLOSE_RESULT:FAIL:{e}", flush=True)

    async def close_all_dms(self):
        self.cancelled = False
        self.control_flags["cancelled"] = False
        dms = [
            ch
            for ch in self.client.private_channels
            if isinstance(ch, discord.DMChannel)
        ]
        total = len(dms)
        closed = 0
        print(f"DM_CLOSE_ALL_START:{total}", flush=True)
        for dm in dms:
            if self.cancelled:
                break
            try:
                await dm.close()
                closed += 1
                print(f"DM_CLOSE_ALL_PROGRESS:{closed}:{total}", flush=True)
            except Exception as e:
                print(f"DM_CLOSE_ALL_ERROR:{dm.id}:{e}", flush=True)
            await asyncio.sleep(random.uniform(0.5, 1.5))
        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        print("DM_CLOSE_ALL_RESULT:{}:{}/{}".format(status, closed, total), flush=True)

    async def leave_group_dm(self, channel_id: int):
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                channel = await self.client.fetch_channel(channel_id)
            if isinstance(channel, discord.GroupChannel):
                await channel.leave(silent=True)
                print(f"GROUP_DM_LEAVE_RESULT:OK:{channel_id}", flush=True)
            else:
                print("GROUP_DM_LEAVE_RESULT:FAIL:Nao e grupo", flush=True)
        except Exception as e:
            print(f"GROUP_DM_LEAVE_RESULT:FAIL:{e}", flush=True)

    async def leave_all_group_dms(self):
        self.cancelled = False
        self.control_flags["cancelled"] = False
        groups = [
            ch
            for ch in self.client.private_channels
            if isinstance(ch, discord.GroupChannel)
        ]
        total = len(groups)
        left = 0
        print(f"GROUP_DM_LEAVE_ALL_START:{total}", flush=True)
        for g in groups:
            if self.cancelled:
                break
            try:
                await g.leave(silent=True)
                left += 1
                print(f"GROUP_DM_LEAVE_ALL_PROGRESS:{left}:{total}", flush=True)
            except Exception as e:
                print(f"GROUP_DM_LEAVE_ALL_ERROR:{g.id}:{e}", flush=True)
            await asyncio.sleep(random.uniform(1.0, 2.5))
        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        print(f"GROUP_DM_LEAVE_ALL_RESULT:{status}:{left}/{total}", flush=True)

    async def list_dms(self):
        dms = []
        try:
            channels = await self.client.fetch_private_channels()
        except Exception as e:
            print(f"DM_LIST_FETCH_ERROR: {e} - usando cache", flush=True)
            channels = list(self.client.private_channels)

        for ch in channels:
            try:
                if isinstance(ch, discord.DMChannel):
                    r = ch.recipient
                    if not r:
                        continue
                    dms.append(
                        {
                            "id": ch.id,
                            "type": "dm",
                            "name": r.name,
                            "global_name": getattr(r, "global_name", r.name) or r.name,
                            "avatar_url": str(r.display_avatar.url)
                            if r.display_avatar
                            else "",
                            "user_id": r.id,
                            "last_message_id": ch.last_message_id,
                        }
                    )
                elif isinstance(ch, discord.GroupChannel):
                    icon_url = str(ch.icon.url) if ch.icon else ""
                    dms.append(
                        {
                            "id": ch.id,
                            "type": "group",
                            "name": str(ch),
                            "global_name": str(ch),
                            "avatar_url": icon_url,
                            "owner_id": ch.owner_id,
                            "members": len(ch.recipients) + 1,
                            "last_message_id": ch.last_message_id,
                        }
                    )
            except Exception as e:
                print(f"DM_LIST_CHANNEL_ERROR: {ch.id}: {e}", flush=True)

        print(f"DM_LIST_RESULT:{json.dumps(dms)}", flush=True)

    async def scan_guilds_for_messages(self):
        self.cancelled = False
        self.control_flags["cancelled"] = False
        guilds = list(self.client.guilds)
        total = len(guilds)
        results = []
        print(f"MSG_SCAN_GUILDS_START:{total}", flush=True)
        for i, guild in enumerate(guilds):
            if self.cancelled:
                break
            print(f"MSG_SCAN_GUILDS_CHECKING:{i + 1}:{total}:{guild.name}", flush=True)
            print("INFO:TARGET_TYPE:channel", flush=True)
            try:
                found = False
                async for msg in guild.search(authors=[self.client.user], limit=5):
                    found = True
                    break
                if found:
                    results.append(
                        {
                            "guild_id": guild.id,
                            "guild_name": guild.name,
                            "has_messages": True,
                        }
                    )
                    print(f"MSG_SCAN_GUILDS_FOUND:{guild.name}:{guild.id}", flush=True)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(1.5, 3.0))
        print(f"MSG_SCAN_GUILDS_RESULT:{json.dumps(results)}", flush=True)

    async def delete_from_data_package(self, entries):
        self.cancelled = False
        self.paused = False
        self.control_flags["cancelled"] = False
        total = len(entries)
        deleted = 0
        failed = 0
        not_found = 0
        current_delay = self.config.BASE_DELAY
        consecutive_429 = 0

        print(f"MSG_DATAPACK_START:{total}", flush=True)
        for i, entry in enumerate(entries):
            if self.cancelled:
                break
            while self.paused and not self.cancelled:
                await asyncio.sleep(0.5)

            ch_id, msg_id = entry["channel_id"], entry["message_id"]
            try:
                await self.client.http.delete_message(ch_id, msg_id)
                deleted += 1
                consecutive_429 = 0
                print(f"MSG_DATAPACK_PROGRESS:{deleted}:{total}", flush=True)
            except discord.NotFound:
                not_found += 1
                deleted += 1
                consecutive_429 = 0
                print(f"MSG_DATAPACK_PROGRESS:{deleted}:{total}", flush=True)
            except discord.HTTPException as e:
                if e.status == 429:
                    consecutive_429 += 1
                    retry_after = getattr(e, "retry_after", 5.0)
                    wait = max(retry_after, 2.0) + random.uniform(1.0, 3.0)
                    current_delay = min(current_delay * 2, self.config.MAX_DELAY)
                    print(
                        f"MSG_DATAPACK_RATELIMIT:{consecutive_429}:{wait:.0f}",
                        flush=True,
                    )
                    if consecutive_429 >= self.config.MAX_429_CONSECUTIVE:
                        print(
                            "MSG_DATAPACK_RESULT:ABORTADO:Muitos 429. Deletados {}/{}".format(
                                deleted, total
                            ),
                            flush=True,
                        )
                        return
                    await asyncio.sleep(wait)
                    try:
                        await self.client.http.delete_message(ch_id, msg_id)
                        deleted += 1
                        consecutive_429 = 0
                    except discord.NotFound:
                        deleted += 1
                        not_found += 1
                    except Exception:
                        failed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                if "captcha" in str(e).lower():
                    print("MSG_DATAPACK_RESULT:ABORTADO:CAPTCHA", flush=True)
                    return

            if i > 0 and i % self.config.BATCH_SIZE == 0:
                await asyncio.sleep(
                    random.uniform(
                        self.config.BATCH_PAUSE_MIN, self.config.BATCH_PAUSE_MAX
                    )
                )
            elif i < total - 1:
                await asyncio.sleep(current_delay)

        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        print(f"MSG_DATAPACK_RESULT:{status}:Deletados {deleted}/{total}", flush=True)

    async def count_messages(self, channel_id: int):
        self.cancelled = False
        self.control_flags["cancelled"] = False
        channel, target_type, error = await self._resolve_channel(channel_id)
        if error:
            print(f"MSG_COUNT_RESULT:ERRO:{error}", flush=True)
            return

        channel_name = self._channel_display_name(channel)
        print(f"INFO:CANAL_DETECTADO:{channel_name}", flush=True)
        print(f"INFO:TARGET_TYPE:{target_type}", flush=True)

        count = await self._get_quick_count(channel)

        name = self._channel_display_name(channel)
        print(f"MSG_COUNT_RESULT:OK:{count}:{name}", flush=True)

    async def _get_quick_count(self, channel) -> int:
        count = 0
        try:
            async for msg in channel.search(authors=[self.client.user], limit=1):
                if hasattr(msg, "total_results") and msg.total_results is not None:
                    count = msg.total_results
                else:
                    count = 1
                break
        except Exception:
            pass

        if count == 0 or isinstance(channel, discord.DMChannel):
            try:
                msgs = await self._collect_messages_quiet(channel, limit=1000)
                if msgs:
                    count = len(msgs)
            except Exception:
                pass
        return count

    async def _resolve_channel(self, target_id):
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
                channel = await user.create_dm()
                if channel:
                    return channel, "user", None
            except Exception:
                pass
        except discord.Forbidden:
            return None, None, "Sem acesso ao canal"
        except Exception as e:
            return None, None, f"Erro: {e}"

        return None, None, "Canal ou usuario nao encontrado"

    def _channel_display_name(self, channel):
        if isinstance(channel, discord.DMChannel):
            return f"DM com {channel.recipient}" if channel.recipient else "DM"
        elif isinstance(channel, discord.GroupChannel):
            return f"Grupo: {channel}"
        elif isinstance(channel, discord.TextChannel):
            return f"#{channel.name} em {channel.guild.name}"
        elif isinstance(channel, discord.Thread):
            return f"Thread: {channel.name}"
        return str(channel)

    async def _collect_messages(self, channel):
        print("STATUS:CONTANDO_SEARCH", flush=True)
        messages = []
        try:
            count = 0
            async for msg in channel.search(authors=[self.client.user], limit=None):
                if self.cancelled:
                    return None
                messages.append(msg)
                count += 1
                if count % 25 == 0:
                    print(f"COUNT_PROGRESS:{count}", flush=True)
                    await asyncio.sleep(0.05)
        except Exception as e:
            print(f"INFO:SEARCH_FALLBACK:{str(e)[:80]}", flush=True)

        print("STATUS:CONTANDO_HISTORY", flush=True)
        seen_ids = {m.id for m in messages}
        hist_limit = 3000 if messages else 5000
        try:
            async for msg in channel.history(limit=hist_limit, oldest_first=False):
                if self.cancelled:
                    return None
                if self._is_my_message(msg) and msg.id not in seen_ids:
                    seen_ids.add(msg.id)
                    messages.append(msg)
                    if len(messages) % 25 == 0:
                        print(f"COUNT_PROGRESS:{len(messages)}", flush=True)
        except Exception as e:
            print(f"INFO:HISTORY_FALLBACK_ERR:{str(e)[:50]}", flush=True)

        messages.sort(key=lambda m: m.id, reverse=True)
        return messages

    async def _verify_clean(self, channel):
        count = 0
        try:
            async for msg in channel.history(limit=50):
                if self._is_my_message(msg):
                    count += 1
        except Exception:
            pass
        return count

    async def _collect_messages_quiet(self, channel, limit=2000):
        messages = []
        try:
            async for msg in channel.search(authors=[self.client.user], limit=None):
                if self.cancelled:
                    break
                messages.append(msg)
        except Exception:
            pass

        seen_ids = {m.id for m in messages}
        try:
            async for msg in channel.history(limit=limit, oldest_first=False):
                if self.cancelled:
                    break
                if self._is_my_message(msg) and msg.id not in seen_ids:
                    seen_ids.add(msg.id)
                    messages.append(msg)
        except Exception:
            pass

        messages.sort(key=lambda m: m.id, reverse=True)
        return messages

    async def _wait_for_start(self, timeout=300):
        self.control_flags["started"] = False
        waited = 0
        while (
            not self.control_flags.get("started")
            and not self.control_flags.get("cancelled")
            and waited < timeout
        ):
            await asyncio.sleep(0.5)
            waited += 0.5

        if self.control_flags.get("cancelled") or waited >= timeout:
            return False
        return True
