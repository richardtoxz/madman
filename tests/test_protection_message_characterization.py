"""Golden da análise de DM recebida na Proteção (`ProtectionManager.on_message`).

python -m tests.test_protection_message_characterization --update
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from tests import characterization
from tests import test_protection_manager as protection_fakes
from worker.protection.manager import ProtectionManager

NAME = "protection_message"
SCAM = "Claim free nitro agora: https://example.xyz/presente"
INVITE = "entra ai https://discord.gg/abcdef"


def _message(message_id, author, content="oi", *, channel_type=discord.ChannelType.private):
    return protection_fakes._Message(
        message_id, author, channel=protection_fakes._Channel(channel_type=channel_type), content=content
    )


def _stranger(user_id=10):
    return protection_fakes._Author(user_id)


def _friend(user_id=20):
    return protection_fakes._Author(user_id, friend=True)


async def _run(messages, *, enabled=True, count_flood=True, extract_error=False):
    recorder = characterization.Recorder()
    manager = ProtectionManager(SimpleNamespace(user=SimpleNamespace(id=999)))
    if enabled:
        manager.enable()

    async def apply_dm_action(message, threat):
        recorder.note("action", message.id, threat.kind, threat.reason)

    def emit_event(event, **kwargs):
        threat = kwargs.get("threat")
        recorder.note("event", event, getattr(kwargs.get("author"), "id", None), getattr(threat, "kind", None))

    manager._apply_dm_action = apply_dm_action
    manager._emit_event = emit_event
    if extract_error:

        def broken(message):
            raise RuntimeError("payload")

        manager._extract_message_payload = broken

    with patch("worker.protection.manager.time.monotonic", return_value=500.0):
        for message in messages:
            await manager.on_message(message, count_flood=count_flood)
    return {"log": recorder.log, "stats": manager.stats, "last_error": manager.last_error}


def _burst(author, count, *, start=100):
    return [_message(start + index, author, f"mensagem {index}") for index in range(count)]


async def collect() -> dict:
    config = ProtectionManager(SimpleNamespace(user=SimpleNamespace(id=999))).config
    burst = config.DM_BURST_MAX_MESSAGES + 1
    raid = config.DM_RAID_MAX_UNIQUE + 1
    invites = config.INVITE_SPAM_MAX_MESSAGES + 1
    stranger = _stranger()
    friend = _friend()
    no_author = SimpleNamespace(id=5, author=None, channel=protection_fakes._Channel(), content="oi")
    return {
        "disabled": await _run([_message(1, stranger, SCAM)], enabled=False),
        "not_private": await _run([_message(1, stranger, SCAM, channel_type=discord.ChannelType.text)]),
        "own_message": await _run([_message(1, protection_fakes._Author(999), SCAM)]),
        "no_author": await _run([no_author]),
        "clean_message": await _run([_message(1, stranger, "bom dia")]),
        "scam": await _run([_message(1, stranger, SCAM)]),
        "duplicate_message": await _run([_message(1, stranger, SCAM), _message(1, stranger, SCAM)]),
        "action_reserved_once": await _run([_message(1, stranger, SCAM), _message(2, stranger, SCAM + " ")]),
        "trusted_threat_alert_once": await _run([_message(1, friend, SCAM), _message(2, friend, SCAM + " ")]),
        "invite_first": await _run([_message(1, stranger, INVITE)]),
        "invite_repeated": await _run(
            [_message(index, stranger, f"{INVITE} {index}") for index in range(1, invites + 1)]
        ),
        "invite_trusted": await _run([_message(index, friend, f"{INVITE} {index}") for index in range(1, invites + 1)]),
        "flood_burst": await _run(_burst(stranger, burst)),
        "flood_raid": await _run([_message(200 + n, _stranger(300 + n), f"oi {n}") for n in range(raid)]),
        "flood_not_counted": await _run(_burst(stranger, burst), count_flood=False),
        "flood_trusted": await _run(_burst(friend, burst)),
        "extract_error": await _run([_message(1, stranger, SCAM)], extract_error=True),
    }


class ProtectionMessageCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_protection_message_matches_golden(self):
        await characterization.check(self, NAME, collect)


if __name__ == "__main__":
    characterization.main(NAME, collect)
