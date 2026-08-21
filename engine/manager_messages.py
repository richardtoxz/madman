import json


class MessagesMixin:

    async def _stream_message_cleanup(self, user_id: int, command: str, prefixes: list, update_callback=None):
        p = self._get_proc(user_id)
        if not p:
            return "❌ Não conectado"

        last_result = "Iniciando..."

        async for text in p.send_and_stream(command, prefixes, timeout=600):
            if text.startswith("IPC_ERROR:"):
                last_result = "ERRO: Falha de IPC com o runner"
                break

            if text.startswith("TIMEOUT:"):
                last_result = "ERRO: Timeout"
                break

            if text.startswith("INFO:CANAL_DETECTADO:"):
                if update_callback:
                    await update_callback(None, None, "CHANNEL_DETECTED", text.replace("INFO:CANAL_DETECTADO:", ""))

            elif text.startswith("STATUS:CONTANDO") or text.startswith("STATUS:COUNTING"):
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

            elif "RESULT:" in text:
                parts = text.split(":", 2)
                if len(parts) == 3:
                    last_result = f"{parts[1]}:{parts[2]}"
                else:
                    last_result = parts[-1]
                break

        return last_result

    async def send_command(self, user_id: int, target_id: int, update_callback=None):
        prefixes = [
            "INFO:", "STATUS:", "TOTAL:", "COUNT_PROGRESS:",
            "MSG_CLEAN_", "MSG_DEL_", "PROGRESS:", "RESULT:",
        ]
        return await self._stream_message_cleanup(user_id, f"TARGET:{target_id}", prefixes, update_callback)

    async def clean_channel(self, user_id: int, channel_id: int, update_callback=None):
        return await self.send_command(user_id, channel_id, update_callback)

    async def clean_all_dms(self, user_id: int, update_callback=None):
        p = self._get_proc(user_id)
        if not p:
            return "❌ Não conectado"

        prefixes = ["MSG_CLEAN_ALL_DMS_", "MSG_DEL_", "MSG_CLEAN_", "PROGRESS:"]
        last_result = "Iniciando..."

        async for text in p.send_and_stream("MSG_CLEAN_ALL_DMS", prefixes, timeout=3600):
            if text.startswith("IPC_ERROR:"):
                last_result = "ERRO: Falha de IPC com o runner"
                break

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

    async def clean_guild_messages(self, user_id: int, guild_id: int, update_callback=None):
        prefixes = ["INFO:", "STATUS:", "TOTAL:", "COUNT_PROGRESS:", "MSG_CLEAN_GUILD_", "MSG_DEL_", "RESULT:", "PROGRESS:"]
        return await self._stream_message_cleanup(user_id, f"MSG_CLEAN_GUILD:{guild_id}", prefixes, update_callback)

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

    async def scan_guilds_for_messages(self, user_id: int, update_callback=None):
        p = self._get_proc(user_id)
        if not p:
            return []

        prefixes = ["MSG_SCAN_GUILDS_"]
        results = []

        async for text in p.send_and_stream("MSG_SCAN_GUILDS", prefixes, timeout=600):
            if text.startswith("IPC_ERROR:"):
                break

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

    async def list_open_dms(self, user_id: int):
        p = self._get_proc(user_id)
        if not p:
            return []
        
        raw = await p.send_and_wait("DM_LIST", "DM_LIST_RESULT:", timeout=15.0)
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                pass
        return []

    async def close_single_dm(self, user_id: int, channel_id: int):
        p = self._get_proc(user_id)
        if not p:
            return False, "Nao conectado"
        raw = await p.send_and_wait(
            f"DM_CLOSE:{channel_id}", "DM_CLOSE_RESULT:", timeout=30.0
        )
        if raw and raw.startswith("OK:"):
            return True, "DM fechada"
        return False, (raw or "Tempo limite ao fechar a DM")

    async def close_all_dms(self, user_id: int, update_callback=None):
        return await self._run_bulk_stream(
            user_id, "DM_CLOSE_ALL", ["DM_CLOSE_ALL_"], update_callback, timeout=1800
        )

    async def leave_all_group_dms(self, user_id: int, update_callback=None):
        return await self._run_bulk_stream(
            user_id,
            "GROUP_DM_LEAVE_ALL",
            ["GROUP_DM_LEAVE_ALL_"],
            update_callback,
            timeout=1800,
        )
