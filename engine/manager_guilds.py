import json


class GuildMixin:

    @staticmethod
    def _guild_fallback(message: str) -> dict:
        return {
            "items": [],
            "total": 0,
            "page": 0,
            "pages": 1,
            "fresh": False,
            "warning": message,
        }

    async def get_guild_stats(self, user_id: int):
        p = self._get_proc(user_id)
        if not p:
            return {
                "total": 0,
                "owners": 0,
                "muted": 0,
                "guild_ids": [],
                "owner_ids": [],
                "fresh": False,
                "warning": "Conta nao autenticada.",
            }
        result = await p.send_and_wait_json(
            "GUILD_STATS", "GUILD_STATS_RESULT:", timeout=30.0
        )
        return result or {
            "total": 0,
            "owners": 0,
            "muted": 0,
            "guild_ids": [],
            "owner_ids": [],
            "fresh": False,
            "warning": "Worker sem resposta.",
        }

    async def get_guilds_page(
        self, user_id: int, page: int = 0, page_size: int = 20
    ):
        p = self._get_proc(user_id)
        if not p:
            return self._guild_fallback("Conta nao autenticada.")
        payload = json.dumps(
            {"page": int(page), "page_size": int(page_size)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = await p.send_and_wait_json(
            f"GUILD_LIST_PAGE_JSON:{payload}",
            "GUILD_LIST_RESULT:",
            timeout=30.0,
        )
        return result or self._guild_fallback("Worker sem resposta.")

    async def start_guild_cleanup(self, user_id: int, action: str, update_callback=None):
        action = str(action).upper()
        if action not in {"LEAVE", "MUTE"}:
            return "ABORTADO:acao de servidor invalida"
        whitelist = sorted(
            {int(guild_id) for guild_id in self.user_guild_whitelists.get(user_id, [])}
        )
        cmd = "GUILD_LEAVE_ALL_JSON" if action == "LEAVE" else "GUILD_MUTE_ALL_JSON"
        prefix = "GUILD_LEAVE_" if action == "LEAVE" else "GUILD_MUTE_"
        payload = json.dumps(
            {"whitelist": whitelist}, ensure_ascii=False, separators=(",", ":")
        )
        return await self._run_bulk_stream(
            user_id, f"{cmd}:{payload}", [prefix], update_callback, timeout=7200
        )
