"""Modo demo do painel web: um manager falso com dados inventados, para desenvolver o front.

Liga com `python -m panel.main --demo`. Nada aqui fala com o Discord nem sobe worker.
As rotas, a segurança e o front são os de verdade; só o `SelfbotManager` é trocado.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from engine.credentials import clean_token
from presence_protocol import validate_custom

DEMO_USER_ID = 100000000000000001
DEMO_USERNAME = "madman.demo"
DEMO_MODES = ("normal", "empty", "error")
MAX_LATENCY_MS = 10_000
ERROR_MESSAGE = "Erro simulado pelo modo demo."
NOT_CONNECTED = {"ok": False, "code": "NOT_CONNECTED", "message": "Conta não autenticada."}

_FIRST = (
    "Ana",
    "Bruno",
    "Carla",
    "Diego",
    "Eduarda",
    "Felipe",
    "Gabi",
    "Heitor",
    "Isa",
    "João",
    "Kaique",
    "Larissa",
    "Marcos",
    "Nina",
    "Otávio",
    "Paula",
    "Rafa",
    "Sofia",
    "Thiago",
    "Vitória",
    "Wesley",
    "Yasmin",
    "Lucas",
    "Bia",
)
_LAST = ("Souza", "Lima", "Costa", "Rocha", "Alves", "Melo", "Dias", "Nunes", "Ramos", "Pires", "Teixeira", "Moura")
_GUILDS = (
    "Café dos Devs",
    "Clube do Valorant",
    "Rinha de Bots",
    "Estudos Python BR",
    "Lo-fi & Chill",
    "Guilda Aurora",
    "Minecraft Survival",
    "Memes Ilimitados",
    "Design Brasil",
    "Speedrunners",
    "Clube do Livro",
    "Torneio Semanal",
    "Animes da Temporada",
    "Servidor da Faculdade",
)
_GAMES = ("Valorant", "Minecraft", "League of Legends", "Counter-Strike 2", "Stardew Valley", "Elden Ring")
_MESSAGES = (
    "alguém on pra uma partida hoje?",
    "olha esse PR quando puder",
    "@everyone reunião às 20h no canal de voz",
    "valeu pela ajuda ontem!",
    "o deploy caiu de novo kkk",
    "bora fechar o torneio sábado?",
)
_STATUSES = ("online", "online", "idle", "dnd", "offline", "offline")
# Ícones de insígnia públicos do Discord (o painel só carrega imagens de cdn.discordapp.com).
_BADGES = (
    {
        "url": "https://cdn.discordapp.com/badge-icons/8a88d63823d8a71cd5e390baa45efa02.png",
        "description": "HypeSquad Bravery",
    },
    {
        "url": "https://cdn.discordapp.com/badge-icons/6bdc42827a38498929a4920da12695d9.png",
        "description": "Desenvolvedor Ativo",
    },
)


@dataclass
class DemoState:
    mode: str = "normal"
    latency_ms: int = 0
    connected: bool = True

    def as_dict(self) -> dict:
        return {"ok": True, "mode": self.mode, "latency_ms": self.latency_ms, "connected": self.connected}


def _emoji(value) -> dict | None:
    """Emoji no formato que o worker devolve: {name, id, url}; texto vira emoji Unicode."""
    if isinstance(value, dict):
        return value
    return {"name": value, "id": None, "url": None} if value else None


def _now_ms() -> int:
    return int(time.time() * 1000)


class DemoManager:
    """Faz o papel do `SelfbotManager` para as rotas web, com dados em memória."""

    def __init__(self, *, seed: int = 42, step_delay: float = 0.25) -> None:
        self.state = DemoState()
        self.step_delay = step_delay
        self._rng = random.Random(seed)
        self._used_names: set[str] = set()
        self._cancel = False
        self._settings: dict[str, Any] = {"webhook_url": "https://discord.com/api/webhooks/1/demo"}
        self._whitelist: list[int] = []
        self._guild_whitelist: list[int] = []
        self._protection_enabled_users: set[int] = {DEMO_USER_ID}
        self._tracker_settings: dict[int, dict] = {}
        self._rich_enabled = True
        self._rich_custom: dict | None = None
        self._rich_saved_ms = _now_ms()
        self._login_ms = _now_ms() - 47 * 60_000
        self._status = {"status": "online", "text": "codando o modo demo", "emoji": _emoji("💻"), "expires_at_ms": None}
        self._voice: dict[str, Any] = {"in_voice": True, "channel_name": "Sala de estudos", "guild_name": _GUILDS[3]}
        self._build_data()

    # ------------------------------------------------------------------ dados

    def _id(self) -> int:
        return self._rng.randrange(10**17, 10**18)

    def _person(self) -> str:
        """Nome inédito; só repete quando as combinações acabam."""
        for _ in range(50):
            name = f"{self._rng.choice(_FIRST)} {self._rng.choice(_LAST)}"
            if name not in self._used_names:
                break
        self._used_names.add(name)
        return name

    def _build_data(self) -> None:
        rng = self._rng
        now = _now_ms()
        self._friends = []
        for _ in range(42):
            name = self._person()
            status = rng.choice(_STATUSES)
            activity = f"Jogando {rng.choice(_GAMES)}" if status != "offline" and rng.random() < 0.35 else None
            self._friends.append(
                {
                    "id": self._id(),
                    "name": name,
                    "username": name.lower().replace(" ", "."),
                    "status": status,
                    "activity": activity,
                }
            )
        for rank, friend in enumerate(rng.sample(self._friends, 8), start=1):
            friend["rank"] = rank
        self._incoming = [{"id": self._id(), "name": self._person()} for _ in range(3)]
        self._outgoing = [{"id": self._id(), "name": self._person()} for _ in range(2)]
        self._blocked = [{"id": self._id(), "name": self._person()} for _ in range(4)]
        self._guilds = [
            {"id": self._id(), "name": name, "owner": index in (2, 9), "muted": index % 4 == 0, "unavailable": False}
            for index, name in enumerate(_GUILDS)
        ]
        self._dms = []
        dm_friends = iter(rng.sample(self._friends, 26))
        for index in range(26):
            is_group = index % 5 == 0
            friend = next(dm_friends)
            self._dms.append(
                {
                    "id": self._id(),
                    "type": "group" if is_group else "dm",
                    "name": f"Grupo {rng.choice(_GAMES)}" if is_group else friend["username"],
                    "global_name": None if is_group else friend["name"],
                    "user_id": None if is_group else friend["id"],
                    "avatar_url": None,
                    "members": rng.randint(3, 9) if is_group else None,
                    "is_owner": is_group and index == 5,
                    "is_request": not is_group and index == 7,
                    "is_spam": not is_group and index == 11,
                    "unread": rng.random() < 0.3,
                    "last_activity_ms": now - rng.randint(1, 60 * 24 * 40) * 60_000,
                }
            )
        self._quests = [
            {"id": str(self._id()), "name": f"Jogue {game} por 15 minutos", "progress": f"{progress}%"}
            for game, progress in zip(_GAMES[:4], (0, 35, 80, 100))
        ]
        self._sessions = [
            {
                "os": "Windows",
                "platform": "Discord Desktop",
                "location": "São Paulo, BR",
                "last_used_ms": now - 60_000,
                "current": True,
            },
            {
                "os": "Android",
                "platform": "Discord Android",
                "location": "São Paulo, BR",
                "last_used_ms": now - 3 * 3600_000,
                "current": False,
            },
            {
                "os": "Linux",
                "platform": "Chrome",
                "location": "Campinas, BR",
                "last_used_ms": now - 5 * 86400_000,
                "current": False,
            },
        ]
        self._mentions = []
        for index in range(32):
            guild = rng.choice(self._guilds)
            channel_id, message_id = self._id(), self._id()
            self._mentions.append(
                {
                    "id": message_id,
                    "author": self._person(),
                    "author_avatar": None,
                    "content": rng.choice(_MESSAGES),
                    "created_ms": now - (index + 1) * rng.randint(20, 300) * 60_000,
                    "guild_name": guild["name"],
                    "where": f"#{rng.choice(('geral', 'avisos', 'off-topic', 'dev'))}",
                    "kind": rng.choice(("user", "user", "role", "everyone")),
                    "jump_url": f"https://discord.com/channels/{guild['id']}/{channel_id}/{message_id}",
                }
            )
        self._mentions.sort(key=lambda item: item["id"], reverse=True)
        tracked = rng.sample(self._friends, 2)
        self._tracker_targets = [int(friend["id"]) for friend in tracked]
        self._tracker_settings[DEMO_USER_ID] = {
            "target_ids": list(self._tracker_targets),
            "webhook_url": self._settings["webhook_url"],
        }

    # ------------------------------------------------------------------ estado da demo

    def set_state(
        self, *, mode: str | None = None, latency_ms: int | None = None, connected: bool | None = None
    ) -> dict:
        if mode is not None:
            self.state.mode = mode
        if latency_ms is not None:
            self.state.latency_ms = latency_ms
        if connected is not None:
            self.state.connected = connected
        return self.state.as_dict()

    @property
    def _empty(self) -> bool:
        return self.state.mode == "empty"

    async def _call(self) -> dict | None:
        """Espera a latência simulada; devolve o erro do modo atual, ou None para seguir."""
        if self.state.latency_ms:
            await asyncio.sleep(self.state.latency_ms / 1000)
        if not self.state.connected:
            return dict(NOT_CONNECTED)
        if self.state.mode == "error":
            return {"ok": False, "code": "DEMO_ERROR", "message": ERROR_MESSAGE}
        return None

    async def _tuple_call(self) -> tuple[bool, str] | None:
        error = await self._call()
        return (False, error["message"]) if error else None

    # ------------------------------------------------------------------ sessão e conta

    @property
    def active_processes(self) -> dict:
        if not self.state.connected:
            return {}
        return {DEMO_USER_ID: SimpleNamespace(username="Madman Demo", is_alive=True)}

    def is_authenticated(self, user_id: int) -> bool:
        return self.state.connected

    def subscribe_events(self, user_id: int, prefix: str, callback) -> None:
        """Sem worker, não há eventos; as rotas assinam mesmo assim."""

    async def shutdown(self) -> None:
        self._cancel = True

    async def authenticate(self, user_id: int, token: str) -> tuple[bool, str]:
        if self.state.latency_ms:
            await asyncio.sleep(self.state.latency_ms / 1000)
        if len(clean_token(token)) < 30:
            return False, "Token inválido. Confira o valor configurado."
        self.state.connected = True
        return True, "Conectado como Madman Demo (modo demo)."

    async def disconnect(self, user_id: int) -> tuple[bool, str]:
        self.state.connected = False
        return True, "Conta desconectada (modo demo)."

    def get_user_settings(self, user_id: int) -> dict:
        return dict(self._settings)

    def get_webhook_url(self, user_id: int) -> str | None:
        return self._settings.get("webhook_url")

    def set_user_settings(self, user_id: int, **kwargs) -> dict:
        self._settings.update(kwargs)
        return dict(self._settings)

    def _public_user(self) -> dict:
        return {"id": str(DEMO_USER_ID), "username": DEMO_USERNAME, "display_name": "Madman Demo", "avatar_url": None}

    async def account_status(self, user_id: int) -> dict:
        return await self._call() or {"ok": True, **self._status, "user": self._public_user()}

    async def account_status_set(self, user_id: int, payload: dict) -> dict:
        if error := await self._call():
            return error
        if payload.get("clear"):
            self._status.update(text=None, emoji=None, expires_at_ms=None)
        for key in ("status", "text", "expires_at_ms"):
            if key in payload:
                self._status[key] = payload[key]
        if "emoji" in payload:
            self._status["emoji"] = _emoji(payload["emoji"])
        return {"ok": True, **self._status, "user": self._public_user(), "message": "Status atualizado."}

    async def account_profile(self, user_id: int, *, refresh: bool = False) -> dict:
        if error := await self._call():
            return error
        profile = {
            **self._public_user(),
            "banner_url": None,
            "accent": "#7c5cff",
            "theme": ["#3b2a6b", "#123b4a"],
            "bio": "Conta de demonstração do painel. Nada aqui é real.",
            "pronouns": "ele/dele",
            "badges": [] if self._empty else list(_BADGES),
            "guild_tag": "MAD",
            "name_style": {"colors": ["#a78bfa", "#38bdf8"], "effect": "gradient"},
            "decoration_url": None,
            "complete": True,
        }
        return {
            "ok": True,
            "status": self._status["status"],
            "emoji": self._status["emoji"],
            "text": self._status["text"],
            "profile": profile,
        }

    async def account_sessions(self, user_id: int) -> dict:
        return await self._call() or {"ok": True, "sessions": [] if self._empty else list(self._sessions)}

    async def account_mentions(self, user_id: int, *, limit: int = 25, before: int | None = None) -> dict:
        if error := await self._call():
            return error
        items = [] if self._empty else [m for m in self._mentions if before is None or m["id"] < before]
        return {"ok": True, "mentions": items[:limit], "has_more": len(items) > limit}

    async def account_mentions_delete(self, user_id: int, ids: list[int]) -> dict:
        if error := await self._call():
            return error
        wanted = set(ids)
        before = len(self._mentions)
        self._mentions = [m for m in self._mentions if m["id"] not in wanted]
        removed = before - len(self._mentions)
        return {"ok": True, "deleted": removed, "message": f"{removed} menção(ões) removida(s)."}

    def _unread(self) -> dict:
        if self._empty:
            return {"ok": True, "channels": 0, "private": 0, "guild": 0, "mentions": 0}
        private = sum(1 for dm in self._dms if dm["unread"])
        return {"ok": True, "channels": private + 4, "private": private, "guild": 4, "mentions": len(self._mentions)}

    async def account_unread(self, user_id: int) -> dict:
        return await self._call() or self._unread()

    async def account_ack_all(self, user_id: int, scope: str = "all") -> dict:
        if error := await self._call():
            return error
        acked = self._unread()["channels"]
        for dm in self._dms:
            dm["unread"] = False
        return {"ok": True, "acked": acked, "message": f"{acked} conversa(s) marcadas como lidas."}

    # ------------------------------------------------------------------ amigos

    async def get_rel_stats(self, user_id: int) -> dict:
        if error := await self._call():
            return error
        friends = [] if self._empty else self._friends
        return {
            "friends": len(friends),
            "blocked": 0 if self._empty else len(self._blocked),
            "incoming_requests": 0 if self._empty else len(self._incoming),
            "outgoing_requests": 0 if self._empty else len(self._outgoing),
            "online_friends": sum(1 for f in friends if f["status"] != "offline"),
            "total_relationships": len(friends),
            "fresh": True,
            "warning": None,
        }

    @staticmethod
    def _page(items: list, page: int, page_size: int) -> dict:
        pages = max(1, -(-len(items) // page_size))
        page = min(max(0, page), pages - 1)
        return {
            "items": items[page * page_size : (page + 1) * page_size],
            "total": len(items),
            "page": page,
            "pages": pages,
        }

    async def get_friends_list(self, user_id: int, *, page: int = 0, page_size: int = 20, sort: str = "status") -> dict:
        if error := await self._call():
            return error
        order = {"online": 0, "idle": 1, "dnd": 2, "offline": 3}
        items = (
            []
            if self._empty
            else [{**f, "closeness": f.get("rank") if sort == "interaction" else None} for f in self._friends]
        )
        items.sort(key=lambda f: (f["closeness"] is None, f["closeness"] or 0, order[f["status"]], f["name"]))
        return {
            **self._page(items, page, page_size),
            "sort": sort,
            "affinity_available": True,
            "fresh": True,
            "warning": None,
        }

    async def get_pending_requests(self, user_id: int, *, page: int = 0, page_size: int = 20) -> dict:
        if error := await self._call():
            return error
        return {
            "incoming": [] if self._empty else list(self._incoming),
            "outgoing": [] if self._empty else list(self._outgoing),
        }

    async def get_blocked_list(self, user_id: int, *, page: int = 0, page_size: int = 20) -> dict:
        if error := await self._call():
            return error
        return self._page([] if self._empty else list(self._blocked), page, page_size)

    async def get_closest_friends(self, user_id: int, limit: int) -> dict:
        if error := await self._call():
            return error
        ranked = sorted((f for f in self._friends if f.get("rank")), key=lambda f: f["rank"])
        return {"ok": True, "items": [{"id": f["id"], "name": f["name"]} for f in ranked[:limit]]}

    async def send_friend_request(self, user_id: int, username: str) -> dict:
        if error := await self._call():
            return error
        if not username:
            return {"ok": False, "message": "Informe o nome de usuário."}
        self._outgoing.append({"id": self._id(), "name": username})
        return {"ok": True, "message": f"Pedido enviado para {username}."}

    async def set_friend_nickname(self, user_id: int, target_id: int, nickname) -> dict:
        return await self._call() or {"ok": True, "message": "Apelido atualizado."}

    async def block_user(self, user_id: int, target_id: int) -> dict:
        if error := await self._call():
            return error
        friend = next((f for f in self._friends if f["id"] == target_id), None)
        if friend:
            self._friends.remove(friend)
        self._blocked.append({"id": target_id, "name": friend["name"] if friend else str(target_id)})
        return {"ok": True, "message": "Usuário bloqueado."}

    async def unblock_user(self, user_id: int, target_id: int) -> dict:
        if error := await self._call():
            return error
        self._blocked = [b for b in self._blocked if b["id"] != target_id]
        return {"ok": True, "message": "Usuário desbloqueado."}

    # ------------------------------------------------------------------ proteção (whitelists)

    def get_whitelist(self, user_id: int) -> list[int]:
        return list(self._whitelist)

    def get_guild_whitelist(self, user_id: int) -> list[int]:
        return list(self._guild_whitelist)

    async def toggle_whitelist(self, user_id: int, target_id: int) -> bool:
        if target_id in self._whitelist:
            self._whitelist.remove(target_id)
            return False
        self._whitelist.append(target_id)
        return True

    async def add_to_whitelist(self, user_id: int, ids: list[int]) -> list[int]:
        added = [i for i in ids if i not in self._whitelist]
        self._whitelist.extend(added)
        return added

    async def protection_status(self, user_id: int) -> dict:
        return await self._call() or {"ok": True, "enabled": user_id in self._protection_enabled_users}

    async def protection_enable(self, user_id: int) -> dict:
        if error := await self._call():
            return error
        self._protection_enabled_users.add(user_id)
        return {"ok": True, "message": "Proteção contra grupos ativada."}

    async def protection_disable(self, user_id: int) -> dict:
        if error := await self._call():
            return error
        self._protection_enabled_users.discard(user_id)
        return {"ok": True, "message": "Proteção contra grupos desativada."}

    async def protection_configure(self, user_id: int, raw_ids) -> dict:
        if error := await self._call():
            return error
        ids = [int(str(i)) for i in raw_ids or [] if str(i).strip().isdigit()]
        self._whitelist = list(dict.fromkeys(ids))
        return {"ok": True, "message": "Lista de amigos permitidos salva."}

    async def toggle_guild_whitelist(self, user_id: int, guild_id: int) -> None:
        if guild_id in self._guild_whitelist:
            self._guild_whitelist.remove(guild_id)
        else:
            self._guild_whitelist.append(guild_id)

    async def update_guild_whitelist(self, user_id: int, visible_ids, selected_ids) -> None:
        visible = {int(str(i)) for i in visible_ids or [] if str(i).isdigit()}
        selected = [int(str(i)) for i in selected_ids or [] if str(i).isdigit()]
        self._guild_whitelist = [g for g in self._guild_whitelist if g not in visible] + selected

    async def clear_guild_whitelist(self, user_id: int) -> None:
        self._guild_whitelist = []

    # ------------------------------------------------------------------ servidores

    async def get_guild_stats(self, user_id: int) -> dict:
        if error := await self._call():
            return error
        guilds = [] if self._empty else self._guilds
        return {
            "total": len(guilds),
            "owners": sum(g["owner"] for g in guilds),
            "muted": sum(g["muted"] for g in guilds),
            "mute_unknown": 0,
            "unavailable": 0,
            "guild_ids": [g["id"] for g in guilds],
            "owner_ids": [g["id"] for g in guilds if g["owner"]],
            "fresh": True,
            "warning": None,
        }

    async def get_guilds_page(self, user_id: int, *, page: int = 0, page_size: int = 20) -> dict:
        if error := await self._call():
            return error
        items = [] if self._empty else sorted(self._guilds, key=lambda g: g["name"].casefold())
        return {**self._page(items, page, page_size), "fresh": True, "warning": None}

    # ------------------------------------------------------------------ conversas e limpeza

    async def list_open_dms(self, user_id: int, refresh: bool = False) -> list[dict]:
        if await self._call():
            return []
        return [] if self._empty else [dict(dm) for dm in self._dms]

    async def close_single_dm(self, user_id: int, channel_id: int) -> tuple[bool, str]:
        if error := await self._tuple_call():
            return error
        before = len(self._dms)
        self._dms = [dm for dm in self._dms if dm["id"] != channel_id]
        return (len(self._dms) < before, "Conversa fechada." if len(self._dms) < before else "Conversa não encontrada.")

    async def leave_single_group_dm(self, user_id: int, channel_id: int) -> tuple[bool, str]:
        return await self.close_single_dm(user_id, channel_id)

    async def scan_guilds_for_messages(self, user_id: int) -> list[dict]:
        if await self._call() or self._empty:
            return []
        return [{"id": g["id"], "name": g["name"], "count": self._rng.randint(3, 400)} for g in self._guilds[:6]]

    async def preview_clean(self, user_id: int, target_id: int, *, guild: bool = False) -> dict:
        if error := await self._call():
            return error
        name = next((g["name"] for g in self._guilds if g["id"] == target_id), None) if guild else None
        name = name or next((dm["global_name"] or dm["name"] for dm in self._dms if dm["id"] == target_id), None)
        return {"ok": True, "name": name or f"Canal {target_id}", "estimated_total": self._rng.randint(5, 900)}

    async def _fake_job(self, update_callback, total: int, verb: str) -> dict:
        """Progresso falso de uma operação em lote, no mesmo formato das reais."""
        if error := await self._call():
            return {"status": "ERRO", "deleted": 0, "total": 0, "text": error["message"]}
        self._cancel = False
        total = 0 if self._empty else total
        for step in range(1, total + 1):
            if self._cancel:
                return {
                    "status": "CANCELADO",
                    "deleted": step - 1,
                    "total": total,
                    "text": f"Cancelado após {step - 1} de {total}.",
                }
            await asyncio.sleep(self.step_delay)
            await update_callback(step, total, "PROCESSANDO", f"{verb} {step} de {total}")
        return {
            "status": "COMPLETADO",
            "deleted": total,
            "total": total,
            "text": f"{verb}: {total} de {total} concluídos.",
        }

    async def execute_clean(self, user_id: int, target_id: int, *, guild: bool = False, update_callback=None) -> dict:
        return await self._fake_job(update_callback, 12, "Mensagens apagadas")

    async def clean_channels_batch(self, user_id: int, ids: list[int], update_callback=None) -> dict:
        return await self._fake_job(update_callback, max(1, len(ids)) * 4, "Mensagens apagadas")

    async def clean_all_dms(self, user_id: int, update_callback=None) -> dict:
        return await self._fake_job(update_callback, 20, "Mensagens apagadas")

    async def close_all_dms(self, user_id: int, update_callback=None) -> dict:
        result = await self._fake_job(update_callback, len(self._dms), "Conversas fechadas")
        if result["status"] == "COMPLETADO":
            self._dms = [dm for dm in self._dms if dm["type"] == "group"]
        return result

    async def leave_all_group_dms(self, user_id: int, update_callback=None) -> dict:
        groups = [dm for dm in self._dms if dm["type"] == "group"]
        result = await self._fake_job(update_callback, len(groups), "Grupos deixados")
        if result["status"] == "COMPLETADO":
            self._dms = [dm for dm in self._dms if dm["type"] != "group"]
        return result

    async def control_clean(self, user_id: int, signal: str) -> bool:
        if signal == "CANCEL":
            self._cancel = True
        return True

    async def start_rel_cleanup(self, user_id: int, update_callback=None) -> dict:
        removable = [f for f in self._friends if f["id"] not in self._whitelist]
        result = await self._fake_job(update_callback, len(removable), "Amigos removidos")
        if result["status"] == "COMPLETADO":
            self._friends = [f for f in self._friends if f["id"] in self._whitelist]
        return result

    async def unblock_all(self, user_id: int, update_callback=None) -> dict:
        result = await self._fake_job(update_callback, len(self._blocked), "Desbloqueados")
        if result["status"] == "COMPLETADO":
            self._blocked = []
        return result

    async def accept_all_requests(self, user_id: int, update_callback=None) -> dict:
        result = await self._fake_job(update_callback, len(self._incoming), "Pedidos aceitos")
        if result["status"] == "COMPLETADO":
            self._friends.extend(
                {**r, "username": r["name"].lower(), "status": "online", "activity": None} for r in self._incoming
            )
            self._incoming = []
        return result

    async def deny_all_requests(self, user_id: int, update_callback=None) -> dict:
        result = await self._fake_job(update_callback, len(self._incoming), "Pedidos recusados")
        if result["status"] == "COMPLETADO":
            self._incoming = []
        return result

    async def start_guild_cleanup(self, user_id: int, action: str, update_callback=None) -> dict:
        keep = set(self._guild_whitelist)
        targets = [g for g in self._guilds if g["id"] not in keep and not g["owner"]]
        verb = "Servidores deixados" if action == "LEAVE" else "Servidores silenciados"
        result = await self._fake_job(update_callback, len(targets), verb)
        if result["status"] == "COMPLETADO" and action == "LEAVE":
            self._guilds = [g for g in self._guilds if g["id"] in keep or g["owner"]]
        return result

    # ------------------------------------------------------------------ acompanhamento

    def _tracked_user(self, target_id: int) -> dict:
        friend = next((f for f in self._friends if f["id"] == target_id), None) or {
            "id": target_id,
            "name": "Usuário acompanhado",
            "username": "usuario",
            "status": "online",
        }
        game = _GAMES[target_id % len(_GAMES)]
        return {
            "target_id": target_id,
            "user": {
                "id": target_id,
                "username": friend["username"],
                "display_name": friend["name"],
                "status": friend["status"] if friend["status"] != "offline" else "idle",
                "avatar_url": None,
                "decoration_url": None,
                "guild_tag": None,
                "name_style": None,
                "mutual_servers": 3,
                "devices": [{"device": "desktop", "status": "online"}, {"device": "mobile", "status": "idle"}],
                "voice": {"channel": "Sala de estudos", "guild": _GUILDS[3]} if target_id % 2 else None,
                "profile": {
                    "bio": "Perfil de demonstração.",
                    "pronouns": "",
                    "theme": ["#1e3a5f", "#3b1e5f"],
                    "accent": "#38bdf8",
                    "badges": [],
                },
                "activities": [
                    {
                        "type": "playing",
                        "name": game,
                        "detail": "Partida ranqueada",
                        "state": "Em grupo",
                        "party": "2 de 5",
                        "started_at": _now_ms() - 25 * 60_000,
                        "image_url": None,
                    }
                ],
            },
        }

    async def tracker_status(self, user_id: int) -> dict:
        if error := await self._call():
            return error
        enabled = bool(self._tracker_settings.get(user_id)) and not self._empty
        targets = self._tracker_targets if enabled else []
        return {
            "ok": True,
            "enabled": enabled,
            "target_ids": [str(t) for t in targets],
            "targets": [self._tracked_user(t) for t in targets],
        }

    async def tracker_start(self, user_id: int, targets, webhook_url: str) -> dict:
        if error := await self._call():
            return error
        raw = targets if isinstance(targets, list) else str(targets).replace(",", " ").split()
        ids = [int(str(t)) for t in raw if str(t).strip().isdigit()][:10]
        if not ids:
            return {"ok": False, "message": "Informe ao menos um ID."}
        self._tracker_targets = ids
        self._tracker_settings[user_id] = {"target_ids": ids, "webhook_url": webhook_url}
        return {"ok": True, "message": f"Acompanhando {len(ids)} usuário(s)."}

    async def tracker_stop(self, user_id: int) -> dict:
        if error := await self._call():
            return error
        self._tracker_settings.pop(user_id, None)
        return {"ok": True, "message": "Acompanhamento encerrado."}

    # ------------------------------------------------------------------ missões

    async def quest_list(self, user_id: int) -> dict:
        return await self._call() or {"ok": True, "quests": [] if self._empty else list(self._quests)}

    async def quest_start(self, user_id: int, quest_id: str) -> dict:
        return await self._call() or {"ok": True, "message": "Missão iniciada (modo demo)."}

    async def quest_auto(self, user_id: int) -> dict:
        return await self._call() or {"ok": True, "message": "Modo automático ligado (modo demo)."}

    async def quest_cancel(self, user_id: int) -> dict:
        return await self._call() or {"ok": True, "message": "Missão cancelada."}

    async def quest_config(self, user_id: int, webhook_url: str | None = None) -> dict:
        return await self._call() or {"ok": True}

    # ------------------------------------------------------------------ voz

    def _voice_status(self) -> dict:
        return {
            "ok": True,
            "in_voice": bool(self._voice.get("in_voice")),
            "channel_name": self._voice.get("channel_name"),
            "guild_name": self._voice.get("guild_name"),
            "private": False,
            "following": self._voice.get("following"),
        }

    async def voice_status(self, user_id: int) -> dict:
        return await self._call() or self._voice_status()

    async def voice_join(self, user_id: int, channel_id: int) -> dict:
        if error := await self._call():
            return error
        self._voice.update(in_voice=True, channel_name=f"Canal {channel_id}")
        return {**self._voice_status(), "message": "Entrou na call."}

    async def voice_leave(self, user_id: int) -> dict:
        if error := await self._call():
            return error
        self._voice.update(in_voice=False, channel_name=None, guild_name=None)
        return {**self._voice_status(), "message": "Saiu da call."}

    async def voice_follow(self, user_id: int, target_id: int) -> dict:
        if error := await self._call():
            return error
        self._voice["following"] = str(target_id)
        return {**self._voice_status(), "message": "Seguindo o amigo nas calls."}

    async def voice_unfollow(self, user_id: int) -> dict:
        if error := await self._call():
            return error
        self._voice["following"] = None
        return {**self._voice_status(), "message": "Parou de seguir."}

    # ------------------------------------------------------------------ Rich Presence

    def _rich_default(self) -> dict:
        return {
            "source": "default",
            "type": "playing",
            "name": "Madman",
            "details": "Madman Services",
            "state": "aylo.me/to | Proteção Ativa",
            "large_image": None,
            "large_text": "Madman Services",
            "small_image": None,
            "small_text": "Standard",
            "buttons": [],
            "started_at_ms": self._login_ms,
        }

    def _rich_live(self) -> dict | None:
        if not self._rich_enabled:
            return None
        custom = self._rich_custom
        if custom is None:
            return self._rich_default()
        start = self._login_ms if custom["timer"] == "since_start" else self._rich_saved_ms
        ends = start + custom["duration_minutes"] * 60_000 if custom["timer"] == "duration" else None
        preview = {
            "source": "custom",
            **{
                key: custom.get(key)
                for key in (
                    "type",
                    "name",
                    "details",
                    "state",
                    "status_display",
                    "details_url",
                    "state_url",
                    "stream_url",
                    "party_current",
                    "party_max",
                    "platform",
                )
            },
            "buttons": list(custom.get("buttons") or []),
            "started_at_ms": None if custom["timer"] == "none" else start,
            "ends_at_ms": ends,
        }
        # Sem o proxy do Discord, a prévia não tem imagem (como quando o proxy falha de verdade).
        for image_key, text_key in (("large_image", "large_text"), ("small_image", "small_text")):
            preview[image_key] = None
            preview[text_key] = None
            preview[image_key.replace("_image", "_url")] = None
        return preview

    def _rich_status(self) -> dict:
        seed = validate_custom(
            {
                "type": "playing",
                "name": "Madman",
                "details": "Madman Services",
                "state": "aylo.me/to",
                "timer": "since_start",
            }
        )
        return {
            "ok": True,
            "enabled": self._rich_enabled,
            "custom": self._rich_custom,
            "default": self._rich_default(),
            "seed": seed,
            "live": self._rich_live(),
            "task": None,
        }

    async def rich_presence_status(self, user_id: int) -> dict:
        return await self._call() or self._rich_status()

    async def rich_presence_set(self, user_id: int, enabled, custom) -> dict:
        if error := await self._call():
            return error
        if not isinstance(enabled, bool):
            return {
                "ok": False,
                "code": "INVALID_RICH_PRESENCE",
                "message": "Informe se o Rich Presence fica ligado ou desligado.",
            }
        try:
            normalized = validate_custom(custom) if custom is not None else None
        except ValueError as exc:
            return {"ok": False, "code": "INVALID_RICH_PRESENCE", "message": str(exc)}
        if normalized != self._rich_custom:
            self._rich_saved_ms = _now_ms()
        self._rich_enabled, self._rich_custom = enabled, normalized
        return self._rich_status()
