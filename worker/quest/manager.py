from __future__ import annotations

import asyncio
import json
import random
import re
from contextlib import suppress
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from ..core.config import QuestConfig
from ..core.ipc import emit_line, sanitize_ipc_field
from .api_client import QuestAPIClient
from .constants import PLAY_TASKS, VIDEO_TASKS
from .models import Quest
from .webhook_dispatch import QuestWebhookDispatchMixin


class QuestDataError(RuntimeError):
    pass


class _QueuedQuest(NamedTuple):
    """Uma Quest que a fila automática vai executar, com a tarefa escolhida."""

    quest: Quest
    task_name: str
    task: dict
    target: int
    progress: float


class QuestManager(QuestWebhookDispatchMixin):
    """Quest completion adapted from Equicord Questify's execution model.

    Work is sequential, cancellable, bounded, and only considered successful
    after Discord returns a valid completed status or a final refresh confirms
    completion.
    """

    def __init__(self, client):
        self.client = client
        self.api = QuestAPIClient(client)
        self.config = QuestConfig()
        self.webhook_url: str | None = None
        self._webhook_session = None
        self._activity_session: aiohttp.ClientSession | None = None
        self._progress_webhook_bucket: dict[str, int] = {}
        self._last_presence_detail: str | None = None
        self._active_quest_id: str | None = None
        self._cancelled = False
        self._task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self.enrollment_blocked_until: str | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def active_quest_id(self) -> str | None:
        return self._active_quest_id

    def _spawn(self, coroutine, *, name: str) -> asyncio.Task:
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def cancel(self) -> bool:
        self._cancelled = True
        task = self._task
        was_running = task is not None and not task.done()
        if was_running and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._active_quest_id = None
        self._last_presence_detail = None
        return was_running

    async def shutdown(self):
        await self.cancel()
        if self._background_tasks:
            for task in tuple(self._background_tasks):
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self._activity_session is not None and not self._activity_session.closed:
            await self._activity_session.close()
        self._activity_session = None
        await self.api.close()
        await self.close_webhook_session()

    @staticmethod
    def _normalize_webhook_url(value) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        try:
            parsed = urlparse(text)
        except ValueError:
            return None
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"discord.com", "discordapp.com"}
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        if not re.fullmatch(
            r"/api(?:/v\d+)?/webhooks/\d{5,25}/[A-Za-z0-9._-]{20,}",
            parsed.path,
        ):
            return None
        return text

    async def set_runtime_config(
        self,
        *,
        webhook_url: str | None,
        request_id: str | None = None,
    ) -> dict:
        normalized_webhook = self._normalize_webhook_url(webhook_url)
        if webhook_url and not normalized_webhook:
            result = {
                "ok": False,
                "code": "INVALID_WEBHOOK",
                "message": "WEBHOOK_URL inválida.",
            }
            emit_line("QUEST:CONFIG_RESULT:" + self._json(result), request_id)
            return result

        self.webhook_url = normalized_webhook
        if not self.webhook_url and self._webhook_session is not None:
            await self.close_webhook_session()

        result = {
            "ok": True,
            "code": "CONFIG_UPDATED",
            "webhook": bool(self.webhook_url),
            "message": "Configuração de Quest atualizada.",
        }
        emit_line("QUEST:CONFIG_RESULT:" + self._json(result), request_id)
        return result

    @staticmethod
    def _json(value: dict | list) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _safe_error(error: BaseException) -> str:
        status = getattr(error, "status", None)
        code = getattr(error, "code", None)
        suffix = ""
        if isinstance(status, int):
            suffix += f" HTTP {status}"
        if isinstance(code, int) and code:
            suffix += f" (código {code})"
        return f"{type(error).__name__}{suffix}"

    @staticmethod
    def _quest_label(quest: Quest) -> str:
        return sanitize_ipc_field(quest.quest_name, limit=120)

    def _emit_result(self, request_id: str | None, **payload) -> None:
        emit_line("QUEST:RESULT:" + self._json(payload), request_id)

    def _dispatch_background(self, coroutine, *, name: str) -> None:
        self._spawn(coroutine, name=name)

    def _init_progress_bucket(self, quest_id: str, progress: float, target: int):
        pct = min(100, int((progress / target) * 100)) if target > 0 else 0
        self._progress_webhook_bucket[quest_id] = pct // 10

    def _can_send_progress_webhook(self, quest_id: str, progress: float, target: int) -> bool:
        if target <= 0:
            return False
        bucket = min(100, int((progress / target) * 100)) // 10
        if bucket <= self._progress_webhook_bucket.get(quest_id, -1):
            return False
        self._progress_webhook_bucket[quest_id] = bucket
        return True

    async def fetch_quests(self) -> list[Quest]:
        data = await self.api.get("/quests/@me")
        if isinstance(data, list):
            raw_quests = data
            self.enrollment_blocked_until = None
        elif isinstance(data, dict):
            raw_quests = data.get("quests")
            self.enrollment_blocked_until = data.get("quest_enrollment_blocked_until")
        else:
            raise QuestDataError("Discord retornou um formato desconhecido para Quests")

        if not isinstance(raw_quests, list):
            raise QuestDataError("A resposta de Quests não contém uma lista válida")

        quests: list[Quest] = []
        for raw in raw_quests:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            with suppress(TypeError):
                quests.append(Quest(raw))
        return quests

    async def _refresh_quest(self, quest: Quest) -> Quest | None:
        quests = await self.fetch_quests()
        fresh = next((item for item in quests if item.id == quest.id), None)
        if fresh is not None:
            quest.raw = fresh.raw
            quest.config = fresh.config
            quest.user_status = fresh.user_status
        return fresh

    async def _verify_completion(self, quest: Quest) -> bool:
        if quest.is_completed:
            return True
        await asyncio.sleep(self.config.COMPLETION_VERIFY_DELAY_SECONDS)
        try:
            fresh = await self._refresh_quest(quest)
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return bool(fresh and fresh.is_completed)

    async def accept_quest(self, quest: Quest, request_id=None) -> bool:
        emit_line(
            f"QUEST:AUTO:Aceite '{self._quest_label(quest)}' manualmente no Discord e execute novamente.",
            request_id,
        )
        return False

    async def claim_reward(self, quest: Quest, request_id=None) -> bool:
        emit_line(
            f"QUEST:AUTO:Recompensa de '{self._quest_label(quest)}' pronta para resgate manual no Discord.",
            request_id,
        )
        return False

    def _quest_record(self, quest: Quest) -> dict:
        task_name, task = quest.get_task_info()
        target = quest.task_target(task)
        progress = quest.get_progress(task_name) if task_name else 0.0
        if quest.is_claimed:
            status = "CLAIMED"
        elif quest.is_completed:
            status = "COMPLETED"
        elif quest.is_expired:
            status = "EXPIRED"
        elif quest.is_enrolled:
            status = "ENROLLED"
        else:
            status = "AVAILABLE"
        return {
            "id": quest.id,
            "name": quest.quest_name,
            "application": quest.application_name,
            "task": task_name or "UNKNOWN",
            "status": status,
            "progress": int(progress),
            "target": target,
            "auto_supported": quest.get_task_info(auto_only=True)[0] is not None,
        }

    async def list_quests(self) -> dict:
        try:
            quests = await self.fetch_quests()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return {
                "ok": False,
                "code": "FETCH_FAILED",
                "message": "Falha ao buscar Quests: " + self._safe_error(error),
                "quests": [],
            }
        return {
            "ok": True,
            "code": "QUESTS_LISTED",
            "quests": [self._quest_record(quest) for quest in quests],
            "enrollment_blocked_until": self.enrollment_blocked_until,
        }

    async def start_auto(self, request_id: str | None = None) -> dict:
        if self.is_running:
            return {
                "ok": False,
                "code": "ALREADY_RUNNING",
                "message": "Já existe uma fila de Quests em execução.",
                "active_quest_id": self._active_quest_id,
            }
        self._cancelled = False
        self._task = asyncio.create_task(
            self.auto_complete_all(request_id=request_id),
            name="quest-auto-complete",
        )
        return {
            "ok": True,
            "code": "STARTED",
            "message": "Fila de Quests iniciada.",
        }

    async def start_quest(self, quest_id: str, request_id: str | None = None) -> dict:
        """Reserve the execution slot before starting a single Quest.

        The reservation prevents two IPC commands received in the same event
        loop turn from both passing an `is_running` check.
        """
        if self.is_running:
            return {
                "ok": False,
                "code": "ALREADY_RUNNING",
                "message": "Já existe uma Quest em execução.",
                "active_quest_id": self._active_quest_id,
            }
        self._cancelled = False
        self._task = asyncio.create_task(
            self.complete_quest(quest_id, request_id=request_id),
            name=f"quest-complete-{quest_id}",
        )
        return {"ok": True, "code": "STARTED", "message": "Quest iniciada."}

    async def auto_complete_all(self, request_id: str | None = None):
        current = asyncio.current_task()
        if self._task is None:
            self._task = current
        summary = {
            "ok": True,
            "code": "FINISHED",
            "completed": 0,
            "failed": 0,
            "manual_enroll": 0,
            "manual_claim": 0,
            "unsupported": 0,
            "message": "Todas as Quests foram processadas.",
        }
        try:
            try:
                quests = await self.fetch_quests()
            except Exception as error:
                summary.update(
                    ok=False,
                    code="FETCH_FAILED",
                    message="Falha ao buscar Quests: " + self._safe_error(error),
                )
                emit_line("QUEST:ERRO:" + summary["message"], request_id)
                return

            if not quests:
                summary.update(code="EMPTY", message="Nenhuma Quest encontrada.")
                emit_line("QUEST:AUTO:Nenhuma Quest encontrada.", request_id)
                return

            actionable = await self._triage_quests(quests, summary, request_id)
            if not actionable:
                self._close_empty_queue(summary)
                return

            emit_line(
                f"QUEST:AUTO:Processando {len(actionable)} Quest(s) em sequência...",
                request_id,
            )
            for item in actionable:
                if self._cancelled:
                    summary.update(ok=False, code="CANCELLED", message="Fila cancelada.")
                    break
                await self._run_queued_quest(item, summary, request_id)

            if summary["failed"]:
                summary.update(
                    ok=False,
                    code="PARTIAL_FAILURE",
                    message="Fila finalizada com uma ou mais Quests não confirmadas.",
                )
            elif summary["completed"]:
                summary["message"] = "Quests concluídas; resgate as recompensas manualmente."
        except asyncio.CancelledError:
            summary.update(ok=False, code="CANCELLED", message="Fila cancelada.")
        finally:
            self._active_quest_id = None
            self._last_presence_detail = None
            self._emit_result(request_id, **summary)
            if self._task is current:
                self._task = None

    async def _triage_quests(self, quests: list[Quest], summary: dict, request_id: str | None) -> list[_QueuedQuest]:
        """Separa as Quests que a fila executa, contando e anunciando as que ficam de fora."""
        actionable: list[_QueuedQuest] = []
        for quest in quests:
            if quest.is_expired or quest.is_claimed:
                continue
            if quest.is_completed:
                summary["manual_claim"] += 1
                emit_line(
                    f"QUEST:AUTO:🎁 '{self._quest_label(quest)}' aguarda resgate manual.",
                    request_id,
                )
                continue
            if not quest.is_enrolled:
                summary["manual_enroll"] += 1
                emit_line(
                    f"QUEST:AUTO:⏸️ Aceite '{self._quest_label(quest)}' manualmente no Discord.",
                    request_id,
                )
                continue

            task_name, task = quest.get_task_info(auto_only=True)
            if not task_name or not task:
                summary["unsupported"] += 1
                known_name, _ = quest.get_task_info()
                emit_line(
                    f"QUEST:AUTO:⏭️ '{self._quest_label(quest)}' requer execução manual ({known_name or 'tipo desconhecido'}).",
                    request_id,
                )
                continue
            target = quest.task_target(task)
            progress = quest.get_progress(task_name)
            if target <= 0:
                summary["unsupported"] += 1
                emit_line(
                    f"QUEST:AUTO:⏭️ '{self._quest_label(quest)}' possui alvo inválido.",
                    request_id,
                )
                continue
            if progress >= target:
                if await self._verify_completion(quest):
                    summary["manual_claim"] += 1
                else:
                    summary["failed"] += 1
                continue
            actionable.append(_QueuedQuest(quest, task_name, task, target, progress))

        actionable.sort(key=lambda item: item.quest.expires_at or datetime.max.replace(tzinfo=timezone.utc))
        return actionable

    @staticmethod
    def _close_empty_queue(summary: dict) -> None:
        if summary["manual_enroll"]:
            summary.update(
                code="MANUAL_ENROLL_REQUIRED",
                message="Aceite as Quests indicadas no Discord e execute novamente.",
            )
        elif summary["manual_claim"]:
            summary.update(
                code="MANUAL_CLAIM_REQUIRED",
                message="As recompensas concluídas aguardam resgate manual.",
            )
        else:
            summary.update(code="NOTHING_PENDING", message="Nenhuma Quest pendente compatível.")

    async def _run_queued_quest(
        self,
        item: _QueuedQuest,
        summary: dict,
        request_id: str | None,
    ) -> None:
        quest, task_name, task, target, progress = item
        self._active_quest_id = quest.id
        self._init_progress_bucket(quest.id, progress, target)
        emit_line(
            f"QUEST:AUTO:{self._quest_label(quest)} [{task_name}] {int(progress)}/{target}",
            request_id,
        )
        self._dispatch_background(
            self.dispatch_quest_start(quest, task_name),
            name=f"quest-webhook-start-{quest.id}",
        )

        presence_token = None
        if self.client.presence_manager:
            presence_token = await self.client.presence_manager.set_task("Quest", self._quest_label(quest))
        try:
            success = await self._run_task_logic(quest, task_name, task, target, request_id)
            if success:
                summary["completed"] += 1
                summary["manual_claim"] += 1
                emit_line(
                    f"QUEST:AUTO:✅ '{self._quest_label(quest)}' concluída; resgate a recompensa no Discord.",
                    request_id,
                )
                self._dispatch_background(
                    self.dispatch_quest_complete(quest=quest, task_name=task_name),
                    name=f"quest-webhook-complete-{quest.id}",
                )
            else:
                summary["failed"] += 1
                if not self._cancelled:
                    emit_line(
                        f"QUEST:AUTO:❌ '{self._quest_label(quest)}' não foi confirmada pelo Discord.",
                        request_id,
                    )
        except asyncio.CancelledError:
            summary.update(ok=False, code="CANCELLED", message="Fila cancelada.")
            raise
        except Exception as error:
            summary["failed"] += 1
            emit_line(
                f"QUEST:ERRO:{self._quest_label(quest)}: {self._safe_error(error)}",
                request_id,
            )
        finally:
            await self._release_quest_run(quest.id, presence_token)

    async def _release_quest_run(self, quest_id: str, presence_token) -> None:
        """Libera o que uma Quest em execução ocupava e devolve a presença ao estado anterior."""
        self._progress_webhook_bucket.pop(quest_id, None)
        self._active_quest_id = None
        self._last_presence_detail = None
        if self.client.presence_manager:
            if presence_token:
                await self.client.presence_manager.complete_task(presence_token)
            else:
                await self.client.presence_manager.set_main()

    async def complete_quest(self, quest_id: str, request_id: str | None = None):
        current = asyncio.current_task()
        presence_token = None
        if self.is_running and self._task is not current:
            self._emit_result(
                request_id,
                ok=False,
                code="ALREADY_RUNNING",
                message="Já existe uma Quest em execução.",
            )
            return
        if self._task is None or self._task.done():
            self._task = current
        self._cancelled = False
        try:
            quests = await self.fetch_quests()
            quest = next((item for item in quests if item.id == quest_id), None)
            blocker = self._quest_start_blocker(quest)
            if blocker:
                self._emit_result(request_id, **blocker)
                return
            task_name, task = quest.get_task_info(auto_only=True)
            target = quest.task_target(task)
            if not task_name or not task or target <= 0:
                self._emit_result(request_id, ok=False, code="UNSUPPORTED", message="Tipo de Quest não suportado.")
                return
            self._active_quest_id = quest.id
            progress = quest.get_progress(task_name)
            self._init_progress_bucket(quest.id, progress, target)
            if self.client.presence_manager:
                presence_token = await self.client.presence_manager.set_task("Quest", self._quest_label(quest))
            self._dispatch_background(
                self.dispatch_quest_start(quest, task_name),
                name=f"quest-webhook-start-{quest.id}",
            )
            success = await self._run_task_logic(quest, task_name, task, target, request_id)
            self._emit_result(
                request_id,
                ok=success,
                code="COMPLETED" if success else "NOT_CONFIRMED",
                message=(
                    "Quest concluída; resgate manualmente." if success else "O Discord não confirmou a conclusão."
                ),
            )
        except asyncio.CancelledError:
            self._emit_result(request_id, ok=False, code="CANCELLED", message="Quest cancelada.")
        except Exception as error:
            self._emit_result(
                request_id,
                ok=False,
                code="FAILED",
                message="Falha: " + self._safe_error(error),
            )
        finally:
            await self._release_quest_run(quest_id, presence_token)
            if self._task is current:
                self._task = None

    @staticmethod
    def _quest_start_blocker(quest: Quest | None) -> dict | None:
        """O resultado que impede iniciar a Quest, ou None quando ela pode rodar."""
        if quest is None:
            return {"ok": False, "code": "NOT_FOUND", "message": "Quest não encontrada."}
        if quest.is_expired:
            return {"ok": False, "code": "EXPIRED", "message": "Quest expirada."}
        if quest.is_completed:
            return {"ok": True, "code": "ALREADY_COMPLETED", "message": "Quest já concluída; resgate manualmente."}
        if not quest.is_enrolled:
            return {"ok": False, "code": "MANUAL_ENROLL_REQUIRED", "message": "Aceite a Quest manualmente no Discord."}
        return None

    async def _update_presence(self, text: str):
        if self.client.presence_manager and self._active_quest_id:
            if self._last_presence_detail == text:
                return
            self._last_presence_detail = text
            await self.client.presence_manager.update_detail(text)

    async def _emit_progress(
        self,
        quest: Quest,
        task_name: str,
        progress: float,
        target: int,
        request_id: str | None,
    ) -> None:
        progress = min(float(target), max(0.0, progress))
        pct = min(100, int((progress / target) * 100)) if target else 0
        emit_line(
            f"QUEST:PROGRESS:{int(progress)}/{target} ({pct}%)",
            request_id,
        )
        await self._update_presence("Concluindo Quest")
        if self._can_send_progress_webhook(quest.id, progress, target):
            self._dispatch_background(
                self.dispatch_quest_progress(
                    quest=quest,
                    task_name=task_name,
                    progress=int(progress),
                    target=target,
                ),
                name=f"quest-webhook-progress-{quest.id}",
            )

    async def _run_task_logic(
        self,
        quest: Quest,
        task_name: str,
        task: dict,
        target: int,
        request_id: str | None,
    ) -> bool:
        if task_name in VIDEO_TASKS:
            return await self.doing_watch_video_quest(quest, task_name, target, request_id)
        if task_name in PLAY_TASKS:
            return await self.doing_play_on_platform_quest(quest, task_name, task, target, request_id)
        if task_name == "ACHIEVEMENT_IN_ACTIVITY":
            return await self.doing_achievement_in_activity_quest(quest, task, target, request_id)
        emit_line(
            f"QUEST:ERRO:'{self._quest_label(quest)}' requer execução manual ({task_name}).",
            request_id,
        )
        return False

    async def _post_status_with_retries(
        self,
        quest: Quest,
        path: str,
        payload: dict,
        request_id: str | None,
        label: str,
    ) -> bool:
        attempts = self.config.REQUEST_RETRY_ATTEMPTS
        for attempt in range(1, attempts + 1):
            try:
                data = await self.api.post(path, payload, quest_id=quest.id)
                if not quest.update_status(data):
                    raise QuestDataError("resposta sem user_status")
                return True
            except asyncio.CancelledError:
                raise
            except Exception as error:
                emit_line(
                    f"QUEST:WARN:{label} tentativa {attempt}/{attempts}: {self._safe_error(error)}",
                    request_id,
                )
                if attempt < attempts:
                    await asyncio.sleep(self.config.REQUEST_RETRY_DELAY_SECONDS * attempt)
        return False

    async def doing_watch_video_quest(self, quest: Quest, task_name: str, target: int, request_id) -> bool:
        enrolled_at = quest.enrolled_at
        if enrolled_at is None:
            emit_line("QUEST:ERRO:Quest de vídeo sem enrolled_at válido.", request_id)
            return False

        progress = min(float(target), quest.get_progress(task_name))
        deadline = asyncio.get_running_loop().time() + max(120.0, (target - progress) + 180.0)
        while progress < target and not self._cancelled:
            if quest.is_expired or asyncio.get_running_loop().time() >= deadline:
                return False
            elapsed = max(
                0.0,
                (datetime.now(timezone.utc) - enrolled_at).total_seconds(),
            )
            maximum_allowed = min(float(target), elapsed + self.config.MAX_FUTURE_SECONDS)
            step = random.uniform(
                self.config.VIDEO_REPORT_MIN_SECONDS,
                self.config.VIDEO_REPORT_MAX_SECONDS,
            )
            next_progress = min(float(target), progress + step, maximum_allowed)
            if next_progress <= progress + 0.05:
                await asyncio.sleep(min(1.0, max(0.1, progress - maximum_allowed + 0.1)))
                continue

            reported = await self._post_status_with_retries(
                quest,
                "/quests/{quest_id}/video-progress",
                {"timestamp": round(next_progress, 6)},
                request_id,
                "video-progress",
            )
            if not reported:
                return False
            server_progress = quest.get_progress(task_name)
            progress = min(float(target), max(next_progress, server_progress))
            await self._emit_progress(quest, task_name, progress, target, request_id)
            if quest.is_completed:
                return True

            if progress >= target:
                break

            await asyncio.sleep(
                random.uniform(
                    self.config.VIDEO_REPORT_MIN_SECONDS,
                    self.config.VIDEO_REPORT_MAX_SECONDS,
                )
            )

        if self._cancelled:
            return False
        return await self._verify_completion(quest)

    def _resolve_stream_key(self) -> str | None:
        user = getattr(self.client, "user", None)
        user_id = getattr(user, "id", None)
        if not user_id:
            return None

        voice_clients = list(getattr(self.client, "voice_clients", []) or [])
        legacy_voice = getattr(self.client, "voice_client", None)
        if legacy_voice is not None:
            voice_clients.append(legacy_voice)
        for voice_client in voice_clients:
            channel = getattr(voice_client, "channel", None)
            channel_id = getattr(channel, "id", None)
            if not channel_id:
                continue
            guild_id = getattr(getattr(channel, "guild", None), "id", None)
            if guild_id:
                return f"guild:{guild_id}:{channel_id}:{user_id}"
            return f"call:{channel_id}:{user_id}"

        for channel in getattr(self.client, "private_channels", []) or []:
            channel_id = getattr(channel, "id", None)
            if channel_id:
                return f"call:{channel_id}:{user_id}"
        return None

    async def doing_play_on_platform_quest(
        self,
        quest: Quest,
        task_name: str,
        task: dict,
        target: int,
        request_id,
    ) -> bool:
        application_id = quest.application_id_for(task_name, task)
        if not application_id or not application_id.isdigit():
            emit_line("QUEST:ERRO:Quest sem application_id válido.", request_id)
            return False

        payload: dict
        if task_name == "PLAY_ACTIVITY":
            stream_key = self._resolve_stream_key()
            if not stream_key:
                emit_line(
                    "QUEST:ERRO:PLAY_ACTIVITY requer um canal de voz/DM válido para formar o stream_key.",
                    request_id,
                )
                return False
            payload = {"stream_key": stream_key}
        else:
            payload = {"application_id": application_id}

        progress = quest.get_progress(task_name)
        stagnant = 0
        deadline = asyncio.get_running_loop().time() + max(180.0, (target - progress) + 300.0)
        while not self._cancelled:
            if quest.is_expired or asyncio.get_running_loop().time() >= deadline:
                return False
            previous = progress
            ok = await self._post_status_with_retries(
                quest,
                "/quests/{quest_id}/heartbeat",
                {**payload, "terminal": False},
                request_id,
                "heartbeat",
            )
            if not ok:
                return False
            progress = quest.get_progress(task_name)
            await self._emit_progress(quest, task_name, progress, target, request_id)
            if quest.is_completed or progress >= target:
                terminal_ok = await self._post_status_with_retries(
                    quest,
                    "/quests/{quest_id}/heartbeat",
                    {**payload, "terminal": True},
                    request_id,
                    "heartbeat terminal",
                )
                return terminal_ok and await self._verify_completion(quest)

            stagnant = stagnant + 1 if progress <= previous else 0
            if stagnant >= self.config.MAX_STAGNANT_HEARTBEATS:
                emit_line(
                    "QUEST:WARN:O Discord não avançou o progresso após três heartbeats.",
                    request_id,
                )
                return False
            remaining = max(0.0, target - progress)
            await asyncio.sleep(
                min(self.config.HEARTBEAT_MAX_INTERVAL_SECONDS, remaining) + self.config.HEARTBEAT_BUFFER_SECONDS
            )
        return False

    async def _get_activity_session(self) -> aiohttp.ClientSession:
        if self._activity_session is None or self._activity_session.closed:
            self._activity_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25.0))
        return self._activity_session

    async def _activity_post(
        self,
        application_id: str,
        endpoint: str,
        payload: dict,
        headers: dict,
    ) -> tuple[bool, dict | None]:
        if not application_id.isdigit() or endpoint not in ("authorize", "quest/progress"):
            raise ValueError("Invalid Discord Activity request")
        session = await self._get_activity_session()
        url = f"https://{application_id}.discordsays.com/.proxy/acf/{endpoint}"
        async with session.post(url, json=payload, headers=headers) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                data = None
            return response.status < 400, data if isinstance(data, dict) else None

    async def doing_achievement_in_activity_quest(
        self,
        quest: Quest,
        task: dict,
        target: int,
        request_id,
    ) -> bool:
        application_id = quest.application_id_for("ACHIEVEMENT_IN_ACTIVITY", task)
        if not application_id or not application_id.isdigit():
            emit_line("QUEST:ERRO:Achievement sem application_id válido.", request_id)
            return False

        existing_token_ids: set[str] | None = None
        try:
            # Preserve grants the user already had. Only the temporary grant
            # created by this run is eligible for cleanup.
            existing_token_ids = await self._existing_oauth_token_ids()
            auth_code = await self._authorize_oauth_code(application_id)
            base_headers = await self._activity_headers(quest, application_id)

            authorized, authorize_data = await self._activity_post(
                application_id,
                "authorize",
                {"code": auth_code},
                {**base_headers, "X-Auth-Token": ""},
            )
            progress_token = authorize_data.get("token") if authorize_data else None
            if not authorized or not isinstance(progress_token, str) or not progress_token:
                raise QuestDataError("Activity recusou a autorização")

            progressed, _ = await self._activity_post(
                application_id,
                "quest/progress",
                {"progress": target},
                {**base_headers, "X-Auth-Token": progress_token},
            )
            if not progressed:
                raise QuestDataError("Activity recusou o progresso")
            await self._emit_progress(quest, "ACHIEVEMENT_IN_ACTIVITY", target, target, request_id)
            return await self._verify_completion(quest)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            emit_line(
                "QUEST:ERRO:Achievement não concluído: " + self._safe_error(error),
                request_id,
            )
            return False
        finally:
            # Match Questify: remove the temporary OAuth grant even if progress
            # verification fails.
            await self._revoke_new_oauth_grant(application_id, existing_token_ids)

    async def _existing_oauth_token_ids(self) -> set[str] | None:
        try:
            existing_tokens = await self.api.get("/oauth2/tokens")
            if not isinstance(existing_tokens, list):
                return None
            return {
                str(entry.get("id"))
                for entry in existing_tokens
                if isinstance(entry, dict) and entry.get("id") is not None
            }
        except Exception:
            return None

    async def _authorize_oauth_code(self, application_id: str) -> str:
        auth_response = await self.api.post(
            "/oauth2/authorize",
            {"authorize": True},
            query={
                "client_id": application_id,
                "response_type": "code",
                "scope": "identify applications.entitlements",
                "state": "",
            },
        )
        location = auth_response.get("location") if isinstance(auth_response, dict) else None
        auth_code = parse_qs(urlparse(str(location)).query).get("code", [None])[0] if location else None
        if not auth_code:
            raise QuestDataError("Discord não retornou o código OAuth")
        return auth_code

    async def _activity_headers(self, quest: Quest, application_id: str) -> dict[str, str]:
        """Cabeçalhos da Activity, com o proxy ticket que o Discord emite para a aplicação."""
        ticket_response = await self.api.post(
            "/applications/{application_id}/proxy-tickets",
            {},
            application_id=application_id,
        )
        ticket = ticket_response.get("ticket") if isinstance(ticket_response, dict) else None
        if not ticket:
            raise QuestDataError("Discord não retornou proxy ticket")

        referrer = f"https://{application_id}.discordsays.com/?" + urlencode(
            {
                "instance_id": "example-cl-instance",
                "platform": "desktop",
                "discord_proxy_ticket": ticket,
            }
        )
        user_agent = getattr(getattr(self.client, "http", None), "user_agent", "")
        headers = {
            "Content-Type": "application/json",
            "X-Discord-Quest-ID": quest.id,
            "Referer": referrer,
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        return headers

    async def _revoke_new_oauth_grant(self, application_id: str, existing_token_ids: set[str] | None) -> None:
        """Remove o grant criado por esta execução; nunca toca nos que o usuário já tinha."""
        try:
            token_id = None
            tokens = await self.api.get("/oauth2/tokens")
            if isinstance(tokens, list):
                for entry in tokens:
                    application = entry.get("application", {}) if isinstance(entry, dict) else {}
                    candidate_id = str(entry.get("id", ""))
                    if (
                        str(application.get("id", "")) == application_id
                        and candidate_id
                        and existing_token_ids is not None
                        and candidate_id not in existing_token_ids
                    ):
                        token_id = candidate_id
                        break
            if token_id:
                await self.api.delete("/oauth2/tokens/{token_id}", token_id=token_id)
        except Exception:
            pass
