from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from worker.relationships.manager import RelationshipManager


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
        {"code": 10013, "message": "Unknown User"},
    )


def _forbidden():
    return discord.Forbidden(
        _FakeResponse(403, "Forbidden"),
        {"code": 50013, "message": "Missing Permissions"},
    )


class _FakeRelationship:
    def __init__(
        self,
        user_id: int,
        relationship_type,
        *,
        name: str | None = None,
        delete_error: Exception | None = None,
    ):
        self.user = SimpleNamespace(
            id=user_id,
            name=name or f"user{user_id}",
            global_name=None,
            display_avatar=None,
        )
        self.type = relationship_type
        self.delete_error = delete_error
        self.deleted = False
        self.accepted = False
        self.nick = None
        self.since = None
        self.status = discord.Status.offline
        self.activity = None
        self.on_delete = None

    async def delete(self):
        if self.on_delete:
            self.on_delete()
        if self.delete_error:
            if isinstance(self.delete_error, discord.NotFound):
                self.type = discord.RelationshipType.none
            raise self.delete_error
        self.deleted = True
        self.type = discord.RelationshipType.none

    async def accept(self):
        self.accepted = True
        self.type = discord.RelationshipType.friend

    async def edit(self, *, nick):
        self.nick = nick


class _FakeClient:
    def __init__(self, relationships=(), *, fetch_error: Exception | None = None):
        self.relationships = list(relationships)
        self.fetch_error = fetch_error
        self.fetch_calls = 0
        self.user = SimpleNamespace(id=999)
        self.sent_friend_requests = []
        self.users = {}

    async def fetch_relationships(self):
        self.fetch_calls += 1
        if self.fetch_error:
            raise self.fetch_error
        return [
            relationship for relationship in self.relationships if relationship.type != discord.RelationshipType.none
        ]

    async def send_friend_request(self, username):
        self.sent_friend_requests.append(username)

    def get_user(self, user_id):
        return self.users.get(user_id)

    async def fetch_user(self, user_id):
        return self.users[user_id]


class RelationshipManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_preview_is_bounded_but_total_is_complete(self):
        relationships = [_FakeRelationship(i, discord.RelationshipType.incoming_request) for i in range(1, 31)]
        manager = RelationshipManager(_FakeClient(relationships))

        data = await manager.get_pending_requests(page=0, page_size=25)

        self.assertTrue(data["fresh"])
        self.assertEqual(data["incoming_total"], 30)
        self.assertEqual(len(data["incoming"]), 25)
        self.assertEqual(data["outgoing_total"], 0)
        self.assertEqual(data["pages"], 2)

        second_page = await manager.get_pending_requests(page=1, page_size=25)
        self.assertEqual(len(second_page["incoming"]), 5)
        self.assertEqual(second_page["incoming"][0]["id"], 26)

    async def test_read_falls_back_to_cache_and_marks_result_stale(self):
        cached = _FakeRelationship(1, discord.RelationshipType.friend)
        manager = RelationshipManager(_FakeClient([cached], fetch_error=RuntimeError("offline")))

        stats = await manager.get_stats()

        self.assertFalse(stats["fresh"])
        self.assertEqual(stats["friends"], 1)
        self.assertIsNotNone(stats["warning"])

    async def test_destructive_bulk_aborts_when_fresh_list_is_unavailable(self):
        relationship = _FakeRelationship(1, discord.RelationshipType.friend)
        manager = RelationshipManager(_FakeClient([relationship], fetch_error=RuntimeError("offline")))

        with patch("worker.relationships.manager.emit_line") as emit:
            result = await manager.remove_all_friends([])

        self.assertEqual(result["status"], "ABORTADO")
        self.assertFalse(relationship.deleted)
        self.assertTrue(any("REL_CLEANUP_RESULT:ABORTADO" in call.args[0] for call in emit.call_args_list))

    async def test_remove_all_respects_whitelist_and_not_found_is_idempotent(self):
        keep = _FakeRelationship(1, discord.RelationshipType.friend)
        remove = _FakeRelationship(2, discord.RelationshipType.friend)
        already_removed = _FakeRelationship(
            3,
            discord.RelationshipType.friend,
            delete_error=_not_found(),
        )
        manager = RelationshipManager(_FakeClient([keep, remove, already_removed]))

        with (
            patch("worker.relationships.manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.relationships.manager.emit_line"),
        ):
            result = await manager.remove_all_friends([1])

        self.assertEqual(result["status"], "COMPLETADO")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["already_done"], 1)
        self.assertFalse(keep.deleted)
        self.assertTrue(remove.deleted)

    async def test_forbidden_item_produces_partial_verified_result(self):
        relationship = _FakeRelationship(
            1,
            discord.RelationshipType.blocked,
            delete_error=_forbidden(),
        )
        manager = RelationshipManager(_FakeClient([relationship]))

        with (
            patch("worker.relationships.manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.relationships.manager.emit_line"),
        ):
            result = await manager.unblock_all()

        self.assertEqual(result["status"], "PARCIAL")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["remaining"], 1)

    async def test_accept_all_only_changes_incoming_requests(self):
        incoming = _FakeRelationship(1, discord.RelationshipType.incoming_request)
        outgoing = _FakeRelationship(2, discord.RelationshipType.outgoing_request)
        manager = RelationshipManager(_FakeClient([incoming, outgoing]))

        with (
            patch("worker.relationships.manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.relationships.manager.emit_line"),
        ):
            result = await manager.accept_all_requests()

        self.assertEqual(result["status"], "COMPLETADO")
        self.assertTrue(incoming.accepted)
        self.assertFalse(outgoing.accepted)
        self.assertEqual(outgoing.type, discord.RelationshipType.outgoing_request)

    async def test_disappeared_incoming_request_is_not_reported_as_accepted(self):
        incoming = _FakeRelationship(
            1,
            discord.RelationshipType.incoming_request,
        )

        async def disappeared_accept():
            incoming.type = discord.RelationshipType.none
            raise _not_found()

        incoming.accept = disappeared_accept
        manager = RelationshipManager(_FakeClient([incoming]))

        with (
            patch("worker.relationships.manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.relationships.manager.emit_line"),
        ):
            result = await manager.accept_all_requests()

        self.assertEqual(result["status"], "PARCIAL")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["remaining"], 1)

    async def test_cancel_stops_before_next_relationship(self):
        first = _FakeRelationship(1, discord.RelationshipType.friend)
        second = _FakeRelationship(2, discord.RelationshipType.friend)
        manager = RelationshipManager(_FakeClient([first, second]))
        first.on_delete = lambda: setattr(manager, "cancelled", True)

        with (
            patch("worker.relationships.manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.relationships.manager.emit_line"),
        ):
            result = await manager.remove_all_friends([])

        self.assertEqual(result["status"], "CANCELADO")
        self.assertEqual(result["processed"], 1)
        self.assertTrue(first.deleted)
        self.assertFalse(second.deleted)

    async def test_two_accounts_run_the_same_operation_independently(self):
        first = _FakeRelationship(1, discord.RelationshipType.blocked)
        second = _FakeRelationship(2, discord.RelationshipType.blocked)
        first_manager = RelationshipManager(_FakeClient([first]))
        second_manager = RelationshipManager(_FakeClient([second]))

        with (
            patch("worker.relationships.manager.BulkConfig", return_value=_FastBulkConfig()),
            patch("worker.relationships.manager.emit_line"),
        ):
            results = await asyncio.gather(first_manager.unblock_all(), second_manager.unblock_all())

        self.assertEqual([result["status"] for result in results], ["COMPLETADO"] * 2)
        self.assertTrue(first.deleted)
        self.assertTrue(second.deleted)

    async def test_second_bulk_on_same_account_is_rejected(self):
        relationship = _FakeRelationship(1, discord.RelationshipType.blocked)
        manager = RelationshipManager(_FakeClient([relationship]))
        await manager._bulk_lock.acquire()
        try:
            with patch("worker.relationships.manager.emit_line"):
                result = await manager.unblock_all()
        finally:
            manager._bulk_lock.release()

        self.assertEqual(result["status"], "ABORTADO")
        self.assertFalse(relationship.deleted)

    async def test_nickname_uses_fresh_relationship_edit(self):
        relationship = _FakeRelationship(1, discord.RelationshipType.friend)
        manager = RelationshipManager(_FakeClient([relationship]))

        result = await manager.set_friend_nickname(1, "Colega")

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "UPDATED")
        self.assertEqual(relationship.nick, "Colega")

    async def test_bulk_rejects_malformed_id_before_starting(self):
        # Os IDs alvo sao lidos antes do _START: um ID invalido falha antes de qualquer operacao.
        malformed = _FakeRelationship(1, discord.RelationshipType.friend)
        malformed.user.id = "nao-numerico"
        manager = RelationshipManager(_FakeClient([malformed]))
        calls = []

        async def operation(relationship):
            calls.append(relationship)

        with patch("worker.relationships.manager.emit_line") as emit:
            with self.assertRaises(ValueError):
                await manager._run_bulk(
                    relationships=[malformed],
                    operation=operation,
                    desired_remaining_types=(discord.RelationshipType.friend,),
                    desired_result_type=None,
                    not_found_is_success=True,
                    prefix="REL_X",
                    request_id=None,
                    config=_FastBulkConfig(),
                )

        self.assertEqual(calls, [])
        emit.assert_not_called()

    def test_progress_label_cannot_inject_protocol_fields(self):
        self.assertEqual(
            RelationshipManager._ipc_label("nome:ruim\nquebra||REQ:"),
            "nome：ruim quebraREQ：",
        )


if __name__ == "__main__":
    unittest.main()
