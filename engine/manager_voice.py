import json


class VoiceMixin:
    @staticmethod
    def _voice_error(code: str, message: str) -> dict:
        return {"ok": False, "code": code, "message": message}

    @staticmethod
    def _voice_command(prefix: str, payload: dict) -> str:
        return prefix + json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )

    async def _voice_action(
        self, user_id: int, command: str, *, timeout: float = 30.0
    ) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._voice_error("NOT_CONNECTED", "Conta nao autenticada.")
        result = await process.send_and_wait_json(
            command, "VOICE_RESULT:", timeout=timeout
        )
        if isinstance(result, dict):
            return result
        return self._voice_error(
            "NO_RESPONSE", "O worker nao retornou uma resposta de voz valida."
        )

    async def voice_join(self, user_id: int, channel_id: int) -> dict:
        return await self._voice_action(
            user_id,
            self._voice_command(
                "VOICE_JOIN_JSON:", {"channel_id": int(channel_id)}
            ),
        )

    async def voice_leave(self, user_id: int) -> dict:
        return await self._voice_action(user_id, "VOICE_LEAVE", timeout=20.0)

    async def voice_follow(self, user_id: int, target_id: int) -> dict:
        return await self._voice_action(
            user_id,
            self._voice_command(
                "VOICE_FOLLOW_JSON:", {"target_id": int(target_id)}
            ),
        )

    async def voice_unfollow(self, user_id: int) -> dict:
        return await self._voice_action(user_id, "VOICE_UNFOLLOW", timeout=20.0)

    async def voice_status(self, user_id: int) -> dict:
        process = self._get_proc(user_id)
        if not process:
            return self._voice_error("NOT_CONNECTED", "Conta nao autenticada.")
        result = await process.send_and_wait_json(
            "VOICE_STATUS", "VOICE_STATUS_RESULT:", timeout=10.0
        )
        if isinstance(result, dict):
            return result
        return self._voice_error(
            "NO_RESPONSE", "O worker nao retornou o estado de voz."
        )
