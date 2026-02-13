"""
Mixin de operações de relacionamento para o SelfbotManager.
"""
import json


class RelationshipMixin:
    """Métodos do SelfbotManager relacionados a friends, blocked e requests."""

    async def get_rel_stats(self, user_id: int):
        p = self._get_proc(user_id)
        return await p.send_and_wait_json("REL_STATS", "REL_STATS_RESULT:") if p else None

    async def get_friends_list(self, user_id: int):
        p = self._get_proc(user_id)
        return (await p.send_and_wait_json("REL_FRIENDS_LIST", "REL_FRIENDS_RESULT:")) or [] if p else []

    async def get_pending_requests(self, user_id: int):
        p = self._get_proc(user_id)
        return (
            (await p.send_and_wait_json("REL_PENDING_LIST", "REL_PENDING_RESULT:"))
            or {"incoming": [], "outgoing": []}
            if p else {"incoming": [], "outgoing": []}
        )

    async def get_blocked_list(self, user_id: int):
        p = self._get_proc(user_id)
        return (await p.send_and_wait_json("REL_BLOCKED_LIST", "REL_BLOCKED_RESULT:")) or [] if p else []

    async def send_friend_request(self, user_id: int, username: str):
        p = self._get_proc(user_id)
        if not p:
            raise Exception("Não conectado")
        raw = await p.send_and_wait(f"REL_ADD_FRIEND:{username}", "REL_ADD_FRIEND_RESULT:")
        if raw and raw.startswith("OK"):
            return True
        raise Exception(raw or "Sem resposta")

    async def set_friend_nickname(self, user_id: int, target_id: int, nickname: str = None):
        p = self._get_proc(user_id)
        if not p:
            return False
        nick_str = nickname if nickname else ""
        raw = await p.send_and_wait(f"REL_NICKNAME:{target_id}:{nick_str}", "REL_NICKNAME_RESULT:")
        return raw == "OK" if raw else False

    async def block_user(self, user_id: int, target_id: int):
        p = self._get_proc(user_id)
        if not p:
            raise Exception("Não conectado")
        raw = await p.send_and_wait(f"REL_BLOCK:{target_id}", "REL_BLOCK_RESULT:")
        if raw and raw.startswith("OK"):
            return True
        raise Exception(raw or "Sem resposta")

    async def unblock_user(self, user_id: int, target_id: int):
        p = self._get_proc(user_id)
        if not p:
            raise Exception("Não conectado")
        raw = await p.send_and_wait(f"REL_UNBLOCK:{target_id}", "REL_UNBLOCK_RESULT:")
        if raw and raw.startswith("OK"):
            return True
        raise Exception(raw or "Sem resposta")

    async def start_rel_cleanup(self, user_id: int, update_callback=None):
        wl = ",".join(map(str, self.user_whitelists.get(user_id, [])))
        return await self._run_bulk_stream(
            user_id, f"REL_REMOVE_ALL:{wl}",
            ["REL_CLEANUP_"], update_callback
        )

    async def unblock_all(self, user_id: int):
        return await self._run_bulk_stream(user_id, "REL_UNBLOCK_ALL", ["REL_UNBLOCK_"])

    async def accept_all_requests(self, user_id: int):
        return await self._run_bulk_stream(user_id, "REL_ACCEPT_ALL", ["REL_ACCEPT_"])

    async def deny_all_requests(self, user_id: int):
        return await self._run_bulk_stream(user_id, "REL_DENY_ALL", ["REL_DENY_"])
