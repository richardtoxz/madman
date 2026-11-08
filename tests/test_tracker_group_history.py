from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self

from worker.core.config import TrackerConfig
from worker.tracker.history import collect_group_history
from worker.tracker.manager import TrackerManager

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def _message(message_id: int, author_id: int, minutes_ago: int = 10):
    return NS(
        id=message_id,
        author=NS(id=author_id),
        created_at=NOW - timedelta(minutes=minutes_ago),
        channel=NS(id=1),
    )


class _FakeGuild:
    def __init__(self, guild_id: int, messages):
        self.id = guild_id
        self.messages = list(messages)
        self.search_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        wanted = {obj.id for obj in kwargs["authors"]}
        cursor = getattr(kwargs.get("before"), "id", None)

        async def iterator():
            sent = 0
            for message in self.messages:
                if message.author.id not in wanted:
                    continue
                if cursor is not None and message.id >= cursor:
                    continue
                if sent >= kwargs["limit"]:
                    return
                sent += 1
                yield message

        return iterator()


def _tracker(target_id: int):
    tracker = TrackerManager(NS())
    tracker.target_id = target_id
    tracker.enabled = True
    tracker._started_at = NOW
    tracker._run_id = 1
    tracker._capture_message = AsyncMock()
    tracker._channel_allowed = lambda *args, **kwargs: True
    return tracker


class GroupHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, guilds, trackers, config=None):
        client = NS(guilds=guilds)
        config = config or TrackerConfig()
        with patch("worker.tracker.history.emit_line"):
            with patch.object(config, "HISTORY_GUILD_PAUSE", 0, create=True):
                await collect_group_history(client, [(t, t._run_id) for t in trackers], asyncio.Lock(), config)

    async def test_one_search_per_guild_routes_messages_by_author(self):
        messages = [_message(100 - i, 42 if i % 2 else 77) for i in range(10)]
        guild = _FakeGuild(1, messages)
        first, second = _tracker(42), _tracker(77)

        await self._run([guild], [first, second])

        self.assertEqual(len(guild.search_calls), 1)
        self.assertEqual({o.id for o in guild.search_calls[0]["authors"]}, {42, 77})
        self.assertEqual(first._capture_message.await_count, 5)
        self.assertEqual(second._capture_message.await_count, 5)
        for call in first._capture_message.await_args_list:
            self.assertEqual(call.args[0].author.id, 42)
        self.assertEqual(first._history_status, "complete")
        self.assertEqual(first._stats["history_guilds_scanned"], 1)

    async def test_busy_author_does_not_starve_quiet_author(self):
        config = TrackerConfig()
        busy = [_message(1000 - i, 42) for i in range(config.HISTORY_PER_GUILD * 3)]
        quiet = [_message(10 - i, 77, minutes_ago=600) for i in range(3)]
        guild = _FakeGuild(1, busy + quiet)
        first, second = _tracker(42), _tracker(77)

        await self._run([guild], [first, second], config)

        self.assertEqual(second._capture_message.await_count, 3)
        self.assertEqual(first._capture_message.await_count, config.HISTORY_PER_GUILD)
        self.assertEqual(first._stats["history_guilds_limited"], 1)
        # A 1a busca e consumida so pelo autor ativo; a 2a continua apenas com o outro.
        self.assertEqual(len(guild.search_calls), 2)
        self.assertEqual({o.id for o in guild.search_calls[1]["authors"]}, {77})

    async def test_removed_tracker_is_skipped(self):
        guild = _FakeGuild(1, [_message(5, 42), _message(4, 77)])
        first, second = _tracker(42), _tracker(77)
        second.enabled = False

        await self._run([guild], [first, second])

        self.assertEqual({o.id for o in guild.search_calls[0]["authors"]}, {42})
        second._capture_message.assert_not_awaited()

    async def test_search_error_marks_partial_and_continues(self):
        broken = _FakeGuild(1, [])
        broken.search = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("falha"))
        good = _FakeGuild(2, [_message(9, 42)])
        first = _tracker(42)

        await self._run([broken, good], [first])

        self.assertEqual(first._capture_message.await_count, 1)
        self.assertEqual(first._stats["history_errors"], 1)
        self.assertEqual(first._history_status, "partial")


if __name__ == "__main__":
    unittest.main()
