"""Golden dos eventos do Tracker: perfil público, voz e edição de mensagem.

python -m tests.test_tracker_events_characterization --update
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self

from tests import characterization
from tests import test_tracker_events as tracker_fakes

NAME = "tracker_events"
TARGET = 42
NOW = 1000.0


class _Channel:
    """Canal falso com `repr` estável, para o golden não gravar endereços de memória."""

    def __init__(self, channel_id, *, allowed=True, name="geral", guild_id=20):
        self.id = channel_id
        self.name = name
        self.guild = SimpleNamespace(id=guild_id, name="Guild", me="me")
        self.allowed = allowed

    def permissions_for(self, member):
        return SimpleNamespace(view_channel=self.allowed, read_message_history=True)

    def __repr__(self):
        return f"<canal {self.id} {self.name}>"


def _channel(channel_id, **options):
    return _Channel(channel_id, **options)


class _Client:
    def __init__(self, recorder, profiles=()):
        self.recorder = recorder
        self.profiles = list(profiles)

    def get_channel(self, channel_id):
        self.recorder.note("get_channel", channel_id)
        if channel_id == 666:
            return _channel(channel_id, allowed=False)
        return _channel(channel_id)

    async def fetch_user_profile(self, user_id, **kwargs):
        self.recorder.note("fetch_profile", user_id, kwargs)
        profile = self.profiles.pop(0)
        if isinstance(profile, BaseException):
            raise profile
        return profile


class _Tracker(tracker_fakes._EventTracker):
    def __init__(self, recorder, profiles=()):
        super().__init__()
        self.recorder = recorder
        self.client = _Client(recorder, profiles)
        self.tracked_voice_channels = set()
        self.msg_buffer = SimpleNamespace(flush=self._record_flush)

    async def _record_flush(self, channel_id):
        self.recorder.note("flush", channel_id)

    async def _dispatch_log(self, **kwargs):
        self.recorder.note("log", kwargs)

    async def _dispatch_voice_embed(self, *args, **kwargs):
        self.recorder.note("voice_embed", args, kwargs)

    def _cache_message(self, message_id, data):
        self.recorder.note("cache", message_id)
        self._msg_cache[int(message_id)] = data


class _HashableBadge:
    """Insígnia num conjunto: `profile_visuals` fatia a lista e falha com um set."""

    description = "Set"
    id = "s"


def _profile(**overrides):
    values = {
        "bio": "bio inicial",
        "connections": [
            SimpleNamespace(type="github", name="alvo", url="https://github.com/alvo", visible=True),
            SimpleNamespace(type="steam", name="oculto", url=None, visible=False),
        ],
        "badges": [SimpleNamespace(description="Early Supporter", id="early")],
        "name": "alvo",
        "global_name": "Alvo",
        "display_avatar": "https://cdn/avatar.png",
        "banner": None,
        "metadata": SimpleNamespace(pronouns="ele/dele"),
        "accent_colour": None,
        "premium_type": SimpleNamespace(name="nitro"),
        "premium_since": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "premium_guild_since": None,
        "is_blocker": lambda: False,
    }
    values.update(overrides)
    profile = SimpleNamespace(**values)
    profile.user = SimpleNamespace(id=TARGET, name=profile.name)
    return profile


def _tracker_state(tracker):
    names = ("bio", "connected_accounts", "badges", "profile_identity", "profile_card", "profile_initialized")
    state = {name: getattr(tracker.state, name, None) for name in names}
    state["stats"] = tracker._stats
    return state


async def _profiles(*profiles, emit_changes=(True,)):
    recorder = characterization.Recorder()
    tracker = _Tracker(recorder, profiles)
    flags = list(emit_changes) + [True] * len(profiles)
    with (
        patch("worker.tracker.events.time.monotonic", return_value=NOW),
        patch("worker.tracker.events.emit_line", recorder.emit_line),
    ):
        for index in range(len(profiles)):
            await tracker._fetch_profile(emit_changes=flags[index])
    return {"log": recorder.log, "state": _tracker_state(tracker)}


def _voice_state(channel=None, **flags):
    values = {
        "channel": channel,
        "self_mute": False,
        "self_deaf": False,
        "self_stream": False,
        "self_video": False,
        "mute": False,
        "deaf": False,
        "suppress": False,
        "requested_to_speak_at": None,
        "afk": False,
    }
    values.update(flags)
    return SimpleNamespace(**values)


async def _voice(member_id, before, after, *, setup=None, enabled=True):
    recorder = characterization.Recorder()
    tracker = _Tracker(recorder)
    tracker.enabled = enabled
    if setup:
        setup(tracker)
    member = SimpleNamespace(id=member_id, guild=SimpleNamespace(id=20, name="Guild"))
    with patch("worker.tracker.events.time.monotonic", return_value=NOW):
        await tracker.on_voice_state_update(member, before, after)
    return {
        "log": recorder.log,
        "tracked": tracker.tracked_voice_channels,
        "in_voice": tracker.state.in_voice_channel_id,
        "sessions": tracker.state.voice_sessions,
        "stats": tracker._stats,
    }


def _session(tracker):
    tracker.state.voice_sessions[20] = {"channel_id": 1, "started": NOW - 90, "partial": True}
    tracker.tracked_voice_channels.add(1)


async def _edit(data, *, cache=None, cached_message=None, channel_id=10, enabled=True):
    recorder = characterization.Recorder()
    tracker = _Tracker(recorder)
    tracker.enabled = enabled
    if cache is not None:
        tracker._msg_cache[99] = dict(cache)
    payload = SimpleNamespace(message_id=99, channel_id=channel_id, cached_message=cached_message, data=data)
    await tracker.on_raw_message_edit(payload)
    return {"log": recorder.log, "cache": tracker._msg_cache.get(99)}


CACHED = {"author_id": TARGET, "content": "antes", "attachments": [], "embeds": []}


async def collect() -> dict:
    changed = _profile(
        bio="bio nova",
        connections=[SimpleNamespace(type="x", name="alvo", url=None, visible=True)],
        badges=[SimpleNamespace(description=None, id="hypesquad")],
    )
    renamed = _profile(global_name="Novo Nome", premium_type=None)
    cases = {
        "profile first_fetch": await _profiles(_profile()),
        "profile no_changes": await _profiles(_profile(), _profile()),
        "profile bio_connections_badges_changed": await _profiles(_profile(), changed),
        "profile identity_changed": await _profiles(_profile(), renamed),
        "profile emit_changes_false": await _profiles(_profile(), changed, emit_changes=(True, False)),
        "profile none": await _profiles(None),
        "profile blocker": await _profiles(_profile(is_blocker=lambda: True)),
        "profile visuals_error": await _profiles(_profile(badges={_HashableBadge()})),
        "profile fetch_error": await _profiles(RuntimeError("x")),
    }

    voice = _channel(1, name="voz")
    other = _channel(2, name="outra")
    hidden = _channel(3, allowed=False)
    cases.update(
        {
            "voice disabled": await _voice(TARGET, _voice_state(), _voice_state(voice), enabled=False),
            "voice channels_not_allowed": await _voice(TARGET, _voice_state(hidden), _voice_state(None)),
            "voice other_member_joins_tracked": await _voice(
                7, _voice_state(), _voice_state(voice), setup=lambda t: t.tracked_voice_channels.add(1)
            ),
            "voice other_member_leaves_tracked": await _voice(
                7, _voice_state(voice), _voice_state(), setup=lambda t: t.tracked_voice_channels.add(1)
            ),
            "voice other_member_untracked": await _voice(7, _voice_state(), _voice_state(voice)),
            "voice other_member_same_channel": await _voice(
                7, _voice_state(voice), _voice_state(voice, self_mute=True)
            ),
            "voice target_joins": await _voice(TARGET, _voice_state(), _voice_state(voice)),
            "voice target_leaves_with_session": await _voice(
                TARGET, _voice_state(voice), _voice_state(), setup=_session
            ),
            "voice target_moves": await _voice(TARGET, _voice_state(voice), _voice_state(other), setup=_session),
            "voice target_moves_to_hidden": await _voice(
                TARGET, _voice_state(voice), _voice_state(hidden), setup=_session
            ),
            "voice target_flags_only": await _voice(
                TARGET,
                _voice_state(voice),
                _voice_state(voice, self_mute=True, self_deaf=True, self_stream=True, mute=True),
            ),
            "voice target_flags_off": await _voice(
                TARGET,
                _voice_state(voice, self_video=True, deaf=True, suppress=True),
                _voice_state(voice),
            ),
            "voice target_stage_and_afk": await _voice(
                TARGET,
                _voice_state(voice),
                _voice_state(voice, requested_to_speak_at="agora", afk=True),
            ),
            "voice target_stage_withdrawn": await _voice(
                TARGET,
                _voice_state(voice, requested_to_speak_at="agora", afk=True),
                _voice_state(voice),
            ),
            "voice target_no_change": await _voice(TARGET, _voice_state(voice), _voice_state(voice)),
        }
    )

    cached_message = SimpleNamespace(
        id=99,
        author=SimpleNamespace(id=TARGET, name="alvo", display_avatar=SimpleNamespace(url="https://cdn/a.png")),
        content="do cache",
        attachments=[],
        embeds=[],
        stickers=[],
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        channel=SimpleNamespace(id=10),
    )
    attachment = {"id": "5", "filename": "foto.png", "url": "https://cdn/foto.png", "size": 10}
    cases.update(
        {
            "edit disabled": await _edit({"content": "x"}, cache=CACHED, enabled=False),
            "edit channel_not_allowed": await _edit({"content": "x"}, cache=CACHED, channel_id=666),
            "edit author_from_cached_message": await _edit({"content": "depois"}, cached_message=cached_message),
            "edit author_from_cache": await _edit({"content": "depois"}, cache=CACHED),
            "edit author_from_payload": await _edit({"content": "depois", "author": {"id": str(TARGET)}}),
            "edit author_payload_invalid": await _edit({"content": "depois", "author": {"id": "abc"}}),
            "edit other_author": await _edit({"content": "depois"}, cache={**CACHED, "author_id": 7}),
            "edit content_same": await _edit({"content": "antes"}, cache=CACHED),
            "edit content_empty": await _edit({"content": None}, cache=CACHED),
            "edit attachments_changed": await _edit({"attachments": [attachment]}, cache=CACHED),
            "edit embeds_changed": await _edit(
                {"embeds": [{"title": "novo", "image": {"url": "https://img"}}, "invalido"]}, cache=CACHED
            ),
            "edit embeds_removed": await _edit({"embeds": []}, cache={**CACHED, "embeds": [{"title": "velho"}]}),
            "edit nothing_changed": await _edit({"pinned": True}, cache=CACHED),
        }
    )
    return cases


class TrackerEventsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tracker_events_match_golden(self):
        await characterization.check(self, NAME, collect)


if __name__ == "__main__":
    characterization.main(NAME, collect)
