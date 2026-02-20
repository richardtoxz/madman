"""
Mixin de operações de proteção para o SelfbotManager.
"""
import json


class ProtectionMixin:
    """Métodos do SelfbotManager relacionados a proteções automáticas."""

    async def protection_enable(self, user_id: int) -> str:
        p = self._get_proc(user_id)
        if not p:
            return "Não autenticado."
        result = await p.send_and_wait(
            "PROTECTION_ENABLE", "PROTECTION:", timeout=5.0
        )
        return result if result else "Timeout."

    async def protection_disable(self, user_id: int) -> str:
        p = self._get_proc(user_id)
        if not p:
            return "Não autenticado."
        result = await p.send_and_wait(
            "PROTECTION_DISABLE", "PROTECTION:", timeout=5.0
        )
        return result if result else "Timeout."

    async def protection_status(self, user_id: int) -> dict | None:
        p = self._get_proc(user_id)
        if not p:
            return None
        result = await p.send_and_wait(
            "PROTECTION_STATUS", "PROTECTION:STATUS:", timeout=5.0
        )
        if result and result.startswith("PROTECTION:STATUS:"):
            try:
                return json.loads(result.split(":", 2)[2])
            except (json.JSONDecodeError, IndexError):
                pass
        return None
