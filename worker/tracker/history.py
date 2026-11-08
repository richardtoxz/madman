from __future__ import annotations

import asyncio
from datetime import timedelta

import discord

from ..core.ipc import emit_line


class TrackerHistoryMixin:
    async def _collect_history(self, run_id: int):
        self._history_status = "running"
        try:
            # A shared lock bounds REST searches when multiple IDs are added together.
            async with self._history_lock:
                for guild in tuple(self.client.guilds):
                    if not self.enabled or run_id != self._run_id:
                        return
                    if self._stats["history_messages"] >= self.config.HISTORY_TOTAL_LIMIT:
                        self._history_status = "limited"
                        return
                    try:
                        await asyncio.wait_for(
                            self._search_guild_history(guild, run_id),
                            timeout=self.config.HISTORY_GUILD_TIMEOUT,
                        )
                        self._stats["history_guilds_scanned"] += 1
                    except Exception as error:
                        self._stats["history_errors"] += 1
                        emit_line(f"TRACKER:HISTORY_ERROR:{guild.id}:{type(error).__name__}")
                    await asyncio.sleep(self.config.HISTORY_GUILD_PAUSE)
            self._history_status = "partial" if self._stats["history_errors"] else "complete"
        except asyncio.CancelledError:
            self._history_status = "cancelled"
            raise

    async def _search_guild_history(self, guild, run_id: int):
        remaining = self.config.HISTORY_TOTAL_LIMIT - self._stats["history_messages"]
        limit = min(self.config.HISTORY_PER_GUILD, remaining)
        count = 0
        async for message in guild.search(
            authors=[discord.Object(id=self.target_id)],
            after=self._started_at - timedelta(days=self.config.HISTORY_DAYS),
            before=self._started_at,
            limit=limit,
            oldest_first=False,
        ):
            if not self.enabled or run_id != self._run_id:
                return
            count += 1
            if message.author.id != self.target_id or not self._channel_allowed(message.channel, history=True):
                continue
            # Search results can overlap with gateway messages delivered during startup.
            queue = self._webhook_queue
            if queue is not None and queue.qsize() >= max(1, queue.maxsize // 2):
                await queue.join()
            if not self.enabled or run_id != self._run_id:
                return
            await self._capture_message(message, historical=True)
        if count >= limit:
            self._stats["history_guilds_limited"] += 1


async def _backpressure(tracker) -> None:
    queue = tracker._webhook_queue
    if queue is not None and queue.qsize() >= max(1, queue.maxsize // 2):
        await queue.join()


async def _search_guild_group(guild, active, config) -> None:
    """Uma busca por servidor para todos os alvos (authors=[...]), em vez de uma por alvo.

    O resultado vem misturado e em ordem cronologica inversa; cada mensagem e
    entregue ao tracker do autor, respeitando o limite por servidor de cada um.
    Se um autor muito ativo esgotar o limite combinado antes dos demais, uma nova
    rodada continua do ponto parado so com os alvos que ainda nao atingiram o teto.
    """
    caps = {tracker: config.HISTORY_PER_GUILD for tracker, _ in active}
    seen = {tracker: 0 for tracker, _ in active}
    limited = set()
    cursor = None
    for _ in range(6):
        open_ = [
            (tracker, run_id)
            for tracker, run_id in active
            if seen[tracker] < caps[tracker]
            and tracker.enabled
            and run_id == tracker._run_id
            and tracker._stats["history_messages"] < config.HISTORY_TOTAL_LIMIT
        ]
        if not open_:
            break
        by_author = {tracker.target_id: (tracker, run_id) for tracker, run_id in open_}
        limit = sum(
            min(
                caps[tracker] - seen[tracker],
                config.HISTORY_TOTAL_LIMIT - tracker._stats["history_messages"],
            )
            for tracker, _ in open_
        )
        if limit < 1:
            break
        window = timedelta(days=config.HISTORY_DAYS)
        newest = max(tracker._started_at for tracker, _ in open_)
        oldest = min(tracker._started_at for tracker, _ in open_) - window
        returned = 0
        last_id = None
        async for message in guild.search(
            authors=[discord.Object(id=target_id) for target_id in by_author],
            after=oldest,
            before=cursor if cursor is not None else newest,
            limit=limit,
            oldest_first=False,
        ):
            returned += 1
            last_id = message.id
            entry = by_author.get(message.author.id)
            if entry is None:
                continue
            tracker, run_id = entry
            if not tracker.enabled or run_id != tracker._run_id:
                continue
            if seen[tracker] >= caps[tracker]:
                continue
            if tracker._stats["history_messages"] >= config.HISTORY_TOTAL_LIMIT:
                continue
            seen[tracker] += 1
            created = message.created_at
            if created > tracker._started_at or created < tracker._started_at - window:
                continue
            if not tracker._channel_allowed(message.channel, history=True):
                continue
            await _backpressure(tracker)
            if not tracker.enabled or run_id != tracker._run_id:
                continue
            await tracker._capture_message(message, historical=True)
        for tracker, _ in open_:
            if seen[tracker] >= caps[tracker]:
                limited.add(tracker)
        if returned < limit or last_id is None:
            break
        cursor = discord.Object(id=last_id)
    for tracker in limited:
        tracker._stats["history_guilds_limited"] += 1


async def collect_group_history(client, members, lock, config) -> None:
    """Coleta o historico de varios trackers da mesma conta com uma busca por servidor."""
    members = list(members)

    def alive():
        return [(t, r) for t, r in members if t.enabled and r == t._run_id]

    for tracker, _ in alive():
        tracker._history_status = "running"
    try:
        async with lock:
            for guild in tuple(getattr(client, "guilds", ())):
                active = []
                for tracker, run_id in alive():
                    if tracker._stats["history_messages"] >= config.HISTORY_TOTAL_LIMIT:
                        tracker._history_status = "limited"
                        continue
                    active.append((tracker, run_id))
                if not active:
                    break
                try:
                    await asyncio.wait_for(
                        _search_guild_group(guild, active, config),
                        timeout=config.HISTORY_GUILD_TIMEOUT,
                    )
                    for tracker, _ in active:
                        tracker._stats["history_guilds_scanned"] += 1
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    for tracker, _ in active:
                        tracker._stats["history_errors"] += 1
                    emit_line(f"TRACKER:HISTORY_ERROR:{guild.id}:{type(error).__name__}")
                await asyncio.sleep(config.HISTORY_GUILD_PAUSE)
        for tracker, _ in alive():
            if tracker._history_status == "running":
                tracker._history_status = "partial" if tracker._stats["history_errors"] else "complete"
    except asyncio.CancelledError:
        for tracker, _ in alive():
            tracker._history_status = "cancelled"
        raise
