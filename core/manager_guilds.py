"""
Mixin de operações de guild para o SelfbotManager.
"""


class GuildMixin:
    """Métodos do SelfbotManager relacionados a guilds/servidores."""

    async def get_guild_stats(self, user_id: int):
        p = self._get_proc(user_id)
        return await p.send_and_wait_json("GUILD_STATS", "GUILD_STATS_RESULT:") if p else None

    async def start_guild_cleanup(self, user_id: int, action: str, update_callback=None):
        wl = ",".join(map(str, self.user_guild_whitelists.get(user_id, [])))
        cmd = "GUILD_LEAVE_ALL" if action == "LEAVE" else "GUILD_MUTE_ALL"
        prefix = "GUILD_LEAVE_" if action == "LEAVE" else "GUILD_MUTE_"
        return await self._run_bulk_stream(
            user_id, f"{cmd}:{wl}", [prefix], update_callback, timeout=600
        )
