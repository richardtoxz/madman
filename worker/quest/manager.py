from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from ..config import QuestConfig
from ..ipc import emit_line
from .api_client import QuestAPIClient
from .models import Quest
from .webhook_dispatch import QuestWebhookDispatchMixin


class QuestManager(QuestWebhookDispatchMixin):
    def __init__(self, client):
        self.client = client
        self.api = QuestAPIClient(client)
        self.config = QuestConfig()
        self.webhook_url: str | None = None
        self.captcha_api_key: str | None = None
        self._webhook_session = None
        self._progress_webhook_bucket: dict[str, int] = {}
        self._last_presence_detail: str | None = None
        self._active_quest_id: str | None = None
        self._cancelled = False
        self._task: asyncio.Task | None = None
        self._sync_runtime_config_to_client()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def cancel(self):
        self._cancelled = True
        if self._task and not self._task.done():
            self._task.cancel()
        self._active_quest_id = None

    async def shutdown(self):
        self.cancel()
        await self.api.close()
        await self.close_webhook_session()

    def _sync_runtime_config_to_client(self):
        self.client._quest_webhook_url = self.webhook_url
        self.client._quest_captcha_api_key = self.captcha_api_key

    def _normalize_webhook_url(self, value) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        if not text.startswith("https://discord.com/api/webhooks/"):
            return None
        return text

    def _normalize_captcha_key(self, value) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        return text or None

    async def set_runtime_config(
        self,
        *,
        webhook_url: str | None,
        captcha_api_key: str | None,
        request_id: str | None = None,
    ):
        normalized_webhook = self._normalize_webhook_url(webhook_url)
        if webhook_url and not normalized_webhook:
            emit_line("QUEST:ERRO:WEBHOOK_URL invalida", request_id)
            return

        self.webhook_url = normalized_webhook
        self.captcha_api_key = self._normalize_captcha_key(captcha_api_key)
        self._sync_runtime_config_to_client()

        if not self.webhook_url and self._webhook_session is not None:
            await self.close_webhook_session()

        emit_line(
            (
                "QUEST:CONFIG:"
                f"webhook={'on' if self.webhook_url else 'off'}:"
                f"captcha={'on' if self.captcha_api_key else 'off'}"
            ),
            request_id,
        )

    def _init_progress_bucket(self, quest_id: str, progress: float, target: int):
        if target <= 0:
            self._progress_webhook_bucket[quest_id] = -1
            return
        pct = min(100, int((progress / target) * 100))
        self._progress_webhook_bucket[quest_id] = pct // 10

    def _can_send_progress_webhook(self, quest_id: str, progress: int, target: int) -> bool:
        if target <= 0:
            return False

        pct = min(100, int((progress / target) * 100))
        bucket = pct // 10
        last_bucket = self._progress_webhook_bucket.get(quest_id, -1)

        if bucket <= last_bucket:
            return False

        self._progress_webhook_bucket[quest_id] = bucket
        return True

    async def fetch_quests(self) -> list[Quest]:
        try:
            data = await self.api.get("/quests/@me")
            raw_quests = data if isinstance(data, list) else data.get("quests", [])
            return [Quest(q) for q in raw_quests]
        except Exception as exc:
            emit_line(f"QUEST:ERRO:Falha ao buscar quests: {exc}")
            return []

    async def accept_quest(self, quest: Quest, request_id=None) -> bool:
        is_android = quest.is_mobile_video_only
        try:
            data = await self.api.post(
                "/quests/{quest_id}/enroll",
                {
                    "location": 12 if is_android else 11,
                    "is_targeted": False,
                    "metadata_raw": None,
                    "metadata_sealed": None,
                },
                quest_id=quest.id,
                headers={"AndroidRequest": "true" if is_android else "false"},
            )
            quest.update_status(data)
            emit_line(
                f"QUEST:AUTO:Inscrito em '{quest.quest_name}' via {'Android' if is_android else 'Desktop'}.",
                request_id,
            )
            task_name, _ = quest.get_task_info()
            asyncio.create_task(
                self.dispatch_quest_start(quest, task_name or "UNKNOWN")
            )
            return True
        except Exception as exc:
            emit_line(
                f"QUEST:ERRO:Falha ao inscrever '{quest.quest_name}': {exc}",
                request_id,
            )
            asyncio.create_task(
                self.dispatch_quest_error(
                    quest_name=quest.quest_name,
                    detail=f"Falha ao inscrever: {exc}",
                )
            )
            return False

    async def claim_reward(self, quest: Quest, request_id=None) -> bool:
        if quest.is_claimed:
            return True

        try:
            payload = {
                "platform": (
                    quest.config.get("rewards_config", {}).get("platforms", [0])[0]
                    if quest.config.get("rewards_config")
                    else 0
                ),
                "location": 11,
                "is_targeted": False,
                "metadata_raw": None,
                "metadata_sealed": None,
                "traffic_metadata_raw": quest.raw.get("traffic_metadata_raw"),
                "traffic_metadata_sealed": quest.raw.get("traffic_metadata_sealed"),
            }
            data = await asyncio.wait_for(
                self.api.post(
                    "/quests/{quest_id}/claim-reward", payload, quest_id=quest.id
                ),
                timeout=20.0,
            )
            quest.update_status(data)
            asyncio.create_task(self.dispatch_quest_reward(quest=quest, success=True))
            return True
        except asyncio.TimeoutError:
            emit_line(
                f"QUEST:AUTO:⚠️ Timeout ao resgatar {quest.id}. Possivel captcha — resgate manualmente no Discord.",
                request_id,
            )
            asyncio.create_task(
                self.dispatch_quest_captcha(
                    service="hcaptcha",
                    sitekey=None,
                    status="timeout-no-claim",
                )
            )
            return False
        except Exception as exc:
            if "captcha" in str(exc).lower() or "403" in str(exc) or "400" in str(exc):
                emit_line(
                    f"QUEST:AUTO:⚠️ Captcha/bloqueio ao resgatar {quest.id}. Resgate manualmente no Discord.",
                    request_id,
                )
                asyncio.create_task(
                    self.dispatch_quest_captcha(
                        service="hcaptcha",
                        sitekey=None,
                        status="blocked-on-claim",
                    )
                )
            else:
                emit_line(f"QUEST:AUTO:⚠️ Erro ao resgatar: {exc}", request_id)
            asyncio.create_task(
                self.dispatch_quest_reward(
                    quest=quest,
                    success=False,
                    detail=str(exc),
                )
            )
            return False

    async def list_quests(self, request_id: str | None = None):
        quests = await self.fetch_quests()
        if not quests:
            emit_line("QUEST:LIST:EMPTY", request_id)
            return

        for quest in quests:
            task_name, task_info = quest.get_task_info()
            target = int(task_info.get("target", 0)) if task_info else 0
            progress = quest.get_progress(task_name) if task_name else 0

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

            emit_line(
                f"QUEST:LIST:{quest.id}|{quest.quest_name}|{quest.application_name}|{task_name or 'UNKNOWN'}|{status}|{int(progress)}/{target}",
                request_id,
            )

        emit_line(f"QUEST:LIST_END:{len(quests)}", request_id)

    async def _update_presence(self, text: str):
        if self.client.presence_manager and self._active_quest_id:
            if self._last_presence_detail == text:
                return
            self._last_presence_detail = text
            await self.client.presence_manager.update_detail(text)

    async def auto_complete_all(self, request_id: str | None = None):
        if self.is_running:
            self.cancel()
            await asyncio.sleep(1)

        self._cancelled = False
        self._task = asyncio.current_task()

        try:
            quests = await self.fetch_quests()
            if not quests:
                emit_line("QUEST:AUTO:Nenhuma quest encontrada.", request_id)
                return

            actionable = []
            for quest in quests:
                if quest.is_expired or quest.is_claimed:
                    continue

                if quest.is_completed and not quest.is_claimed:
                    emit_line(
                        f"QUEST:AUTO:Resgatando '{quest.quest_name}'...", request_id
                    )
                    if await self.claim_reward(quest, request_id):
                        emit_line(
                            f"QUEST:AUTO:✅ '{quest.quest_name}' resgatada!", request_id
                        )
                    continue

                task_name, task_info = quest.get_task_info()
                if not task_name:
                    continue

                if not quest.is_enrolled:
                    if not await self.accept_quest(quest, request_id):
                        continue

                target = int(task_info.get("target", 0))
                progress = quest.get_progress(task_name)
                if progress < target:
                    actionable.append((quest, task_name, target, progress))

            if not actionable:
                emit_line("QUEST:AUTO:Nenhuma quest pendente.", request_id)
                return

            emit_line(f"QUEST:AUTO:Completando {len(actionable)} quest(s)...", request_id)

            for quest, task_name, target, progress in actionable:
                if self._cancelled:
                    emit_line("QUEST:AUTO:Cancelado.", request_id)
                    return

                self._init_progress_bucket(quest.id, progress, target)
                self._active_quest_id = quest.id
                emit_line(
                    f"QUEST:AUTO:{quest.quest_name} [{task_name}] {int(progress)}/{target}",
                    request_id,
                )

                presence_token = None
                if self.client.presence_manager:
                    presence_token = await self.client.presence_manager.set_task(
                        "Quest", quest.quest_name
                    )

                try:
                    success = await self._run_task_logic(quest, task_name, target, request_id)
                    if success:
                        emit_line(
                            f"QUEST:AUTO:✅ '{quest.quest_name}' completa!", request_id
                        )
                        asyncio.create_task(
                            self.dispatch_quest_complete(
                                quest=quest,
                                task_name=task_name,
                            )
                        )
                        await asyncio.sleep(2)
                        if await self.claim_reward(quest, request_id):
                            emit_line(
                                f"QUEST:AUTO:🎁 '{quest.quest_name}' resgatada!",
                                request_id,
                            )
                    elif self._cancelled:
                        emit_line(
                            f"QUEST:AUTO:Cancelado em '{quest.quest_name}'.",
                            request_id,
                        )
                        asyncio.create_task(
                            self.dispatch_quest_error(
                                quest_name=quest.quest_name,
                                detail="Quest cancelada pelo usuario",
                            )
                        )
                    else:
                        emit_line(
                            f"QUEST:AUTO:❌ '{quest.quest_name}' falhou.", request_id
                        )
                        asyncio.create_task(
                            self.dispatch_quest_error(
                                quest_name=quest.quest_name,
                                detail="Falha no fluxo de tarefa",
                            )
                        )
                except Exception as exc:
                    emit_line(f"QUEST:ERRO:{quest.quest_name}: {exc}", request_id)
                    asyncio.create_task(
                        self.dispatch_quest_error(
                            quest_name=quest.quest_name,
                            detail=str(exc),
                        )
                    )
                finally:
                    self._active_quest_id = None
                    self._last_presence_detail = None
                    if self.client.presence_manager:
                        if presence_token:
                            await self.client.presence_manager.complete_task(
                                presence_token
                            )
                        else:
                            await self.client.presence_manager.set_main()

            emit_line("QUEST:AUTO:Todas as quests processadas!", request_id)
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def complete_quest(self, quest_id: str, request_id: str | None = None):
        if self.is_running:
            self.cancel()
            await asyncio.sleep(1)

        self._cancelled = False
        self._active_quest_id = quest_id

        quests = await self.fetch_quests()
        quest = next((item for item in quests if item.id == quest_id), None)
        if not quest:
            emit_line(f"QUEST:ERRO:Quest {quest_id} nao encontrada", request_id)
            return

        if quest.is_expired:
            emit_line(f"QUEST:ERRO:Quest {quest_id} expirada", request_id)
            return

        if quest.is_completed:
            emit_line(
                f"QUEST:ERRO:Quest '{quest.quest_name}' ja foi completada", request_id
            )
            return

        if not quest.is_enrolled:
            if not await self.accept_quest(quest, request_id):
                return

        task_name, task_info = quest.get_task_info()
        if not task_name or not task_info:
            emit_line(
                f"QUEST:ERRO:Quest '{quest.quest_name}' nao possui tarefa reconhecida",
                request_id,
            )
            return

        target = int(task_info.get("target", 0))
        current_progress = quest.get_progress(task_name)
        self._init_progress_bucket(quest.id, current_progress, target)

        emit_line(
            f"QUEST:STARTING:{quest.quest_name}|{task_name}|{int(current_progress)}/{target}",
            request_id,
        )

        presence_token = None
        if self.client.presence_manager:
            presence_token = await self.client.presence_manager.set_task(
                "Quest Completer", quest.quest_name
            )

        self._task = asyncio.create_task(
            self._run_single_quest(quest, task_name, target, presence_token, request_id)
        )

    async def _run_single_quest(
        self,
        quest: Quest,
        task_name: str,
        target: int,
        presence_token,
        request_id,
    ):
        try:
            success = await self._run_task_logic(quest, task_name, target, request_id)
            if success:
                emit_line(f"QUEST:COMPLETE:{quest.quest_name}", request_id)
                asyncio.create_task(
                    self.dispatch_quest_complete(quest=quest, task_name=task_name)
                )
                if await self.claim_reward(quest, request_id):
                    emit_line(f"QUEST:REWARD:{quest.quest_name}", request_id)
            elif self._cancelled:
                emit_line(f"QUEST:CANCELLED:{quest.quest_name}", request_id)
                asyncio.create_task(
                    self.dispatch_quest_error(
                        quest_name=quest.quest_name,
                        detail="Quest cancelada pelo usuario",
                    )
                )
            else:
                emit_line(f"QUEST:FAILED:{quest.quest_name}", request_id)
                asyncio.create_task(
                    self.dispatch_quest_error(
                        quest_name=quest.quest_name,
                        detail="Quest falhou",
                    )
                )
        except Exception as exc:
            emit_line(f"QUEST:ERRO:{quest.quest_name}: {exc}", request_id)
            asyncio.create_task(
                self.dispatch_quest_error(
                    quest_name=quest.quest_name,
                    detail=str(exc),
                )
            )
        finally:
            self._active_quest_id = None
            self._task = None
            self._last_presence_detail = None
            if self.client.presence_manager and presence_token:
                await self.client.presence_manager.complete_task(presence_token)

    async def _run_task_logic(
        self, quest: Quest, task_name: str, target: int, request_id
    ) -> bool:
        if task_name in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            return await self.doing_watch_video_quest(quest, task_name, target, request_id)

        if task_name in (
            "PLAY_ON_DESKTOP",
            "PLAY_ON_DESKTOP_V2",
            "PLAY_ON_XBOX",
            "PLAY_ON_PLAYSTATION",
        ):
            return await self.doing_play_on_platform_quest(
                quest, task_name, target, request_id
            )

        if task_name == "PLAY_ACTIVITY":
            return await self.doing_play_activity_quest(quest, task_name, target, request_id)

        if task_name == "ACHIEVEMENT_IN_ACTIVITY":
            return await self.doing_achievement_in_activity_quest(quest, target, request_id)

        if task_name == "STREAM_ON_DESKTOP":
            emit_line(
                f"QUEST:ERRO:Quest '{quest.quest_name}' usa STREAM_ON_DESKTOP que requer app desktop real.",
                request_id,
            )
            return False

        emit_line(
            f"QUEST:ERRO:Quest '{quest.quest_name}' usa tarefa nao suportada: {task_name}",
            request_id,
        )
        return False

    async def doing_watch_video_quest(
        self, quest: Quest, task_name: str, target: int, request_id
    ) -> bool:
        enrolled_ts = time.time()
        enrolled_str = quest.user_status.get("enrolled_at")
        if enrolled_str:
            try:
                enrolled_ts = datetime.fromisoformat(
                    enrolled_str.replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                pass

        progress = quest.get_progress(task_name)
        max_future = self.config.MAX_FUTURE_SECONDS
        speed = 7
        interval = 1.0
        completed = False

        while progress < target and not self._cancelled:
            max_allowed = (time.time() - enrolled_ts) + max_future
            diff = max_allowed - progress
            timestamp = progress + speed

            if diff >= speed:
                new_progress = min(target, timestamp + random.random())
                try:
                    data = await self.api.post(
                        "/quests/{quest_id}/video-progress",
                        {"timestamp": new_progress},
                        quest_id=quest.id,
                    )
                    quest.update_status(data)
                    completed = bool(data.get("completed_at")) if data else False
                    progress = min(target, timestamp)

                    pct = min(100, int((progress / target) * 100)) if target else 0
                    await self._update_presence("Farmando quests")
                    emit_line(
                        f"QUEST:PROGRESS:{int(progress)}/{target} ({pct}%)",
                        request_id,
                    )
                    if self._can_send_progress_webhook(quest.id, int(progress), target):
                        asyncio.create_task(
                            self.dispatch_quest_progress(
                                quest=quest,
                                task_name=task_name,
                                progress=int(progress),
                                target=target,
                            )
                        )

                    if completed:
                        return True
                except Exception as exc:
                    emit_line(f"QUEST:WARN:video-progress erro: {exc}", request_id)

            if timestamp >= target:
                break

            await asyncio.sleep(interval)

        if not self._cancelled and not completed:
            try:
                data = await self.api.post(
                    "/quests/{quest_id}/video-progress",
                    {"timestamp": target},
                    quest_id=quest.id,
                )
                quest.update_status(data)
                return True
            except Exception:
                return False

        return not self._cancelled

    async def doing_play_on_platform_quest(
        self, quest: Quest, task_name: str, target: int, request_id
    ) -> bool:
        interval = 20.0
        while not quest.is_completed and not self._cancelled:
            progress = quest.get_progress(task_name)
            try:
                data = await self.api.post(
                    "/quests/{quest_id}/heartbeat",
                    {"application_id": quest.application_id, "terminal": False},
                    quest_id=quest.id,
                )
                quest.update_status(data)
                progress = quest.get_progress(task_name)
                pct = min(100, int((progress / target) * 100)) if target else 0
                await self._update_presence("Farmando quests")
                emit_line(
                    f"QUEST:PROGRESS:{int(progress)}/{target} ({pct}%)",
                    request_id,
                )
                if self._can_send_progress_webhook(quest.id, int(progress), target):
                    asyncio.create_task(
                        self.dispatch_quest_progress(
                            quest=quest,
                            task_name=task_name,
                            progress=int(progress),
                            target=target,
                        )
                    )
            except Exception as exc:
                emit_line(f"QUEST:WARN:heartbeat erro: {exc}", request_id)

            if progress >= target:
                break

            await asyncio.sleep(interval)

        if self._cancelled:
            return False

        try:
            data = await self.api.post(
                "/quests/{quest_id}/heartbeat",
                {"application_id": quest.application_id, "terminal": True},
                quest_id=quest.id,
            )
            quest.update_status(data)
            return True
        except Exception:
            return False

    def _resolve_stream_key(self) -> str:
        channel_id = None
        voice_client = getattr(self.client, "voice_client", None)
        if voice_client and getattr(voice_client, "channel", None):
            channel_id = getattr(voice_client.channel, "id", None)

        if channel_id is None:
            for guild in getattr(self.client, "guilds", []):
                me = getattr(guild, "me", None)
                voice_state = getattr(me, "voice", None) if me else None
                if voice_state and getattr(voice_state, "channel", None):
                    channel_id = getattr(voice_state.channel, "id", None)
                    break

        if channel_id is None:
            channel_id = "1"

        return f"call:{channel_id}:1"

    async def doing_play_activity_quest(
        self, quest: Quest, task_name: str, target: int, request_id
    ) -> bool:
        interval = 20.0
        stream_key = self._resolve_stream_key()

        while not quest.is_completed and not self._cancelled:
            progress = quest.get_progress(task_name)
            try:
                data = await self.api.post(
                    "/quests/{quest_id}/heartbeat",
                    {"stream_key": stream_key, "terminal": False},
                    quest_id=quest.id,
                )
                quest.update_status(data)
                progress = quest.get_progress(task_name)
                pct = min(100, int((progress / target) * 100)) if target else 0
                await self._update_presence("Farmando quests")
                emit_line(
                    f"QUEST:PROGRESS:{int(progress)}/{target} ({pct}%)",
                    request_id,
                )
                if self._can_send_progress_webhook(quest.id, int(progress), target):
                    asyncio.create_task(
                        self.dispatch_quest_progress(
                            quest=quest,
                            task_name=task_name,
                            progress=int(progress),
                            target=target,
                        )
                    )
            except Exception as exc:
                emit_line(f"QUEST:WARN:activity heartbeat erro: {exc}", request_id)

            if progress >= target:
                break

            await asyncio.sleep(interval)

        if self._cancelled:
            return False

        try:
            data = await self.api.post(
                "/quests/{quest_id}/heartbeat",
                {"stream_key": stream_key, "terminal": True},
                quest_id=quest.id,
            )
            quest.update_status(data)
            return True
        except Exception:
            return False

    async def doing_achievement_in_activity_quest(
        self, quest: Quest, target: int, request_id
    ) -> bool:
        try:
            query = {
                "response_type": "code",
                "client_id": quest.application_id,
                "scope": "identify applications.commands applications.entitlements",
                "state": "",
            }
            auth_response = await self.api.post(
                "/oauth2/authorize",
                {
                    "permissions": "0",
                    "authorize": True,
                    "integration_type": 1,
                    "location_context": {
                        "guild_id": "10000",
                        "channel_id": "10000",
                        "channel_type": 10000,
                    },
                },
                query=query,
            )

            location = (auth_response or {}).get("location")
            if not location:
                emit_line(
                    f"QUEST:ERRO:Quest '{quest.quest_name}' nao recebeu location do /authorize.",
                    request_id,
                )
                return False

            parsed = urlparse(location)
            auth_code = parse_qs(parsed.query).get("code", [None])[0]
            if not auth_code:
                emit_line(
                    f"QUEST:ERRO:Quest '{quest.quest_name}' nao recebeu auth code.",
                    request_id,
                )
                return False

            progress_token = None
            session = await self.api._get_session()
            async with session.post(
                f"https://{quest.application_id}.discordsays.com/.proxy/acf/authorize",
                json={"code": auth_code},
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://discord.com",
                    "Referer": "https://discord.com/channels/@me",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/138.0.0.0 Safari/537.36"
                    ),
                },
            ) as response:
                body = await response.json(content_type=None)
                if isinstance(body, dict):
                    progress_token = body.get("token")

            if not progress_token:
                emit_line(
                    f"QUEST:ERRO:Quest '{quest.quest_name}' nao recebeu token Discord Says.",
                    request_id,
                )
                return False

            async with session.post(
                f"https://{quest.application_id}.discordsays.com/.proxy/acf/quest/progress",
                json={"progress": target},
                headers={
                    "Content-Type": "application/json",
                    "x-auth-token": progress_token,
                    "Origin": "https://discord.com",
                    "Referer": "https://discord.com/channels/@me",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/138.0.0.0 Safari/537.36"
                    ),
                },
            ) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Discord Says retornou HTTP {response.status}")

            return True
        except Exception as exc:
            emit_line(
                f"QUEST:ERRO:Quest '{quest.quest_name}' falhou no fluxo Discord Says: {exc}",
                request_id,
            )
            return False
