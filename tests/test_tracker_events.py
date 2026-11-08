from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace

from worker.tracker.events import TrackerEventMixin
from worker.tracker.support import TrackerState


class _Client:
    def get_channel(self, channel_id):
        return SimpleNamespace(
            id=channel_id,
            name="geral",
            guild=SimpleNamespace(id=20, name="Guild", me=object()),
            permissions_for=lambda _: SimpleNamespace(view_channel=True, read_message_history=True),
        )


class _EventTracker(TrackerEventMixin):
    def __init__(self):
        self.enabled = True
        self.target_id = 42
        self._run_id = 1
        self.state = TrackerState()
        self.client = _Client()
        self.msg_buffer = SimpleNamespace(flush=self._flush_buffer)
        self._msg_cache = {}
        self._pending_offline_task = None
        self._pending_presence_task = None
        self._profile_fetch_task = None
        self._stats = {"last_error": None, "subscription_errors": 0}
        self.logs = []
        self.config = SimpleNamespace(
            PRESENCE_SNAPSHOT_TTL=300.0,
            PRESENCE_COALESCE_DELAY=0.01,
            OFFLINE_DEBOUNCE=0.01,
            PROFILE_FETCH_THROTTLE=900.0,
            TYPING_THROTTLE=15.0,
        )

    async def _flush_buffer(self, channel_id):
        return None

    async def _dispatch_log(self, **kwargs):
        self.logs.append(kwargs)

    def _format_activity(self, activity):
        return str(activity)


class TrackerEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_relationship_presence_without_guild_is_supported_and_coalesced(self):
        tracker = _EventTracker()
        user = SimpleNamespace(id=42, name="target")
        before = SimpleNamespace(id=42, user=user)
        after = SimpleNamespace(
            id=42,
            user=user,
            status="online",
            activities=("game",),
            desktop_status="online",
            mobile_status="offline",
            web_status="offline",
            embedded_status="offline",
        )

        await tracker.on_presence_update(before, after)
        await asyncio.sleep(0.03)

        self.assertIn(0, tracker.state.guild_presence)
        self.assertEqual(len(tracker.logs), 1)
        self.assertEqual(tracker.logs[0]["event_type"], "presence")

    async def test_partial_embed_only_edit_uses_cached_author_and_keeps_content(self):
        tracker = _EventTracker()
        tracker._msg_cache[99] = {
            "author_id": 42,
            "content": "texto original",
            "attachments": [],
            "embeds": [],
        }
        payload = SimpleNamespace(
            message_id=99,
            channel_id=10,
            cached_message=None,
            data={"embeds": [{"title": "novo"}]},
        )

        await tracker.on_raw_message_edit(payload)

        self.assertEqual(tracker._msg_cache[99]["content"], "texto original")
        self.assertEqual(len(tracker.logs), 1)
        self.assertEqual(tracker.logs[0]["event_type"], "message_edit")

    def test_stale_online_snapshot_does_not_mask_a_new_offline_event(self):
        tracker = _EventTracker()
        tracker.state.guild_presence = {
            1: {
                "status": "online",
                "activities": (),
                "devices": (),
                "updated_at": time.monotonic() - 301,
            },
            2: {
                "status": "offline",
                "activities": (),
                "devices": (),
                "updated_at": time.monotonic(),
            },
        }

        status, _, _ = tracker._select_effective_presence()
        self.assertEqual(status, "offline")

    def test_recent_but_older_online_snapshot_does_not_override_new_offline(self):
        tracker = _EventTracker()
        now = time.monotonic()
        tracker.state.guild_presence = {
            1: {"status": "online", "activities": (), "devices": (), "updated_at": now - 1},
            2: {"status": "offline", "activities": (), "devices": (), "updated_at": now},
        }
        self.assertEqual(tracker._select_effective_presence()[0], "offline")

    async def test_poll_vote_and_private_call_are_captured_for_target(self):
        tracker = _EventTracker()
        tracker.config.INCLUDE_PRIVATE_CHANNELS = True
        tracker._msg_cache[100] = {
            "poll": {
                "question": "Escolha",
                "answers": [{"id": 2, "text": "Resposta B"}],
            }
        }
        vote = SimpleNamespace(
            user_id=42,
            channel_id=10,
            message_id=100,
            guild_id=20,
            answer_id=2,
        )
        await tracker.on_raw_poll_vote_add(vote)

        target = SimpleNamespace(id=42)
        call = SimpleNamespace(
            channel=SimpleNamespace(id=30, recipient=target, recipients=[]),
            initiator=target,
            members=[target],
            ringing=[],
        )
        await tracker.on_call_create(call)

        self.assertEqual(tracker.logs[0]["event_type"], "poll_vote")
        self.assertEqual(tracker.logs[1]["event_type"], "private_call")


if __name__ == "__main__":
    unittest.main()
