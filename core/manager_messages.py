"""
Mixin de operações de mensagens para o SelfbotManager.
"""
import json


class MessagesMixin:
    """Métodos do SelfbotManager relacionados a limpeza e gerenciamento de mensagens."""

    # ── Limpeza em 1 canal (TARGET) ──────────────────────────

    async def send_command(self, user_id: int, target_id: int, update_callback=None):
        """Limpa msgs em 1 canal. Usa o processo principal via stream."""
        p = self._get_proc(user_id)
        if not p:
            return "❌ Não logado"

        prefixes = [
            "INFO:", "STATUS:", "TOTAL:", "COUNT_PROGRESS:",
            "MSG_CLEAN_", "MSG_DEL_", "PROGRESS:", "RESULT:",
        ]

        last_result = "Processo iniciado..."
        total_found = 0

        async for text in p.send_and_stream(f"TARGET:{target_id}", prefixes, timeout=600):
            if text.startswith("TIMEOUT:"):
                last_result = "Timeout"
                break

            if text.startswith("INFO:CANAL_DETECTADO:"):
                name = text.replace("INFO:CANAL_DETECTADO:", "")
                if update_callback:
                    await update_callback(None, None, "CHANNEL_DETECTED", name)

            elif text.startswith("STATUS:CONTANDO") or text.startswith("STATUS:COUNTING"):
                if update_callback:
                    await update_callback(None, None, "COUNTING", None)

            elif text.startswith("TOTAL:"):
                try:
                    total_found = int(text.split(":")[1])
                    if update_callback:
                        await update_callback(0, total_found, "WAITING_START", None)
                except Exception:
                    pass

            elif text.startswith("STATUS:WAITING_START"):
                if update_callback:
                    await update_callback(None, None, "WAITING_START", None)

            elif "PROGRESS:" in text:
                parts = text.split(":")
                nums = [p for p in parts if p.isdigit()]
                if len(nums) >= 2:
                    try:
                        curr, total = int(nums[-2]), int(nums[-1])
                        if update_callback:
                            await update_callback(curr, total, "CLEANING", None)
                    except Exception:
                        pass

            elif text.startswith("STATUS:PAUSED"):
                if update_callback:
                    await update_callback(None, None, "PAUSED", None)

            elif text.startswith("STATUS:STARTED"):
                if update_callback:
                    await update_callback(None, None, "STARTED", None)

            elif text.startswith("INFO:TARGET_TYPE:"):
                if update_callback:
                    await update_callback(None, None, "TARGET_TYPE", text.replace("INFO:TARGET_TYPE:", ""))

            elif "RATELIMIT" in text:
                if update_callback:
                    await update_callback(None, None, "RATE_LIMITED", text)

            elif "BATCH_PAUSE" in text:
                if update_callback:
                    await update_callback(None, None, "BATCH_PAUSE", text)

            elif text.startswith("MSG_CLEAN_RESULT:") or text.startswith("RESULT:"):
                parts = text.split(":", 2)
                last_result = parts[-1] if len(parts) >= 2 else text
                break

        return last_result

    async def clean_channel(self, user_id: int, channel_id: int, update_callback=None):
        return await self.send_command(user_id, channel_id, update_callback)

    # ── Limpeza em TODAS as DMs ──────────────────────────────

    async def clean_all_dms(self, user_id: int, update_callback=None):
        p = self._get_proc(user_id)
        if not p:
            return "❌ Não conectado"

        prefixes = ["MSG_CLEAN_ALL_DMS_", "MSG_DEL_", "MSG_CLEAN_"]
        last_result = "Iniciando..."

        async for text in p.send_and_stream("MSG_CLEAN_ALL_DMS", prefixes, timeout=3600):
            if text.startswith("TIMEOUT:"):
                last_result = "ERRO: Timeout"
                break

            if text.startswith("MSG_CLEAN_ALL_DMS_START:"):
                total = int(text.split(":")[1])
                if update_callback:
                    await update_callback(0, total, "ALL_DMS_START", f"{total} DMs")

            elif text.startswith("MSG_CLEAN_ALL_DMS_CHANNEL:"):
                parts = text.split(":")
                if len(parts) >= 4:
                    idx, total, name = int(parts[1]), int(parts[2]), parts[3]
                    if update_callback:
                        await update_callback(idx, total, "ALL_DMS_CHANNEL", name)

            elif text.startswith("MSG_CLEAN_ALL_DMS_FOUND:"):
                parts = text.split(":")
                if len(parts) >= 3:
                    if update_callback:
                        await update_callback(None, None, "ALL_DMS_FOUND", f"{parts[1]}: {parts[2]} msgs")

            elif text.startswith("MSG_CLEAN_ALL_DMS_SKIP:"):
                pass

            elif "PROGRESS:" in text:
                parts = text.split(":")
                nums = [x for x in parts if x.isdigit()]
                if len(nums) >= 2:
                    curr, total = int(nums[-2]), int(nums[-1])
                    if update_callback:
                        await update_callback(curr, total, "CLEANING", None)

            elif text.startswith("MSG_CLEAN_ALL_DMS_RESULT:"):
                last_result = text.replace("MSG_CLEAN_ALL_DMS_RESULT:", "")
                break

            elif "RATELIMIT" in text:
                if update_callback:
                    await update_callback(None, None, "RATE_LIMITED", text)

        return last_result

    # ── Limpeza em Guild inteira ──────────────────────────────

    async def clean_guild_messages(self, user_id: int, guild_id: int, update_callback=None):
        p = self._get_proc(user_id)
        if not p:
            return "❌ Não conectado"

        prefixes = ["INFO:", "STATUS:", "TOTAL:", "COUNT_PROGRESS:", "MSG_CLEAN_GUILD_", "MSG_DEL_", "RESULT:"]
        last_result = "Iniciando..."

        async for text in p.send_and_stream(f"MSG_CLEAN_GUILD:{guild_id}", prefixes, timeout=600):
            if text.startswith("TIMEOUT:"):
                last_result = "ERRO: Timeout"
                break

            if text.startswith("INFO:CANAL_DETECTADO:"):
                if update_callback:
                    await update_callback(None, None, "CHANNEL_DETECTED", text.replace("INFO:CANAL_DETECTADO:", ""))

            elif text.startswith("STATUS:CONTANDO"):
                if update_callback:
                    await update_callback(None, None, "COUNTING", None)

            elif text.startswith("TOTAL:"):
                try:
                    total = int(text.split(":")[1])
                    if update_callback:
                        await update_callback(0, total, "WAITING_START", None)
                except Exception:
                    pass

            elif text.startswith("STATUS:WAITING_START"):
                if update_callback:
                    await update_callback(None, None, "WAITING_START", None)

            elif "PROGRESS:" in text:
                parts = text.split(":")
                nums = [x for x in parts if x.isdigit()]
                if len(nums) >= 2:
                    curr, total = int(nums[-2]), int(nums[-1])
                    if update_callback:
                        await update_callback(curr, total, "CLEANING", None)

            elif "RATELIMIT" in text:
                if update_callback:
                    await update_callback(None, None, "RATE_LIMITED", text)

            elif "BATCH_PAUSE" in text:
                if update_callback:
                    await update_callback(None, None, "BATCH_PAUSE", text)

            elif text.startswith("MSG_CLEAN_GUILD_RESULT:") or text.startswith("RESULT:"):
                parts = text.split(":", 2)
                last_result = parts[-1] if len(parts) >= 2 else text
                break

        return last_result

    # ── Deletar por ID ───────────────────────────────────────

    async def delete_message_by_id(self, user_id: int, channel_id: int, message_id: int):
        p = self._get_proc(user_id)
        if not p:
            return False, "Não conectado"
        raw = await p.send_and_wait(
            f"MSG_DELETE_ID:{channel_id}:{message_id}", "MSG_DELETE_ID_RESULT:", timeout=10.0
        )
        if raw and raw.startswith("OK"):
            return True, "Mensagem deletada"
        elif raw and raw.startswith("NOT_FOUND"):
            return True, "Mensagem já não existe"
        elif raw and raw.startswith("FORBIDDEN"):
            return False, "Sem permissão"
        return False, raw or "Sem resposta"

    # ── Contar msgs ──────────────────────────────────────────

    async def count_messages(self, user_id: int, channel_id: int):
        p = self._get_proc(user_id)
        if not p:
            return 0, "Não conectado"
        raw = await p.send_and_wait(f"MSG_COUNT:{channel_id}", "MSG_COUNT_RESULT:", timeout=120.0)
        if raw and raw.startswith("OK:"):
            parts = raw.split(":")
            count = int(parts[1]) if len(parts) >= 2 else 0
            name = parts[2] if len(parts) >= 3 else "Canal"
            return count, name
        return 0, raw or "Erro"

    # ── Scan guilds ──────────────────────────────────────────

    async def scan_guilds_for_messages(self, user_id: int, update_callback=None):
        p = self._get_proc(user_id)
        if not p:
            return []

        prefixes = ["MSG_SCAN_GUILDS_"]
        results = []

        async for text in p.send_and_stream("MSG_SCAN_GUILDS", prefixes, timeout=600):
            if text.startswith("TIMEOUT:"):
                break

            if text.startswith("MSG_SCAN_GUILDS_START:"):
                total = int(text.split(":")[1])
                if update_callback:
                    await update_callback(0, total, "SCAN_START", "")

            elif text.startswith("MSG_SCAN_GUILDS_CHECKING:"):
                parts = text.split(":")
                if len(parts) >= 4:
                    if update_callback:
                        await update_callback(int(parts[1]), int(parts[2]), "SCAN_CHECKING", parts[3])

            elif text.startswith("MSG_SCAN_GUILDS_FOUND:"):
                parts = text.split(":")
                if len(parts) >= 3 and update_callback:
                    await update_callback(None, None, "SCAN_FOUND", parts[1])

            elif text.startswith("MSG_SCAN_GUILDS_RESULT:"):
                data = text.replace("MSG_SCAN_GUILDS_RESULT:", "")
                try:
                    results = json.loads(data)
                except json.JSONDecodeError:
                    pass
                break

        return results

    # ── Data Package ─────────────────────────────────────────

    async def delete_from_data_package(self, user_id: int, entries: list, update_callback=None):
        p = self._get_proc(user_id)
        if not p:
            return "❌ Não conectado"

        payload = json.dumps(entries)
        prefixes = ["MSG_DATAPACK_"]
        last_result = "Iniciando..."

        async for text in p.send_and_stream(f"MSG_DATAPACK:{payload}", prefixes, timeout=3600):
            if text.startswith("TIMEOUT:"):
                last_result = "ERRO: Timeout"
                break

            if text.startswith("MSG_DATAPACK_START:"):
                total = int(text.split(":")[1])
                if update_callback:
                    await update_callback(0, total, "DATAPACK_START", "")

            elif text.startswith("MSG_DATAPACK_PROGRESS:"):
                parts = text.split(":")
                if len(parts) >= 3:
                    curr, total = int(parts[1]), int(parts[2])
                    if update_callback:
                        await update_callback(curr, total, "CLEANING", None)

            elif text.startswith("MSG_DATAPACK_RATELIMIT:"):
                if update_callback:
                    await update_callback(None, None, "RATE_LIMITED", text)

            elif text.startswith("MSG_DATAPACK_RESULT:"):
                last_result = text.replace("MSG_DATAPACK_RESULT:", "")
                break

        return last_result

    # ── DM: Listar ───────────────────────────────────────────

    async def list_dms(self, user_id: int):
        p = self._get_proc(user_id)
        return (await p.send_and_wait_json("DM_LIST", "DM_LIST_RESULT:", timeout=15.0)) or [] if p else []

    # ── DM: Fechar 1 ─────────────────────────────────────────

    async def close_dm(self, user_id: int, channel_id: int):
        p = self._get_proc(user_id)
        if not p:
            raise Exception("Não conectado")
        raw = await p.send_and_wait(f"DM_CLOSE:{channel_id}", "DM_CLOSE_RESULT:")
        if raw and raw.startswith("OK"):
            return True
        raise Exception(raw or "Sem resposta")

    # ── DM: Fechar todas ─────────────────────────────────────

    async def close_all_dms(self, user_id: int, update_callback=None):
        return await self._run_bulk_stream(
            user_id, "DM_CLOSE_ALL", ["DM_CLOSE_ALL_"], update_callback, timeout=300
        )

    # ── Group DM: Sair de 1 ──────────────────────────────────

    async def leave_group_dm(self, user_id: int, channel_id: int):
        p = self._get_proc(user_id)
        if not p:
            raise Exception("Não conectado")
        raw = await p.send_and_wait(f"GROUP_DM_LEAVE:{channel_id}", "GROUP_DM_LEAVE_RESULT:")
        if raw and raw.startswith("OK"):
            return True
        raise Exception(raw or "Sem resposta")

    # ── Group DM: Sair de todos ──────────────────────────────

    async def leave_all_group_dms(self, user_id: int, update_callback=None):
        return await self._run_bulk_stream(
            user_id, "GROUP_DM_LEAVE_ALL", ["GROUP_DM_LEAVE_ALL_"], update_callback, timeout=300
        )
