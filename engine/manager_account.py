import json


class AccountMixin:
    """Status personalizado, sessões autorizadas, menções e leitura da conta."""

    @staticmethod
    def _account_error(code: str, message: str) -> dict:
        return {"ok": False, "code": code, "message": message}

    async def _account_action(
        self, user_id: int, command: str, *, timeout: float = 30.0
    ) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._account_error("NOT_CONNECTED", "Conta não autenticada.")
        result = await process.send_and_wait_json(
            command, "ACCOUNT_RESULT:", timeout=timeout
        )
        if isinstance(result, dict):
            return result
        return self._account_error(
            "NO_RESPONSE", "O worker não respondeu a tempo."
        )

    @staticmethod
    def _account_command(prefix: str, payload: dict) -> str:
        return prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    async def account_status(self, user_id: int) -> dict:
        return await self._account_action(user_id, "ACCOUNT_STATUS", timeout=10.0)

    async def account_profile(self, user_id: int, *, refresh: bool = False) -> dict:
        command = "ACCOUNT_PROFILE_REFRESH" if refresh else "ACCOUNT_PROFILE"
        return await self._account_action(user_id, command, timeout=15.0)

    async def account_status_set(self, user_id: int, payload: dict) -> dict:
        return await self._account_action(
            user_id, self._account_command("ACCOUNT_STATUS_SET_JSON:", payload)
        )

    async def account_sessions(self, user_id: int) -> dict:
        return await self._account_action(user_id, "ACCOUNT_SESSIONS")

    async def account_mentions(
        self, user_id: int, *, limit: int = 25, before: int | None = None
    ) -> dict:
        return await self._account_action(
            user_id,
            self._account_command(
                "ACCOUNT_MENTIONS_JSON:",
                {"limit": int(limit), "before": str(before) if before else None},
            ),
        )

    async def account_mentions_delete(self, user_id: int, ids: list[int]) -> dict:
        # Até 100 remoções com pausa entre elas.
        return await self._account_action(
            user_id,
            self._account_command(
                "ACCOUNT_MENTION_DELETE_JSON:", {"ids": [str(i) for i in ids]}
            ),
            timeout=90.0,
        )

    async def account_unread(self, user_id: int) -> dict:
        return await self._account_action(user_id, "ACCOUNT_UNREAD", timeout=10.0)

    async def account_ack_all(self, user_id: int, scope: str = "all") -> dict:
        return await self._account_action(
            user_id,
            self._account_command("ACCOUNT_ACK_ALL_JSON:", {"scope": scope}),
            timeout=180.0,
        )
