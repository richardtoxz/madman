from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o discord.py-self isolado
import discord

from worker.protection_manager import ProtectionManager


class _Author:
    def __init__(
        self,
        user_id: int,
        *,
        friend: bool = False,
        blocked: bool = False,
        bot: bool = False,
        system: bool = False,
        name: str | None = None,
        block_error: Exception | None = None,
    ):
        self.id = user_id
        self.friend = friend
        self.blocked = blocked
        self.bot = bot
        self.system = system
        self.name = name or f"user{user_id}"
        self.block_error = block_error
        self.block_calls = 0
        self.relationship = SimpleNamespace(
            type=(
                discord.RelationshipType.friend
                if friend
                else (
                    discord.RelationshipType.blocked
                    if blocked
                    else discord.RelationshipType.none
                )
            )
        )

    def __str__(self):
        return self.name

    def is_friend(self):
        return self.friend

    def is_blocked(self):
        return self.blocked

    async def block(self):
        self.block_calls += 1
        if self.block_error:
            raise self.block_error
        self.blocked = True
        self.relationship.type = discord.RelationshipType.blocked


class _Channel:
    def __init__(self, *, channel_type=discord.ChannelType.private, close_error=None):
        self.type = channel_type
        self.close_error = close_error
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error


class _Embed:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


class _Message:
    def __init__(
        self,
        message_id: int,
        author: _Author,
        *,
        channel: _Channel | None = None,
        content: str = "",
        components=(),
        embeds=(),
        attachments=(),
        message_snapshots=(),
    ):
        self.id = message_id
        self.author = author
        self.channel = channel or _Channel()
        self.content = content
        self.components = list(components)
        self.embeds = list(embeds)
        self.attachments = list(attachments)
        self.message_snapshots = list(message_snapshots)


class _Relationship:
    def __init__(self, user_id: int, *, error: Exception | None = None):
        self.type = discord.RelationshipType.incoming_request
        self.user = _Author(user_id)
        self.error = error
        self.delete_calls = 0

    async def delete(self):
        self.delete_calls += 1
        if self.error:
            raise self.error


class ProtectionManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = ProtectionManager(SimpleNamespace(user=SimpleNamespace(id=999)))
        self.manager.enable()

    def test_suspicious_tld_alone_is_not_a_scam(self):
        self.assertIsNone(
            self.manager.classify_threat("Artigo: https://example.xyz/noticias")
        )

    def test_lure_and_external_risky_domain_is_a_scam(self):
        self.assertEqual(
            self.manager.classify_threat(
                "Claim free nitro agora: https://example.xyz/presente"
            ),
            "SCAM",
        )

    def test_safe_domain_and_event_url_avoid_false_positives(self):
        self.assertIsNone(
            self.manager.classify_threat("free nitro https://github.com/example")
        )
        self.assertIsNone(
            self.manager.classify_threat("https://discord.com/events/123/456")
        )

    async def test_single_invite_does_not_close_dm_but_repeated_invites_do(self):
        author = _Author(1)
        channel = _Channel()
        for message_id in range(1, 4):
            await self.manager.on_message(
                _Message(
                    message_id,
                    author,
                    channel=channel,
                    content="https://discord.gg/example",
                )
            )

        self.assertEqual(channel.close_calls, 1)
        self.assertEqual(author.block_calls, 0)
        self.assertEqual(self.manager.stats["spam_closed"], 1)

    async def test_nested_button_and_embed_urls_are_scanned(self):
        button_author = _Author(2)
        button_channel = _Channel()
        nested = {"children": [{"url": "https://d1scord-gift.example/claim"}]}
        await self.manager.on_message(
            _Message(10, button_author, channel=button_channel, components=[nested])
        )

        embed_author = _Author(3)
        embed_channel = _Channel()
        embed = _Embed(
            {
                "title": "Claim free nitro",
                "fields": [{"name": "Link", "value": "https://gift.example.xyz/x"}],
            }
        )
        await self.manager.on_message(
            _Message(11, embed_author, channel=embed_channel, embeds=[embed])
        )

        self.assertEqual(button_author.block_calls, 1)
        self.assertEqual(button_channel.close_calls, 1)
        self.assertEqual(embed_author.block_calls, 1)
        self.assertEqual(embed_channel.close_calls, 1)

    async def test_dangerous_attachment_is_detected_without_downloading_it(self):
        author = _Author(4)
        channel = _Channel()
        attachment = SimpleNamespace(
            filename="SPOILER_foto.png.exe",
            url="https://cdn.discordapp.com/attachments/1/file.exe",
            description=None,
            title=None,
        )
        await self.manager.on_message(
            _Message(20, author, channel=channel, attachments=[attachment])
        )

        self.assertEqual(author.block_calls, 1)
        self.assertEqual(channel.close_calls, 1)

    async def test_per_sender_burst_only_closes_the_offending_dm(self):
        self.manager.config.DM_BURST_MAX_MESSAGES = 3
        offender = _Author(5)
        offender_channel = _Channel()
        innocent = _Author(6)
        innocent_channel = _Channel()

        await self.manager.on_message(_Message(30, innocent, channel=innocent_channel))
        for message_id in range(31, 34):
            await self.manager.on_message(
                _Message(message_id, offender, channel=offender_channel)
            )

        self.assertEqual(offender_channel.close_calls, 1)
        self.assertEqual(innocent_channel.close_calls, 0)

    async def test_global_dm_raid_acts_only_on_current_new_sender(self):
        self.manager.config.DM_RAID_MAX_UNIQUE = 3
        channels = []
        for user_id in range(10, 13):
            channel = _Channel()
            channels.append(channel)
            await self.manager.on_message(
                _Message(100 + user_id, _Author(user_id), channel=channel)
            )

        self.assertEqual([channel.close_calls for channel in channels], [0, 0, 1])

    async def test_friend_and_whitelist_never_receive_automatic_action(self):
        friend = _Author(20, friend=True)
        friend_channel = _Channel()
        allowed = _Author(21)
        allowed_channel = _Channel()
        self.manager.configure([21])

        with patch("worker.protection_manager.emit_line") as emit:
            await self.manager.on_message(
                _Message(
                    200,
                    friend,
                    channel=friend_channel,
                    content="claim free nitro https://bad.example.xyz/x",
                )
            )
            await self.manager.on_message(
                _Message(
                    201,
                    allowed,
                    channel=allowed_channel,
                    content="claim free nitro https://bad.example.xyz/x",
                )
            )

        self.assertEqual(friend.block_calls, 0)
        self.assertEqual(friend_channel.close_calls, 0)
        self.assertEqual(allowed.block_calls, 0)
        self.assertEqual(allowed_channel.close_calls, 0)
        self.assertEqual(self.manager.stats["trusted_ignored"], 2)
        self.assertEqual(emit.call_count, 2)

    async def test_group_messages_and_own_messages_are_ignored(self):
        group_author = _Author(30)
        group_channel = _Channel(channel_type=discord.ChannelType.group)
        own_author = _Author(999)
        own_channel = _Channel()

        await self.manager.on_message(
            _Message(
                300,
                group_author,
                channel=group_channel,
                content="claim free nitro https://bad.example.xyz/x",
            )
        )
        await self.manager.on_message(
            _Message(
                301,
                own_author,
                channel=own_channel,
                content="claim free nitro https://bad.example.xyz/x",
            )
        )

        self.assertEqual(group_channel.close_calls, 0)
        self.assertEqual(own_channel.close_calls, 0)

    async def test_message_edit_is_scanned_without_counting_a_second_message(self):
        self.manager.config.DM_BURST_MAX_MESSAGES = 2
        author = _Author(40)
        channel = _Channel()
        before = _Message(400, author, channel=channel, content="ola")
        after = _Message(
            400,
            author,
            channel=channel,
            content="claim free nitro https://bad.example.xyz/x",
        )

        await self.manager.on_message(before)
        await self.manager.on_message_edit(before, after)

        self.assertEqual(author.block_calls, 1)
        self.assertEqual(channel.close_calls, 1)
        self.assertEqual(self.manager.stats["dm_floods"], 0)

    async def test_friend_request_flood_rejects_threshold_and_respects_whitelist(self):
        self.manager.config.FR_FLOOD_MAX_COUNT = 3
        first = _Relationship(50)
        second = _Relationship(51)
        threshold = _Relationship(52)
        protected = _Relationship(53)
        self.manager.configure([53])

        for relationship in (first, second, threshold, protected):
            await self.manager.on_relationship_add(relationship)

        self.assertEqual(first.delete_calls, 0)
        self.assertEqual(second.delete_calls, 0)
        self.assertEqual(threshold.delete_calls, 1)
        self.assertEqual(protected.delete_calls, 0)
        self.assertEqual(self.manager.stats["fr_floods"], 1)
        self.assertEqual(self.manager.stats["fr_rejected"], 1)

    async def test_api_errors_are_contained_and_reported(self):
        author = _Author(60, block_error=RuntimeError("secret response"))
        channel = _Channel(close_error=RuntimeError("private response"))

        with patch("worker.protection_manager.emit_line") as emit:
            await self.manager.on_message(
                _Message(
                    600,
                    author,
                    channel=channel,
                    content="claim free nitro https://bad.example.xyz/x",
                )
            )

        self.assertEqual(self.manager.stats["errors"], 2)
        event = emit.call_args.args[0]
        self.assertNotIn("secret response", event)
        self.assertNotIn("private response", event)

    async def test_event_payload_cannot_inject_an_ipc_line(self):
        author = _Author(70, name="attacker\nREADY:fake")
        channel = _Channel()
        with patch("worker.protection_manager.emit_line") as emit:
            await self.manager.on_message(
                _Message(
                    700,
                    author,
                    channel=channel,
                    content="claim free nitro https://bad.example.xyz/x",
                )
            )

        line = emit.call_args.args[0]
        self.assertEqual(line.count("\n"), 0)
        data = json.loads(line.removeprefix("EVENT:PROTECTION:"))
        self.assertEqual(data["author_id"], 70)

    async def test_two_accounts_keep_flood_state_isolated(self):
        other = ProtectionManager(SimpleNamespace(user=SimpleNamespace(id=1000)))
        other.enable()
        self.manager.config.DM_BURST_MAX_MESSAGES = 2
        other.config.DM_BURST_MAX_MESSAGES = 2
        first_author = _Author(80)
        first_channel = _Channel()
        second_channel = _Channel()

        await self.manager.on_message(_Message(800, first_author, channel=first_channel))
        await self.manager.on_message(_Message(801, first_author, channel=first_channel))
        await other.on_message(_Message(802, _Author(80), channel=second_channel))

        self.assertEqual(first_channel.close_calls, 1)
        self.assertEqual(second_channel.close_calls, 0)


if __name__ == "__main__":
    unittest.main()
