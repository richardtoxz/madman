import json


class QuestMixin:
    async def quest_list(self, user_id: int) -> str:
        p = self._get_proc(user_id)
        if not p:
            return "Não autenticado."
        result = await p.send_and_wait("QUEST_LIST", "QUEST:LIST_END:", timeout=15.0)
        return result if result else "Timeout ao buscar quests."

    async def quest_start(self, user_id: int, quest_id: str) -> bool:
        return await self.send_raw_command(user_id, f"QUEST_START:{quest_id}")

    async def quest_cancel(self, user_id: int) -> bool:
        return await self.send_raw_command(user_id, "QUEST_CANCEL")

    async def quest_auto(self, user_id: int) -> bool:
        return await self.send_raw_command(user_id, "QUEST_AUTO")

    async def quest_config(
        self,
        user_id: int,
        *,
        webhook_url: str | None,
        captcha_api_key: str | None,
    ) -> bool:
        payload = json.dumps(
            {
                "webhook_url": webhook_url,
                "captcha_api_key": captcha_api_key,
            },
            separators=(",", ":"),
        )
        return await self.send_raw_command(user_id, f"QUEST_CONFIG:{payload}")
