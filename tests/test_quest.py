from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from worker.quest.api_client import QuestAPIClient
from worker.quest.manager import QuestManager
from worker.quest.models import Quest


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _quest_payload(
    task_name: str,
    *,
    quest_id: str = "123456789012345678",
    target: int = 60,
    progress: int = 0,
    enrolled: bool = True,
    completed: bool = False,
    claimed: bool = False,
    application_in_config: bool = True,
    name: str = "Quest teste",
) -> dict:
    now = datetime.now(timezone.utc)
    task = {"target": target, "applications": [{"id": "987654321098765432"}]}
    status = {}
    if enrolled:
        status["enrolled_at"] = _iso(now - timedelta(minutes=5))
    if completed:
        status["completed_at"] = _iso(now)
    if claimed:
        status["claimed_at"] = _iso(now)
    status["progress"] = {task_name: {"value": progress}}
    config = {
        "messages": {"quest_name": name},
        "expires_at": _iso(now + timedelta(days=1)),
        "task_config_v2": {"tasks": {task_name: task}},
    }
    if application_in_config:
        config["application"] = {
            "id": "111111111111111111",
            "name": "Aplicação teste",
        }
    return {"id": quest_id, "config": config, "user_status": status}


class _Client:
    def __init__(self):
        self.presence_manager = None
        self.user = SimpleNamespace(id=222222222222222222)
        self.voice_clients = []
        self.private_channels = []
        self.http = SimpleNamespace(user_agent="Discord test")


class QuestModelTests(unittest.TestCase):
    def test_application_id_falls_back_to_task_applications(self):
        quest = Quest(
            _quest_payload("PLAY_ON_DESKTOP", application_in_config=False)
        )
        task_name, task = quest.get_task_info(auto_only=True)

        self.assertEqual(task_name, "PLAY_ON_DESKTOP")
        self.assertEqual(
            quest.application_id_for(task_name, task), "987654321098765432"
        )

    def test_latest_progress_task_is_preferred_and_video_progress_is_shared(self):
        raw = _quest_payload("PLAY_ON_DESKTOP")
        raw["config"]["task_config_v2"]["tasks"]["WATCH_VIDEO"] = {
            "target": 100
        }
        raw["user_status"]["progress"] = {
            "PLAY_ON_DESKTOP": {
                "value": 10,
                "updated_at": "2026-01-01T00:00:00Z",
            },
            "WATCH_VIDEO_ON_MOBILE": {
                "value": 42,
                "updated_at": "2026-08-01T00:00:00Z",
            },
        }
        quest = Quest(raw)

        self.assertEqual(quest.get_task_info(auto_only=True)[0], "WATCH_VIDEO")
        self.assertEqual(quest.get_progress("WATCH_VIDEO"), 42)

    def test_error_payload_cannot_replace_valid_user_status(self):
        quest = Quest(_quest_payload("WATCH_VIDEO", progress=12))
        before = quest.user_status.copy()

        updated = quest.update_status(
            {"message": "rate limited", "retry_after": 120, "code": 123}
        )

        self.assertFalse(updated)
        self.assertEqual(quest.user_status, before)

    def test_status_updates_are_merged_without_losing_enrollment(self):
        quest = Quest(_quest_payload("WATCH_VIDEO", progress=12))
        self.assertTrue(
            quest.update_status(
                {"progress": {"WATCH_VIDEO": {"value": 20}}}
            )
        )
        self.assertTrue(quest.is_enrolled)
        self.assertEqual(quest.get_progress("WATCH_VIDEO"), 20)


class QuestAPIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_library_http_client_and_relative_route(self):
        calls = []

        class Http:
            async def request(self, route, **kwargs):
                calls.append((route, kwargs))
                return {"ok": True}

        client = QuestAPIClient(SimpleNamespace(http=Http()))
        response = await client.post(
            "/quests/{quest_id}/heartbeat",
            {"terminal": False},
            quest_id="123",
        )

        self.assertEqual(response, {"ok": True})
        route, kwargs = calls[0]
        self.assertEqual(route.method, "POST")
        self.assertEqual(route.path, "/quests/{quest_id}/heartbeat")
        self.assertTrue(route.url.endswith("/quests/123/heartbeat"))
        self.assertEqual(kwargs["json"], {"terminal": False})

    async def test_rejects_absolute_urls(self):
        client = QuestAPIClient(SimpleNamespace(http=SimpleNamespace()))
        with self.assertRaises(ValueError):
            await client.get("https://attacker.invalid/quests/@me")
        with self.assertRaises(ValueError):
            await client.get("//attacker.invalid/quests/@me")
        with self.assertRaises(ValueError):
            await client.get("/quests/@me?redirect=https://attacker.invalid")


class QuestManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = _Client()
        self.manager = QuestManager(self.client)

    async def asyncTearDown(self):
        await self.manager.shutdown()

    async def test_fetch_tracks_enrollment_block_and_validates_shape(self):
        class Api:
            async def get(_, path):
                return {
                    "quests": [_quest_payload("WATCH_VIDEO")],
                    "quest_enrollment_blocked_until": "2026-08-21T20:00:00Z",
                }

            async def close(_):
                return None

        self.manager.api = Api()
        quests = await self.manager.fetch_quests()

        self.assertEqual(len(quests), 1)
        self.assertEqual(
            self.manager.enrollment_blocked_until, "2026-08-21T20:00:00Z"
        )

    async def test_auto_queue_never_enrolls_or_claims_automatically(self):
        available = Quest(_quest_payload("WATCH_VIDEO", enrolled=False))
        completed = Quest(
            _quest_payload(
                "WATCH_VIDEO",
                quest_id="223456789012345678",
                completed=True,
            )
        )

        async def fetch():
            return [available, completed]

        self.manager.fetch_quests = fetch
        lines = []
        with patch(
            "worker.quest.manager.emit_line",
            side_effect=lambda message, request_id=None: lines.append(message),
        ):
            await self.manager.auto_complete_all("request")

        result_line = next(line for line in lines if line.startswith("QUEST:RESULT:"))
        result = json.loads(result_line.removeprefix("QUEST:RESULT:"))
        self.assertEqual(result["manual_enroll"], 1)
        self.assertEqual(result["manual_claim"], 1)
        self.assertEqual(result["code"], "MANUAL_ENROLL_REQUIRED")

    async def test_single_quest_start_reserves_the_execution_slot(self):
        blocker = asyncio.Event()

        async def fetch():
            await blocker.wait()
            return []

        self.manager.fetch_quests = fetch
        first = await self.manager.start_quest("123456789012345678", "first")
        second = await self.manager.start_quest("223456789012345678", "second")

        self.assertTrue(first["ok"])
        self.assertEqual(second["code"], "ALREADY_RUNNING")
        self.assertTrue(await self.manager.cancel())

    async def test_play_heartbeat_uses_task_application_and_requires_confirmation(self):
        quest = Quest(
            _quest_payload(
                "PLAY_ON_DESKTOP",
                target=60,
                progress=0,
                application_in_config=False,
            )
        )
        calls = []
        completed_at = _iso(datetime.now(timezone.utc))

        class Api:
            async def post(_, path, payload, **params):
                calls.append((path, payload, params))
                return {
                    "enrolled_at": quest.user_status["enrolled_at"],
                    "completed_at": completed_at,
                    "progress": {"PLAY_ON_DESKTOP": {"value": 60}},
                }

            async def close(_):
                return None

        self.manager.api = Api()
        task_name, task = quest.get_task_info(auto_only=True)
        result = await self.manager.doing_play_on_platform_quest(
            quest, task_name, task, 60, None
        )

        self.assertTrue(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0][1],
            {"application_id": "987654321098765432", "terminal": False},
        )
        self.assertTrue(calls[1][1]["terminal"])

    async def test_play_stops_after_three_heartbeats_without_progress(self):
        quest = Quest(_quest_payload("PLAY_ON_DESKTOP", target=60, progress=0))
        calls = []

        class Api:
            async def post(_, path, payload, **params):
                calls.append(payload)
                return {
                    "enrolled_at": quest.user_status["enrolled_at"],
                    "progress": {"PLAY_ON_DESKTOP": {"value": 0}},
                }

            async def close(_):
                return None

        self.manager.api = Api()
        self.manager.config.HEARTBEAT_MAX_INTERVAL_SECONDS = 0
        self.manager.config.HEARTBEAT_BUFFER_SECONDS = 0
        task_name, task = quest.get_task_info(auto_only=True)
        result = await self.manager.doing_play_on_platform_quest(
            quest, task_name, task, 60, None
        )

        self.assertFalse(result)
        self.assertEqual(len(calls), 3)
        self.assertFalse(any(call["terminal"] for call in calls))

    async def test_activity_stream_key_uses_real_guild_channel_and_user(self):
        channel = SimpleNamespace(
            id=333333333333333333,
            guild=SimpleNamespace(id=444444444444444444),
        )
        self.client.voice_clients = [SimpleNamespace(channel=channel)]

        self.assertEqual(
            self.manager._resolve_stream_key(),
            "guild:444444444444444444:333333333333333333:222222222222222222",
        )

    async def test_achievement_uses_proxy_ticket_quest_header_and_minimal_scope(self):
        quest = Quest(
            _quest_payload(
                "ACHIEVEMENT_IN_ACTIVITY",
                target=1,
                application_in_config=False,
            )
        )
        api_calls = []
        activity_calls = []
        token_reads = 0

        class Api:
            async def post(_, path, payload, **kwargs):
                api_calls.append(("POST", path, payload, kwargs))
                if path == "/oauth2/authorize":
                    return {"location": "https://callback.invalid/?code=oauth-code"}
                return {"ticket": "proxy-ticket"}

            async def get(_, path):
                nonlocal token_reads
                token_reads += 1
                api_calls.append(("GET", path, None, {}))
                old = {
                    "id": "old-user-grant",
                    "application": {"id": "987654321098765432"},
                }
                new = {
                    "id": "temporary-quest-grant",
                    "application": {"id": "987654321098765432"},
                }
                return [old] if token_reads == 1 else [old, new]

            async def delete(_, path, **kwargs):
                api_calls.append(("DELETE", path, None, kwargs))

            async def close(_):
                return None

        async def activity_post(app_id, endpoint, payload, headers):
            activity_calls.append((app_id, endpoint, payload, headers))
            if endpoint == "authorize":
                return True, {"token": "activity-token"}
            return True, None

        async def verify(_quest):
            return True

        self.manager.api = Api()
        self.manager._activity_post = activity_post
        self.manager._verify_completion = verify
        _, task = quest.get_task_info(auto_only=True)
        result = await self.manager.doing_achievement_in_activity_quest(
            quest, task, 1, None
        )

        self.assertTrue(result)
        oauth = next(
            call for call in api_calls if call[0] == "POST" and call[1] == "/oauth2/authorize"
        )
        self.assertEqual(
            oauth[3]["query"]["scope"], "identify applications.entitlements"
        )
        self.assertEqual(
            next(
                call[1]
                for call in api_calls
                if call[0] == "POST" and "proxy-tickets" in call[1]
            ),
            "/applications/{application_id}/proxy-tickets",
        )
        self.assertEqual(activity_calls[0][3]["X-Discord-Quest-ID"], quest.id)
        self.assertIn("discord_proxy_ticket=proxy-ticket", activity_calls[0][3]["Referer"])
        deleted = next(call for call in api_calls if call[0] == "DELETE")
        self.assertEqual(deleted[3]["token_id"], "temporary-quest-grant")

    def test_webhook_validation_rejects_lookalike_and_query(self):
        valid = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40
        self.assertEqual(self.manager._normalize_webhook_url(valid), valid)
        self.assertIsNone(
            self.manager._normalize_webhook_url(
                "https://discord.com.attacker.invalid/api/webhooks/123456789012345678/"
                + "a" * 40
            )
        )
        self.assertIsNone(self.manager._normalize_webhook_url(valid + "?wait=true"))


if __name__ == "__main__":
    unittest.main()
