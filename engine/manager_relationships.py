import json


class RelationshipMixin:
    async def get_rel_stats(self, user_id: int):
        p = self._get_proc(user_id)
        return (
            await p.send_and_wait_json("REL_STATS", "REL_STATS_RESULT:") if p else None
        )

    async def get_friends_list(
        self, user_id: int, page: int = 0, page_size: int = 20
    ):
        p = self._get_proc(user_id)
        command = self._json_command(
            "REL_FRIENDS_PAGE_JSON:",
            {"page": int(page), "page_size": int(page_size)},
        )
        return (
            (await p.send_and_wait_json(command, "REL_FRIENDS_RESULT:"))
            or {
                "items": [],
                "total": 0,
                "page": 0,
                "pages": 1,
                "fresh": False,
                "warning": "Worker sem resposta.",
            }
            if p
            else {
                "items": [],
                "total": 0,
                "page": 0,
                "pages": 1,
                "fresh": False,
                "warning": "Conta nao autenticada.",
            }
        )

    async def get_pending_requests(
        self, user_id: int, page: int = 0, page_size: int = 10
    ):
        p = self._get_proc(user_id)
        command = self._json_command(
            "REL_PENDING_PAGE_JSON:",
            {"page": int(page), "page_size": int(page_size)},
        )
        return (
            (await p.send_and_wait_json(command, "REL_PENDING_RESULT:"))
            or {
                "incoming": [],
                "outgoing": [],
                "incoming_total": 0,
                "outgoing_total": 0,
                "page": 0,
                "pages": 1,
                "fresh": False,
                "warning": "Worker sem resposta.",
            }
            if p
            else {
                "incoming": [],
                "outgoing": [],
                "incoming_total": 0,
                "outgoing_total": 0,
                "page": 0,
                "pages": 1,
                "fresh": False,
                "warning": "Conta nao autenticada.",
            }
        )

    async def get_blocked_list(
        self, user_id: int, page: int = 0, page_size: int = 20
    ):
        p = self._get_proc(user_id)
        command = self._json_command(
            "REL_BLOCKED_PAGE_JSON:",
            {"page": int(page), "page_size": int(page_size)},
        )
        return (
            (await p.send_and_wait_json(command, "REL_BLOCKED_RESULT:"))
            or {
                "items": [],
                "total": 0,
                "page": 0,
                "pages": 1,
                "fresh": False,
                "warning": "Worker sem resposta.",
            }
            if p
            else {
                "items": [],
                "total": 0,
                "page": 0,
                "pages": 1,
                "fresh": False,
                "warning": "Conta nao autenticada.",
            }
        )

    async def _relationship_action(
        self, user_id: int, command: str, response_prefix: str
    ) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return {
                "ok": False,
                "code": "NOT_CONNECTED",
                "message": "Conta nao autenticada.",
            }
        result = await process.send_and_wait_json(command, response_prefix, timeout=30.0)
        if isinstance(result, dict):
            return result
        return {
            "ok": False,
            "code": "NO_RESPONSE",
            "message": "O worker nao retornou uma resposta valida.",
        }

    @staticmethod
    def _json_command(prefix: str, payload: dict) -> str:
        return prefix + json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )

    async def send_friend_request(self, user_id: int, username: str):
        return await self._relationship_action(
            user_id,
            self._json_command("REL_ADD_FRIEND_JSON:", {"username": username}),
            "REL_ADD_FRIEND_RESULT:",
        )

    async def set_friend_nickname(
        self, user_id: int, target_id: int, nickname: str = None
    ):
        return await self._relationship_action(
            user_id,
            self._json_command(
                "REL_NICKNAME_JSON:",
                {"target_id": int(target_id), "nickname": nickname},
            ),
            "REL_NICKNAME_RESULT:",
        )

    async def block_user(self, user_id: int, target_id: int):
        return await self._relationship_action(
            user_id,
            self._json_command("REL_BLOCK_JSON:", {"target_id": int(target_id)}),
            "REL_BLOCK_RESULT:",
        )

    async def unblock_user(self, user_id: int, target_id: int):
        return await self._relationship_action(
            user_id,
            self._json_command("REL_UNBLOCK_JSON:", {"target_id": int(target_id)}),
            "REL_UNBLOCK_RESULT:",
        )

    async def start_rel_cleanup(self, user_id: int, update_callback=None):
        wl = ",".join(map(str, self.user_whitelists.get(user_id, [])))
        return await self._run_bulk_stream(
            user_id,
            f"REL_REMOVE_ALL:{wl}",
            ["REL_CLEANUP_"],
            update_callback,
            timeout=7200,
        )

    async def unblock_all(self, user_id: int, update_callback=None):
        return await self._run_bulk_stream(
            user_id,
            "REL_UNBLOCK_ALL",
            ["REL_UNBLOCK_"],
            update_callback,
            timeout=7200,
        )

    async def accept_all_requests(self, user_id: int, update_callback=None):
        return await self._run_bulk_stream(
            user_id,
            "REL_ACCEPT_ALL",
            ["REL_ACCEPT_"],
            update_callback,
            timeout=7200,
        )

    async def deny_all_requests(self, user_id: int, update_callback=None):
        return await self._run_bulk_stream(
            user_id,
            "REL_DENY_ALL",
            ["REL_DENY_"],
            update_callback,
            timeout=7200,
        )
