from __future__ import annotations

import json


class TrackerMixin:
    @staticmethod
    def _tracker_error(code: str, message: str) -> dict:
        return {"ok": False, "code": code, "message": message, "enabled": False}

    @staticmethod
    def _tracker_start_command(target_id: int, webhook_url: str) -> str:
        return "TRACKER_START_JSON:" + json.dumps(
            {"target_id": int(target_id), "webhook_url": webhook_url},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    async def tracker_start(self, user_id: int, target_id: int, webhook_url: str) -> dict:
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return self._tracker_error("INVALID_TARGET_ID", "ID do alvo invalido.")
        if target_id <= 0 or target_id >= 2**64 or not isinstance(webhook_url, str):
            return self._tracker_error("INVALID_INPUT", "ID do alvo ou webhook invalido.")
        process = self._get_proc(user_id)
        if not process:
            return self._tracker_error("NOT_CONNECTED", "Conta nao autenticada.")
        result = await process.send_and_wait_json(
            self._tracker_start_command(target_id, webhook_url),
            "TRACKER_RESULT:",
            timeout=15.0,
        )
        if not isinstance(result, dict):
            return self._tracker_error("NO_RESPONSE", "O worker nao confirmou o Tracker.")
        if result.get("ok") and result.get("enabled"):
            self._tracker_settings[int(user_id)] = {
                "target_id": target_id,
                "webhook_url": webhook_url,
            }
        return result

    async def tracker_stop(self, user_id: int) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._tracker_error("NOT_CONNECTED", "Conta nao autenticada.")
        result = await process.send_and_wait_json(
            "TRACKER_STOP", "TRACKER_RESULT:", timeout=10.0
        )
        if not isinstance(result, dict):
            return self._tracker_error("NO_RESPONSE", "O worker nao confirmou a parada.")
        if result.get("ok"):
            self._tracker_settings.pop(int(user_id), None)
        return result

    async def tracker_status(self, user_id: int) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._tracker_error("NOT_CONNECTED", "Conta nao autenticada.")
        result = await process.send_and_wait_json(
            "TRACKER_STATUS", "TRACKER_STATUS_RESULT:", timeout=10.0
        )
        if isinstance(result, dict):
            return result
        return self._tracker_error("NO_RESPONSE", "O worker nao retornou o estado do Tracker.")

    async def _restore_tracker_state(self, user_id: int, process) -> dict | None:
        settings = self._tracker_settings.get(int(user_id))
        if not settings:
            return None
        result = await process.send_and_wait_json(
            self._tracker_start_command(settings["target_id"], settings["webhook_url"]),
            "TRACKER_RESULT:",
            timeout=15.0,
        )
        return result if isinstance(result, dict) else None
