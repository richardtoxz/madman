import asyncio
import hmac
import json
import os
import sys
import logging
import time
from collections import deque
from contextlib import nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.credentials import clean_token
from engine.process import SelfbotProcess
from engine.manager_relationships import RelationshipMixin
from engine.manager_guilds import GuildMixin
from engine.manager_messages import MessagesMixin
from engine.manager_voice import VoiceMixin
from engine.manager_protection import ProtectionMixin
from engine.manager_tracker import TrackerMixin
from engine.manager_quest import QuestMixin
from engine.manager_account import AccountMixin
from engine.manager_presence import PresenceMixin
from ipc_protocol import MAX_IPC_LINE_LENGTH, decode_json_payload

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StreamStep:
    """O que uma linha do stream de bulk pede: atualizar o progresso e/ou encerrar."""

    update: tuple | None = None
    result: str | None = None
    # Exceções do callback são ignoradas nas linhas de contagem, como sempre foram.
    swallow_errors: bool = False


def _counted_update(text: str, status: str) -> _StreamStep | None:
    parts = text.split(":")
    if len(parts) < 3:
        return None
    try:
        current, total = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    name = parts[3] if len(parts) >= 4 else ""
    return _StreamStep(update=(current, total, status, name), swallow_errors=True)


def _parse_stream_line(text: str, prefix: str) -> _StreamStep | None:
    """Interpreta uma linha para um prefixo; a primeira regra que casa decide, nesta ordem."""
    if text.startswith(f"{prefix}PROGRESS_JSON:"):
        payload = decode_json_payload(text.removeprefix(f"{prefix}PROGRESS_JSON:"))
        if not payload:
            return None
        return _StreamStep(
            update=(
                payload.get("current", 0),
                payload.get("total", 0),
                payload.get("status", "CLEANING"),
                payload.get("name", ""),
            )
        )
    if text.startswith(f"{prefix}RESULT_JSON:"):
        payload = decode_json_payload(text.removeprefix(f"{prefix}RESULT_JSON:"))
        return _StreamStep(result=str(payload.get("text") or json.dumps(payload))) if payload else None
    if text.startswith(f"{prefix}START:"):
        try:
            total = int(text.split(":")[1])
        except (IndexError, ValueError):
            return None
        return _StreamStep(update=(0, total, "WAITING_START", ""), swallow_errors=True)
    if text.startswith(f"{prefix}PROGRESS:"):
        return _counted_update(text, "CLEANING")
    if text.startswith(f"{prefix}FAILED:"):
        return _counted_update(text, "ERROR")
    if text.startswith(f"{prefix}BATCH_PAUSE:"):
        parts = text.split(":")
        if len(parts) < 4:
            return None
        try:
            current, total = int(parts[2]), int(parts[3])
        except ValueError:
            return None
        return _StreamStep(update=(current, total, "BATCH_PAUSE", f"Pausa ({parts[1]}s)"), swallow_errors=True)
    if "RATELIMIT" in text and prefix in text:
        return _StreamStep(update=(None, None, "RATE_LIMITED", text))
    if text.startswith(f"{prefix}RESULT:"):
        return _StreamStep(result=text.removeprefix(f"{prefix}RESULT:"))
    if text.startswith(f"{prefix}CANCELLED"):
        return _StreamStep(result="CANCELADO")
    if "ABORT" in text and prefix in text:
        return _StreamStep(result=f"ABORTADO: {text}")
    if text.startswith(f"{prefix}ERROR:"):
        return _StreamStep(update=(None, None, "ERROR", text))
    return None


class SelfbotManager(
    RelationshipMixin,
    GuildMixin,
    MessagesMixin,
    VoiceMixin,
    ProtectionMixin,
    TrackerMixin,
    QuestMixin,
    AccountMixin,
    PresenceMixin,
):
    IDENTIFY_COOLDOWN = 5.5
    STARTUP_TIMEOUT = 30.0
    DEFAULT_MAX_ACTIVE_WORKERS = 25
    # Cada tentativa abre um processo e um login no Discord saindo do IP do servidor, que todas as
    # contas dividem. Falhas em série de uma conta param por um tempo; conexões que dão certo não contam.
    CONNECT_FAILURE_LIMIT = 5
    CONNECT_FAILURE_WINDOW = 600.0
    _SENSITIVE_ENV_FRAGMENTS = (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "API_KEY",
        "CREDENTIAL",
        "COOKIE",
        "AUTH",
        "SESSION",
    )

    def __init__(self):
        self.active_processes: dict[int, SelfbotProcess] = {}
        self._session_tokens: dict[int, str] = {}
        self._session_locks: dict[int, asyncio.Lock] = {}
        self._supervisor_tasks: dict[int, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._event_subscriptions: dict[int, dict[str, list]] = {}
        self._protection_listeners: dict[int, Any] = {}
        self._manual_disconnects: set[int] = set()
        self._protection_enabled_users: set[int] = set()
        self._tracker_settings: dict[int, dict] = {}
        self._quest_settings: dict[int, dict] = {}
        self._rich_presence: dict[int, dict] = {}
        self._settings_state: dict[int, dict[str, Any]] = {}
        self._quest_sessions: dict[int, dict[str, Any]] = {}
        self.user_whitelists: dict[int, list[int]] = {}
        self._identify_lock = asyncio.Lock()
        self._last_identify_at: float = 0.0
        self._bulk_semaphore = asyncio.Semaphore(3)
        self._user_bulk_locks: dict[int, asyncio.Lock] = {}
        self.user_guild_whitelists: dict[int, list[int]] = {}
        raw_cap = os.environ.get("MAX_ACTIVE_WORKERS", "").strip()
        self.max_active_workers: int = (
            int(raw_cap) if raw_cap.isdigit() and int(raw_cap) > 0 else self.DEFAULT_MAX_ACTIVE_WORKERS
        )
        self._shutting_down = False
        self._connect_failures: dict[int, deque[float]] = {}
        self._clock = time.monotonic

    def _connect_blocked(self, user_id: int) -> bool:
        failures = self._connect_failures.get(user_id)
        if not failures:
            return False
        cutoff = self._clock() - self.CONNECT_FAILURE_WINDOW
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._connect_failures.pop(user_id, None)
            return False
        return len(failures) >= self.CONNECT_FAILURE_LIMIT

    def _record_connect_failure(self, user_id: int) -> None:
        self._connect_failures.setdefault(user_id, deque()).append(self._clock())

    def get_user_settings(self, user_id: int) -> dict[str, Any]:
        uid = int(user_id)
        settings = dict(self._quest_settings.get(uid) or {})
        settings.update(self._settings_state.get(uid) or {})
        return settings

    def set_user_settings(self, user_id: int, **kwargs) -> dict[str, Any]:
        uid = int(user_id)
        current = dict(self._settings_state.get(uid) or {})
        current.update(kwargs)
        self._settings_state[uid] = current
        if "webhook_url" in kwargs:
            webhook_url = kwargs["webhook_url"]
            if webhook_url:
                self._quest_settings[uid] = {"webhook_url": webhook_url}
            else:
                self._quest_settings.pop(uid, None)
        return dict(current)

    def get_webhook_url(self, user_id: int) -> str | None:
        return self.get_user_settings(user_id).get("webhook_url")

    def get_quest_sessions(self) -> dict[int, dict[str, Any]]:
        return self._quest_sessions

    def set_protection_listener(self, user_id: int, callback) -> None:
        uid = int(user_id)
        previous = self._protection_listeners.pop(uid, None)
        if previous is not None:
            self.unsubscribe_events(uid, "EVENT:PROTECTION:", previous)
        self._protection_listeners[uid] = callback
        self.subscribe_events(uid, "EVENT:PROTECTION:", callback)

    async def authenticate(self, user_id: int, token: str) -> tuple[bool, str]:
        token = clean_token(token)
        if len(token) < 30 or len(token) > 512 or any(character in token for character in ("\n", "\r", "\x00")):
            return False, "Token inválido. Confira o valor configurado."
        if self._shutting_down:
            return False, "O painel está sendo encerrado."

        lock = self._session_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if user_id in self.active_processes:
                proc = self.active_processes[user_id]
                if proc.is_alive:
                    return False, "Selfbot já está autenticado e ativo."
                log.warning("Processo morto encontrado para user %s, limpando", user_id)
                await self._cleanup_process(user_id)

            if self._connect_blocked(user_id):
                log.warning("Conexao recusada por excesso de falhas: user=%s", user_id)
                return False, "Muitas tentativas de conexão com falha. Aguarde alguns minutos e tente de novo."

            alive_count = sum(1 for p in self.active_processes.values() if getattr(p, "is_alive", False))
            if alive_count >= self.max_active_workers:
                return False, "Limite máximo de contas simultâneas atingido no servidor."

            for owner_id, active_token in self._session_tokens.items():
                if owner_id != user_id and hmac.compare_digest(active_token, token):
                    return False, "Esta conta já está associada a outra sessão ativa."

            self._manual_disconnects.discard(user_id)
            ok, sp_or_error = await self._spawn_ready_process(token)
            if not ok:
                self._record_connect_failure(user_id)
                return False, sp_or_error

            sp = sp_or_error
            self._session_tokens[user_id] = token
            self.active_processes[user_id] = sp
            self._attach_event_subscriptions(user_id, sp)
            self._start_supervisor(user_id)

            log.info("Selfbot autenticado: user_id=%s, username=%s", user_id, sp.username)
            return True, f"Autenticado como **{sp.username}**"

    @classmethod
    def _worker_environment(cls) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not any(fragment in key.upper() for fragment in cls._SENSITIVE_ENV_FRAGMENTS)
        }
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    async def _spawn_ready_process(self, token: str) -> tuple[bool, SelfbotProcess | str]:
        project_root = Path(__file__).resolve().parents[1]
        worker_package_dir = project_root / "worker"
        if not worker_package_dir.is_dir():
            return False, f"Pacote do selfbot não encontrado: {worker_package_dir}"

        sp: SelfbotProcess | None = None
        try:
            async with self._identify_lock:
                now = asyncio.get_running_loop().time()
                wait = self.IDENTIFY_COOLDOWN - (now - self._last_identify_at)
                if wait > 0:
                    log.info(
                        "LOGIN_STAGGER: Aguardando %.1fs para o próximo Identify...",
                        wait,
                    )
                    await asyncio.sleep(wait)

                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "worker.run_selfbot",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=project_root,
                    env=self._worker_environment(),
                    limit=MAX_IPC_LINE_LENGTH,
                )
                sp = SelfbotProcess(process, "sync_presence", redactions=(token,))
                sp.start_reader()
                if not await sp.send_startup_secret(token):
                    await sp.close()
                    return False, "Falha ao enviar credencial ao worker."
                self._last_identify_at = asyncio.get_running_loop().time()

            ready = await self._wait_ready(sp, timeout=self.STARTUP_TIMEOUT)
        except asyncio.CancelledError:
            if sp is not None:
                await asyncio.shield(sp.close())
            raise
        except OSError as error:
            if sp is not None:
                await sp.close()
            return False, f"Falha ao criar subprocess: {error}"
        except Exception as error:
            if sp is not None:
                await sp.close()
            return False, f"Falha ao inicializar subprocess: {type(error).__name__}"

        if not ready:
            error_code = sp.startup_error_code
            error_message = sp.startup_error
            await sp.close()
            if error_code == "login_failed":
                return False, "Token inválido ou revogado."
            return False, error_message or "O worker não conseguiu inicializar."

        return True, sp

    async def disconnect(self, user_id: int) -> tuple[bool, str]:
        lock = self._session_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            proc = self.active_processes.get(user_id)
            has_session = user_id in self._session_tokens
            if not proc and not has_session:
                return False, "Nenhum selfbot ativo para desconectar."

            self._manual_disconnects.add(user_id)
            username = proc.username if proc else "desconhecido"
            await self._stop_supervisor(user_id)
            await self._cleanup_process(user_id)
            self._session_tokens.pop(user_id, None)
            self._event_subscriptions.pop(user_id, None)
            self._protection_listeners.pop(user_id, None)
            self._protection_enabled_users.discard(user_id)
            self._tracker_settings.pop(user_id, None)
            self._quest_settings.pop(user_id, None)
            self._rich_presence.pop(user_id, None)
            self._settings_state.pop(user_id, None)
            self._quest_sessions.pop(user_id, None)
            self._user_bulk_locks.pop(user_id, None)

            self.user_whitelists.pop(user_id, None)
            self.user_guild_whitelists.pop(user_id, None)
            self._manual_disconnects.discard(user_id)

            log.info("Selfbot desconectado: user_id=%s, username=%s", user_id, username)
            return True, "Desconectado com sucesso."

    def is_authenticated(self, user_id: int) -> bool:
        if user_id in self._session_tokens:
            return True
        proc = self.active_processes.get(user_id)
        return proc is not None and getattr(proc, "is_alive", True)

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

    async def add_to_whitelist(self, user_id: int, target_ids, limit: int = 150) -> list[int]:
        """Adiciona sem remover ninguem; devolve so os IDs que entraram agora."""
        wl = self.user_whitelists.setdefault(user_id, [])
        added = []
        for target_id in target_ids:
            target_id = int(target_id)
            if target_id in wl or len(wl) >= limit:
                continue
            wl.append(target_id)
            added.append(target_id)
        return added

    async def toggle_guild_whitelist(self, user_id: int, guild_id: int) -> bool:
        wl = self.user_guild_whitelists.setdefault(user_id, [])
        if guild_id in wl:
            wl.remove(guild_id)
            return False
        wl.append(guild_id)
        return True

    async def update_guild_whitelist(self, user_id: int, visible_ids, selected_ids) -> list[int]:
        visible = {int(guild_id) for guild_id in visible_ids}
        selected = {int(guild_id) for guild_id in selected_ids} & visible
        current = set(self.user_guild_whitelists.get(user_id, []))
        updated = (current - visible) | selected
        self.user_guild_whitelists[user_id] = sorted(updated)
        return self.user_guild_whitelists[user_id]

    async def clear_guild_whitelist(self, user_id: int) -> int:
        return len(self.user_guild_whitelists.pop(user_id, []))

    def get_whitelist(self, user_id: int) -> list[int]:
        return self.user_whitelists.get(user_id, [])

    def get_guild_whitelist(self, user_id: int) -> list[int]:
        return list(self.user_guild_whitelists.get(user_id, []))

    def _get_proc(self, user_id: int) -> SelfbotProcess | None:
        proc = self.active_processes.get(user_id)
        if proc and not proc.is_alive:
            log.warning("Processo morto detectado para user %s", user_id)
            task = asyncio.create_task(self._cleanup_process(user_id), name=f"cleanup-worker-{user_id}")
            self._background_tasks.add(task)
            task.add_done_callback(self._background_task_done)
            return None
        return proc

    def _log_task_exception(self, task: asyncio.Task, message: str) -> None:
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            log.warning(
                "%s: %s",
                message,
                type(error).__name__,
                exc_info=(type(error), error, error.__traceback__),
            )

    def _background_task_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        self._log_task_exception(task, f"Tarefa em segundo plano falhou ({task.get_name()})")

    async def _cleanup_process(self, user_id: int):
        proc = self.active_processes.pop(user_id, None)
        if proc:
            await proc.close()

    def _start_supervisor(self, user_id: int):
        previous = self._supervisor_tasks.pop(user_id, None)
        if previous and not previous.done():
            previous.cancel()
        task = asyncio.create_task(self._supervise_process(user_id), name=f"supervisor-worker-{user_id}")
        self._supervisor_tasks[user_id] = task
        task.add_done_callback(lambda completed, uid=user_id: self._supervisor_done(uid, completed))

    def _supervisor_done(self, user_id: int, task: asyncio.Task) -> None:
        if self._supervisor_tasks.get(user_id) is task:
            self._supervisor_tasks.pop(user_id, None)
        self._log_task_exception(task, f"Supervisor do worker {user_id} falhou")

    async def _stop_supervisor(self, user_id: int):
        task = self._supervisor_tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _supervise_process(self, user_id: int):
        backoff = 2.0

        while user_id in self._session_tokens:
            if user_id in self._manual_disconnects:
                return

            proc = self.active_processes.get(user_id)
            if proc and proc.is_alive:
                try:
                    await proc.process.wait()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    log.warning("Falha aguardando worker %s: %s", user_id, error)

            if user_id in self._manual_disconnects or user_id not in self._session_tokens:
                return

            await self._reap_dead_process(user_id)

            token = self._session_tokens.get(user_id)
            if not token:
                return

            log.warning("SELFBOT_AUTO_RESTART:user=%s:backoff=%.1fs", user_id, backoff)
            await asyncio.sleep(backoff)

            ok, sp_or_error = await self._spawn_ready_process(token)
            if ok:
                await self._adopt_restarted_process(user_id, sp_or_error)
                backoff = 2.0
                continue

            log.error("SELFBOT_AUTO_RESTART_FAILED:user=%s:error=%s", user_id, sp_or_error)
            if "Token inválido" in str(sp_or_error):
                self._forget_user(user_id)
                return
            backoff = min(backoff * 2, 60.0)

    async def _reap_dead_process(self, user_id: int) -> None:
        dead_proc = self.active_processes.get(user_id)
        if dead_proc and not dead_proc.is_alive:
            dead_proc.emit_event("PROCESS:EXIT:UNEXPECTED")
            self.active_processes.pop(user_id, None)
            await dead_proc.close()

    async def _adopt_restarted_process(self, user_id: int, sp: SelfbotProcess) -> None:
        self.active_processes[user_id] = sp
        self._attach_event_subscriptions(user_id, sp)
        self._report_restore(
            user_id,
            sp,
            await self._restore_protection_state(user_id, sp),
            active=user_id in self._protection_enabled_users,
            message="Falha ao restaurar protecoes apos reinicio: user=%s",
            event="PROTECTION",
        )
        self._report_restore(
            user_id,
            sp,
            await self._restore_tracker_state(user_id, sp),
            active=user_id in self._tracker_settings,
            message="Falha ao restaurar Tracker apos reinicio: user=%s",
            event="TRACKER",
        )
        self._report_restore(
            user_id,
            sp,
            await self._restore_quest_state(user_id, sp),
            active=user_id in self._quest_settings,
            message="Falha ao restaurar configuração de Quest após reinício: user=%s",
            event="QUEST",
        )
        self._report_restore(
            user_id,
            sp,
            await self._restore_rich_presence_state(user_id, sp),
            active=user_id in self._rich_presence,
            message="Falha ao restaurar Rich Presence apos reinicio: user=%s",
            event="RICH_PRESENCE",
        )
        sp.emit_event("PROCESS:RESTARTED:OK")

    @staticmethod
    def _report_restore(user_id: int, sp, restored, *, active: bool, message: str, event: str) -> None:
        if active and not (restored and restored.get("ok")):
            log.warning(message, user_id)
            sp.emit_event(f"PROCESS:STATE_RESTORE_FAILED:{event}")

    def _forget_user(self, user_id: int) -> None:
        """Descarta tudo que a sessão do usuário guardava; o token foi rejeitado."""
        self._session_tokens.pop(user_id, None)
        self._event_subscriptions.pop(user_id, None)
        self._protection_listeners.pop(user_id, None)
        self._protection_enabled_users.discard(user_id)
        self._tracker_settings.pop(user_id, None)
        self._quest_settings.pop(user_id, None)
        self._rich_presence.pop(user_id, None)
        self._settings_state.pop(user_id, None)
        self._quest_sessions.pop(user_id, None)
        self._user_bulk_locks.pop(user_id, None)
        self.user_whitelists.pop(user_id, None)
        self.user_guild_whitelists.pop(user_id, None)

    async def _wait_ready(self, proc: SelfbotProcess, timeout: float = 30.0) -> bool:
        return await proc.wait_until_ready(timeout)

    async def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        user_ids = set(self._session_tokens) | set(self.active_processes)
        self._manual_disconnects.update(user_ids)

        await asyncio.gather(
            *(self._stop_supervisor(user_id) for user_id in tuple(self._supervisor_tasks)),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(self._cleanup_process(user_id) for user_id in tuple(self.active_processes)),
            return_exceptions=True,
        )

        for task in tuple(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

        self._session_tokens.clear()
        self._event_subscriptions.clear()
        self._protection_listeners.clear()
        self._protection_enabled_users.clear()
        self._tracker_settings.clear()
        self._quest_settings.clear()
        self._rich_presence.clear()
        self._settings_state.clear()
        self._quest_sessions.clear()
        self._user_bulk_locks.clear()
        self.user_whitelists.clear()
        self.user_guild_whitelists.clear()
        self._manual_disconnects.clear()

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

        user_lock = self._user_bulk_locks.setdefault(int(user_id), asyncio.Lock())
        async with user_lock:
            async for text in proc.send_and_stream(command, prefixes, timeout):
                if text.startswith("TIMEOUT:"):
                    return "ERRO: Timeout"
                if text.startswith("IPC_ERROR:"):
                    return "ERRO: Falha de IPC com o runner"
                for prefix in prefixes:
                    step = _parse_stream_line(text, prefix)
                    if step is None:
                        continue
                    if step.update and update_callback:
                        with suppress(Exception) if step.swallow_errors else nullcontext():
                            await update_callback(*step.update)
                    if step.result is not None:
                        return step.result
        return "Iniciando..."

    def subscribe_events(self, user_id: int, prefix: str, callback):
        callbacks = self._event_subscriptions.setdefault(user_id, {}).setdefault(prefix, [])
        if callback not in callbacks:
            callbacks.append(callback)
        proc = self._get_proc(user_id)
        if proc:
            proc.subscribe_event(prefix, callback)

    def unsubscribe_events(self, user_id: int, prefix: str, callback):
        user_subscriptions = self._event_subscriptions.get(user_id, {})
        callbacks = user_subscriptions.get(prefix, [])
        with suppress(ValueError):
            callbacks.remove(callback)
        if not callbacks:
            user_subscriptions.pop(prefix, None)
        if not user_subscriptions:
            self._event_subscriptions.pop(user_id, None)
        proc = self._get_proc(user_id)
        if proc:
            proc.unsubscribe_event(prefix, callback)

    def _attach_event_subscriptions(self, user_id: int, proc: SelfbotProcess) -> None:
        for prefix, callbacks in self._event_subscriptions.get(user_id, {}).items():
            for callback in callbacks:
                proc.subscribe_event(prefix, callback)
