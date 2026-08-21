from __future__ import annotations

import json


class QuestMixin:
    @staticmethod
    def _quest_error(code: str, message: str) -> dict:
        return {"ok": False, "code": code, "message": message}

    @staticmethod
    def _quest_config_command(webhook_url: str | None) -> str:
        payload = json.dumps(
            {"webhook_url": webhook_url},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"QUEST_CONFIG:{payload}"

    async def quest_list(self, user_id: int) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._quest_error("NOT_CONNECTED", "Conta não autenticada.")
        result = await process.send_and_wait_json(
            "QUEST_LIST", "QUEST_LIST_RESULT:", timeout=15.0
        )
        if isinstance(result, dict):
            return result
        return self._quest_error("NO_RESPONSE", "O worker não retornou as Quests.")

    async def quest_start(self, user_id: int, quest_id: str) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._quest_error("NOT_CONNECTED", "Conta não autenticada.")
        quest_id = str(quest_id).strip()
        if not quest_id.isdigit() or int(quest_id) <= 0:
            return self._quest_error("INVALID_QUEST_ID", "ID de Quest inválido.")
        result = await process.send_and_wait_json(
            f"QUEST_START:{quest_id}", "QUEST_START_RESULT:", timeout=10.0
        )
        if isinstance(result, dict):
            return result
        return self._quest_error("NO_RESPONSE", "O worker não iniciou a Quest.")

    async def quest_cancel(self, user_id: int) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._quest_error("NOT_CONNECTED", "Conta não autenticada.")
        result = await process.send_and_wait_json(
            "QUEST_CANCEL", "QUEST_CANCEL_RESULT:", timeout=10.0
        )
        if isinstance(result, dict):
            return result
        return self._quest_error("NO_RESPONSE", "O worker não confirmou o cancelamento.")

    async def quest_auto(self, user_id: int) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._quest_error("NOT_CONNECTED", "Conta não autenticada.")
        result = await process.send_and_wait_json(
            "QUEST_AUTO", "QUEST_AUTO_RESULT:", timeout=10.0
        )
        if isinstance(result, dict):
            return result
        return self._quest_error("NO_RESPONSE", "O worker não iniciou a fila de Quests.")

    async def quest_config(
        self,
        user_id: int,
        *,
        webhook_url: str | None,
    ) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._quest_error("NOT_CONNECTED", "Conta não autenticada.")
        result = await process.send_and_wait_json(
            self._quest_config_command(webhook_url),
            "QUEST:CONFIG_RESULT:",
            timeout=10.0,
        )
        if isinstance(result, dict):
            if result.get("ok"):
                if webhook_url:
                    self._quest_settings[int(user_id)] = {
                        "webhook_url": webhook_url
                    }
                else:
                    self._quest_settings.pop(int(user_id), None)
            return result
        return self._quest_error("NO_RESPONSE", "O worker não confirmou a configuração.")

    async def _restore_quest_state(self, user_id: int, process) -> dict | None:
        settings = self._quest_settings.get(int(user_id))
        if not settings:
            return None
        result = await process.send_and_wait_json(
            self._quest_config_command(settings["webhook_url"]),
            "QUEST:CONFIG_RESULT:",
            timeout=10.0,
        )
        return result if isinstance(result, dict) else None
