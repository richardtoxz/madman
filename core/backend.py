"""
Backend principal — gerencia processos selfbot e despacha comandos.

Fluxo:
- 'autenticar' = receber token + conectar automaticamente
- 'desconectar' = destruir processo + remover todo estado
- Sem estado intermediário — ou está autenticado ou não
"""
import asyncio
import os
import sys
import logging

from core.process import SelfbotProcess
from core.manager_relationships import RelationshipMixin
from core.manager_guilds import GuildMixin
from core.manager_messages import MessagesMixin

log = logging.getLogger(__name__)


class SelfbotManager(RelationshipMixin, GuildMixin, MessagesMixin):
    """Orquestra processos selfbot e delega operações para mixins."""

    def __init__(self):
        self.active_processes: dict[int, SelfbotProcess] = {}
        self.user_whitelists: dict[int, list[int]] = {}
        self.user_guild_whitelists: dict[int, list[int]] = {}

    # ── Autenticação ─────────────────────────────────────────

    async def authenticate(
        self, user_id: int, token: str, status: str = "online"
    ) -> tuple[bool, str]:
        """
        Autentica e conecta o selfbot em uma única operação atômica.
        O token é passado direto ao subprocess — não fica salvo no manager.

        Returns:
            (success, message)
        """
        # Validação do token
        if not token or not token.strip():
            return False, "Token inválido. Forneça um token válido."
        token = token.strip()

        # Se já existe processo ativo
        if user_id in self.active_processes:
            proc = self.active_processes[user_id]
            if proc.is_alive:
                return False, "Selfbot já está autenticado e ativo."
            # Processo morto — limpa antes de reconectar
            log.warning("Processo morto encontrado para user %s, limpando", user_id)
            await self._cleanup_process(user_id)

        # Localiza o script
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "selfbot_runner", "run_selfbot.py"
        )
        if not os.path.exists(script_path):
            return False, f"Script do selfbot não encontrado: {script_path}"

        # Spawna o subprocess
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path, token, status,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return False, f"Falha ao criar subprocess: {e}"

        sp = SelfbotProcess(process, status)
        sp.start_reader()
        self.active_processes[user_id] = sp

        # Espera o READY com timeout
        ready = await self._wait_ready(sp, timeout=30.0)
        if not ready:
            await self._cleanup_process(user_id)
            return False, "Selfbot não conectou a tempo. Verifique se o token é válido."

        log.info("Selfbot autenticado: user_id=%s, username=%s", user_id, sp.username)
        return True, f"✅ Autenticado como **{sp.username}**"

    async def disconnect(self, user_id: int) -> tuple[bool, str]:
        """
        Desconecta o selfbot e remove completamente todo o estado.
        Destrói o processo + limpa whitelists.
        """
        proc = self.active_processes.get(user_id)
        if not proc:
            return False, "Nenhum selfbot ativo para desconectar."

        username = proc.username or "desconhecido"
        await self._cleanup_process(user_id)

        # Limpa todo estado associado ao user
        self.user_whitelists.pop(user_id, None)
        self.user_guild_whitelists.pop(user_id, None)

        log.info("Selfbot desconectado: user_id=%s, username=%s", user_id, username)
        return True, f"✅ Desconectado com sucesso."

    def is_authenticated(self, user_id: int) -> bool:
        """Verifica se o user tem um selfbot autenticado e vivo."""
        proc = self.active_processes.get(user_id)
        return proc is not None and proc.is_alive

    # ── Controle de Limpeza ──────────────────────────────────

    async def control_clean(self, user_id: int, signal: str) -> bool:
        """Envia START, PAUSE, RESUME ou CANCEL para o selfbot."""
        proc = self._get_proc(user_id)
        if proc and proc.is_alive:
            try:
                await proc.send(signal)
                return True
            except RuntimeError:
                return False
        return False

    # ── Whitelists ───────────────────────────────────────────

    async def toggle_whitelist(self, user_id: int, target_id: int) -> bool:
        """Toggle um user na whitelist. Retorna True se adicionou, False se removeu."""
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

    # ── Helpers internos ─────────────────────────────────────

    def _get_proc(self, user_id: int) -> SelfbotProcess | None:
        proc = self.active_processes.get(user_id)
        if proc and not proc.is_alive:
            log.warning("Processo morto detectado para user %s", user_id)
            asyncio.create_task(self._cleanup_process(user_id))
            return None
        return proc

    async def _cleanup_process(self, user_id: int):
        """Fecha o processo e remove do registro."""
        proc = self.active_processes.pop(user_id, None)
        if proc:
            await proc.close()

    async def _wait_ready(self, proc: SelfbotProcess, timeout: float = 30.0) -> bool:
        """Espera até o processo emitir READY ou timeout."""
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
        self, user_id: int, command: str, prefixes: list,
        update_callback=None, timeout: float = 600
    ) -> str:
        """Helper genérico para operações bulk que emitem progresso."""
        proc = self._get_proc(user_id)
        if not proc:
            return "❌ Não autenticado"

        last_result = "Iniciando..."

        async for text in proc.send_and_stream(command, prefixes, timeout):
            if text.startswith("TIMEOUT:"):
                last_result = "ERRO: Timeout"
                break

            for prefix in prefixes:
                if text.startswith(f"{prefix}START:"):
                    try:
                        total = int(text.split(":")[1])
                        if update_callback:
                            await update_callback(0, total, "STARTING", "")
                    except Exception:
                        pass

                elif text.startswith(f"{prefix}PROGRESS:"):
                    parts = text.split(":")
                    if len(parts) >= 3:
                        try:
                            curr, total = int(parts[1]), int(parts[2])
                            name = parts[3] if len(parts) >= 4 else ""
                            if update_callback:
                                await update_callback(curr, total, "REMOVING", name)
                        except Exception:
                            pass

                elif text.startswith(f"{prefix}BATCH_PAUSE:"):
                    parts = text.split(":")
                    if len(parts) >= 4:
                        try:
                            secs, curr, total = parts[1], int(parts[2]), int(parts[3])
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
                    break

                elif text.startswith(f"{prefix}CANCELLED"):
                    last_result = "CANCELADO"
                    break

                elif "ABORT" in text and prefix in text:
                    last_result = f"ABORTADO: {text}"
                    break

                elif text.startswith(f"{prefix}ERROR:"):
                    if update_callback:
                        await update_callback(None, None, "ERROR", text)

        return last_result

    # ── Pub/Sub ──────────────────────────────────────────────

    def subscribe_events(self, user_id: int, prefix: str, callback):
        proc = self._get_proc(user_id)
        if proc:
            proc.subscribe_event(prefix, callback)

    def unsubscribe_events(self, user_id: int, prefix: str, callback):
        proc = self.active_processes.get(user_id)
        if proc:
            proc.unsubscribe_event(prefix, callback)