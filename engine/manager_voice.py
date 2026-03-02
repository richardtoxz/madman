class VoiceMixin:

    async def voice_join(self, user_id: int, channel_id: int) -> str:
        p = self._get_proc(user_id)
        if not p:
            return "Não autenticado."
        result = await p.send_and_wait(
            f"VOICE_JOIN:{channel_id}", "VOICE_STATUS:", timeout=10.0
        )
        return result.split(":", 1)[-1] if result else "Timeout ao conectar."

    async def voice_leave(self, user_id: int) -> str:
        p = self._get_proc(user_id)
        if not p:
            return "Não autenticado."
        result = await p.send_and_wait("VOICE_LEAVE", "VOICE_STATUS:", timeout=10.0)
        return result.split(":", 1)[-1] if result else "Timeout ao desconectar."

    async def voice_follow(self, user_id: int, target_id: int) -> str:
        p = self._get_proc(user_id)
        if not p:
            return "Não autenticado."
        result = await p.send_and_wait(
            f"VOICE_FOLLOW:{target_id}", "VOICE_STATUS:", timeout=10.0
        )
        return result.split(":", 1)[-1] if result else "Timeout ao ativar follow."

    async def voice_unfollow(self, user_id: int) -> str:
        p = self._get_proc(user_id)
        if not p:
            return "Não autenticado."
        result = await p.send_and_wait(
            "VOICE_UNFOLLOW", "VOICE_STATUS:", timeout=10.0
        )
        return result.split(":", 1)[-1] if result else "Timeout ao desativar follow."
