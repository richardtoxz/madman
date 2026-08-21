from __future__ import annotations

import json


class ProtectionMixin:
    @staticmethod
    def _protection_error(code: str, message: str) -> dict:
        return {"ok": False, "code": code, "message": message}

    @staticmethod
    def _protection_command(prefix: str, whitelist) -> str:
        parsed = sorted({int(user_id) for user_id in whitelist})
        return prefix + json.dumps(
            {"whitelist": parsed}, ensure_ascii=False, separators=(",", ":")
        )

    async def _protection_action(
        self, user_id: int, command: str, *, timeout: float = 10.0
    ) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._protection_error("NOT_CONNECTED", "Conta nao autenticada.")
        result = await process.send_and_wait_json(
            command, "PROTECTION_RESULT:", timeout=timeout
        )
        if isinstance(result, dict):
            return result
        return self._protection_error(
            "NO_RESPONSE", "O worker nao retornou uma resposta de protecao valida."
        )

    async def protection_enable(self, user_id: int) -> dict:
        whitelist = self.get_whitelist(user_id)
        result = await self._protection_action(
            user_id,
            self._protection_command("PROTECTION_ENABLE_JSON:", whitelist),
        )
        if result.get("ok"):
            self._protection_enabled_users.add(int(user_id))
        return result

    async def protection_disable(self, user_id: int) -> dict:
        result = await self._protection_action(user_id, "PROTECTION_DISABLE")
        if result.get("ok"):
            self._protection_enabled_users.discard(int(user_id))
        return result

    async def protection_configure(self, user_id: int, whitelist) -> dict:
        try:
            parsed = sorted({int(target_id) for target_id in whitelist})
        except (TypeError, ValueError):
            return self._protection_error("INVALID_WHITELIST", "Whitelist invalida.")
        if len(parsed) > 150 or any(
            target_id <= 0 or target_id >= 2**64 for target_id in parsed
        ):
            return self._protection_error(
                "INVALID_WHITELIST", "A whitelist contem IDs invalidos."
            )

        result = await self._protection_action(
            user_id,
            self._protection_command("PROTECTION_CONFIG_JSON:", parsed),
        )
        if result.get("ok"):
            self.user_whitelists[int(user_id)] = parsed
        return result

    async def protection_status(self, user_id: int) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._protection_error("NOT_CONNECTED", "Conta nao autenticada.")
        result = await process.send_and_wait_json(
            "PROTECTION_STATUS", "PROTECTION_STATUS_RESULT:", timeout=10.0
        )
        if isinstance(result, dict):
            if result.get("enabled"):
                self._protection_enabled_users.add(int(user_id))
            else:
                self._protection_enabled_users.discard(int(user_id))
            return result
        return self._protection_error(
            "NO_RESPONSE", "O worker nao retornou o estado das protecoes."
        )

    async def _restore_protection_state(self, user_id: int, process) -> dict | None:
        if int(user_id) not in self._protection_enabled_users:
            return None
        command = self._protection_command(
            "PROTECTION_ENABLE_JSON:", self.get_whitelist(user_id)
        )
        result = await process.send_and_wait_json(
            command, "PROTECTION_RESULT:", timeout=10.0
        )
        return result if isinstance(result, dict) else None
