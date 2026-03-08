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
        self._run_id = 0
        self._subscribe_task: asyncio.Task | None = None

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
        self.state = TrackerState()
        self.msg_buffer.clear()
        self._msg_cache.clear()
        self._msg_cache_order.clear()
        self.tracked_voice_channels.clear()

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

        count = 0
        found_member = None

        for guild in self.client.guilds:
            if not self.enabled or run_id != self._run_id:
                return
            try:
                await guild.subscribe(member_updates=True)
                member = guild.get_member(self.target_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(self.target_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        continue

                if member:
                    found_member = member
                    await guild.subscribe_to(members=[member])
                    count += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                emit_line(f"TRACKER:SUBSCRIBE_ERR:{guild.id}:{e}")

        if found_member and self.enabled and run_id == self._run_id:
            await self._dispatch_initial_state(found_member)

    @tasks.loop(minutes=TrackerConfig.SUBSCRIPTION_INTERVAL)
    async def _subscription_loop(self):
        if not self.enabled or not self.client.is_ready():
            return
        count = 0
        for guild in self.client.guilds:
            member = guild.get_member(self.target_id)
            if member:
                try:
                    await guild.subscribe_to(members=[member])
                    count += 1
                except Exception:
                    pass

    @_subscription_loop.before_loop
    async def _before_subscription(self):
        await self.client.wait_until_ready()
