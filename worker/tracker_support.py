import asyncio

from collections import OrderedDict
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
    devices_repr: list = field(default_factory=list)
    guild_presence: dict[int, dict] = field(default_factory=dict)
    bio: str | None = None
    connected_accounts: list = field(default_factory=list)
    in_voice_channel_id: int | None = None
    last_typed_at: float = 0.0
    last_profile_fetch: float = 0.0


class MessageBuffer:
    def __init__(self, flush_callback):
        self._buffers: OrderedDict[int, list[dict]] = OrderedDict()
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task] = set()
        self._flush_callback = flush_callback
        self.config = TrackerConfig()

    def add(self, channel_id: int, msg_data: dict):
        if (
            channel_id not in self._buffers
            and len(self._buffers) >= self.config.BUFFER_MAX_CHANNELS
        ):
            oldest_channel, messages = self._buffers.popitem(last=False)
            timer = self._timers.pop(oldest_channel, None)
            if timer:
                timer.cancel()
            self._schedule_callback(oldest_channel, messages)

        if channel_id not in self._buffers:
            self._buffers[channel_id] = []

        self._buffers[channel_id].append(msg_data)
        self._buffers.move_to_end(channel_id)

        if channel_id in self._timers:
            self._timers[channel_id].cancel()

        if len(self._buffers[channel_id]) >= self.config.MESSAGE_BATCH_MAX:
            messages = self._buffers.pop(channel_id)
            self._timers.pop(channel_id, None)
            self._schedule_callback(channel_id, messages)
            return

        self._timers[channel_id] = asyncio.get_running_loop().call_later(
            self.config.FLUSH_DELAY, self._schedule_flush, channel_id
        )

    def _track_task(self, task: asyncio.Task):
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task):
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def _schedule_flush(self, channel_id: int):
        self._track_task(asyncio.create_task(self._flush(channel_id)))

    def _schedule_callback(self, channel_id: int, messages: list[dict]):
        if messages:
            self._track_task(
                asyncio.create_task(self._flush_callback(channel_id, messages))
            )

    async def _flush(self, channel_id: int):
        messages = self._buffers.pop(channel_id, [])
        self._timers.pop(channel_id, None)
        if messages:
            await self._flush_callback(channel_id, messages)

    async def flush(self, channel_id: int):
        timer = self._timers.pop(channel_id, None)
        if timer:
            timer.cancel()
        await self._flush(channel_id)

    async def clear(self):
        for handle in self._timers.values():
            handle.cancel()
        self._buffers.clear()
        self._timers.clear()
        tasks = tuple(task for task in self._tasks if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


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

    attachments = []
    for attachment in getattr(message, "attachments", []):
        attachments.append(
            {
                "url": str(getattr(attachment, "url", "")),
                "filename": str(getattr(attachment, "filename", "arquivo")),
                "content_type": getattr(attachment, "content_type", None),
                "size": int(getattr(attachment, "size", 0) or 0),
                "spoiler": bool(getattr(attachment, "is_spoiler", lambda: False)()),
                "duration": getattr(attachment, "duration", None),
            }
        )

    embeds = []
    for embed in getattr(message, "embeds", [])[:5]:
        embeds.append(
            {
                "title": getattr(embed, "title", None),
                "description": getattr(embed, "description", None),
                "url": getattr(embed, "url", None),
            }
        )

    reply = None
    reference = getattr(message, "reference", None)
    resolved = getattr(reference, "resolved", None)
    if isinstance(resolved, discord.Message):
        reply = {
            "message_id": resolved.id,
            "author_name": getattr(resolved.author, "name", "desconhecido"),
            "content": resolved.content or "",
        }
    elif reference and getattr(reference, "message_id", None):
        reply = {"message_id": reference.message_id}

    poll_data = None
    poll = getattr(message, "poll", None)
    if poll is not None:
        poll_data = {
            "question": str(getattr(poll, "question", "")),
            "answers": [
                {"id": getattr(answer, "id", None), "text": str(answer)}
                for answer in getattr(poll, "answers", [])[:10]
            ],
            "multiple": bool(getattr(poll, "multiple", False)),
        }

    return {
        "author_id": message.author.id,
        "author_name": message.author.name,
        "author_avatar": str(message.author.display_avatar.url),
        "content": message.content or "",
        "timestamp": ts_fmt(message.created_at),
        "message_id": message.id,
        "attachments": attachments,
        "stickers": stickers,
        "embeds": embeds,
        "reply": reply,
        "poll": poll_data,
        "message_type": str(getattr(message, "type", "default")),
        "jump_url": getattr(message, "jump_url", None),
    }
