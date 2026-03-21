import asyncio
import os
import sys
import logging

from engine.process import SelfbotProcess
from engine.manager_relationships import RelationshipMixin
from engine.manager_guilds import GuildMixin
from engine.manager_messages import MessagesMixin
from engine.manager_voice import VoiceMixin
from engine.manager_protection import ProtectionMixin
from engine.manager_quest import QuestMixin

log = logging.getLogger(__name__)


class SelfbotManager(
    RelationshipMixin,
    GuildMixin,
    MessagesMixin,
    VoiceMixin,
    ProtectionMixin,
    QuestMixin,
):
    IDENTIFY_COOLDOWN = 5.5

    def __init__(self):
        self.active_processes: dict[int, SelfbotProcess] = {}
        self._session_tokens: dict[int, str] = {}
        self._supervisor_tasks: dict[int, asyncio.Task] = {}
        self._manual_disconnects: set[int] = set()
        self.user_whitelists: dict[int, list[int]] = {}
        self._identify_lock = asyncio.Lock()
        self._last_identify_at: float = 0.0
        self._bulk_semaphore = asyncio.Semaphore(3)
        self.user_guild_whitelists: dict[int, list[int]] = {}

    async def authenticate(self, user_id: int, token: str) -> tuple[bool, str]:
        if not token or not token.strip():
            return False, "Token inválido. Forneça um token válido."
        token = token.strip()
        if user_id in self.active_processes:
            proc = self.active_processes[user_id]
            if proc.is_alive:
                return False, "Selfbot já está autenticado e ativo."
            log.warning("Processo morto encontrado para user %s, limpando", user_id)
            await self._cleanup_process(user_id)

        self._manual_disconnects.discard(user_id)
        self._session_tokens[user_id] = token

        ok, sp_or_error = await self._spawn_ready_process(token)
        if not ok:
            self._session_tokens.pop(user_id, None)
            return False, sp_or_error

        sp = sp_or_error
        self.active_processes[user_id] = sp
        self._start_supervisor(user_id)

        log.info("Selfbot autenticado: user_id=%s, username=%s", user_id, sp.username)
        return True, f"Autenticado como **{sp.username}**"

    async def _spawn_ready_process(
        self, token: str
    ) -> tuple[bool, SelfbotProcess | str]:
        project_root = os.path.dirname(os.path.dirname(__file__))
        worker_package_dir = os.path.join(project_root, "worker")
        if not os.path.exists(worker_package_dir):
            return False, f"Pacote do selfbot não encontrado: {worker_package_dir}"

        async with self._identify_lock:
            now = asyncio.get_running_loop().time()
            wait = self.IDENTIFY_COOLDOWN - (now - self._last_identify_at)
            if wait > 0:
                log.info(
                    "LOGIN_STAGGER: Aguardando %.1fs para o próximo Identify...", wait
                )
                await asyncio.sleep(wait)

            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "worker.run_selfbot",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=project_root,
                )
                process.stdin.write(f"{token}\n".encode())
                await process.stdin.drain()
                self._last_identify_at = asyncio.get_running_loop().time()
            except OSError as e:
                return False, f"Falha ao criar subprocess: {e}"

        sp = SelfbotProcess(process, "sync_presence")
        sp.start_reader()

        ready = await self._wait_ready(sp, timeout=30.0)
        if not ready:
            await sp.close()
            return False, "Selfbot não conectou a tempo. Verifique se o token é válido."

        return True, sp

    async def disconnect(self, user_id: int) -> tuple[bool, str]:
        proc = self.active_processes.get(user_id)
        has_session = user_id in self._session_tokens
        if not proc and not has_session:
            return False, "Nenhum selfbot ativo para desconectar."

        self._manual_disconnects.add(user_id)
        username = proc.username if proc else "desconhecido"
        await self._cleanup_process(user_id)
        self._stop_supervisor(user_id)
        self._session_tokens.pop(user_id, None)

        self.user_whitelists.pop(user_id, None)
        self.user_guild_whitelists.pop(user_id, None)

        log.info("Selfbot desconectado: user_id=%s, username=%s", user_id, username)
        return True, "Desconectado com sucesso."

    def is_authenticated(self, user_id: int) -> bool:
        if user_id in self._session_tokens:
            return True
        proc = self.active_processes.get(user_id)
        return proc is not None and proc.is_alive

    async def control_clean(self, user_id: int, signal: str) -> bool:
        proc = self._get_proc(user_id)
        if proc and proc.is_alive:
            return await proc.send(signal)
        return False

    async def send_raw_command(self, user_id: int, command: str) -> bool:
        proc = self._get_proc(user_id)
        if proc and proc.is_alive:
            return await proc.send(command)
        return False

    async def toggle_whitelist(self, user_id: int, target_id: int) -> bool:
        wl = self.user_whitelists.setdefault(user_id, [])
        if target_id in wl:
            wl.remove(target_id)
            return False
        wl.append(target_id)
        return True

    async def toggle_guild_whitelist(self, user_id: int, guild_id: int) -> bool:
        wl = self.user_guild_whitelists.setdefault(user_id, [])
        if guild_id in wl:
            wl.remove(guild_id)
            return False
        wl.append(guild_id)
        return True

    def get_whitelist(self, user_id: int) -> list[int]:
        return self.user_whitelists.get(user_id, [])

    def get_guild_whitelist(self, user_id: int) -> list[int]:
        return self.user_guild_whitelists.get(user_id, [])

    def _get_proc(self, user_id: int) -> SelfbotProcess | None:
        proc = self.active_processes.get(user_id)
        if proc and not proc.is_alive:
            log.warning("Processo morto detectado para user %s", user_id)
            asyncio.get_running_loop().create_task(self._cleanup_process(user_id))
            return None
        return proc

    async def _cleanup_process(self, user_id: int):
        proc = self.active_processes.pop(user_id, None)
        if proc:
            await proc.close()

    def _start_supervisor(self, user_id: int):
        self._stop_supervisor(user_id)
        self._supervisor_tasks[user_id] = asyncio.create_task(
            self._supervise_process(user_id)
        )

    def _stop_supervisor(self, user_id: int):
        task = self._supervisor_tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()

    async def _supervise_process(self, user_id: int):
        backoff = 2.0

        while user_id in self._session_tokens:
            if user_id in self._manual_disconnects:
                return

            proc = self.active_processes.get(user_id)
            if proc and proc.is_alive:
                try:
                    await proc.process.wait()
                except Exception:
                    pass

            if (
                user_id in self._manual_disconnects
                or user_id not in self._session_tokens
            ):
                return

            dead_proc = self.active_processes.get(user_id)
            if dead_proc and not dead_proc.is_alive:
                dead_proc.emit_event("PROCESS:EXIT:UNEXPECTED")
                self.active_processes.pop(user_id, None)

            token = self._session_tokens.get(user_id)
            if not token:
                return

            log.warning("SELFBOT_AUTO_RESTART:user=%s:backoff=%.1fs", user_id, backoff)
            await asyncio.sleep(backoff)

            ok, sp_or_error = await self._spawn_ready_process(token)
            if ok:
                sp = sp_or_error
                self.active_processes[user_id] = sp
                sp.emit_event("PROCESS:RESTARTED:OK")
                backoff = 2.0
                continue

            log.error(
                "SELFBOT_AUTO_RESTART_FAILED:user=%s:error=%s", user_id, sp_or_error
            )
            backoff = min(backoff * 2, 60.0)

    async def _wait_ready(self, proc: SelfbotProcess, timeout: float = 30.0) -> bool:
        try:
            async with asyncio.timeout(timeout):
                while True:
                    if proc.ready:
                        return True
                    if not proc.is_alive:
                        return False
                    await asyncio.sleep(0.2)
        except asyncio.TimeoutError:
            return False

    async def _run_bulk_stream(
        self,
        user_id: int,
        command: str,
        prefixes: list,
        update_callback=None,
        timeout: float = 600,
    ) -> str:
        proc = self._get_proc(user_id)
        if not proc:
            return "❌ Não autenticado"

        last_result = "Iniciando..."
        done = False

        async with self._bulk_semaphore:
            async for text in proc.send_and_stream(command, prefixes, timeout):
                if done or text.startswith("TIMEOUT:"):
                    last_result = "ERRO: Timeout" if not done else last_result
                    break

                if text.startswith("IPC_ERROR:"):
                    last_result = "ERRO: Falha de IPC com o runner"
                    break

                for prefix in prefixes:
                    if text.startswith(f"{prefix}START:"):
                        try:
                            total = int(text.split(":")[1])
                            if update_callback:
                                await update_callback(0, total, "WAITING_START", "")
                        except Exception:
                            pass

                    elif text.startswith(f"{prefix}PROGRESS:"):
                        parts = text.split(":")
                        if len(parts) >= 3:
                            try:
                                curr, total = int(parts[1]), int(parts[2])
                                name = parts[3] if len(parts) >= 4 else ""
                                if update_callback:
                                    await update_callback(curr, total, "CLEANING", name)
                            except Exception:
                                pass

                    elif text.startswith(f"{prefix}BATCH_PAUSE:"):
                        parts = text.split(":")
                        if len(parts) >= 4:
                            try:
                                secs, curr, total = (
                                    parts[1],
                                    int(parts[2]),
                                    int(parts[3]),
                                )
                                if update_callback:
                                    await update_callback(
                                        curr, total, "BATCH_PAUSE", f"Pausa ({secs}s)"
                                    )
                            except Exception:
                                pass

                    elif "RATELIMIT" in text and prefix in text:
                        if update_callback:
                            await update_callback(None, None, "RATE_LIMITED", text)

                    elif text.startswith(f"{prefix}RESULT:"):
                        last_result = (
                            text.split(":", 2)[-1] if text.count(":") >= 2 else text
                        )
                        done = True
                        break

                    elif text.startswith(f"{prefix}CANCELLED"):
                        last_result = "CANCELADO"
                        done = True
                        break

                    elif "ABORT" in text and prefix in text:
                        last_result = f"ABORTADO: {text}"
                        done = True
                        break

                    elif text.startswith(f"{prefix}ERROR:"):
                        if update_callback:
                            await update_callback(None, None, "ERROR", text)

                if done:
                    break

        return last_result

    def subscribe_events(self, user_id: int, prefix: str, callback):
        proc = self._get_proc(user_id)
        if proc:
            proc.subscribe_event(prefix, callback)

    def unsubscribe_events(self, user_id: int, prefix: str, callback):
        proc = self._get_proc(user_id)
        if proc:
            proc.unsubscribe_event(prefix, callback)
