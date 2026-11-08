from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from contextlib import suppress
from urllib.parse import urlsplit

import aiohttp
import discord
from discord.ext import tasks

from ..core.config import TrackerConfig
from ..core.ipc import emit_line
from .card import build_card
from .dispatch import TrackerDispatchMixin
from .events import TrackerEventMixin
from .history import TrackerHistoryMixin
from .style import color_for
from .support import MessageBuffer, TrackerState


_WEBHOOK_PATH = re.compile(r"^/api(?:/v\d+)?/webhooks/(?P<id>\d{15,25})/(?P<token>[A-Za-z0-9._-]{20,})/?$")
_WEBHOOK_HOSTS = {
    "discord.com",
    "canary.discord.com",
    "ptb.discord.com",
    "discordapp.com",
}


class TrackerManager(TrackerDispatchMixin, TrackerEventMixin, TrackerHistoryMixin):
    def __init__(self, client):
        self.client = client
        self.target_id: int | None = None
        self.webhook_url: str | None = None
        self.enabled = False
        self.state = TrackerState()
        self.config = TrackerConfig()
        self.msg_buffer = MessageBuffer(self._on_buffer_flush)
        self._msg_cache: OrderedDict[int, dict] = OrderedDict()
        self._shared_resources = False
        self._shared_session_getter = None
        self.tracked_voice_channels: set[int] = set()
        self.tracked_guild_ids: set[int] = set()
        self._run_id = 0
        self._subscribe_task: asyncio.Task | None = None
        self._pending_offline_task: asyncio.Task | None = None
        self._pending_presence_task: asyncio.Task | None = None
        self._pending_custom_status_task: asyncio.Task | None = None
        self._profile_fetch_task: asyncio.Task | None = None
        self._history_task: asyncio.Task | None = None
        self._history_lock = asyncio.Lock()
        self._subscription_lock = asyncio.Lock()
        self._webhook_send_lock = asyncio.Lock()
        self._history_status = "idle"
        self._started_at = None
        self._webhook_session: aiohttp.ClientSession | None = None
        self._webhook: discord.Webhook | None = None
        self._webhook_queue: asyncio.Queue | None = None
        self._webhook_task: asyncio.Task | None = None
        self._stats = self._new_stats()

    @staticmethod
    def _new_stats() -> dict[str, int | str | None]:
        return {
            "events_captured": 0,
            "webhooks_sent": 0,
            "webhooks_failed": 0,
            "events_dropped": 0,
            "subscription_errors": 0,
            "event_errors": 0,
            "profile_errors": 0,
            "messages_seen": 0,
            "media_seen": 0,
            "interactions_seen": 0,
            "voice_seconds": 0,
            "history_messages": 0,
            "history_errors": 0,
            "history_guilds_scanned": 0,
            "history_guilds_limited": 0,
            "last_error": None,
        }

    @staticmethod
    def normalize_webhook_url(value) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or len(text) > 500:
            return None
        try:
            parsed = urlsplit(text)
        except ValueError:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _WEBHOOK_HOSTS
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or not _WEBHOOK_PATH.fullmatch(parsed.path)
        ):
            return None
        return text.rstrip("/")

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"ok": False, "code": code, "message": message, "enabled": False}

    async def start_tracking(self, target_id, webhook_url: str, *, own_history: bool = True) -> dict:
        try:
            parsed_target = int(target_id)
        except (TypeError, ValueError):
            return self._error("INVALID_TARGET_ID", "ID do alvo invalido.")
        if parsed_target <= 0 or parsed_target >= 2**64:
            return self._error("INVALID_TARGET_ID", "ID do alvo invalido.")

        normalized_webhook = self.normalize_webhook_url(webhook_url)
        if normalized_webhook is None:
            return self._error("INVALID_WEBHOOK", "URL de webhook invalida.")

        await self._stop_runtime()
        self._run_id += 1
        self.target_id = parsed_target
        self.webhook_url = normalized_webhook
        self.state = TrackerState()
        self._stats = self._new_stats()
        self.enabled = True
        self._started_at = discord.utils.utcnow()
        self._history_status = "pending"
        self._webhook_queue = asyncio.Queue(maxsize=self.config.WEBHOOK_QUEUE_MAXLEN)

        try:
            session = await self._get_webhook_session()
            self._webhook = discord.Webhook.from_url(normalized_webhook, session=session)
        except (TypeError, ValueError):
            self.enabled = False
            await self._close_webhook_session()
            self.target_id = None
            self.webhook_url = None
            return self._error("INVALID_WEBHOOK", "URL de webhook invalida.")

        run_id = self._run_id
        self._webhook_task = asyncio.create_task(
            self._webhook_sender_loop(run_id), name=f"tracker-webhook-{parsed_target}"
        )
        if not self._subscription_loop.is_running():
            self._subscription_loop.start()
        self._subscribe_task = asyncio.create_task(
            self._subscribe_to_target(run_id), name=f"tracker-subscribe-{parsed_target}"
        )
        if own_history:
            self._history_task = asyncio.create_task(
                self._collect_history(run_id), name=f"tracker-history-{parsed_target}"
            )

        user = self.client.get_user(parsed_target)
        await self._dispatch_log(
            title="🔍 Tracker Iniciado",
            color=0xD4A5A5,
            description=("Monitoramento ativado. As assinaturas de presença estão sendo preparadas em segundo plano."),
            user=user,
        )
        self._schedule_profile_fetch(emit_changes=False, delay=2.0)
        return self.get_status(code="STARTED", message="Tracker ativado com sucesso.")

    async def stop_tracking(self) -> dict:
        was_enabled = self.enabled
        await self._stop_runtime()
        self._run_id += 1
        self.target_id = None
        self.webhook_url = None
        return {
            "ok": True,
            "code": "STOPPED" if was_enabled else "ALREADY_STOPPED",
            "message": "Tracker desativado." if was_enabled else "Tracker ja estava desativado.",
            "enabled": False,
        }

    async def shutdown(self):
        await self._stop_runtime()
        self._run_id += 1
        self.target_id = None
        self.webhook_url = None

    async def _stop_runtime(self):
        self.enabled = False
        tasks_to_cancel = (
            self._subscribe_task,
            self._pending_offline_task,
            self._pending_presence_task,
            self._pending_custom_status_task,
            self._profile_fetch_task,
            self._history_task,
        )
        pending = [task for task in tasks_to_cancel if task and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._subscribe_task = None
        self._pending_offline_task = None
        self._pending_presence_task = None
        self._pending_custom_status_task = None
        self._profile_fetch_task = None
        self._history_task = None

        loop_task = self._subscription_loop.get_task()
        if loop_task and not loop_task.done():
            self._subscription_loop.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
        await self.msg_buffer.clear()

        if self._webhook_task and not self._webhook_task.done():
            self._webhook_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._webhook_task
        self._webhook_task = None
        self._webhook_queue = None
        self._webhook = None
        await self._close_webhook_session()

        self.state = TrackerState()
        if not self._shared_resources:
            self._msg_cache.clear()
        self.tracked_voice_channels.clear()
        self.tracked_guild_ids.clear()

    def get_status(self, *, code: str = "STATUS", message: str = "") -> dict:
        queue_size = self._webhook_queue.qsize() if self._webhook_queue else 0
        return {
            "ok": True,
            "code": code,
            "message": message,
            "enabled": self.enabled,
            "target_id": self.target_id if self.enabled else None,
            "shared_guilds": len(self.tracked_guild_ids),
            "tracked_voice_channels": len(self.tracked_voice_channels),
            "queue_size": queue_size,
            "history_status": self._history_status,
            "history_days": self.config.HISTORY_DAYS,
            "profile_status": "available" if self.state.profile_initialized else "pending_or_unavailable",
            "public_connections": len(self.state.connected_accounts),
            "scope": "visible_guild_channels",
            "user": build_card(self),
            **self._stats,
        }

    def _cache_message(self, message_id: int, data: dict):
        self._msg_cache[int(message_id)] = data
        self._msg_cache.move_to_end(int(message_id))
        while len(self._msg_cache) > self.config.MSG_CACHE_MAXLEN:
            self._msg_cache.popitem(last=False)

    def _queue_webhook(self, embed: discord.Embed, event_type: str):
        queue = self._webhook_queue
        if not self.enabled or queue is None:
            return
        embed.colour = color_for(event_type, self.state.status)
        item = (self._run_id, embed, event_type)
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
                queue.task_done()
                self._stats["events_dropped"] += 1
            with suppress(asyncio.QueueFull):
                queue.put_nowait(item)
        self._stats["events_captured"] += 1

    async def _webhook_sender_loop(self, run_id: int):
        while self.enabled and run_id == self._run_id:
            queue = self._webhook_queue
            if queue is None:
                return
            item_run_id, embed, event_type = await queue.get()
            try:
                if item_run_id != self._run_id or not self.enabled or not self._webhook:
                    continue
                for attempt in range(self.config.WEBHOOK_RETRY_ATTEMPTS):
                    try:
                        async with self._webhook_send_lock:
                            await self._webhook.send(
                                embed=embed,
                                username="Madman Tracker",
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        self._stats["webhooks_sent"] += 1
                        self._stats["last_error"] = None
                        break
                    except (discord.DiscordServerError, aiohttp.ClientError, OSError, TimeoutError):
                        if attempt + 1 >= self.config.WEBHOOK_RETRY_ATTEMPTS:
                            self._record_webhook_error(event_type, "TRANSIENT_ERROR")
                            break
                        await asyncio.sleep(2**attempt)
                    except (discord.Forbidden, discord.NotFound):
                        self._record_webhook_error(event_type, "WEBHOOK_UNAVAILABLE")
                        break
                    except discord.HTTPException:
                        self._record_webhook_error(event_type, "HTTP_ERROR")
                        break
                    except Exception:
                        self._record_webhook_error(event_type, "UNEXPECTED_ERROR")
                        break
            finally:
                queue.task_done()

    def _record_webhook_error(self, event_type: str, code: str):
        self._stats["webhooks_failed"] += 1
        self._stats["last_error"] = code
        emit_line(f"TRACKER:WEBHOOK_ERROR:{event_type}:{code}")

    async def _get_webhook_session(self):
        if self._shared_session_getter is not None:
            self._webhook_session = await self._shared_session_getter()
            return self._webhook_session
        if self._webhook_session is None or self._webhook_session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._webhook_session = aiohttp.ClientSession(timeout=timeout)
        return self._webhook_session

    async def _close_webhook_session(self):
        if not self._shared_resources:
            if self._webhook_session is not None and not self._webhook_session.closed:
                await self._webhook_session.close()
        self._webhook_session = None

    async def _pause_if_ws_ratelimited(self):
        if self.client.is_ws_ratelimited():
            await asyncio.sleep(self.config.WS_RATELIMIT_PAUSE)

    async def _resolve_member_for_guild(self, guild: discord.Guild):
        member = guild.get_member(self.target_id)
        if member is not None:
            return member
        try:
            members = await guild.query_members(
                user_ids=[self.target_id],
                limit=5,
                presences=True,
                cache=True,
                subscribe=False,
            )
            return members[0] if members else None
        except Exception as error:
            self._stats["subscription_errors"] += 1
            emit_line(f"TRACKER:MEMBER_QUERY_ERROR:{guild.id}:{type(error).__name__}")
            # A failed query is not evidence that a known member has left.
            raise

    async def _ensure_guild_subscription(self, guild: discord.Guild, run_id: int) -> discord.Member | None:
        async with self._subscription_lock:
            return await self._subscribe_guild_member(guild, run_id)

    async def _subscribe_guild_member(self, guild, run_id):
        if not self.enabled or run_id != self._run_id or not self.target_id:
            return None
        await self._pause_if_ws_ratelimited()
        try:
            if not guild.is_subscribed_to(features=["typing", "threads", "member_updates"]):
                await guild.subscribe(typing=True, threads=True, member_updates=True)
        except Exception as error:
            self._stats["subscription_errors"] += 1
            emit_line(f"TRACKER:SUBSCRIBE_ERROR:{guild.id}:{type(error).__name__}")
            return None

        try:
            member = await self._resolve_member_for_guild(guild)
        except Exception:
            return None
        if member is None:
            self.tracked_guild_ids.discard(guild.id)
            return None
        self.tracked_guild_ids.add(guild.id)
        try:
            if not guild.is_subscribed_to(features=["member_updates"], members=[member]):
                await guild.subscribe_to(members=[member])
        except Exception as error:
            self._stats["subscription_errors"] += 1
            emit_line(f"TRACKER:SUBSCRIBE_MEMBER_ERROR:{guild.id}:{type(error).__name__}")
            return None
        voice = getattr(member, "voice", None)
        if voice and self._channel_allowed(voice.channel):
            if guild.id not in self.state.voice_sessions:
                self.state.voice_sessions[guild.id] = {
                    "channel_id": voice.channel.id,
                    "started": time.monotonic(),
                    "partial": True,
                }
                self.tracked_voice_channels.add(voice.channel.id)
                await self._dispatch_voice_embed(
                    "🔊 Já estava no canal de voz (início desconhecido)",
                    0x9DF9EF,
                    member,
                    voice.channel,
                    guild,
                )
        return member

    async def _subscribe_to_target(self, run_id: int):
        await asyncio.sleep(1.0)
        if not self.target_id or not self.enabled or run_id != self._run_id:
            return

        guilds = list(self.client.guilds)
        guilds.sort(key=lambda guild: guild.get_member(self.target_id) is None)
        found_member = None
        for index, guild in enumerate(guilds, 1):
            if not self.enabled or run_id != self._run_id:
                return
            member = await self._ensure_guild_subscription(guild, run_id)
            if member is not None and found_member is None:
                found_member = member
            if index % self.config.SUBSCRIBE_BATCH_SIZE == 0:
                await asyncio.sleep(self.config.SUBSCRIBE_BATCH_PAUSE)

        if found_member and self.enabled and run_id == self._run_id:
            if not self.state.initial_state_sent:
                await self._dispatch_initial_state(found_member)
            elif self._select_effective_snapshot() is None:
                # A new READY may fill the member cache without replaying a presence event.
                await self.on_presence_update(found_member, found_member)

    async def on_reconnect(self):
        if not self.enabled:
            return
        # RESUMED follows event replay. Cleanup belongs in on_disconnect so that
        # updates already received during replay are not erased here.
        previous_task = self._subscribe_task
        if previous_task and not previous_task.done():
            previous_task.cancel()
            await asyncio.gather(previous_task, return_exceptions=True)
        self._subscribe_task = asyncio.create_task(
            self._subscribe_to_target(self._run_id),
            name=f"tracker-resubscribe-{self.target_id}",
        )

    async def on_disconnect(self):
        if not self.enabled:
            return
        now = time.monotonic()
        for session in self.state.voice_sessions.values():
            self._stats["voice_seconds"] += max(0, int(now - session["started"]))
        self.state.voice_sessions.clear()
        self.tracked_voice_channels.clear()
        self.state.guild_presence.clear()
        self._cancel_pending_presence()
        self._cancel_pending_offline()
        self._cancel_pending_custom_status()

    async def on_guild_join(self, guild):
        if self.enabled:
            await self._ensure_guild_subscription(guild, self._run_id)

    async def on_guild_remove(self, guild):
        self.tracked_guild_ids.discard(guild.id)
        self.state.guild_presence.pop(guild.id, None)
        session = self.state.voice_sessions.pop(guild.id, None)
        if session:
            self.tracked_voice_channels.discard(session["channel_id"])

    @tasks.loop(seconds=TrackerConfig.SUBSCRIPTION_INTERVAL_SECONDS)
    async def _subscription_loop(self):
        if not self.enabled or not self.client.is_ready():
            return
        run_id = self._run_id
        guilds = tuple(self.client.guilds)
        self.tracked_guild_ids.intersection_update(guild.id for guild in guilds)
        for index, guild in enumerate(guilds, 1):
            if not self.enabled or run_id != self._run_id:
                return
            await self._ensure_guild_subscription(guild, run_id)
            if index % self.config.SUBSCRIBE_BATCH_SIZE == 0:
                await asyncio.sleep(self.config.SUBSCRIBE_BATCH_PAUSE)

    @_subscription_loop.before_loop
    async def _before_subscription(self):
        await self.client.wait_until_ready()
        # Startup has its own scan; do not immediately scan twice.
        await asyncio.sleep(self.config.SUBSCRIPTION_INTERVAL_SECONDS)
