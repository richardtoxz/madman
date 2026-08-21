from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from worker.voice_manager import VoiceManager


def _state(channel=None, *, self_mute=False, self_deaf=False):
    return SimpleNamespace(
        channel=channel,
        self_mute=self_mute,
        self_deaf=self_deaf,
    )


class _FakePresence:
    def __init__(self):
        self.calls = []

    async def set_task(self, name, detail=None):
        self.calls.append(("task", name, detail))

    async def set_main(self):
        self.calls.append(("main",))


class _FakeGuild:
    def __init__(self, guild_id: int, name: str):
        self.id = guild_id
        self.name = name
        self.unavailable = False
        self.voice_channels = []
        self.stage_channels = []
        self.me = SimpleNamespace(id=999)
        self.client = None
        self.manager = None
        self.change_calls = []
        self.change_error = None
        self.confirm_changes = True

    def get_member(self, user_id):
        for channel in (*self.voice_channels, *self.stage_channels):
            state = channel.voice_states.get(int(user_id))
            if state is not None:
                return SimpleNamespace(id=int(user_id), voice=state)
        return None

    async def change_voice_state(self, **kwargs):
        self.change_calls.append(kwargs)
        if self.change_error:
            raise self.change_error
        if not self.confirm_changes:
            return

        self_id = int(self.client.user.id)
        before_channel = None
        before_state = None
        for guild in self.client.guilds:
            for channel in (*guild.voice_channels, *guild.stage_channels):
                existing = channel.voice_states.pop(self_id, None)
                if existing is not None:
                    before_channel = channel
                    before_state = existing

        channel = kwargs.get("channel")
        if channel is None:
            after_state = _state(None)
        else:
            after_state = _state(
                channel,
                self_mute=bool(kwargs.get("self_mute")),
                self_deaf=bool(kwargs.get("self_deaf")),
            )
            channel.voice_states[self_id] = after_state

        await self.manager.on_voice_state_update(
            self.client.user,
            before_state or _state(before_channel),
            after_state,
        )


def _voice_channel(
    guild: _FakeGuild,
    channel_id: int,
    *,
    name: str | None = None,
    connect: bool = True,
    view_channel: bool = True,
    move_members: bool = False,
    user_limit: int = 0,
    stage: bool = False,
):
    channel_type = discord.StageChannel if stage else discord.VoiceChannel
    channel = MagicMock(spec=channel_type)
    channel.id = channel_id
    channel.name = name or f"voice{channel_id}"
    channel.guild = guild
    channel.user_limit = user_limit
    channel.voice_states = {}
    channel.permissions_for.return_value = SimpleNamespace(
        connect=connect,
        view_channel=view_channel,
        move_members=move_members,
    )
    (guild.stage_channels if stage else guild.voice_channels).append(channel)
    return channel


class _FakeClient:
    def __init__(self, guilds):
        self.user = SimpleNamespace(id=999, name="self")
        self.guilds = list(guilds)
        self.presence_manager = _FakePresence()
        self.fetch_results = {}
        self.fetch_error = None
        self.fetch_calls = []
        for guild in self.guilds:
            guild.client = self

    def get_channel(self, channel_id):
        for guild in self.guilds:
            for channel in (*guild.voice_channels, *guild.stage_channels):
                if int(channel.id) == int(channel_id):
                    return channel
        return None

    def get_guild(self, guild_id):
        return next((guild for guild in self.guilds if guild.id == guild_id), None)

    async def fetch_channel(self, channel_id):
        self.fetch_calls.append(int(channel_id))
        if self.fetch_error:
            raise self.fetch_error
        return self.fetch_results[int(channel_id)]


def _manager(*guilds):
    client = _FakeClient(guilds)
    manager = VoiceManager(client)
    manager.STATE_TIMEOUT = 0.05
    for guild in guilds:
        guild.manager = manager
    return manager, client


async def _finish_reconciles(manager: VoiceManager):
    while manager._background_tasks:
        await asyncio.gather(*tuple(manager._background_tasks))


class VoiceManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_join_confirms_gateway_state_with_mute_and_deaf(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10, name="AFK")
        manager, _ = _manager(guild)

        with patch("worker.voice_manager.emit_line"):
            result = await manager.join_voice(channel.id)
            status = await manager.get_status()

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "JOINED")
        self.assertEqual(manager.desired_channel_id, channel.id)
        self.assertEqual(guild.change_calls[0]["channel"], channel)
        self.assertIs(guild.change_calls[0]["self_mute"], True)
        self.assertIs(guild.change_calls[0]["self_deaf"], True)
        self.assertEqual(status["mode"], "AFK")
        self.assertTrue(status["self_mute"])
        self.assertTrue(status["self_deaf"])

    async def test_uncached_channel_is_fetched_before_join(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10)
        manager, client = _manager(guild)
        original_get = client.get_channel
        client.get_channel = lambda channel_id: None
        client.fetch_results[channel.id] = channel

        with patch("worker.voice_manager.emit_line"):
            result = await manager.join_voice(channel.id)

        self.assertTrue(result["ok"])
        self.assertEqual(client.fetch_calls, [channel.id])
        client.get_channel = original_get

    async def test_non_voice_channel_is_rejected_without_gateway_change(self):
        guild = _FakeGuild(1, "Guild")
        manager, client = _manager(guild)
        client.fetch_results[10] = SimpleNamespace(id=10, name="texto", guild=guild)

        result = await manager.join_voice(10)

        self.assertEqual(result["code"], "NOT_VOICE_CHANNEL")
        self.assertEqual(guild.change_calls, [])

    async def test_permission_and_capacity_are_checked_locally(self):
        guild = _FakeGuild(1, "Guild")
        forbidden = _voice_channel(guild, 10, connect=False)
        full = _voice_channel(guild, 20, user_limit=1)
        full.voice_states[123] = _state(full)
        manager, _ = _manager(guild)

        forbidden_result = await manager.join_voice(forbidden.id)
        full_result = await manager.join_voice(full.id)

        self.assertEqual(forbidden_result["code"], "CONNECT_FORBIDDEN")
        self.assertEqual(full_result["code"], "CHANNEL_FULL")
        self.assertEqual(guild.change_calls, [])

    async def test_stage_channel_is_supported(self):
        guild = _FakeGuild(1, "Guild")
        stage = _voice_channel(guild, 10, stage=True)
        manager, _ = _manager(guild)

        with patch("worker.voice_manager.emit_line"):
            result = await manager.join_voice(stage.id)

        self.assertTrue(result["ok"])
        self.assertEqual(result["channel_id"], stage.id)

    async def test_join_is_not_reported_success_without_confirmation(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10)
        guild.confirm_changes = False
        manager, _ = _manager(guild)

        result = await manager.join_voice(channel.id)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "JOIN_UNCONFIRMED")
        self.assertIsNone(manager.desired_channel_id)

    async def test_unavailable_guild_is_rejected_without_gateway_change(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10)
        guild.unavailable = True
        manager, _ = _manager(guild)

        result = await manager.join_voice(channel.id)

        self.assertEqual(result["code"], "GUILD_UNAVAILABLE")
        self.assertEqual(guild.change_calls, [])

    async def test_leave_is_idempotent_and_clears_confirmed_state(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10)
        manager, _ = _manager(guild)

        with patch("worker.voice_manager.emit_line"):
            first = await manager.leave_voice()
            await manager.join_voice(channel.id)
            second = await manager.leave_voice()
            status = await manager.get_status()

        self.assertEqual(first["code"], "ALREADY_LEFT")
        self.assertEqual(second["code"], "LEFT")
        self.assertFalse(status["in_voice"])
        self.assertIsNone(manager.desired_channel_id)

    async def test_switching_guilds_leaves_previous_voice_first(self):
        first_guild = _FakeGuild(1, "First")
        second_guild = _FakeGuild(2, "Second")
        first = _voice_channel(first_guild, 10)
        second = _voice_channel(second_guild, 20)
        manager, _ = _manager(first_guild, second_guild)

        with patch("worker.voice_manager.emit_line"):
            await manager.join_voice(first.id)
            result = await manager.join_voice(second.id)

        self.assertTrue(result["ok"])
        self.assertIsNone(first_guild.change_calls[-1]["channel"])
        self.assertEqual(second_guild.change_calls[-1]["channel"], second)
        self.assertNotIn(999, first.voice_states)
        self.assertIn(999, second.voice_states)

    async def test_follow_waits_without_member_cache_then_tracks_voice_states(self):
        guild = _FakeGuild(1, "Guild")
        first = _voice_channel(guild, 10, name="First")
        second = _voice_channel(guild, 20, name="Second")
        manager, _ = _manager(guild)

        with patch("worker.voice_manager.emit_line"):
            waiting = await manager.start_follow(123)
            target_state = _state(first)
            first.voice_states[123] = target_state
            await manager.on_voice_state_update(
                SimpleNamespace(id=123), _state(None), target_state
            )
            await _finish_reconciles(manager)

            first.voice_states.pop(123)
            moved_state = _state(second)
            second.voice_states[123] = moved_state
            await manager.on_voice_state_update(
                SimpleNamespace(id=123), target_state, moved_state
            )
            await _finish_reconciles(manager)

        self.assertEqual(waiting["code"], "FOLLOW_WAITING")
        self.assertEqual(manager.current_channel_id, second.id)
        self.assertTrue(manager.is_following)

    async def test_rapid_target_moves_reconcile_only_the_latest_channel(self):
        guild = _FakeGuild(1, "Guild")
        first = _voice_channel(guild, 10, name="First")
        second = _voice_channel(guild, 20, name="Second")
        manager, _ = _manager(guild)

        with patch("worker.voice_manager.emit_line"):
            await manager.start_follow(123)
            first_state = _state(first)
            first.voice_states[123] = first_state
            await manager.on_voice_state_update(
                SimpleNamespace(id=123), _state(None), first_state
            )

            first.voice_states.pop(123)
            second_state = _state(second)
            second.voice_states[123] = second_state
            await manager.on_voice_state_update(
                SimpleNamespace(id=123), first_state, second_state
            )
            await _finish_reconciles(manager)

        self.assertEqual(manager.current_channel_id, second.id)
        joins = [
            call["channel"].id
            for call in guild.change_calls
            if call.get("channel") is not None
        ]
        self.assertEqual(joins, [second.id])

    async def test_follow_leaves_when_target_leaves_but_stays_active(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10)
        channel.voice_states[123] = _state(channel)
        manager, _ = _manager(guild)

        with patch("worker.voice_manager.emit_line"):
            await manager.start_follow(123)
            before = channel.voice_states.pop(123)
            await manager.on_voice_state_update(
                SimpleNamespace(id=123), before, _state(None)
            )
            await _finish_reconciles(manager)
            status = await manager.get_status()

        self.assertTrue(status["following"])
        self.assertFalse(status["in_voice"])
        self.assertEqual(status["mode"], "FOLLOW")

    async def test_stop_follow_disables_it_and_disconnects(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10)
        channel.voice_states[123] = _state(channel)
        manager, _ = _manager(guild)

        with patch("worker.voice_manager.emit_line"):
            await manager.start_follow(123)
            result = await manager.stop_follow()

        self.assertEqual(result["code"], "UNFOLLOWED")
        self.assertFalse(manager.is_following)
        self.assertFalse(manager.is_in_voice)

    async def test_self_follow_is_rejected(self):
        guild = _FakeGuild(1, "Guild")
        manager, _ = _manager(guild)

        result = await manager.start_follow(999)

        self.assertEqual(result["code"], "SELF_TARGET")

    async def test_afk_channel_is_restored_after_gateway_reconnect(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10)
        manager, client = _manager(guild)

        with patch("worker.voice_manager.emit_line"):
            await manager.join_voice(channel.id)
            before = channel.voice_states.pop(client.user.id)
            await manager.on_voice_state_update(client.user, before, _state(None))
            self.assertFalse(manager.is_in_voice)
            await manager.on_reconnect()

        self.assertTrue(manager.is_in_voice)
        self.assertEqual(manager.current_channel_id, channel.id)
        self.assertEqual(manager.desired_channel_id, channel.id)

    async def test_busy_operation_is_rejected_only_for_same_account(self):
        first_guild = _FakeGuild(1, "First")
        second_guild = _FakeGuild(2, "Second")
        first_channel = _voice_channel(first_guild, 10)
        second_channel = _voice_channel(second_guild, 20)
        first, _ = _manager(first_guild)
        second, _ = _manager(second_guild)
        await first._operation_lock.acquire()
        try:
            with patch("worker.voice_manager.emit_line"):
                busy, independent = await asyncio.gather(
                    first.join_voice(first_channel.id),
                    second.join_voice(second_channel.id),
                )
        finally:
            first._operation_lock.release()

        self.assertEqual(busy["code"], "BUSY")
        self.assertTrue(independent["ok"])

    async def test_event_labels_cannot_inject_ipc_lines(self):
        guild = _FakeGuild(1, "Guild")
        channel = _voice_channel(guild, 10, name="ruim:\n||REQ:abc")
        manager, client = _manager(guild)

        with patch("worker.voice_manager.emit_line") as emit:
            await manager.on_voice_state_update(
                client.user, _state(None), _state(channel)
            )

        line = emit.call_args.args[0]
        self.assertNotIn("\n", line)
        self.assertNotIn("||REQ:", line)


if __name__ == "__main__":
    unittest.main()
