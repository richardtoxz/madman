"""Golden da resolução do canal de voz (`VoiceManager._resolve_channel`).

python -m tests.test_voice_resolve_characterization --update
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from tests import characterization
from worker.voice.manager import VoiceManager

NAME = "voice_resolve"
SELF_ID = 999


def _http(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), {"message": "x"})


def _guild(**overrides):
    values = {"id": 20, "name": "Guild", "unavailable": False, "me": SimpleNamespace(id=SELF_ID)}
    values.update(overrides)
    return SimpleNamespace(**values)


_DEFAULT_GUILD = object()


def _voice(channel_id=1, *, guild=_DEFAULT_GUILD, permissions=None, permissions_error=False, user_limit=0, members=()):
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.guild = _guild() if guild is _DEFAULT_GUILD else guild
    channel.user_limit = user_limit
    channel.voice_states = {member: object() for member in members}
    if permissions_error:
        channel.permissions_for.side_effect = RuntimeError("perm")
    else:
        channel.permissions_for.return_value = permissions or SimpleNamespace(
            view_channel=True, connect=True, move_members=False
        )
    return channel


def _typed(spec, channel_id):
    channel = MagicMock(spec=spec)
    channel.id = channel_id
    return channel


class _Client:
    def __init__(self, recorder, *, cached=None, fetches=(), user_dm=None, fetch_user_error=False):
        self.recorder = recorder
        self.user = SimpleNamespace(id=SELF_ID)
        self.cached = cached
        self.fetches = list(fetches)
        self.user_dm = user_dm
        self.fetch_user_error = fetch_user_error

    def get_channel(self, channel_id):
        self.recorder.note("get_channel", channel_id)
        return self.cached

    async def fetch_channel(self, channel_id):
        self.recorder.note("fetch_channel", channel_id)
        result = self.fetches.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def get_user(self, user_id):
        self.recorder.note("get_user", user_id)
        return None

    async def fetch_user(self, user_id):
        self.recorder.note("fetch_user", user_id)
        if self.fetch_user_error or self.user_dm is None:
            raise _http(discord.NotFound, 404)

        async def create_dm():
            self.recorder.note("create_dm", user_id)
            return self.user_dm

        return SimpleNamespace(id=user_id, create_dm=create_dm)


async def _resolve(channel_id=1, **client_options):
    recorder = characterization.Recorder()
    manager = VoiceManager(_Client(recorder, **client_options))
    with patch("worker.voice.manager.asyncio.sleep", recorder.sleeper()):
        channel, error = await manager._resolve_channel(channel_id)
    return {"log": recorder.log, "channel": getattr(channel, "id", None), "error": error}


async def collect() -> dict:
    not_found = _http(discord.NotFound, 404)
    full_permissions = SimpleNamespace(view_channel=True, connect=True, move_members=True)
    return {
        "cached_voice": await _resolve(cached=_voice()),
        "fetched_after_500": await _resolve(fetches=[_http(discord.HTTPException, 500), _voice()]),
        "fetch_500_twice": await _resolve(
            fetches=[_http(discord.HTTPException, 500), _http(discord.HTTPException, 502)]
        ),
        "fetch_400": await _resolve(fetches=[_http(discord.HTTPException, 400)]),
        "fetch_not_found_user_dm": await _resolve(555, fetches=[not_found], user_dm=_typed(discord.DMChannel, 77)),
        "fetch_not_found_no_dm": await _resolve(555, fetches=[not_found]),
        "fetch_not_found_self": await _resolve(SELF_ID, fetches=[not_found]),
        "fetch_forbidden": await _resolve(fetches=[_http(discord.Forbidden, 403)]),
        "fetch_generic_error": await _resolve(fetches=[ValueError("x")]),
        "private_call": await _resolve(cached=_typed(discord.GroupChannel, 8)),
        "not_voice": await _resolve(cached=_typed(discord.TextChannel, 9)),
        "no_guild": await _resolve(cached=_voice(guild=None)),
        "guild_unavailable": await _resolve(cached=_voice(guild=_guild(unavailable=True))),
        "no_me": await _resolve(cached=_voice(guild=_guild(me=None), user_limit=1, members=(1,))),
        "no_view_permission": await _resolve(
            cached=_voice(permissions=SimpleNamespace(view_channel=False, connect=True))
        ),
        "no_connect_permission": await _resolve(
            cached=_voice(permissions=SimpleNamespace(view_channel=True, connect=False))
        ),
        "permissions_error": await _resolve(cached=_voice(permissions_error=True)),
        "full": await _resolve(cached=_voice(user_limit=2, members=(1, 2))),
        "full_with_move_members": await _resolve(
            cached=_voice(user_limit=2, members=(1, 2), permissions=full_permissions)
        ),
        "full_already_inside": await _resolve(cached=_voice(user_limit=2, members=(1, SELF_ID))),
        "voice_states_error": await _resolve(cached=_voice(user_limit="x")),
    }


class VoiceResolveCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_resolve_matches_golden(self):
        await characterization.check(self, NAME, collect)


if __name__ == "__main__":
    characterization.main(NAME, collect)
