from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from worker.guild_manager import GuildManager


class _FastBulkConfig:
    MIN_DELAY = 0.0
    MAX_DELAY = 0.0
    BATCH_SIZE = 10_000
    BATCH_PAUSE_MIN = 0.0
    BATCH_PAUSE_MAX = 0.0
    INITIAL_BACKOFF = 0.0
    MAX_BACKOFF = 0.0


class _FakeResponse:
    def __init__(self, status: int, reason: str):
        self.status = status
        self.reason = reason


def _not_found():
    return discord.NotFound(
        _FakeResponse(404, "Not Found"),
        {"code": 10004, "message": "Unknown Guild"},
    )


def _forbidden():
    return discord.Forbidden(
        _FakeResponse(403, "Forbidden"),
        {"code": 50013, "message": "Missing Permissions"},
    )


class _FakeSettings:
    def __init__(self, *, muted: bool = False, edit_error=None, valid_return=True):
        self.muted = muted
        self.level = (
            discord.NotificationLevel.nothing
            if muted
            else discord.NotificationLevel.server_default
        )
        self.suppress_everyone = muted
        self.suppress_roles = muted
        self.mobile_push = not muted
        self.mute_scheduled_events = muted
        self.notify_highlights = (
            discord.HighlightLevel.disabled
            if muted
            else discord.HighlightLevel.default
        )
        self.edit_error = edit_error
        self.valid_return = valid_return
        self.edit_calls = []

    async def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        if self.edit_error:
            raise self.edit_error
        if not self.valid_return:
            return None
        self.muted = bool(kwargs["muted_until"])
        self.level = kwargs["level"]
        self.suppress_everyone = kwargs["suppress_everyone"]
        self.suppress_roles = kwargs["suppress_roles"]
        self.mobile_push = kwargs["mobile_push"]
        self.mute_scheduled_events = kwargs["mute_scheduled_events"]
        self.notify_highlights = kwargs["notify_highlights"]
        return self


class _FakeCachedGuild:
    def __init__(
        self,
        guild_id: int,
        *,
        name: str | None = None,
        owner_id: int = 1,
        unavailable: bool = False,
        settings: _FakeSettings | None = None,
    ):
        self.id = guild_id
        self.name = name or f"guild{guild_id}"
        self.owner_id = owner_id
        self.unavailable = unavailable
        self.notification_settings = settings or _FakeSettings()
        self.icon = None
        self.approximate_member_count = None
        self.approximate_presence_count = None


class _FakeUserGuild:
    def __init__(
        self,
        guild_id: int,
        *,
        name: str | None = None,
        owner: bool = False,
        leave_error=None,
    ):
        self.id = guild_id
        self.name = name or f"guild{guild_id}"
        self.owner = owner
        self.leave_error = leave_error
        self.joined = True
        self.leave_calls = 0
        self.on_leave = None
        self.icon = None
        self.approximate_member_count = guild_id * 10
        self.approximate_presence_count = guild_id

    async def leave(self):
        self.leave_calls += 1
        if self.on_leave:
            self.on_leave()
        if self.leave_error:
            if isinstance(self.leave_error, discord.NotFound):
                self.joined = False
            raise self.leave_error
        self.joined = False


class _FakeClient:
    def __init__(self, guilds=(), *, fetch_error=None):
        self.user = SimpleNamespace(id=999)
        self._guilds = list(guilds)
        self.fetch_error = fetch_error
        self.fetch_calls = 0
        self.fetch_guild_calls = []
        self.cache = {
            guild.id: _FakeCachedGuild(
                guild.id,
                name=guild.name,
                owner_id=999 if guild.owner else 1,
            )
            for guild in self._guilds
        }
        self.fetched = {}

    @property
    def guilds(self):
        return [self.cache[guild.id] for guild in self._guilds if guild.joined]

    async def fetch_guilds(self, *, with_counts=True):
        self.fetch_calls += 1
        if self.fetch_error:
            raise self.fetch_error
        return [guild for guild in self._guilds if guild.joined]

    def get_guild(self, guild_id):
        return self.cache.get(guild_id)

    async def fetch_guild(self, guild_id, *, with_counts=True):
        self.fetch_guild_calls.append((guild_id, with_counts))
        if guild_id in self.fetched:
            return self.fetched[guild_id]
        raise _not_found()


class GuildManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_is_paginated_and_stats_include_owners(self):
        guilds = [_FakeUserGuild(index, owner=index == 3) for index in range(1, 31)]
        manager = GuildManager(_FakeClient(guilds))

        page = await manager.get_guilds_page(page=1, page_size=25)
        stats = await manager.get_stats()

        self.assertTrue(page["fresh"])
        self.assertEqual(page["total"], 30)
        self.assertEqual(len(page["items"]), 5)
        self.assertEqual(page["pages"], 2)
        self.assertEqual(stats["owners"], 1)
        self.assertEqual(len(stats["guild_ids"]), 30)

    async def test_read_falls_back_to_cache_and_marks_result_stale(self):
        client = _FakeClient([_FakeUserGuild(1)], fetch_error=RuntimeError("offline"))
        manager = GuildManager(client)

        stats = await manager.get_stats()

        self.assertFalse(stats["fresh"])
        self.assertEqual(stats["total"], 1)
        self.assertIsNotNone(stats["warning"])

    async def test_bulk_aborts_when_fresh_list_is_unavailable(self):
        guild = _FakeUserGuild(1)
        manager = GuildManager(
            _FakeClient([guild], fetch_error=RuntimeError("offline"))
        )

        with patch("worker.guild_manager.emit_line") as emit:
            result = await manager.leave_all_guilds([])

        self.assertEqual(result["status"], "ABORTADO")
        self.assertEqual(guild.leave_calls, 0)
        self.assertTrue(
            any("GUILD_LEAVE_RESULT:ABORTADO" in call.args[0] for call in emit.call_args_list)
        )

    async def test_leave_protects_whitelist_and_owned_guilds(self):
        whitelisted = _FakeUserGuild(1)
        owned = _FakeUserGuild(2, owner=True)
        leave = _FakeUserGuild(3)
        missing = _FakeUserGuild(4, leave_error=_not_found())
        manager = GuildManager(_FakeClient([whitelisted, owned, leave, missing]))

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            result = await manager.leave_all_guilds([1])

        self.assertEqual(result["status"], "COMPLETADO")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["already_done"], 1)
        self.assertEqual(whitelisted.leave_calls, 0)
        self.assertEqual(owned.leave_calls, 0)
        self.assertEqual(leave.leave_calls, 1)

    async def test_failed_leave_is_partial_after_final_verification(self):
        guild = _FakeUserGuild(1, leave_error=_forbidden())
        manager = GuildManager(_FakeClient([guild]))

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            result = await manager.leave_all_guilds([])

        self.assertEqual(result["status"], "PARCIAL")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["remaining"], 1)

    async def test_mute_uses_all_current_notification_fields_and_verifies_return(self):
        change = _FakeUserGuild(1)
        already = _FakeUserGuild(2)
        client = _FakeClient([change, already])
        client.cache[2].notification_settings = _FakeSettings(muted=True)
        manager = GuildManager(client)

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            result = await manager.mute_all_guilds([])

        self.assertEqual(result["status"], "COMPLETADO")
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["already_done"], 1)
        kwargs = client.cache[1].notification_settings.edit_calls[0]
        self.assertIs(kwargs["muted_until"], True)
        self.assertEqual(kwargs["level"], discord.NotificationLevel.nothing)
        self.assertIs(kwargs["suppress_everyone"], True)
        self.assertIs(kwargs["suppress_roles"], True)
        self.assertIs(kwargs["mobile_push"], False)
        self.assertIs(kwargs["mute_scheduled_events"], True)
        self.assertEqual(kwargs["notify_highlights"], discord.HighlightLevel.disabled)
        self.assertEqual(client.cache[2].notification_settings.edit_calls, [])

    async def test_unavailable_cached_guild_is_refetched_before_mute(self):
        guild = _FakeUserGuild(1)
        client = _FakeClient([guild])
        client.cache[1].unavailable = True
        resolved = _FakeCachedGuild(1, settings=_FakeSettings())
        client.fetched[1] = resolved
        manager = GuildManager(client)

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            result = await manager.mute_all_guilds([])

        self.assertEqual(result["status"], "COMPLETADO")
        self.assertEqual(client.fetch_guild_calls, [(1, False)])
        self.assertEqual(len(resolved.notification_settings.edit_calls), 1)

    async def test_unconfirmed_mute_is_partial(self):
        guild = _FakeUserGuild(1)
        client = _FakeClient([guild])
        client.cache[1].notification_settings = _FakeSettings(valid_return=False)
        manager = GuildManager(client)

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            result = await manager.mute_all_guilds([])

        self.assertEqual(result["status"], "PARCIAL")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["remaining"], 1)

    async def test_missing_but_still_listed_guild_is_not_reported_as_muted(self):
        guild = _FakeUserGuild(1)
        client = _FakeClient([guild])
        client.cache[1].unavailable = True
        manager = GuildManager(client)

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            result = await manager.mute_all_guilds([])

        self.assertEqual(result["status"], "PARCIAL")
        self.assertEqual(result["failed"], 1)

    async def test_guild_that_disappears_before_mute_is_idempotent(self):
        guild = _FakeUserGuild(1)
        client = _FakeClient([guild])
        client.cache[1].unavailable = True

        async def disappear(guild_id, *, with_counts=True):
            guild.joined = False
            raise _not_found()

        client.fetch_guild = disappear
        manager = GuildManager(client)

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            result = await manager.mute_all_guilds([])

        self.assertEqual(result["status"], "COMPLETADO")
        self.assertEqual(result["already_done"], 1)

    async def test_cancel_stops_before_next_guild(self):
        first = _FakeUserGuild(1)
        second = _FakeUserGuild(2)
        manager = GuildManager(_FakeClient([first, second]))
        first.on_leave = lambda: setattr(manager, "cancelled", True)

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            result = await manager.leave_all_guilds([])

        self.assertEqual(result["status"], "CANCELADO")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(first.leave_calls, 1)
        self.assertEqual(second.leave_calls, 0)

    async def test_two_accounts_run_independently(self):
        first = _FakeUserGuild(1)
        second = _FakeUserGuild(2)
        managers = [
            GuildManager(_FakeClient([first])),
            GuildManager(_FakeClient([second])),
        ]

        with (
            patch("worker.guild_manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.guild_manager.emit_line"),
        ):
            results = await asyncio.gather(
                managers[0].leave_all_guilds([]),
                managers[1].leave_all_guilds([]),
            )

        self.assertEqual([result["status"] for result in results], ["COMPLETADO"] * 2)
        self.assertEqual(first.leave_calls, 1)
        self.assertEqual(second.leave_calls, 1)

    async def test_second_bulk_on_same_account_is_rejected(self):
        guild = _FakeUserGuild(1)
        manager = GuildManager(_FakeClient([guild]))
        await manager._bulk_lock.acquire()
        try:
            with patch("worker.guild_manager.emit_line"):
                result = await manager.leave_all_guilds([])
        finally:
            manager._bulk_lock.release()

        self.assertEqual(result["status"], "ABORTADO")
        self.assertEqual(guild.leave_calls, 0)

    def test_progress_label_cannot_inject_protocol_fields(self):
        self.assertEqual(
            GuildManager._ipc_label("server:ruim\nquebra||REQ:"),
            "server：ruim quebraREQ：",
        )


if __name__ == "__main__":
    unittest.main()
