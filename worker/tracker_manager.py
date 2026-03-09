import os
import asyncio
from contextlib import suppress

import discord
from collections import deque
from discord.ext import tasks
from .config import TrackerConfig
from .ipc import emit_line
from .tracker_dispatch import TrackerDispatchMixin
from .tracker_events import TrackerEventMixin
from .tracker_support import ContextRing, MessageBuffer, TrackerState


class TrackerManager(TrackerDispatchMixin, TrackerEventMixin):
    def __init__(self, client):
        self.client = client
        self.target_id = None
        self.webhook_url = None
        self.enabled = False
        self.state = TrackerState()
        self._webhook_session = None
        self.config = TrackerConfig()
        self.msg_buffer = MessageBuffer(self._on_buffer_flush)
        self.context_ring = ContextRing(maxlen=self.config.CONTEXT_MAXLEN)
        self._msg_cache: dict[int, dict] = {}
        self._msg_cache_order: deque = deque(maxlen=self.config.MSG_CACHE_MAXLEN)
        self.tracked_voice_channels: set[int] = set()
        self.tracked_guild_ids: set[int] = set()
        self._run_id = 0
        self._subscribe_task: asyncio.Task | None = None
        self._pending_offline_task: asyncio.Task | None = None

        target_env = os.getenv("TRACKER_TARGET_ID")
        webhook_env = os.getenv("TRACKER_WEBHOOK_URL")
        if target_env and target_env.isdigit() and webhook_env:
            self.target_id = int(target_env)
            self.webhook_url = webhook_env
            self.enabled = True
            self._run_id = 1
            self._subscription_loop.start()

    def start_tracking(self, target_id: str, webhook_url: str):
        if target_id.isdigit():
            self.target_id = int(target_id)
        self.webhook_url = webhook_url
        self.enabled = True
        self._run_id += 1
        self._reset_runtime_state()
        if self._subscribe_task and not self._subscribe_task.done():
            self._subscribe_task.cancel()
        if not self._subscription_loop.is_running():
            self._subscription_loop.start()
        self._subscribe_task = asyncio.create_task(
            self._subscribe_to_target(self._run_id)
        )

    def _reset_runtime_state(self):
        if self._pending_offline_task and not self._pending_offline_task.done():
            self._pending_offline_task.cancel()
        self._pending_offline_task = None
        self.state = TrackerState()
        self.msg_buffer.clear()
        self._msg_cache.clear()
        self._msg_cache_order.clear()
        self.tracked_voice_channels.clear()
        self.tracked_guild_ids.clear()

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
                limit=1,
                presences=True,
                cache=True,
                subscribe=True,
            )
            if members:
                return members[0]
        except Exception:
            pass

        try:
            return await guild.fetch_member(self.target_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _ensure_guild_subscription(
        self, guild: discord.Guild, run_id: int
    ) -> discord.Member | None:
        if not self.enabled or run_id != self._run_id or not self.target_id:
            return None

        await self._pause_if_ws_ratelimited()

        try:
            if not guild.is_subscribed_to(features=["member_updates"]):
                await guild.subscribe(member_updates=True)
        except Exception as e:
            emit_line(f"TRACKER:SUBSCRIBE_ERR:{guild.id}:{e}")
            return None

        member = await self._resolve_member_for_guild(guild)
        if member is None:
            return None

        self.tracked_guild_ids.add(guild.id)

        try:
            if not guild.is_subscribed_to(
                features=["member_updates"], members=[member]
            ):
                await guild.subscribe_to(members=[member])
        except Exception as e:
            emit_line(f"TRACKER:SUBSCRIBE_MEMBER_ERR:{guild.id}:{e}")
            return None

        return member

    async def stop_tracking(self):
        self.enabled = False
        self._run_id += 1
        self.target_id = None
        self.webhook_url = None
        self._reset_runtime_state()
        await self._cancel_subscribe_task()
        if self._subscription_loop.is_running():
            self._subscription_loop.stop()
        await self._close_webhook_session()

    async def shutdown(self):
        self.enabled = False
        self._run_id += 1
        self.target_id = None
        self.webhook_url = None
        self._reset_runtime_state()
        await self._cancel_subscribe_task()
        if self._subscription_loop.is_running():
            self._subscription_loop.stop()
        await self._close_webhook_session()

    async def _cancel_subscribe_task(self):
        if self._subscribe_task and not self._subscribe_task.done():
            self._subscribe_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._subscribe_task
        self._subscribe_task = None

    async def _close_webhook_session(self):
        if self._webhook_session is None or self._webhook_session.closed:
            self._webhook_session = None
            return

        await self._webhook_session.close()
        self._webhook_session = None

    async def _subscribe_to_target(self, run_id: int):
        await asyncio.sleep(1.5)
        if not self.target_id or not self.enabled or run_id != self._run_id:
            return

        found_member = None
        processed = 0

        for guild in self.client.guilds:
            if not self.enabled or run_id != self._run_id:
                return
            member = await self._ensure_guild_subscription(guild, run_id)
            if member and found_member is None:
                found_member = member

            processed += 1
            if processed % self.config.SUBSCRIBE_BATCH_SIZE == 0:
                await asyncio.sleep(self.config.SUBSCRIBE_BATCH_PAUSE)

        if found_member and self.enabled and run_id == self._run_id:
            await self._dispatch_initial_state(found_member)

    @tasks.loop(seconds=TrackerConfig.SUBSCRIPTION_INTERVAL_SECONDS)
    async def _subscription_loop(self):
        if not self.enabled or not self.client.is_ready():
            return

        run_id = self._run_id
        processed = 0

        for guild_id in tuple(self.tracked_guild_ids):
            if not self.enabled or run_id != self._run_id or not self.target_id:
                return

            guild = self.client.get_guild(guild_id)
            if guild is None:
                self.tracked_guild_ids.discard(guild_id)
                continue

            member = await self._ensure_guild_subscription(guild, run_id)
            if member is None:
                self.tracked_guild_ids.discard(guild_id)

            processed += 1
            if processed % self.config.SUBSCRIBE_BATCH_SIZE == 0:
                await asyncio.sleep(self.config.SUBSCRIBE_BATCH_PAUSE)

    @_subscription_loop.before_loop
    async def _before_subscription(self):
        await self.client.wait_until_ready()
