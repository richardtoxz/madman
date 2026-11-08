from __future__ import annotations

import contextlib
import io
import json
import logging
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from scripts.delete_data_package_messages import (
    AbortCleanup,
    ChannelArchive,
    Checkpoint,
    DataPackageCleaner,
    DiscordRateLimitMonitor,
    PackageError,
    build_parser,
    load_archives,
    load_package_owner,
    prepared_input,
    source_fingerprint,
)
from worker.core.pacing import MessageConfig


class _NotFound(Exception):
    pass


class _Forbidden(Exception):
    pass


class _RateLimited(Exception):
    retry_after = 0.0
    cloudflare = False


class _HTTPException(Exception):
    pass


class _CaptchaRequired(Exception):
    pass


class _MessageType:
    def __init__(self, name: str = "default", *, deletable: bool = True):
        self.name = name
        self.deletable = deletable

    def is_deletable(self):
        return self.deletable


class _Message:
    def __init__(
        self,
        message_id: int,
        author_id: int,
        *,
        deletable: bool = True,
        message_type: str = "default",
        delete_errors=(),
        delete_callback=None,
    ):
        self.id = message_id
        self.author = SimpleNamespace(id=author_id)
        self.type = _MessageType(message_type, deletable=deletable)
        self.delete_errors = list(delete_errors)
        self.delete_callback = delete_callback
        self.deleted = False
        self.delete_calls = 0

    async def delete(self):
        self.delete_calls += 1
        if self.delete_callback is not None:
            self.delete_callback()
        if self.delete_errors:
            raise self.delete_errors.pop(0)
        self.deleted = True


class _HistoryChannel:
    def __init__(self, channel_id: int, messages=()):
        self.id = channel_id
        self.messages = list(messages)
        self.history_calls = []

    def history(self, **kwargs):
        self.history_calls.append(kwargs)

        async def iterator():
            boundary = getattr(kwargs.get("before"), "id", None)
            for message in sorted(self.messages, key=lambda item: item.id, reverse=True):
                if message.deleted:
                    continue
                if boundary is not None and message.id >= boundary:
                    continue
                yield message

        return iterator()


class _DMChannel(_HistoryChannel):
    def __init__(self, channel_id: int, messages=()):
        super().__init__(channel_id, messages)
        self.closed = False

    async def close(self):
        self.closed = True


class _GroupChannel(_HistoryChannel):
    pass


class _GuildChannel(_HistoryChannel):
    pass


class _Client:
    def __init__(self, user_id: int, channels, *, partial_channels=()):
        self.user = SimpleNamespace(id=user_id)
        self.channels = {channel.id: channel for channel in channels}
        self.partial_channels = {channel.id: channel for channel in partial_channels}
        self.private_channels = [channel for channel in channels if isinstance(channel, (_DMChannel, _GroupChannel))]
        self.fetch_calls = []
        self.partial_calls = []
        self.http = SimpleNamespace(delete_channel=self._delete_channel)
        self.http_delete_calls = []

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        self.fetch_calls.append(channel_id)
        channel = self.channels.get(channel_id)
        if channel is None:
            raise _NotFound()
        return channel

    def get_partial_messageable(self, channel_id: int, *, type=None):
        self.partial_calls.append((channel_id, type))
        return self.partial_channels[channel_id]

    async def _delete_channel(self, channel_id: int, *, silent: bool):
        self.http_delete_calls.append((channel_id, silent))


DISCORD = SimpleNamespace(
    DMChannel=_DMChannel,
    GroupChannel=_GroupChannel,
    NotFound=_NotFound,
    Forbidden=_Forbidden,
    RateLimited=_RateLimited,
    HTTPException=_HTTPException,
    CaptchaRequired=_CaptchaRequired,
    ChannelType=SimpleNamespace(private="private"),
)


class DataPackageParserTests(unittest.TestCase):
    def test_cleanup_pacing_matches_main_message_cleanup(self):
        args = build_parser().parse_args(["package.zip"])
        cleaner = DataPackageCleaner(DISCORD)
        self.assertEqual(args.delay, MessageConfig.BASE_DELAY)
        self.assertEqual(cleaner.current_delay, args.delay)

    def test_loads_official_style_tree_and_deduplicates_ids(self):
        first = 120000000000000001
        second = 120000000000000002
        channel_id = 220000000000000001

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            conversation = root / "messages" / f"c{channel_id}"
            conversation.mkdir(parents=True)
            (root / "messages" / "index.json").write_text(
                json.dumps({f"c{channel_id}": "Direct Message with Teste"}),
                encoding="utf-8",
            )
            (conversation / "channel.json").write_text(
                json.dumps({"id": str(channel_id), "type": "DM"}),
                encoding="utf-8",
            )
            (conversation / "messages.json").write_text(
                json.dumps(
                    [
                        {"ID": str(first), "Contents": "a"},
                        {"ID": str(first), "Contents": "duplicada"},
                        {"ID": str(second), "Contents": "b"},
                    ]
                ),
                encoding="utf-8",
            )

            archives = load_archives(root)

        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].channel_id, channel_id)
        self.assertEqual(archives[0].name, "Direct Message with Teste")
        self.assertEqual(archives[0].message_ids, (second, first))
        self.assertEqual(archives[0].kind, "dm")

    def test_loads_zip_and_checkpoint_fingerprint_ignores_its_state_file(self):
        message_id = 120000000000000001
        channel_id = 220000000000000001

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "package"
            conversation = source / f"c{channel_id}"
            conversation.mkdir(parents=True)
            (conversation / "messages.json").write_text(json.dumps([{"id": str(message_id)}]), encoding="utf-8")
            before = source_fingerprint(source)
            state = source / ".madman-data-cleanup-state.json"
            Checkpoint(state, before).record(SimpleNamespace(channel_id=channel_id, to_dict=lambda: {"status": "x"}))
            after = source_fingerprint(source)

            zip_path = root / "package.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(
                    conversation / "messages.json",
                    f"c{channel_id}/messages.json",
                )
                archive.writestr("attachments/arquivo-grande.bin", b"nao extrair")
                archive.writestr(
                    "Account/user.json",
                    json.dumps({"id": "320000000000000001"}),
                )

            with prepared_input(zip_path) as extracted:
                archives = load_archives(extracted)
                package_owner_id = load_package_owner(extracted)
                attachment_was_extracted = (extracted / "attachments" / "arquivo-grande.bin").exists()

        self.assertEqual(before, after)
        self.assertEqual(archives[0].channel_id, channel_id)
        self.assertEqual(archives[0].message_ids, (message_id,))
        self.assertEqual(package_owner_id, 320000000000000001)
        self.assertFalse(attachment_was_extracted)

    def test_checkpoint_rejects_a_different_account(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            first = Checkpoint(state, "fingerprint")
            first.bind_user(320000000000000001)
            resumed = Checkpoint(state, "fingerprint")

            with self.assertRaisesRegex(PackageError, "outra conta"):
                resumed.bind_user(320000000000000002)

    def test_checkpoint_reprocesses_channel_skipped_by_older_version(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            checkpoint = Checkpoint(state, "fingerprint")
            checkpoint.record(
                SimpleNamespace(
                    channel_id=220000000000000001,
                    to_dict=lambda: {"status": "skipped_non_dm"},
                )
            )

            self.assertFalse(checkpoint.is_finished(220000000000000001))

    def test_rejects_transcript_record_without_message_id(self):
        channel_id = 220000000000000001
        with tempfile.TemporaryDirectory() as temp:
            conversation = Path(temp) / f"c{channel_id}"
            conversation.mkdir()
            (conversation / "messages.json").write_text(
                json.dumps([{"Contents": "sem ID"}]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PackageError, "nao possui um ID valido"):
                load_archives(Path(temp))


class DataPackageCleanerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cleaner = DataPackageCleaner(DISCORD, delay=0, verification_attempts=2)
        self.user_id = 320000000000000001

    async def test_more_than_eighty_messages_use_only_the_configured_cooldown(self):
        cleaner = DataPackageCleaner(DISCORD)
        first_id = 120000000000000001
        messages = [_Message(first_id + index, self.user_id) for index in range(81)]
        channel = _DMChannel(220000000000000001, messages)
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(channel.id, "DM", tuple(message.id for message in messages))

        with (
            patch("scripts.delete_data_package_messages.asyncio.sleep", new=AsyncMock()) as sleep,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = await cleaner.clean_channel(client, archive)

        self.assertEqual(result.deleted, 81)
        self.assertTrue(channel.closed)
        self.assertEqual(sleep.await_args_list, [call(MessageConfig.BASE_DELAY)] * 81)

    async def test_deletes_every_target_verifies_absence_then_closes(self):
        oldest = 120000000000000001
        newest = 120000000000000003
        own_old = _Message(oldest, self.user_id)
        own_new = _Message(newest, self.user_id)
        unrelated = _Message(oldest - 1, 99)
        channel = _DMChannel(220000000000000001, [own_old, own_new, unrelated])
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(channel.id, "DM", (oldest, newest))

        with contextlib.redirect_stdout(io.StringIO()):
            result = await self.cleaner.clean_channel(client, archive)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.deleted, 2)
        self.assertEqual(result.remaining, 0)
        self.assertTrue(channel.closed)
        self.assertGreaterEqual(len(channel.history_calls), 2)
        self.assertTrue(all(call["before"].id == newest + 1 for call in channel.history_calls))

    async def test_retries_message_that_failed_and_does_not_skip_it(self):
        message_id = 120000000000000001
        message = _Message(
            message_id,
            self.user_id,
            delete_errors=[_HTTPException("temporaria")],
        )
        channel = _DMChannel(220000000000000001, [message])
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(channel.id, "DM", (message_id,))

        with contextlib.redirect_stdout(io.StringIO()):
            result = await self.cleaner.clean_channel(client, archive)

        self.assertEqual(message.delete_calls, 2)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.failures, 1)
        self.assertTrue(channel.closed)

    async def test_internal_library_rate_limit_increases_following_delay(self):
        monitor = DiscordRateLimitMonitor()

        def report_rate_limit():
            monitor.emit(
                logging.LogRecord(
                    "discord.http",
                    logging.WARNING,
                    __file__,
                    1,
                    "We are being rate limited. Retrying in 1.09 seconds.",
                    (),
                    None,
                )
            )

        message_id = 120000000000000001
        message = _Message(
            message_id,
            self.user_id,
            delete_callback=report_rate_limit,
        )
        channel = _DMChannel(220000000000000001, [message])
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(channel.id, "DM", (message_id,))
        cleaner = DataPackageCleaner(
            DISCORD,
            delay=0.1,
            verification_attempts=1,
            rate_limit_monitor=monitor,
        )

        with (
            patch(
                "scripts.delete_data_package_messages.asyncio.sleep",
                new=AsyncMock(),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = await cleaner.clean_channel(client, archive)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.rate_limits, 1)
        self.assertAlmostEqual(cleaner.current_delay, 1.34)

    async def test_persistent_internal_rate_limits_abort_before_close(self):
        monitor = DiscordRateLimitMonitor()

        def report_rate_limit():
            monitor.emit(
                logging.LogRecord(
                    "discord.http",
                    logging.WARNING,
                    __file__,
                    1,
                    "We are being rate limited. Retrying in 1.00 seconds.",
                    (),
                    None,
                )
            )

        first_id = 120000000000000001
        messages = [
            _Message(first_id + offset, self.user_id, delete_callback=report_rate_limit)
            for offset in range(MessageConfig.MAX_429_CONSECUTIVE)
        ]
        channel = _DMChannel(220000000000000001, messages)
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(
            channel.id,
            "DM",
            tuple(message.id for message in messages),
        )
        cleaner = DataPackageCleaner(
            DISCORD,
            delay=0.1,
            verification_attempts=1,
            rate_limit_monitor=monitor,
        )

        with (
            patch(
                "scripts.delete_data_package_messages.asyncio.sleep",
                new=AsyncMock(),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(AbortCleanup, "persistente"):
                await cleaner.clean_channel(client, archive)

        self.assertFalse(channel.closed)

    async def test_closed_dm_uses_safe_partial_channel_fallback(self):
        message_id = 120000000000000001
        channel = _DMChannel(
            220000000000000001,
            [_Message(message_id, self.user_id)],
        )
        client = _Client(self.user_id, [], partial_channels=[channel])
        archive = ChannelArchive(
            channel.id,
            "DM fechada",
            (message_id,),
            kind="dm",
        )

        with contextlib.redirect_stdout(io.StringIO()):
            result = await self.cleaner.clean_channel(client, archive)

        self.assertEqual(result.status, "complete")
        self.assertEqual(client.partial_calls, [(channel.id, "private")])
        self.assertEqual(client.http_delete_calls, [(channel.id, False)])

    async def test_author_mismatch_and_non_deletable_prevent_close(self):
        first = 120000000000000001
        second = 120000000000000002
        other_author = _Message(first, 99)
        system = _Message(
            second,
            self.user_id,
            deletable=False,
            message_type="recipient_add",
        )
        channel = _DMChannel(220000000000000001, [other_author, system])
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(channel.id, "DM", (first, second))

        with contextlib.redirect_stdout(io.StringIO()):
            result = await self.cleaner.clean_channel(client, archive)

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.author_mismatches, 1)
        self.assertEqual(result.non_deletable, {"recipient_add": 1})
        self.assertEqual(result.remaining, 1)
        self.assertFalse(channel.closed)

    async def test_non_deletable_event_is_reported_and_dm_is_closed(self):
        message_id = 120000000000000001
        system = _Message(
            message_id,
            self.user_id,
            deletable=False,
            message_type="call",
        )
        channel = _DMChannel(220000000000000001, [system])
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(channel.id, "DM", (message_id,))

        with contextlib.redirect_stdout(io.StringIO()):
            result = await self.cleaner.clean_channel(client, archive)

        self.assertEqual(result.status, "complete_with_skips")
        self.assertEqual(result.non_deletable, {"call": 1})
        self.assertEqual(result.remaining, 0)
        self.assertEqual(system.delete_calls, 0)
        self.assertTrue(channel.closed)

    async def test_run_continues_after_only_non_deletable_event(self):
        first_id = 120000000000000001
        second_id = 120000000000000002
        first_channel = _DMChannel(
            220000000000000001,
            [
                _Message(
                    first_id,
                    self.user_id,
                    deletable=False,
                    message_type="call",
                )
            ],
        )
        second_message = _Message(second_id, self.user_id)
        second_channel = _DMChannel(220000000000000002, [second_message])
        client = _Client(self.user_id, [first_channel, second_channel])
        archives = [
            ChannelArchive(first_channel.id, "primeira", (first_id,)),
            ChannelArchive(second_channel.id, "segunda", (second_id,)),
        ]

        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Checkpoint(Path(temp) / "state.json", "fingerprint")
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = await self.cleaner.run(client, archives, checkpoint)

        self.assertEqual(exit_code, 0)
        self.assertTrue(first_channel.closed)
        self.assertEqual(second_message.delete_calls, 1)
        self.assertTrue(second_channel.closed)

    async def test_inaccessible_guild_does_not_block_other_guilds(self):
        first_channel_id = 220000000000000001
        second_message_id = 120000000000000002
        second_channel = _GuildChannel(
            220000000000000002,
            [_Message(second_message_id, self.user_id)],
        )
        client = _Client(self.user_id, [second_channel])
        archives = [
            ChannelArchive(
                first_channel_id,
                "inacessivel",
                (120000000000000001,),
                kind="guild",
            ),
            ChannelArchive(
                second_channel.id,
                "acessivel",
                (second_message_id,),
                kind="guild",
            ),
        ]

        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Checkpoint(Path(temp) / "state.json", "fingerprint")
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = await self.cleaner.run(client, archives, checkpoint)

            first_status = checkpoint.data["channels"][str(first_channel_id)]["status"]
            second_status = checkpoint.data["channels"][str(second_channel.id)]["status"]

        self.assertEqual(exit_code, 1)
        self.assertEqual(first_status, "inaccessible")
        self.assertEqual(second_status, "complete")
        self.assertTrue(second_channel.messages[0].deleted)

    async def test_run_rejects_account_different_from_package_owner(self):
        message_id = 120000000000000001
        message = _Message(message_id, self.user_id)
        channel = _DMChannel(220000000000000001, [message])
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(channel.id, "DM", (message_id,))

        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Checkpoint(Path(temp) / "state.json", "fingerprint")
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = await self.cleaner.run(
                    client,
                    [archive],
                    checkpoint,
                    package_owner_id=99,
                )

        self.assertEqual(exit_code, 2)
        self.assertEqual(message.delete_calls, 0)
        self.assertFalse(channel.closed)

    async def test_guild_messages_are_deleted_without_leaving_channel(self):
        message_id = 120000000000000001
        message = _Message(message_id, self.user_id)
        channel = _GuildChannel(220000000000000001, [message])
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(
            channel.id,
            "servidor",
            (message_id,),
            kind="guild",
        )

        with contextlib.redirect_stdout(io.StringIO()):
            result = await self.cleaner.clean_channel(client, archive)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.deleted, 1)
        self.assertFalse(result.closed)
        self.assertIn("servidor mantido aberto", result.detail)

    async def test_group_messages_are_deleted_without_closing_group(self):
        message_id = 120000000000000001
        message = _Message(message_id, self.user_id)
        channel = _GroupChannel(220000000000000001, [message])
        client = _Client(self.user_id, [channel])
        archive = ChannelArchive(
            channel.id,
            "grupo",
            (message_id,),
            kind="group_dm",
        )

        with contextlib.redirect_stdout(io.StringIO()):
            result = await self.cleaner.clean_channel(client, archive)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.deleted, 1)
        self.assertEqual(result.remaining, 0)
        self.assertFalse(result.closed)
        self.assertIn("grupo mantido aberto", result.detail)


if __name__ == "__main__":
    unittest.main()
