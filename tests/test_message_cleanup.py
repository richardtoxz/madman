from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from worker.config import ControlState
from worker.message_manager import MessageManager


class _FastConfig:
    BASE_DELAY = 0.0
    MIN_DELAY = 0.0
    MAX_DELAY = 0.0
    BACKOFF_MULTIPLIER = 1.0
    COOLDOWN_STEP = 0.0
    COOLDOWN_AFTER = 1
    MAX_429_CONSECUTIVE = 2
    BATCH_SIZE = 10_000
    BATCH_PAUSE_MIN = 0.0
    BATCH_PAUSE_MAX = 0.0


class _FakeResponse:
    status = 404
    reason = "Not Found"


class _FakeMessage:
    def __init__(
        self,
        message_id: int,
        author_id: int,
        *,
        message_type=discord.MessageType.default,
        total_results: int | None = None,
        indexing: bool = False,
        delete_error: Exception | None = None,
    ):
        self.id = message_id
        self.author = SimpleNamespace(id=author_id)
        self.type = message_type
        self.total_results = total_results
        self.doing_deep_historical_index = indexing
        self.delete_error = delete_error
        self.deleted = False

    async def delete(self):
        if self.delete_error:
            raise self.delete_error
        self.deleted = True


class _FakeDestination:
    def __init__(self, search_messages=(), history_messages=()):
        self.id = 9001
        self.search_messages = list(search_messages)
        self.history_messages = list(history_messages)
        self.search_calls = []
        self.history_calls = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)

        async def iterator():
            messages = list(self.search_messages)
            limit = kwargs.get("limit")
            if limit is not None:
                messages = messages[:limit]
            for message in messages:
                if not message.deleted:
                    yield message

        return iterator()

    def history(self, **kwargs):
        self.history_calls.append(kwargs)

        async def iterator():
            for message in list(self.history_messages):
                if not message.deleted:
                    yield message

        return iterator()


class MessageCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=42)
        self.client = SimpleNamespace(user=self.user)
        self.manager = MessageManager(self.client, ControlState())
        self.manager.config = _FastConfig()

    def test_only_own_deletable_messages_are_selected(self):
        own = _FakeMessage(1, 42)
        other = _FakeMessage(2, 99)
        system = _FakeMessage(
            3, 42, message_type=discord.MessageType.recipient_add
        )

        self.assertTrue(self.manager._is_deletable_message(own))
        self.assertFalse(self.manager._is_deletable_message(other))
        self.assertFalse(self.manager._is_deletable_message(system))

    async def test_search_count_uses_first_result_total_without_full_scan(self):
        messages = [
            _FakeMessage(1, 42, total_results=2),
            _FakeMessage(
                2,
                42,
                total_results=2,
                message_type=discord.MessageType.recipient_add,
            ),
        ]
        destination = _FakeDestination(search_messages=messages)

        with patch("worker.message_helpers.emit_line"):
            count = await self.manager._count_destination_messages(destination)

        self.assertEqual(count, 2)
        self.assertEqual(
            destination.search_calls,
            [{"authors": [self.user], "limit": 1, "oldest_first": False}],
        )
        self.assertEqual(destination.history_calls, [])

    async def test_indexing_search_falls_back_to_unlimited_history(self):
        search_message = _FakeMessage(
            1, 42, total_results=1, indexing=True
        )
        history_messages = [
            _FakeMessage(2, 42),
            _FakeMessage(3, 99),
            _FakeMessage(
                4, 42, message_type=discord.MessageType.recipient_remove
            ),
            _FakeMessage(5, 42),
        ]
        destination = _FakeDestination(
            search_messages=[search_message], history_messages=history_messages
        )

        with patch("worker.message_helpers.emit_line"):
            count = await self.manager._count_destination_messages(destination)

        self.assertEqual(count, 2)
        self.assertEqual(
            destination.history_calls,
            [{"limit": None, "oldest_first": False}],
        )

    async def test_complete_delete_search_skips_redundant_history_scan(self):
        first = _FakeMessage(1, 42, total_results=3)
        second = _FakeMessage(2, 42, total_results=3)
        third = _FakeMessage(3, 42, total_results=3)
        destination = _FakeDestination(
            search_messages=[first, second, third],
            history_messages=[first, second, third],
        )

        stream = self.manager._iter_destination_messages(destination)
        with patch("worker.message_helpers.emit_line"):
            stats = await self.manager._delete_messages_adaptive(
                stream, total_hint=3
            )

        self.assertEqual(stats["deletadas"], 3)
        self.assertTrue(all(message.deleted for message in (first, second, third)))
        self.assertEqual(destination.history_calls, [])

    async def test_non_deletable_search_hit_is_excluded_from_final_total(self):
        regular = _FakeMessage(1, 42, total_results=2)
        system = _FakeMessage(
            2,
            42,
            total_results=2,
            message_type=discord.MessageType.recipient_add,
        )
        destination = _FakeDestination(search_messages=[regular, system])

        with patch("worker.message_helpers.emit_line"):
            stats = await self.manager._delete_messages_adaptive(
                self.manager._iter_destination_messages(destination), total_hint=2
            )

        result = self.manager._result_text(stats, remaining=0, initial_total=2)
        self.assertEqual(stats["deletadas"], 1)
        self.assertEqual(stats["ignoradas"], 1)
        self.assertIn("Deletadas 1 de 1", result)
        self.assertIn("ignoradas 1", result)
        self.assertEqual(destination.history_calls, [])

    async def test_incomplete_delete_search_falls_back_to_history(self):
        first = _FakeMessage(1, 42, total_results=3)
        second = _FakeMessage(2, 42)
        third = _FakeMessage(3, 42)
        destination = _FakeDestination(
            search_messages=[first], history_messages=[first, second, third]
        )

        stream = self.manager._iter_destination_messages(destination)
        with patch("worker.message_helpers.emit_line"):
            stats = await self.manager._delete_messages_adaptive(
                stream, total_hint=3
            )

        self.assertEqual(stats["deletadas"], 3)
        self.assertTrue(all(message.deleted for message in (first, second, third)))
        self.assertEqual(
            destination.history_calls,
            [{"limit": None, "oldest_first": False}],
        )

    async def test_quick_count_does_not_scan_history_while_indexing(self):
        search_message = _FakeMessage(
            1, 42, total_results=1_500, indexing=True
        )
        destination = _FakeDestination(
            search_messages=[search_message],
            history_messages=[
                _FakeMessage(message_id, 42) for message_id in range(2, 1502)
            ],
        )

        with patch("worker.message_helpers.emit_line"):
            count = await self.manager._get_quick_count(destination)

        self.assertEqual(count, 1_500)
        self.assertEqual(destination.history_calls, [])
        self.assertEqual(
            destination.search_calls,
            [{"authors": [self.user], "limit": 1, "oldest_first": False}],
        )

    async def test_final_verification_treats_empty_search_as_clean(self):
        destination = _FakeDestination(history_messages=[_FakeMessage(1, 99)])

        with patch("worker.message_helpers.emit_line"):
            remaining = await self.manager._verify_clean(destination)

        self.assertEqual(remaining, 0)
        self.assertEqual(destination.history_calls, [])

    async def test_already_deleted_message_is_not_reported_as_failure(self):
        error = discord.NotFound(
            _FakeResponse(), {"code": 10008, "message": "Unknown Message"}
        )
        message = _FakeMessage(1, 42, delete_error=error)

        with patch("worker.message_helpers.emit_line"):
            stats = await self.manager._delete_messages_adaptive(
                [message], total_hint=1
            )

        self.assertEqual(stats["ausentes"], 1)
        self.assertEqual(stats["falhas"], 0)
        self.assertEqual(stats["proibidas"], 0)

    async def test_large_async_source_is_processed_as_a_stream(self):
        deleted = 0

        class StreamingMessage(_FakeMessage):
            async def delete(inner_self):
                nonlocal deleted
                deleted += 1
                inner_self.deleted = True

        async def messages():
            for message_id in range(1, 1501):
                yield StreamingMessage(message_id, 42)

        with patch("worker.message_helpers.emit_line"):
            stats = await self.manager._delete_messages_adaptive(
                messages(), total_hint=1500
            )

        self.assertEqual(deleted, 1500)
        self.assertEqual(stats["deletadas"], 1500)
        self.assertEqual(stats["falhas"], 0)

    def test_unavailable_history_produces_partial_result(self):
        self.manager._unavailable_sources.add(("history", 123, "FORBIDDEN"))
        result = self.manager._result_text(
            self.manager._empty_stats(), remaining=0, initial_total=0
        )

        self.assertTrue(result.startswith("RESULT:PARCIAL:"))
        self.assertIn("fontes indisponiveis 1", result)

    async def test_new_operation_requests_graceful_stop_before_replacing_task(self):
        stopped = False

        async def previous_operation():
            nonlocal stopped
            while not self.manager.cancelled:
                await asyncio.sleep(0)
            stopped = True

        previous = asyncio.create_task(previous_operation())
        self.manager._current_task = previous
        await asyncio.sleep(0)

        await self.manager._reboot_state()

        self.assertTrue(stopped)
        self.assertTrue(previous.done())
        self.assertFalse(previous.cancelled())
        self.assertFalse(self.manager.cancelled)

    def test_ipc_labels_cannot_break_progress_protocol(self):
        self.assertEqual(
            self.manager._ipc_label("nome:com\nquebra\x00"),
            "nome：com quebra",
        )


if __name__ == "__main__":
    unittest.main()
