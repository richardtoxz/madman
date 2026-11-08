from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from tests.test_relationship_manager import _FakeClient, _FakeRelationship
from worker.core import commands as ipc_commands
from worker.relationships import commands as relationship_commands
from worker.relationships.manager import RelationshipManager


def _friend(user_id: int, name: str, status=discord.Status.offline):
    relationship = _FakeRelationship(user_id, discord.RelationshipType.friend, name=name)
    relationship.status = status
    return relationship


class _AffinityClient(_FakeClient):
    def __init__(self, relationships, scores=None, *, affinity_error=None):
        super().__init__(relationships)
        self.scores = scores or {}
        self.affinity_error = affinity_error
        self.affinity_calls = 0

    async def user_affinities(self):
        self.affinity_calls += 1
        if self.affinity_error:
            raise self.affinity_error
        return [SimpleNamespace(user_id=user_id, affinity=score) for user_id, score in self.scores.items()]


class FriendAffinityTests(unittest.IsolatedAsyncioTestCase):
    def _manager(self, **kwargs):
        relationships = [
            _friend(1, "ana", discord.Status.online),
            _friend(2, "bia"),
            _friend(3, "caio"),
            _friend(4, "duda", discord.Status.online),
            _FakeRelationship(5, discord.RelationshipType.blocked, name="bloqueado"),
        ]
        # 5 e 9 nao sao amigos: nao podem aparecer no ranking.
        scores = {3: 9.5, 2: 4.0, 5: 50.0, 9: 80.0, 4: 0.0}
        client = _AffinityClient(relationships, scores, **kwargs)
        return RelationshipManager(client), client

    async def test_default_order_is_unchanged_and_skips_the_affinity_call(self):
        manager, client = self._manager()

        data = await manager.get_friends_list()

        self.assertEqual([item["id"] for item in data["items"]], [1, 4, 2, 3])
        self.assertEqual(data["sort"], "status")
        self.assertEqual(client.affinity_calls, 0)
        self.assertTrue(all(item["closeness"] is None for item in data["items"]))

    async def test_interaction_order_puts_most_talked_friends_first(self):
        manager, _ = self._manager()

        data = await manager.get_friends_list(sort="interaction")

        self.assertTrue(data["affinity_available"])
        self.assertEqual([item["id"] for item in data["items"]], [3, 2, 1, 4])
        self.assertEqual([item["closeness"] for item in data["items"]], [1, 2, None, None])

    async def test_unknown_sort_falls_back_to_status(self):
        manager, client = self._manager()

        data = await manager.get_friends_list(sort="'; drop")

        self.assertEqual(data["sort"], "status")
        self.assertEqual(client.affinity_calls, 0)

    async def test_affinities_are_cached_between_pages(self):
        manager, client = self._manager()

        await manager.get_friends_list(0, 2, "interaction")
        await manager.get_friends_list(1, 2, "interaction")

        self.assertEqual(client.affinity_calls, 1)

    async def test_failed_affinity_call_keeps_the_normal_order_with_warning(self):
        manager, _ = self._manager(affinity_error=RuntimeError("offline"))

        data = await manager.get_friends_list(sort="interaction")

        self.assertFalse(data["affinity_available"])
        self.assertEqual([item["id"] for item in data["items"]], [1, 4, 2, 3])
        self.assertIn("exibindo a ordem normal", data["warning"])

    async def test_closest_returns_only_current_friends_in_rank_order(self):
        manager, _ = self._manager()

        result = await manager.get_closest_friends(10)

        self.assertTrue(result["ok"])
        self.assertEqual([item["id"] for item in result["items"]], [3, 2])
        self.assertEqual(result["ranked_total"], 2)

    async def test_closest_respects_the_limit(self):
        manager, _ = self._manager()

        result = await manager.get_closest_friends(1)

        self.assertEqual([item["id"] for item in result["items"]], [3])

    async def test_closest_reports_when_discord_has_no_ranking(self):
        manager, _ = self._manager(affinity_error=RuntimeError("offline"))

        result = await manager.get_closest_friends(5)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "AFFINITY_UNAVAILABLE")

    async def test_router_passes_sort_and_validates_closest_limit(self):
        bot = SimpleNamespace(
            rel_manager=SimpleNamespace(
                get_friends_list=AsyncMock(return_value={"items": []}),
                get_closest_friends=AsyncMock(return_value={"ok": True, "items": []}),
            )
        )
        with patch.object(ipc_commands, "_emit"), patch.object(ipc_commands, "_emit_json"):
            await relationship_commands.handle_rel_friends_list(
                bot, "REL_FRIENDS_PAGE_JSON:" + json.dumps({"page": 0, "page_size": 20, "sort": "interaction"})
            )
            await relationship_commands.handle_rel_closest(bot, 'REL_CLOSEST_JSON:{"limit": 10}')
            with self.assertRaises(ValueError):
                await relationship_commands.handle_rel_closest(bot, 'REL_CLOSEST_JSON:{"limit": 500}')

        bot.rel_manager.get_friends_list.assert_awaited_once_with(0, 20, "interaction")
        bot.rel_manager.get_closest_friends.assert_awaited_once_with(10)


class ProtectClosestRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer

        from engine.backend import SelfbotManager
        from panel.main import create_web_app
        from panel.web.routes import SESSION_STORE_KEY
        from panel.web.security import SESSION_COOKIE_NAME

        self.manager = SelfbotManager()
        self.app = create_web_app(manager=self.manager, bot=None)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addAsyncCleanup(self.manager.shutdown)
        cookie, record = self.app[SESSION_STORE_KEY].create_session(user_id=1001, username="UserA")
        self.cookies = {SESSION_COOKIE_NAME: cookie}
        self.headers = {"X-CSRF-Token": record["csrf_token"]}

    async def _post(self, body):
        return await self.client.post(
            "/api/social/protect-closest", json=body, headers=self.headers, cookies=self.cookies
        )

    async def test_requires_login_and_csrf(self):
        resp = await self.client.post("/api/social/protect-closest", json={"count": 5})
        self.assertIn(resp.status, (401, 403))
        resp = await self.client.post("/api/social/protect-closest", json={"count": 5}, cookies=self.cookies)
        self.assertEqual(resp.status, 403)

    async def test_invalid_count_never_reaches_the_worker(self):
        self.manager.get_closest_friends = AsyncMock()
        for count in (0, 7, 51, "abc", None):
            with self.subTest(count=count):
                resp = await self._post({"count": count})
                self.assertEqual(resp.status, 400)
        self.manager.get_closest_friends.assert_not_awaited()

    async def test_only_adds_to_the_whitelist_of_the_session_user(self):
        self.manager.user_whitelists[1001] = [20]
        self.manager.user_whitelists[2002] = [99]
        self.manager.get_closest_friends = AsyncMock(
            return_value={
                "ok": True,
                "items": [{"id": 10, "name": "@ana"}, {"id": 20, "name": "@bia"}],
            }
        )

        resp = await self._post({"count": 5, "user_id": 2002})
        data = await resp.json()

        self.manager.get_closest_friends.assert_awaited_once_with(1001, 5)
        self.assertTrue(data["ok"])
        self.assertEqual((data["added"], data["already"], data["skipped"]), (1, 1, 0))
        self.assertEqual(self.manager.user_whitelists[1001], [20, 10])
        self.assertEqual(self.manager.user_whitelists[2002], [99])

    async def test_whitelist_cap_is_respected(self):
        self.manager.user_whitelists[1001] = list(range(1, 150))
        self.manager.get_closest_friends = AsyncMock(
            return_value={
                "ok": True,
                "items": [{"id": 500, "name": "@a"}, {"id": 501, "name": "@b"}],
            }
        )

        data = await (await self._post({"count": 5})).json()

        self.assertEqual((data["added"], data["skipped"]), (1, 1))
        self.assertEqual(len(self.manager.user_whitelists[1001]), 150)

    async def test_overview_forwards_only_known_sorts(self):
        self.manager.get_rel_stats = AsyncMock(return_value={})
        self.manager.get_friends_list = AsyncMock(return_value={"items": []})

        for sort, expected in (("interaction", "interaction"), ("evil", "status")):
            await self.client.get(f"/api/social/overview?tab=friends&sort={sort}", cookies=self.cookies)
            self.assertEqual(self.manager.get_friends_list.await_args.kwargs["sort"], expected)


if __name__ == "__main__":
    unittest.main()
