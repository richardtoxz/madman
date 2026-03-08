import asyncio

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import discord

from .config import TrackerConfig


@dataclass
class TrackerState:
    username: str | None = None
    global_name: str | None = None
    avatar_url: str | None = None
    status: str | None = None
    activities_repr: list = field(default_factory=list)
    bio: str | None = None
    connected_accounts: list = field(default_factory=list)
    in_voice_channel_id: int | None = None
    last_typed_at: float = 0.0
    last_presence_update: float = 0.0
    last_profile_fetch: float = 0.0


class MessageBuffer:
    def __init__(self, flush_callback):
        self._buffers: dict[int, list[dict]] = {}
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._flush_callback = flush_callback
        self.config = TrackerConfig()

    def add(self, channel_id: int, msg_data: dict):
        if channel_id not in self._buffers:
            self._buffers[channel_id] = []

        self._buffers[channel_id].append(msg_data)

        if channel_id in self._timers:
            self._timers[channel_id].cancel()

        loop = asyncio.get_running_loop()
        self._timers[channel_id] = loop.call_later(
            self.config.FLUSH_DELAY,
            lambda cid=channel_id: asyncio.create_task(self._flush(cid)),
        )

    async def _flush(self, channel_id: int):
        messages = self._buffers.pop(channel_id, [])
        self._timers.pop(channel_id, None)
        if messages:
            await self._flush_callback(channel_id, messages)

    def clear(self):
        for handle in self._timers.values():
            handle.cancel()
        self._buffers.clear()
        self._timers.clear()


class ContextRing:
    def __init__(self, maxlen=40):
        self._rings: dict[int, deque] = {}
        self._maxlen = maxlen

    def push(self, channel_id: int, msg_data: dict):
        if channel_id not in self._rings:
            self._rings[channel_id] = deque(maxlen=self._maxlen)
        self._rings[channel_id].append(msg_data)

    def snapshot(self, channel_id: int) -> list[dict]:
        return list(self._rings.get(channel_id, []))


def ts_fmt(dt=None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%d/%m/%Y - %H:%M:%S")


def msg_to_dict(message: discord.Message) -> dict:
    stickers = []
    for s in getattr(message, "stickers", []):
        fmt = getattr(s, "format", None)
        name = getattr(s, "name", "?")
        sticker_id = getattr(s, "id", None)
        if not sticker_id:
            continue

        fmt_name = fmt.name if fmt else "unknown"
        native_url = getattr(s, "url", None)

        embeddable = native_url and not native_url.endswith(".json")
        stickers.append(
            {
                "name": name,
                "url": native_url if embeddable else None,
                "format": fmt_name,
            }
        )

    return {
        "author_id": message.author.id,
        "author_name": message.author.name,
        "author_avatar": str(message.author.display_avatar.url),
        "content": message.content or "",
        "timestamp": ts_fmt(message.created_at),
        "message_id": message.id,
        "attachments": [a.url for a in message.attachments],
        "stickers": stickers,
    }
