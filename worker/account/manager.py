from __future__ import annotations

import asyncio
import datetime
import re
import time
from typing import Any

import discord
from discord.http import Route

from ..tracker.card import profile_visuals

STATUS_VALUES = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible,
}
CUSTOM_TEXT_LIMIT = 128
MAX_EXPIRY = datetime.timedelta(days=30)
EMOJI_NAME = re.compile(r"^[A-Za-z0-9_]{2,32}$")
ACK_BATCH = 100
ACK_PAUSE = 1.0
MENTION_PAUSE = 0.6
# O perfil muda pouco; evita uma busca no Discord a cada abertura do Início.
PROFILE_TTL_SECONDS = 600.0
DEVICE_BY_CLIENT = {"desktop": "desktop", "mobile": "mobile", "web": "web"}
DEVICE_ORDER = ("desktop", "mobile", "web", "embedded")
DEVICE_STATUS_RANK = {"online": 4, "dnd": 3, "idle": 2, "invisible": 1, "offline": 0}


def _result(ok: bool, code: str, message: str, **extra) -> dict[str, Any]:
    return {"ok": ok, "code": code, "message": message, **extra}


def _ms(value: datetime.datetime | None) -> int | None:
    return int(value.timestamp() * 1000) if value else None


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class AccountManager:
    """Conta: status personalizado, sessões autorizadas, menções e leitura."""

    def __init__(self, client):
        self.client = client
        self._lock = asyncio.Lock()
        self._profile_cache: dict[str, Any] | None = None
        self._profile_at = 0.0

    # ------------------------------------------------------------ status

    def _emoji_payload(self, emoji) -> dict[str, Any] | None:
        if emoji is None:
            return None
        emoji_id = getattr(emoji, "id", None)
        payload = {
            "name": getattr(emoji, "name", None) or "",
            "id": str(emoji_id) if emoji_id else None,
            "animated": bool(getattr(emoji, "animated", False)),
        }
        if emoji_id:
            payload["url"] = str(getattr(emoji, "url", "") or "")
        return payload

    def get_custom_status(self) -> dict[str, Any]:
        settings = getattr(self.client, "settings", None)
        if settings is None:
            return _result(False, "SETTINGS_UNAVAILABLE", "As configurações da conta ainda não carregaram.")
        status = getattr(settings, "status", None)
        status_value = getattr(status, "value", None) or "online"
        if status_value == "offline":
            status_value = "invisible"
        custom = settings.custom_activity
        expires_at = getattr(custom, "expires_at", None) if custom else None
        if expires_at is not None and expires_at <= discord.utils.utcnow():
            custom = None
        user = getattr(self.client, "user", None)
        avatar = getattr(getattr(user, "display_avatar", None), "url", None)
        return _result(
            True,
            "STATUS",
            "Status carregado.",
            user={
                "username": getattr(user, "name", None),
                "display_name": getattr(user, "global_name", None) or getattr(user, "name", None),
                "avatar_url": str(avatar) if avatar else None,
            },
            status=status_value if status_value in STATUS_VALUES else "online",
            text=(custom.name or "") if custom else "",
            emoji=self._emoji_payload(custom.emoji) if custom else None,
            expires_at_ms=_ms(expires_at) if custom else None,
        )

    @staticmethod
    def _parse_emoji(raw) -> discord.PartialEmoji | None:
        if raw in (None, ""):
            return None
        if isinstance(raw, str):
            text = raw.strip()
            # Emoji Unicode (1 grafema, até alguns code points com modificadores).
            if not text or len(text) > 16 or any(ch.isalnum() and ord(ch) < 128 for ch in text):
                raise ValueError("emoji invalido")
            return discord.PartialEmoji(name=text)
        if isinstance(raw, dict):
            emoji_id = int(str(raw.get("id") or "0"))
            name = str(raw.get("name") or "")
            if emoji_id <= 0 or emoji_id >= 2**64 or not EMOJI_NAME.match(name):
                raise ValueError("emoji invalido")
            return discord.PartialEmoji(name=name, id=emoji_id, animated=bool(raw.get("animated")))
        raise ValueError("emoji invalido")

    async def set_custom_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = getattr(self.client, "settings", None)
        if settings is None:
            return _result(False, "SETTINGS_UNAVAILABLE", "As configurações da conta ainda não carregaram.")

        changes: dict[str, Any] = {}
        status_key = payload.get("status")
        if status_key is not None:
            if status_key not in STATUS_VALUES:
                return _result(False, "INVALID_STATUS", "Status inválido.")
            changes["status"] = STATUS_VALUES[status_key]

        if payload.get("clear"):
            changes["custom_activity"] = None
        elif "text" in payload or "emoji" in payload:
            text = " ".join(str(payload.get("text") or "").split())
            if len(text) > CUSTOM_TEXT_LIMIT:
                return _result(False, "TEXT_TOO_LONG", f"O texto pode ter até {CUSTOM_TEXT_LIMIT} caracteres.")
            try:
                emoji = self._parse_emoji(payload.get("emoji"))
            except (TypeError, ValueError):
                return _result(False, "INVALID_EMOJI", "Emoji inválido. Use um emoji comum ou deixe em branco.")

            expires_at = None
            raw_expiry = payload.get("expires_at_ms")
            if raw_expiry not in (None, "", 0):
                try:
                    expires_at = datetime.datetime.fromtimestamp(int(raw_expiry) / 1000, tz=datetime.timezone.utc)
                except (TypeError, ValueError, OverflowError, OSError):
                    return _result(False, "INVALID_EXPIRY", "Prazo inválido.")
                now = discord.utils.utcnow()
                if expires_at <= now or expires_at - now > MAX_EXPIRY:
                    return _result(False, "INVALID_EXPIRY", "O prazo precisa estar entre agora e 30 dias.")

            if text or emoji:
                changes["custom_activity"] = discord.CustomActivity(text or None, emoji=emoji, expires_at=expires_at)
            else:
                changes["custom_activity"] = None

        if not changes:
            return _result(False, "NOTHING_TO_CHANGE", "Nada para alterar.")

        try:
            await settings.edit(**changes)
        except discord.HTTPException as error:
            return _result(False, f"HTTP_{error.status}", "O Discord recusou a alteração do status.")

        presence = getattr(self.client, "presence_manager", None)
        if presence is not None:
            await presence.refresh()
        result = self.get_custom_status()
        result.update(code="STATUS_SAVED", message="Status atualizado.")
        return result

    # ------------------------------------------------------------ perfil

    async def get_profile(self, *, refresh: bool = False) -> dict[str, Any]:
        """Perfil público da própria conta: insígnias reais, banner, decoração e tema."""

        user = getattr(self.client, "user", None)
        if user is None:
            return _result(False, "NOT_READY", "A conta ainda não terminou de conectar.")
        now = time.monotonic()
        if not refresh and self._profile_cache is not None and now - self._profile_at < PROFILE_TTL_SECONDS:
            return self._with_status(self._profile_cache)

        visuals: dict[str, Any] | None = None
        try:
            profile = await self.client.fetch_user_profile(user.id, with_mutual_guilds=False, with_mutual_friends=False)
            visuals = profile_visuals(profile)
        except asyncio.CancelledError:
            raise
        except Exception:
            visuals = None

        if visuals is None:
            # Sem o perfil não há insígnias: melhor nenhuma do que ícones inventados.
            if self._profile_cache is not None:
                return self._with_status(self._profile_cache)
            avatar = getattr(getattr(user, "display_avatar", None), "url", None)
            fallback = {
                "username": getattr(user, "name", None),
                "display_name": getattr(user, "global_name", None),
                "avatar_url": str(avatar) if avatar else None,
                "badges": [],
                "complete": False,
            }
            return self._with_status(fallback)

        visuals["complete"] = True
        visuals["id"] = str(user.id)
        self._profile_cache = visuals
        self._profile_at = now
        return self._with_status(visuals)

    def _devices(self) -> list[dict[str, str]]:
        """Onde a conta está aberta agora, um item por tipo de aparelho, sem a sessão do painel."""

        connection = getattr(self.client, "_connection", None)
        own = getattr(connection, "session_id", None)
        best: dict[str, str] = {}
        for session in tuple(getattr(self.client, "sessions", ()) or ()):
            session_id = getattr(session, "session_id", None)
            if session_id in (None, "all") or session_id == own:
                continue
            os_name = getattr(getattr(session, "os", None), "name", "")
            client = str(getattr(session, "client", "") or "")
            device = "embedded" if os_name == "playstation" else DEVICE_BY_CLIENT.get(client)
            if device is None:
                continue
            status = str(getattr(session, "status", "") or "")
            status = status if status in DEVICE_STATUS_RANK else "offline"
            current = best.get(device)
            if current is None or DEVICE_STATUS_RANK[status] > DEVICE_STATUS_RANK[current]:
                best[device] = status
        return [{"device": d, "status": best[d]} for d in DEVICE_ORDER if d in best]

    def _with_status(self, profile: dict[str, Any]) -> dict[str, Any]:
        status = self.get_custom_status()
        return _result(
            True,
            "PROFILE",
            "Perfil carregado.",
            profile=dict(profile),
            status=status.get("status") if status.get("ok") else None,
            text=status.get("text", "") if status.get("ok") else "",
            emoji=status.get("emoji") if status.get("ok") else None,
            devices=self._devices(),
        )

    # ------------------------------------------------------------ sessões

    def _current_session_hash(self) -> str | None:
        return getattr(getattr(self.client, "_connection", None), "auth_session_id", None)

    async def list_sessions(self) -> dict[str, Any]:
        try:
            data = await self.client.http.request(Route("GET", "/auth/sessions"))
        except discord.HTTPException as error:
            return _result(False, f"HTTP_{error.status}", "Não foi possível listar as sessões.")
        current = self._current_session_hash()
        sessions = []
        for raw in (data or {}).get("user_sessions", []) or []:
            id_hash = str(raw.get("id_hash") or "")
            if not id_hash:
                continue
            info = raw.get("client_info") or {}
            last_used = discord.utils.parse_time(raw.get("approx_last_used_time"))
            sessions.append(
                {
                    "id_hash": id_hash,
                    "os": _clip(info.get("os") or "Desconhecido", 40),
                    "platform": _clip(info.get("platform") or "", 60),
                    "location": _clip(info.get("location") or "", 80),
                    "last_used_ms": _ms(last_used),
                    "current": bool(current) and id_hash == current,
                }
            )
        sessions.sort(key=lambda s: (not s["current"], -(s["last_used_ms"] or 0)))
        return _result(True, "SESSIONS", "Sessões carregadas.", sessions=sessions)

    # ------------------------------------------------------------ menções

    def _serialize_mention(self, message) -> dict[str, Any]:
        channel = message.channel
        guild = getattr(message, "guild", None)
        author = message.author
        if guild is not None:
            where = f"#{getattr(channel, 'name', '') or 'canal'}"
        elif isinstance(channel, discord.DMChannel):
            where = "Mensagem direta"
        else:
            where = getattr(channel, "name", None) or "Grupo"
        avatar = getattr(getattr(author, "display_avatar", None), "url", None)
        kind = "everyone" if message.mention_everyone else ("role" if message.role_mentions else "direct")
        return {
            "id": str(message.id),
            "channel_id": str(channel.id),
            "guild_id": str(guild.id) if guild else None,
            "guild_name": _clip(guild.name, 80) if guild else None,
            "where": _clip(where, 80),
            "author": _clip(getattr(author, "global_name", None) or author.name, 64),
            "author_avatar": str(avatar) if avatar else None,
            "content": _clip(message.content or ("[anexo]" if message.attachments else ""), 300),
            "created_ms": _ms(message.created_at),
            "jump_url": message.jump_url,
            "kind": kind,
        }

    async def list_mentions(self, *, limit: int = 25, before: int | None = None) -> dict[str, Any]:
        limit = max(1, min(int(limit), 50))
        kwargs: dict[str, Any] = {"limit": limit}
        if before:
            kwargs["before"] = discord.Object(id=int(before))
        items = []
        try:
            async for message in self.client.recent_mentions(**kwargs):
                try:
                    items.append(self._serialize_mention(message))
                except Exception:
                    continue
        except discord.HTTPException as error:
            return _result(False, f"HTTP_{error.status}", "Não foi possível carregar as menções.")
        return _result(
            True,
            "MENTIONS",
            "Menções carregadas.",
            mentions=items,
            has_more=len(items) >= limit,
        )

    async def delete_mentions(self, message_ids: list[int]) -> dict[str, Any]:
        removed = failed = 0
        for index, message_id in enumerate(message_ids[:100]):
            if index:
                await asyncio.sleep(MENTION_PAUSE)
            try:
                await self.client.delete_recent_mention(discord.Object(id=int(message_id)))
                removed += 1
            except discord.NotFound:
                removed += 1
            except discord.HTTPException:
                failed += 1
        return _result(
            failed == 0,
            "MENTIONS_REMOVED" if failed == 0 else "MENTIONS_PARTIAL",
            f"{removed} menção(ões) removida(s) da caixa." + (f" {failed} falharam." if failed else ""),
            removed=removed,
            failed=failed,
        )

    # ------------------------------------------------------------ leitura

    def _unread_channels(self) -> list[tuple[Any, int, int]]:
        state = getattr(self.client, "_connection", None)
        get_read_state = getattr(state, "get_read_state", None)
        if get_read_state is None:
            return []

        channels: list = list(getattr(self.client, "private_channels", []) or [])
        for guild in getattr(self.client, "guilds", []) or []:
            if getattr(guild, "unavailable", False):
                continue
            me = getattr(guild, "me", None)
            for channel in (*getattr(guild, "channels", ()), *getattr(guild, "threads", ())):
                if getattr(channel, "last_message_id", None) is None:
                    continue
                if me is not None:
                    try:
                        if not channel.permissions_for(me).read_messages:
                            continue
                    except Exception:
                        pass
                channels.append(channel)

        unread = []
        for channel in channels:
            last_id = int(getattr(channel, "last_message_id", 0) or 0)
            if not last_id:
                continue
            read_state = get_read_state(channel.id, if_exists=True)
            acked = int(getattr(read_state, "last_acked_id", 0) or 0) if read_state else 0
            if last_id > acked:
                mentions = int(getattr(read_state, "badge_count", 0) or 0) if read_state else 0
                unread.append((channel, last_id, mentions))
        return unread

    def unread_summary(self) -> dict[str, Any]:
        unread = self._unread_channels()
        private = sum(1 for channel, _, _ in unread if getattr(channel, "guild", None) is None)
        return _result(
            True,
            "UNREAD",
            "Resumo de não lidas.",
            channels=len(unread),
            private=private,
            guild=len(unread) - private,
            mentions=sum(mentions for _, _, mentions in unread),
        )

    async def ack_all(self, scope: str = "all") -> dict[str, Any]:
        if self._lock.locked():
            return _result(False, "BUSY", "Já existe uma marcação de leitura em andamento.")
        async with self._lock:
            unread = self._unread_channels()
            if scope == "private":
                unread = [u for u in unread if getattr(u[0], "guild", None) is None]
            elif scope == "guilds":
                unread = [u for u in unread if getattr(u[0], "guild", None) is not None]
            if not unread:
                return _result(True, "NOTHING_UNREAD", "Não havia nada para marcar como lido.", acked=0)

            entries = [
                {"channel_id": str(channel.id), "message_id": str(last_id), "read_state_type": 0}
                for channel, last_id, _ in unread
            ]
            acked = failed = 0
            for start in range(0, len(entries), ACK_BATCH):
                if start:
                    await asyncio.sleep(ACK_PAUSE)
                batch = entries[start : start + ACK_BATCH]
                try:
                    await self.client.http.ack_bulk(batch)
                    acked += len(batch)
                except discord.HTTPException:
                    failed += len(batch)

            return _result(
                failed == 0,
                "ACKED" if failed == 0 else "ACK_PARTIAL",
                f"{acked} conversa(s) marcada(s) como lida(s)." + (f" {failed} falharam." if failed else ""),
                acked=acked,
                failed=failed,
            )
