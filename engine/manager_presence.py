from __future__ import annotations

import json

from presence_protocol import validate_rich_presence_payload


class PresenceMixin:
    @staticmethod
    def _presence_error(code: str, message: str) -> dict:
        return {"ok": False, "code": code, "message": message}

    @staticmethod
    def _rich_presence_command(payload: dict) -> str:
        return "RICH_PRESENCE_SET_JSON:" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    async def _rich_presence_request(self, process, command: str) -> dict:
        result = await process.send_and_wait_json(command, "RICH_PRESENCE_RESULT:", timeout=15.0)
        if isinstance(result, dict):
            return result
        return self._presence_error("NO_RESPONSE", "O worker nao retornou o estado do Rich Presence.")

    async def rich_presence_status(self, user_id: int) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._presence_error("NOT_CONNECTED", "Conta nao autenticada.")
        return await self._rich_presence_request(process, "RICH_PRESENCE_STATUS")

    async def rich_presence_set(self, user_id: int, enabled, custom) -> dict:
        valid, payload = validate_rich_presence_payload({"enabled": enabled, "custom": custom})
        if not valid:
            return payload
        process = self._get_proc(user_id)
        if not process:
            return self._presence_error("NOT_CONNECTED", "Conta nao autenticada.")
        result = await self._rich_presence_request(process, self._rich_presence_command(payload))
        if result.get("ok"):
            self._rich_presence[int(user_id)] = payload
        return result

    async def _restore_rich_presence_state(self, user_id: int, process) -> dict | None:
        saved = self._rich_presence.get(int(user_id))
        if saved is None:
            return None
        valid, payload = validate_rich_presence_payload(saved)
        if not valid:
            return payload
        result = await process.send_and_wait_json(
            self._rich_presence_command(payload), "RICH_PRESENCE_RESULT:", timeout=15.0
        )
        return result if isinstance(result, dict) else None
