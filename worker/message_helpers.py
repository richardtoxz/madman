import asyncio
import random

import discord

from .ipc import emit_line


class MessageStateMixin:
    async def _reboot_state(self):
        if self._current_task and not self._current_task.done():
            self.cancelled = True
            self.control_state.cancelled = True
            self._current_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._current_task), timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass

        self.cancelled = False
        self.paused = False
        self.control_state.cancelled = False
        self.control_state.paused = False
        self.control_state.started = False
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


class MessageDeleteMixin:
    async def _adaptive_delete(
        self,
        message,
        current_delay,
        consecutive_ok,
        consecutive_429,
        prefix,
        request_id: str | None = None,
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
                emit_line(
                    f"{prefix}_RATELIMIT:{consecutive_429}:{self.config.MAX_429_CONSECUTIVE}:{wait_time:.1f}:{current_delay:.2f}",
                    request_id,
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
            if e.status == 403:
                emit_line(f"{prefix}_ERROR:403:{message.id}", request_id)
                return False, current_delay, consecutive_ok, 0, False

            emit_line(f"{prefix}_ERROR:{e.status}:{message.id}:{e.text}", request_id)
            await asyncio.sleep(2)
            return False, current_delay, consecutive_ok, 0, False

        except Exception as e:
            if "captcha" in str(e).lower():
                emit_line(f"{prefix}_ABORT_CAPTCHA", request_id)
                return False, current_delay, 0, 0, True
            emit_line(f"{prefix}_ERROR:UNKNOWN:{message.id}:{e}", request_id)
            return False, current_delay, consecutive_ok, 0, False

    async def _delete_messages_adaptive(
        self, messages, prefix="MSG_DEL", request_id: str | None = None
    ):
        stats = {
            "deletadas": 0,
            "falhas": 0,
            "ratelimits": 0,
            "abortado": False,
            "tempo": 0.0,
        }
        start_time = asyncio.get_running_loop().time()
        total = len(messages)
        current_delay = self.config.BASE_DELAY
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
                emit_line(
                    f"{prefix}_BATCH_PAUSE:{pause:.0f}:{stats['deletadas']}:{total}",
                    request_id,
                )
                await asyncio.sleep(pause)

            (
                success,
                current_delay,
                consecutive_ok,
                consecutive_429,
                should_abort,
            ) = await self._adaptive_delete(
                msg,
                current_delay,
                consecutive_ok,
                consecutive_429,
                prefix,
                request_id,
            )

            if should_abort:
                stats["abortado"] = True
                stats["ratelimits"] = consecutive_429
                break

            if success:
                stats["deletadas"] += 1
            else:
                stats["falhas"] += 1

            emit_line(f"PROGRESS:{stats['deletadas']}:{total}", request_id)

            if i < total - 1 and not self.cancelled:
                await asyncio.sleep(current_delay)

        stats["tempo"] = asyncio.get_running_loop().time() - start_time
        return stats


class MessageLookupMixin:
    async def _remove_own_reactions(self, channel, request_id: str | None = None):
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
            emit_line(f"REACTIONS_REMOVED:{removed}", request_id)

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
        if isinstance(channel, discord.GroupChannel):
            return f"Grupo: {channel}"
        if isinstance(channel, discord.TextChannel):
            return f"#{channel.name} em {channel.guild.name}"
        if isinstance(channel, discord.Thread):
            return f"Thread: {channel.name}"
        return str(channel)

    async def _collect_messages(self, channel, request_id: str | None = None):
        emit_line("STATUS:CONTANDO_SEARCH", request_id)
        messages = []
        try:
            count = 0
            async for msg in channel.search(authors=[self.client.user], limit=None):
                if self.cancelled:
                    return None
                messages.append(msg)
                count += 1
                if count % 25 == 0:
                    emit_line(f"COUNT_PROGRESS:{count}", request_id)
                    await asyncio.sleep(0.05)
        except Exception as e:
            pass

        emit_line("STATUS:CONTANDO_HISTORY", request_id)
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
                        emit_line(f"COUNT_PROGRESS:{len(messages)}", request_id)
        except Exception as e:
            pass

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