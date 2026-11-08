from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from worker.tracker.manager import TrackerManager
from worker.tracker.collection import TrackerCollection
from tracker_protocol import normalize_target_ids


def channel(channel_id=10, *, visible=True, history=True, guild=None):
    return NS(
        id=channel_id,
        name=f"canal-{channel_id}",
        guild=guild or NS(id=20, name="Servidor", me=object()),
        members=[],
        permissions_for=lambda _: NS(view_channel=visible, read_message_history=history),
    )


def message(message_id=100, *, author_id=42, room=None, content="mensagem", **extra):
    import discord

    room = room or channel()
    values = dict(
        id=message_id,
        author=NS(
            id=author_id, name=f"user-{author_id}", display_avatar=NS(url="https://cdn.discordapp.com/avatar.png")
        ),
        channel=room,
        guild=room.guild,
        content=content,
        attachments=[],
        embeds=[],
        mentions=[],
        reference=None,
        created_at=discord.utils.utcnow(),
        jump_url=f"https://discord.com/channels/20/{room.id}/{message_id}",
    )
    values.update(extra)
    return NS(**values)


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.room = channel()
        self.client = NS(get_channel=lambda _: self.room, guilds=[])
        self.manager = TrackerManager(self.client)
        self.manager.enabled = True
        self.manager.target_id = 42
        self.manager._webhook_queue = asyncio.Queue(maxsize=250)
        self.manager.config.HISTORY_GUILD_PAUSE = 0
        self.addAsyncCleanup(self.manager.shutdown)

    def events(self):
        queue = self.manager._webhook_queue
        values = []
        while not queue.empty():
            values.append(queue.get_nowait())
            queue.task_done()
        return values

    async def test_public_profile_connections_initial_snapshot_and_order_independence(self):
        first = NS(type="github", name="dev", visible=True, url="https://github.com/dev")
        second = NS(type="twitch", name="stream", visible=True, url=None)
        hidden = NS(type="test", name="hidden", visible=False)
        profile = NS(name="target", global_name="Target", bio="bio", connections=[first, hidden, second])
        self.client.fetch_user_profile = AsyncMock(return_value=profile)
        await self.manager._fetch_profile(emit_changes=False)
        self.assertEqual(len(self.manager.state.connected_accounts), 2)
        snapshot = self.events()[0][1].to_dict()
        self.assertIn("Perfil Público Inicial", snapshot["title"])
        self.assertIn("github.com/dev", str(snapshot))
        self.assertNotIn("hidden", str(snapshot))
        profile.connections = [second, first]
        await self.manager._fetch_profile(emit_changes=True)
        self.assertEqual(self.events(), [])
        profile.connections = [second]
        await self.manager._fetch_profile(emit_changes=True)
        self.assertIn("removidas", str(self.events()[0][1].to_dict()))
        self.client.fetch_user_profile.assert_awaited_with(42, with_mutual_guilds=False, with_mutual_friends=False)

    async def test_profile_tracks_badges_pronouns_and_nitro_changes(self):
        profile = NS(
            name="target",
            global_name="Target",
            bio="bio",
            connections=[],
            badges=[NS(id="hypesquad", description="HypeSquad Bravery")],
            metadata=NS(pronouns="ele/dele"),
            premium_type=NS(name="none"),
            premium_since=None,
            premium_guild_since=None,
        )
        self.client.fetch_user_profile = AsyncMock(return_value=profile)
        await self.manager._fetch_profile(emit_changes=False)
        initial = str(self.events()[0][1].to_dict())
        self.assertIn("HypeSquad Bravery", initial)
        self.assertIn("ele/dele", initial)

        # Sem mudanca: nada e enviado.
        await self.manager._fetch_profile(emit_changes=True)
        self.assertEqual(self.events(), [])

        profile.badges.append(NS(id="nitro", description="Nitro"))
        profile.metadata = NS(pronouns="ela/dela")
        profile.premium_type = NS(name="nitro")
        await self.manager._fetch_profile(emit_changes=True)
        changed = str(self.events()[0][1].to_dict())
        self.assertIn("Insígnias adicionadas", changed)
        self.assertIn("ela/dela", changed)
        self.assertIn("Assinatura Nitro", changed)

    async def test_message_embed_uses_native_time_channel_mention_and_category_color(self):
        await self.manager.on_message(message(100, content="ola"))
        await asyncio.sleep(0)
        await self.manager._on_buffer_flush(10, [self.manager._msg_cache[100]], channel=self.room)
        embed = self.events()[-1][1]
        data = embed.to_dict()
        self.assertIn("<t:", data["description"])
        self.assertIn(":R>", data["description"])
        self.assertIn("<#10>", data["description"])
        self.assertIn("ola", data["description"])
        self.assertEqual(data["color"], 0x5865F2)
        self.assertIn("Alvo 42", data["footer"]["text"])

    async def test_short_profile_fields_are_inline_and_long_ones_are_not(self):
        await self.manager._dispatch_log(
            title="Teste",
            changes=[("Pronomes", "ele/dele"), ("Bio", "x" * 200)],
            event_type="profile",
        )
        fields = self.events()[-1][1].to_dict()["fields"]
        self.assertTrue(fields[0]["inline"])
        self.assertFalse(fields[1]["inline"])

    async def test_failed_profile_does_not_create_a_false_baseline(self):
        self.client.fetch_user_profile = AsyncMock(
            side_effect=[RuntimeError(), NS(name="target", bio="bio", connections=[])]
        )
        await self.manager._fetch_profile(emit_changes=False)
        self.assertFalse(self.manager.state.profile_initialized)
        await self.manager._fetch_profile(emit_changes=True)
        self.assertEqual(self.events()[0][1].title, "Perfil Público Inicial")

    async def test_private_and_inaccessible_channels_are_excluded(self):
        for room in (NS(id=10, guild=None), channel(visible=False)):
            self.client.get_channel = lambda _, value=room: value
            await self.manager.on_message(message(room=room))
            await self.manager.on_raw_reaction_add(NS(channel_id=10, user_id=42, message_id=100))
            await self.manager.on_raw_message_edit(NS(channel_id=10))
            await self.manager.on_typing(room, NS(id=42), None)
        self.assertEqual(self.manager._msg_cache, {})
        self.assertEqual(self.events(), [])
        self.assertFalse(self.manager._call_involves_target(NS(initiator=NS(id=42))))

    async def test_burst_preserves_every_message_long_text_and_media_links(self):
        media = NS(url="https://cdn.discordapp.com/video.mp4", filename="video.mp4", content_type="video/mp4", size=12)
        for index in range(20):
            await self.manager.on_message(
                message(100 + index, content=f"marker-{index} " + "x" * 1900, attachments=[media])
            )
        await asyncio.sleep(0)
        events = self.events()
        self.assertEqual(len(events), 20)
        for index, (_, embed, _) in enumerate(events):
            self.assertIn(f"marker-{index} ", embed.description)
            self.assertIn("video.mp4](https://cdn.discordapp.com/video.mp4)", embed.description)
            self.assertIn(f"/100" if index == 0 else f"/{100 + index}", embed.description)
            self.assertLessEqual(len(embed), 6000)
        long = message(999, content="a" * 3999 + "TAIL")
        await self.manager.on_message(long)
        await self.manager.msg_buffer.flush(10)
        self.assertIn("TAIL", "".join(event[1].description for event in self.events()))

    async def test_mentions_replies_and_reactions_to_target_messages(self):
        await self.manager.on_message(message())
        await self.manager.msg_buffer.flush(10)
        self.events()
        await self.manager.on_message(message(101, author_id=77, reference=NS(message_id=100, resolved=None)))
        await self.manager.on_message(message(102, author_id=78, mentions=[NS(id=42)]))
        await self.manager.on_raw_reaction_add(NS(channel_id=10, message_id=100, user_id=79, emoji="ok"))
        events = self.events()
        self.assertEqual([event[2] for event in events], ["interaction", "interaction", "reaction"])
        self.assertEqual(self.manager._stats["interactions_seen"], 3)

    async def test_attachment_replacement_edit_deduplicates_and_bulk_delete_uses_new_content(self):
        await self.manager.on_message(message())
        await self.manager.msg_buffer.flush(10)
        self.events()
        payload = NS(
            message_id=100,
            channel_id=10,
            cached_message=None,
            data={"content": "editado", "attachments": [{"filename": "a.png", "url": "https://example.com/a.png"}]},
        )
        await self.manager.on_raw_message_edit(payload)
        self.assertEqual(len(self.events()), 1)
        await self.manager.on_raw_message_edit(payload)
        self.assertEqual(self.events(), [])
        payload.data = {"attachments": [{"filename": "b.png", "url": "https://example.com/b.png"}]}
        await self.manager.on_raw_message_edit(payload)
        self.assertIn("b.png", str(self.events()[0][1].to_dict()))
        await self.manager.on_message(message(101, author_id=77))
        await self.manager.on_raw_bulk_message_delete(
            NS(channel_id=10, message_ids={100, 101, 102}, cached_messages=[message()])
        )
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertIn("editado", events[0][1].description)
        self.assertIn("b.png", events[0][1].description)
        self.assertNotIn(100, self.manager._msg_cache)

    async def test_voice_move_counts_observed_duration_and_flags_do_not_reset_session(self):
        other = channel(11, guild=self.room.guild)
        member = NS(id=42, name="target", guild=self.room.guild)
        self.manager._dispatch_voice_embed = AsyncMock()
        with patch("worker.tracker.events.time.monotonic", return_value=10):
            await self.manager.on_voice_state_update(member, NS(channel=None), NS(channel=self.room))
        with patch("worker.tracker.events.time.monotonic", return_value=15):
            await self.manager.on_voice_state_update(
                member, NS(channel=self.room, self_mute=False), NS(channel=self.room, self_mute=True)
            )
        self.assertEqual(self.manager.state.voice_sessions[20]["started"], 10)
        with patch("worker.tracker.events.time.monotonic", return_value=70):
            await self.manager.on_voice_state_update(member, NS(channel=self.room), NS(channel=other))
        call = self.manager._dispatch_voice_embed.await_args.kwargs
        self.assertEqual(call["duration"], 60)
        self.assertIs(call["previous_channel"], self.room)
        self.assertEqual(self.manager.tracked_voice_channels, {11})
        self.assertEqual(self.manager._stats["voice_seconds"], 60)

    async def test_large_profile_embeds_respect_discord_total_character_limit(self):
        await self.manager._dispatch_log(
            title="t" * 256, description="d" * 4096, changes=[("n" * 256, "v" * 1024)] * 20
        )
        events = self.events()
        self.assertGreater(len(events), 1)
        self.assertTrue(all(len(event[1]) <= 6000 for event in events))
        self.assertEqual(sum(len(event[1].fields) for event in events), 20)

    async def test_history_checks_author_permissions_and_deduplicates_live_messages(self):
        import discord

        self.manager._started_at = discord.utils.utcnow()
        await self.manager.on_message(message(100))
        calls = []

        async def search(**kwargs):
            calls.append(kwargs)
            for item in (
                message(100),
                message(101),
                message(102, author_id=77),
                message(103, room=channel(history=False)),
            ):
                yield item

        self.client.guilds = [NS(id=20, search=search)]
        await self.manager._collect_history(self.manager._run_id)
        self.assertEqual(self.manager._stats["history_messages"], 1)
        self.assertEqual(self.manager._history_status, "complete")
        self.assertEqual(calls[0]["authors"][0].id, 42)
        self.assertEqual((calls[0]["before"] - calls[0]["after"]).days, 7)
        self.assertEqual(calls[0]["limit"], 100)

    async def test_history_failure_continues_to_next_server(self):
        import discord

        self.manager._started_at = discord.utils.utcnow()

        async def broken(**kwargs):
            raise RuntimeError("search unavailable")
            yield

        async def working(**kwargs):
            yield message()

        self.client.guilds = [NS(id=19, search=broken), NS(id=20, search=working)]
        await self.manager._collect_history(self.manager._run_id)
        self.assertEqual(self.manager._history_status, "partial")
        self.assertEqual(self.manager._stats["history_messages"], 1)
        self.assertEqual(self.manager._stats["history_errors"], 1)

    async def test_member_query_failure_keeps_known_guild_for_retry(self):
        self.client.is_ws_ratelimited = lambda: False
        guild = NS(
            id=20,
            is_subscribed_to=lambda **_: True,
            get_member=lambda _: None,
            query_members=AsyncMock(side_effect=TimeoutError()),
        )
        self.manager.tracked_guild_ids.add(20)
        await self.manager._ensure_guild_subscription(guild, self.manager._run_id)
        self.assertIn(20, self.manager.tracked_guild_ids)
        self.assertEqual(self.manager._stats["subscription_errors"], 1)

    async def test_disconnect_ends_observed_voice_and_does_not_double_count(self):
        self.manager.state.voice_sessions[20] = {"channel_id": 10, "started": 10, "partial": False}
        self.manager.tracked_voice_channels.add(10)
        with patch("worker.tracker.manager.time.monotonic", return_value=70):
            await self.manager.on_disconnect()
        with patch("worker.tracker.manager.time.monotonic", return_value=1000):
            await self.manager.on_disconnect()
        self.assertEqual(self.manager._stats["voice_seconds"], 60)
        self.assertFalse(self.manager.state.voice_sessions)
        self.assertFalse(self.manager.tracked_voice_channels)

    async def test_stop_cancels_history_waiting_on_shared_lock(self):
        self.client.guilds = [NS(id=20)]
        await self.manager._history_lock.acquire()
        self.manager._history_task = asyncio.create_task(self.manager._collect_history(self.manager._run_id))
        task = self.manager._history_task
        await asyncio.sleep(0)
        await self.manager.shutdown()
        self.assertTrue(task.cancelled())
        self.manager._history_lock.release()

    async def test_initial_voice_in_any_mutual_guild_is_detected_once(self):
        guild = self.room.guild
        member = NS(id=42, voice=NS(channel=self.room), guild=guild)
        guild.is_subscribed_to = lambda **_: True
        guild.get_member = lambda _: member
        self.client.is_ws_ratelimited = lambda: False
        self.manager._dispatch_voice_embed = AsyncMock()
        await self.manager._ensure_guild_subscription(guild, self.manager._run_id)
        await self.manager._ensure_guild_subscription(guild, self.manager._run_id)
        self.assertEqual(self.manager._dispatch_voice_embed.await_count, 1)
        self.assertTrue(self.manager.state.voice_sessions[20]["partial"])


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40

    async def test_editing_target_set_reuses_retained_trackers_and_rejects_invalid_input_atomically(self):
        collection = TrackerCollection(NS())
        self.addAsyncCleanup(collection.shutdown)

        async def start(tracker, target_id, webhook_url, **kwargs):
            tracker.target_id, tracker.webhook_url, tracker.enabled = target_id, webhook_url, True
            return {"ok": True}

        with patch.object(TrackerManager, "start_tracking", start):
            result = await collection.start_tracking([42, 77], self.WEBHOOK)
            self.assertEqual(result["target_ids"], [42, 77])
            retained, removed = collection.trackers[42], collection.trackers[77]
            retained._stats["messages_seen"] = 9
            invalid = await collection.start_tracking([True], self.WEBHOOK)
            self.assertFalse(invalid["ok"])
            self.assertEqual(list(collection.trackers), [42, 77])
            await collection.start_tracking([42, 88], self.WEBHOOK)
            self.assertIs(collection.trackers[42], retained)
            self.assertFalse(removed.enabled)
            self.assertEqual(collection.get_status()["messages_seen"], 9)
            self.assertIs(collection.trackers[42]._history_lock, collection.trackers[88]._history_lock)

    def test_id_validation_and_deduplication(self):
        self.assertEqual(normalize_target_ids("42,77\n42;88"), [42, 77, 88])
        for value in (True, 1.5, [], [True], [0], [2**64], "42 nope", list(range(1, 12))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_target_ids(value)

    async def test_start_tracking_does_not_hold_collection_lock_during_setup(self):
        collection = TrackerCollection(NS())
        self.addAsyncCleanup(collection.shutdown)
        started = asyncio.Event()
        release = asyncio.Event()

        async def start(tracker, target_id, webhook_url, **kwargs):
            tracker.target_id = target_id
            tracker.webhook_url = webhook_url
            tracker.enabled = True
            started.set()
            await release.wait()
            return {"ok": True}

        with patch.object(TrackerManager, "start_tracking", start):
            task = asyncio.create_task(collection.start_tracking([42], self.WEBHOOK))
            await asyncio.wait_for(started.wait(), timeout=1)
            try:
                await asyncio.wait_for(collection._lock.acquire(), timeout=0.2)
                collection._lock.release()
                lock_acquired = True
            except TimeoutError:
                lock_acquired = False
            release.set()
            result = await task

        self.assertTrue(lock_acquired)
        self.assertTrue(result["ok"])
        self.assertEqual(result["target_ids"], [42])

    async def test_stop_during_start_discards_uncommitted_trackers(self):
        collection = TrackerCollection(NS())
        self.addAsyncCleanup(collection.shutdown)
        started = asyncio.Event()
        release = asyncio.Event()

        async def start(tracker, target_id, webhook_url, **kwargs):
            started.set()
            await release.wait()
            tracker.target_id = target_id
            tracker.webhook_url = webhook_url
            tracker.enabled = True
            return {"ok": True}

        with patch.object(TrackerManager, "start_tracking", start):
            task = asyncio.create_task(collection.start_tracking([42], self.WEBHOOK))
            await asyncio.wait_for(started.wait(), timeout=1)
            stopped = await collection.stop_tracking()
            release.set()
            started_result = await task

        self.assertTrue(stopped["ok"])
        self.assertFalse(started_result.get("enabled"))
        self.assertEqual(list(collection.trackers), [])

    async def test_failure_in_one_target_event_does_not_interrupt_other_targets(self):
        collection = TrackerCollection(NS())
        first, second = TrackerManager(NS()), TrackerManager(NS())
        first.on_message = AsyncMock(side_effect=RuntimeError())
        second.on_message = AsyncMock()
        collection.trackers = {42: first, 77: second}
        await collection.on_message("event")
        second.on_message.assert_awaited_once_with("event")
        self.assertEqual(first._stats["event_errors"], 1)
        await collection.shutdown()


if __name__ == "__main__":
    unittest.main()
