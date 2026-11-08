from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import discord

from ..core.bulk import run_bulk
from ..core.config import BulkConfig
from ..core.ipc import emit_line


class RelationshipManager:
    """Operacoes sociais usando uma fotografia atual das relacoes da conta."""

    MAX_TRANSIENT_ATTEMPTS = 3
    FRIEND_SORTS = ("status", "interaction")
    # O ranking do Discord muda devagar; evita uma chamada HTTP a cada pagina.
    AFFINITY_TTL = 300.0
    MAX_CLOSEST = 50

    def __init__(self, client):
        self.client = client
        self.cancelled = False
        self._bulk_lock = asyncio.Lock()
        self._affinity_cache: dict[int, float] | None = None
        self._affinity_at = 0.0

    @staticmethod
    def _error_code(error: Exception) -> str:
        if isinstance(error, discord.CaptchaRequired):
            return "CAPTCHA"
        if isinstance(error, discord.RateLimited):
            return "RATE_LIMIT_LONGO"
        if isinstance(error, discord.Forbidden):
            return "SEM_PERMISSAO"
        if isinstance(error, discord.NotFound):
            return "NAO_ENCONTRADO"
        if isinstance(error, discord.HTTPException):
            status = getattr(error, "status", 0)
            return f"HTTP_{status}" if status else "HTTP"
        return type(error).__name__.upper()[:40]

    @staticmethod
    def _warning_for(code: str | None) -> str | None:
        if not code:
            return None
        messages = {
            "CAPTCHA": "O Discord exigiu CAPTCHA ao atualizar as relacoes.",
            "RATE_LIMIT_LONGO": "O Discord solicitou uma espera longa ao atualizar as relacoes.",
            "SEM_PERMISSAO": "O Discord recusou a consulta atualizada das relacoes.",
        }
        return messages.get(code, "Nao foi possivel confirmar a lista atual; exibindo o cache local.")

    @staticmethod
    def _ipc_label(value: Any, limit: int = 80) -> str:
        text = " ".join(str(value or "Desconhecido").split())
        text = text.replace("||REQ:", "REQ:").replace(":", "：")
        return text[:limit] or "Desconhecido"

    @staticmethod
    def _display_name(user) -> str:
        username = str(getattr(user, "name", "Desconhecido"))
        global_name = getattr(user, "global_name", None)
        if global_name and global_name != username:
            return f"{global_name} (@{username})"
        return f"@{username}"

    @staticmethod
    def _avatar_url(user) -> str | None:
        avatar = getattr(user, "display_avatar", None)
        if not avatar:
            return None
        try:
            return str(avatar.with_size(64).url)
        except (AttributeError, TypeError, ValueError):
            return str(getattr(avatar, "url", avatar))

    def _basic_info(self, relationship) -> dict[str, Any]:
        user = relationship.user
        return {
            "name": self._display_name(user),
            "id": int(user.id),
            "avatar_url": self._avatar_url(user),
        }

    @staticmethod
    def _page(items: list[Any], page: int, page_size: int) -> tuple[list[Any], int, int]:
        page_size = max(1, min(int(page_size), 25))
        pages = max(1, (len(items) + page_size - 1) // page_size)
        page = max(0, min(int(page), pages - 1))
        start = page * page_size
        return items[start : start + page_size], page, pages

    async def _sleep_interruptibly(self, seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0.0, seconds)
        while not self.cancelled:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return True
            await asyncio.sleep(min(remaining, 0.5))
        return False

    async def _relationship_snapshot(self, *, allow_cache: bool = True) -> tuple[tuple[Any, ...], bool, str | None]:
        """Busca todas as relacoes; cache e somente fallback para leitura."""

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                relationships = await self.client.fetch_relationships()
                return tuple(relationships), True, None
            except asyncio.CancelledError:
                raise
            except (discord.CaptchaRequired, discord.RateLimited) as error:
                last_error = error
                break
            except discord.HTTPException as error:
                last_error = error
                status = int(getattr(error, "status", 0) or 0)
                if attempt == 0 and status >= 500:
                    await asyncio.sleep(1.0)
                    continue
                break
            except Exception as error:
                last_error = error
                break

        code = self._error_code(last_error) if last_error else "DESCONHECIDO"
        if not allow_cache:
            return (), False, code
        try:
            return tuple(self.client.relationships), False, code
        except Exception:
            return (), False, code

    @staticmethod
    def _of_type(relationships: Iterable[Any], *types) -> list[Any]:
        wanted = set(types)
        return [relationship for relationship in relationships if relationship.type in wanted]

    async def get_stats(self):
        relationships, fresh, error_code = await self._relationship_snapshot()
        friends = self._of_type(relationships, discord.RelationshipType.friend)
        blocked = self._of_type(relationships, discord.RelationshipType.blocked)
        incoming = self._of_type(relationships, discord.RelationshipType.incoming_request)
        outgoing = self._of_type(relationships, discord.RelationshipType.outgoing_request)
        online_friends = 0
        for relationship in friends:
            try:
                if relationship.status != discord.Status.offline:
                    online_friends += 1
            except Exception:
                pass

        return {
            "friends": len(friends),
            "blocked": len(blocked),
            "incoming_requests": len(incoming),
            "outgoing_requests": len(outgoing),
            "online_friends": online_friends,
            "total_relationships": len(relationships),
            "fresh": fresh,
            "warning": self._warning_for(error_code),
        }

    async def _affinity_scores(self) -> dict[int, float] | None:
        """Pontuacao de interacao por usuario; None quando o Discord nao respondeu."""

        now = time.monotonic()
        if self._affinity_cache is not None and now - self._affinity_at < self.AFFINITY_TTL:
            return self._affinity_cache
        try:
            affinities = await self.client.user_affinities()
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._affinity_cache
        scores: dict[int, float] = {}
        for affinity in affinities:
            try:
                score = float(affinity.affinity)
            except (TypeError, ValueError):
                continue
            if score > 0:
                scores[int(affinity.user_id)] = score
        self._affinity_cache = scores
        self._affinity_at = now
        return scores

    @staticmethod
    def _closeness_ranks(friend_ids: Iterable[int], scores: dict[int, float]) -> dict[int, int]:
        ranked = sorted(
            (user_id for user_id in friend_ids if user_id in scores),
            key=lambda user_id: (-scores[user_id], user_id),
        )
        return {user_id: position for position, user_id in enumerate(ranked, start=1)}

    async def get_friends_list(self, page: int = 0, page_size: int = 20, sort: str = "status"):
        sort = sort if sort in self.FRIEND_SORTS else "status"
        relationships, fresh, error_code = await self._relationship_snapshot()
        items = []
        for relationship in self._of_type(relationships, discord.RelationshipType.friend):
            user = relationship.user
            activity = None
            try:
                current_activity = relationship.activity
                if current_activity and getattr(current_activity, "name", None):
                    prefixes = {
                        0: "Jogando",
                        1: "Transmitindo",
                        2: "Ouvindo",
                        3: "Assistindo",
                        4: "",
                        5: "Competindo",
                    }
                    activity_type = getattr(getattr(current_activity, "type", None), "value", None)
                    activity = (f"{prefixes.get(activity_type, '')} {current_activity.name}").strip()
            except Exception:
                pass

            try:
                status = str(relationship.status)
            except Exception:
                status = "unknown"

            items.append(
                {
                    **self._basic_info(relationship),
                    "nick": relationship.nick,
                    "since": (int(relationship.since.timestamp()) if relationship.since else None),
                    "status": status,
                    "activity": activity,
                    "username": str(getattr(user, "name", "Desconhecido")),
                }
            )

        affinity_available = False
        ranks: dict[int, int] = {}
        if sort == "interaction":
            scores = await self._affinity_scores()
            affinity_available = scores is not None
            ranks = self._closeness_ranks((item["id"] for item in items), scores or {})
        for item in items:
            item["closeness"] = ranks.get(item["id"])

        status_order = {"online": 0, "idle": 1, "dnd": 2, "offline": 3}
        items.sort(
            key=lambda item: (
                item["closeness"] is None,
                item["closeness"] or 0,
                status_order.get(item["status"], 9),
                item["name"].casefold(),
                item["id"],
            )
        )
        page_items, page, pages = self._page(items, page, page_size)
        warning = self._warning_for(error_code)
        if sort == "interaction" and not affinity_available:
            warning = warning or ("O Discord nao informou com quem voce mais conversa; exibindo a ordem normal.")
        return {
            "items": page_items,
            "total": len(items),
            "page": page,
            "pages": pages,
            "sort": sort,
            "affinity_available": affinity_available,
            "fresh": fresh,
            "warning": warning,
        }

    async def get_closest_friends(self, limit: int = 10):
        """IDs dos amigos atuais com quem a conta mais interage, do mais proximo ao menos."""

        limit = max(1, min(int(limit), self.MAX_CLOSEST))
        relationships, fresh, error_code = await self._relationship_snapshot(allow_cache=False)
        if not fresh:
            return {
                "ok": False,
                "code": error_code or "FETCH_FAILED",
                "message": "Nao foi possivel confirmar a lista atual de amigos.",
            }
        scores = await self._affinity_scores()
        if scores is None:
            return {
                "ok": False,
                "code": "AFFINITY_UNAVAILABLE",
                "message": "O Discord nao informou com quem voce mais conversa.",
            }
        friends = {
            int(relationship.user.id): relationship
            for relationship in self._of_type(relationships, discord.RelationshipType.friend)
        }
        ranks = self._closeness_ranks(friends, scores)
        closest = sorted(ranks, key=ranks.get)[:limit]
        return {
            "ok": True,
            "code": "OK",
            "items": [self._basic_info(friends[user_id]) for user_id in closest],
            "ranked_total": len(ranks),
        }

    async def get_pending_requests(self, page: int = 0, page_size: int = 10):
        relationships, fresh, error_code = await self._relationship_snapshot()
        incoming = sorted(
            self._of_type(relationships, discord.RelationshipType.incoming_request),
            key=lambda relationship: int(relationship.user.id),
        )
        outgoing = sorted(
            self._of_type(relationships, discord.RelationshipType.outgoing_request),
            key=lambda relationship: int(relationship.user.id),
        )
        page_size = max(1, min(int(page_size), 25))
        pages = max(
            1,
            (len(incoming) + page_size - 1) // page_size,
            (len(outgoing) + page_size - 1) // page_size,
        )
        page = max(0, min(int(page), pages - 1))
        start = page * page_size
        return {
            "incoming": [self._basic_info(relationship) for relationship in incoming[start : start + page_size]],
            "outgoing": [self._basic_info(relationship) for relationship in outgoing[start : start + page_size]],
            "incoming_total": len(incoming),
            "outgoing_total": len(outgoing),
            "page": page,
            "pages": pages,
            "fresh": fresh,
            "warning": self._warning_for(error_code),
        }

    async def get_blocked_list(self, page: int = 0, page_size: int = 20):
        relationships, fresh, error_code = await self._relationship_snapshot()
        blocked = sorted(
            self._of_type(relationships, discord.RelationshipType.blocked),
            key=lambda relationship: int(relationship.user.id),
        )
        page_items, page, pages = self._page(blocked, page, page_size)
        return {
            "items": [self._basic_info(relationship) for relationship in page_items],
            "total": len(blocked),
            "page": page,
            "pages": pages,
            "fresh": fresh,
            "warning": self._warning_for(error_code),
        }

    async def _run_bulk(
        self,
        *,
        relationships: list[Any],
        operation: Callable[[Any], Awaitable[Any]],
        desired_remaining_types: tuple[Any, ...],
        desired_result_type: Any | None,
        not_found_is_success: bool,
        prefix: str,
        request_id: str | None,
        config: BulkConfig | None = None,
    ) -> dict[str, Any]:
        async def run_operation(relationship) -> None:
            await operation(relationship)

        return await run_bulk(
            self,
            relationships,
            prefix=prefix,
            request_id=request_id,
            config=config or BulkConfig(),
            emit=emit_line,
            operation=run_operation,
            item_id=lambda relationship: int(relationship.user.id),
            label=lambda relationship: self._ipc_label(
                f"{getattr(relationship.user, 'name', 'usuario')} ({relationship.user.id})"
            ),
            # Delete e idempotente; accept exige que o pedido vire amizade.
            not_found_outcome="already" if not_found_is_success else "failed",
            delay_after_success_only=False,
            verify=lambda target_ids: self._verify_remaining(target_ids, desired_remaining_types, desired_result_type),
        )

    async def _verify_remaining(
        self,
        target_ids: set[int],
        desired_remaining_types: tuple[Any, ...],
        desired_result_type: Any | None,
    ) -> tuple[int | None, str | None]:
        verified, fresh, verification_error = await self._relationship_snapshot(allow_cache=False)
        if not fresh:
            return None, verification_error
        verified_by_id = {
            int(relationship.user.id): relationship
            for relationship in verified
            if int(relationship.user.id) in target_ids
        }
        if desired_result_type is not None:
            remaining = sum(
                1
                for target_id in target_ids
                if target_id not in verified_by_id or verified_by_id[target_id].type != desired_result_type
            )
        else:
            wanted_types = set(desired_remaining_types)
            remaining = sum(1 for relationship in verified_by_id.values() if relationship.type in wanted_types)
        return remaining, verification_error

    async def _bulk_from_fresh_snapshot(
        self,
        *,
        types: tuple[Any, ...],
        operation: Callable[[Any], Awaitable[Any]],
        prefix: str,
        request_id: str | None,
        whitelist: set[int] | None = None,
        desired_result_type: Any | None = None,
        not_found_is_success: bool = True,
    ) -> dict[str, Any]:
        if self._bulk_lock.locked():
            summary = "outra operacao social ja esta em andamento nesta conta"
            emit_line(f"{prefix}_RESULT:ABORTADO:{summary}", request_id)
            return {
                "status": "ABORTADO",
                "processed": 0,
                "total": 0,
                "success": 0,
                "already_done": 0,
                "failed": 0,
                "remaining": None,
                "rate_limits": 0,
                "duration": 0.0,
            }

        async with self._bulk_lock:
            self.cancelled = False
            relationships, fresh, error_code = await self._relationship_snapshot(allow_cache=False)
            if not fresh:
                summary = (
                    f"lista atualizada indisponivel; nenhuma alteracao foi realizada ({(error_code or 'erro').lower()})"
                )
                emit_line(f"{prefix}_RESULT:ABORTADO:{summary}", request_id)
                return {
                    "status": "ABORTADO",
                    "processed": 0,
                    "total": 0,
                    "success": 0,
                    "already_done": 0,
                    "failed": 0,
                    "remaining": None,
                    "rate_limits": 0,
                    "duration": 0.0,
                }

            blocked_ids = whitelist or set()
            selected = [
                relationship
                for relationship in relationships
                if relationship.type in set(types) and int(relationship.user.id) not in blocked_ids
            ]
            return await self._run_bulk(
                relationships=selected,
                operation=operation,
                desired_remaining_types=types,
                desired_result_type=desired_result_type,
                not_found_is_success=not_found_is_success,
                prefix=prefix,
                request_id=request_id,
            )

    async def remove_all_friends(self, whitelist, request_id: str | None = None):
        return await self._bulk_from_fresh_snapshot(
            types=(discord.RelationshipType.friend,),
            operation=lambda relationship: relationship.delete(),
            prefix="REL_CLEANUP",
            request_id=request_id,
            whitelist={int(user_id) for user_id in whitelist},
        )

    async def unblock_all(self, request_id: str | None = None):
        return await self._bulk_from_fresh_snapshot(
            types=(discord.RelationshipType.blocked,),
            operation=lambda relationship: relationship.delete(),
            prefix="REL_UNBLOCK",
            request_id=request_id,
        )

    async def accept_all_requests(self, request_id: str | None = None):
        return await self._bulk_from_fresh_snapshot(
            types=(discord.RelationshipType.incoming_request,),
            operation=lambda relationship: relationship.accept(),
            prefix="REL_ACCEPT",
            request_id=request_id,
            desired_result_type=discord.RelationshipType.friend,
            not_found_is_success=False,
        )

    async def deny_all_requests(self, request_id: str | None = None):
        return await self._bulk_from_fresh_snapshot(
            types=(
                discord.RelationshipType.incoming_request,
                discord.RelationshipType.outgoing_request,
            ),
            operation=lambda relationship: relationship.delete(),
            prefix="REL_DENY",
            request_id=request_id,
        )

    async def _fresh_relationship(self, user_id: int):
        relationships, fresh, error_code = await self._relationship_snapshot(allow_cache=False)
        if not fresh:
            return None, {
                "ok": False,
                "code": error_code or "FETCH_FAILED",
                "message": "Nao foi possivel confirmar a relacao atual.",
            }
        relationship = next(
            (item for item in relationships if int(item.user.id) == int(user_id)),
            None,
        )
        return relationship, None

    def _action_error(self, error: Exception, default: str) -> dict[str, Any]:
        code = self._error_code(error)
        messages = {
            "CAPTCHA": "O Discord exigiu CAPTCHA para concluir a acao.",
            "RATE_LIMIT_LONGO": "O Discord solicitou uma espera longa. Tente mais tarde.",
            "SEM_PERMISSAO": "O Discord nao permitiu esta acao.",
            "NAO_ENCONTRADO": "O usuario ou relacionamento nao foi encontrado.",
        }
        return {"ok": False, "code": code, "message": messages.get(code, default)}

    async def set_friend_nickname(self, user_id, nickname):
        relationship, error = await self._fresh_relationship(int(user_id))
        if error:
            return error
        if not relationship or relationship.type != discord.RelationshipType.friend:
            return {
                "ok": False,
                "code": "NOT_FRIEND",
                "message": "O usuario nao esta na lista de amigos.",
            }
        try:
            await relationship.edit(nick=nickname)
            return {"ok": True, "code": "UPDATED", "message": "Apelido atualizado."}
        except asyncio.CancelledError:
            raise
        except Exception as action_error:
            return self._action_error(action_error, "Falha ao atualizar o apelido.")

    async def send_friend_request_by_name(self, username):
        try:
            await self.client.send_friend_request(username)
            return {"ok": True, "code": "SENT", "message": "Pedido enviado."}
        except asyncio.CancelledError:
            raise
        except Exception as action_error:
            return self._action_error(action_error, "Falha ao enviar o pedido.")

    async def block_user(self, user_id):
        user_id = int(user_id)
        if getattr(self.client.user, "id", None) == user_id:
            return {
                "ok": False,
                "code": "SELF_TARGET",
                "message": "Nao e possivel bloquear a propria conta.",
            }

        relationships, fresh, _ = await self._relationship_snapshot()
        if fresh and any(
            int(item.user.id) == user_id and item.type == discord.RelationshipType.blocked for item in relationships
        ):
            return {"ok": True, "code": "ALREADY_BLOCKED", "message": "Usuario ja bloqueado."}

        try:
            user = self.client.get_user(user_id) or await self.client.fetch_user(user_id)
            await user.block()
            return {"ok": True, "code": "BLOCKED", "message": "Usuario bloqueado."}
        except asyncio.CancelledError:
            raise
        except Exception as action_error:
            return self._action_error(action_error, "Falha ao bloquear o usuario.")

    async def unblock_user(self, user_id):
        relationship, error = await self._fresh_relationship(int(user_id))
        if error:
            return error
        if not relationship or relationship.type != discord.RelationshipType.blocked:
            return {
                "ok": True,
                "code": "ALREADY_UNBLOCKED",
                "message": "Usuario ja esta desbloqueado.",
            }
        try:
            await relationship.delete()
            return {"ok": True, "code": "UNBLOCKED", "message": "Usuario desbloqueado."}
        except discord.NotFound:
            return {
                "ok": True,
                "code": "ALREADY_UNBLOCKED",
                "message": "Usuario ja esta desbloqueado.",
            }
        except asyncio.CancelledError:
            raise
        except Exception as action_error:
            return self._action_error(action_error, "Falha ao desbloquear o usuario.")
