from __future__ import annotations

import asyncio
import time
from contextlib import suppress

import discord

from .ipc import emit_line
from .tracker_support import msg_to_dict


def _short(value, limit: int = 250, fallback: str = "—") -> str:
    text = str(value or fallback).replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _user_from_presence(subject):
    return getattr(subject, "user", subject)


class TrackerEventMixin:
    def _cancel_pending_offline(self):
        if self._pending_offline_task and not self._pending_offline_task.done():
            self._pending_offline_task.cancel()
        self._pending_offline_task = None

    def _cancel_pending_presence(self):
        if self._pending_presence_task and not self._pending_presence_task.done():
            self._pending_presence_task.cancel()
        self._pending_presence_task = None

    def _device_statuses(self, subject) -> list[str]:
        devices = []
        for attr, label in (
            ("desktop_status", "🖥️ Desktop"),
            ("mobile_status", "📱 Mobile"),
            ("web_status", "🌐 Web"),
            ("embedded_status", "🎮 Embedded"),
        ):
            with suppress(Exception):
                status = str(getattr(subject, attr))
                if status and status != "offline":
                    devices.append(f"{label}: {status}")
        return devices

    def _select_effective_presence(self):
        now = time.monotonic()
        fresh = [
            snapshot
            for snapshot in self.state.guild_presence.values()
            if now - snapshot["updated_at"] <= self.config.PRESENCE_SNAPSHOT_TTL
        ]
        if not fresh:
            return None, [], []
        online = [snapshot for snapshot in fresh if snapshot["status"] != "offline"]
        effective = max(online or fresh, key=lambda snapshot: snapshot["updated_at"])
        return (
            effective["status"],
            list(effective["activities"]),
            list(effective["devices"]),
        )

    async def _emit_presence_changes(
        self, subject, status: str, activities: list[str], devices: list[str]
    ):
        changes = []
        if status != self.state.status:
            emojis = {"online": "🟢", "idle": "🌙", "dnd": "⛔", "offline": "⚫"}
            changes.append(("Status", f"{emojis.get(status, '⚪')} `{status.title()}`"))
            self.state.status = status
        if activities != self.state.activities_repr:
            changes.append(
                (
                    "🕹️ Atividades",
                    "\n".join(activities) if activities else "*Nenhuma atividade*",
                )
            )
            self.state.activities_repr = activities
        if devices != self.state.devices_repr:
            changes.append(
                ("💻 Dispositivos", " • ".join(devices) if devices else "*Nenhum online*")
            )
            self.state.devices_repr = devices
        if changes:
            await self._dispatch_log(
                title="👀 Presença Atualizada",
                color=0xA2B9EE,
                changes=changes,
                user=_user_from_presence(subject),
                event_type="presence",
            )

    async def _coalesced_emit_presence(self, subject, run_id: int):
        try:
            await asyncio.sleep(self.config.PRESENCE_COALESCE_DELAY)
            if not self.enabled or run_id != self._run_id:
                return
            status, activities, devices = self._select_effective_presence()
            if status and status != "offline":
                await self._emit_presence_changes(subject, status, activities, devices)
        except asyncio.CancelledError:
            return

    async def _debounced_emit_offline(self, subject, run_id: int):
        try:
            await asyncio.sleep(self.config.OFFLINE_DEBOUNCE)
            if not self.enabled or run_id != self._run_id:
                return
            status, activities, devices = self._select_effective_presence()
            if status == "offline":
                await self._emit_presence_changes(subject, status, activities, devices)
        except asyncio.CancelledError:
            return

    def _schedule_profile_fetch(self, *, emit_changes: bool, delay: float):
        if self._profile_fetch_task and not self._profile_fetch_task.done():
            return
        self._profile_fetch_task = asyncio.create_task(
            self._profile_poll_loop(self._run_id, emit_changes, delay),
            name=f"tracker-profile-{self.target_id}",
        )

    async def _profile_poll_loop(self, run_id: int, emit_changes: bool, delay: float):
        try:
            await asyncio.sleep(delay)
            first = True
            while self.enabled and run_id == self._run_id and self.target_id:
                await self._fetch_profile(emit_changes=emit_changes or not first)
                first = False
                await asyncio.sleep(self.config.PROFILE_FETCH_THROTTLE)
        except asyncio.CancelledError:
            return

    async def _fetch_profile(self, *, emit_changes: bool):
        self.state.last_profile_fetch = time.monotonic()
        try:
            profile = await self.client.fetch_user_profile(self.target_id)
            if not profile:
                return
            bio = getattr(profile, "bio", None)
            connections = [
                f"`{_short(getattr(item, 'type', None), 40)}: "
                f"{_short(getattr(item, 'name', None), 100)}`"
                for item in getattr(profile, "connected_accounts", [])
            ]
            changes = []
            if emit_changes and bio != self.state.bio:
                changes.append(
                    (
                        "Bio editada",
                        f"**Antes:**\n>>> {_short(self.state.bio, 400, '*Vazio*')}\n\n"
                        f"**Depois:**\n>>> {_short(bio, 400, '*Vazio*')}",
                    )
                )
            if emit_changes and connections != self.state.connected_accounts:
                changes.append(("Contas conectadas", "\n".join(connections) or "*Nenhuma*"))
            self.state.bio = bio
            self.state.connected_accounts = connections
            if changes:
                await self._dispatch_log(
                    title="Configurações de Conta Alteradas",
                    color=0x9B59B6,
                    changes=changes,
                    user=getattr(profile, "user", profile),
                    event_type="profile",
                )
        except Exception as error:
            self._stats["last_error"] = "PROFILE_FETCH_ERROR"
            emit_line(f"TRACKER:PROFILE_FETCH_ERROR:{type(error).__name__}")

    @staticmethod
    def _primary_guild_repr(user) -> str:
        primary = getattr(user, "primary_guild", None)
        if primary is None:
            return "Nenhuma"
        guild_id = getattr(primary, "id", None)
        tag = getattr(primary, "tag", None)
        enabled = getattr(primary, "identity_enabled", False)
        return f"{tag or 'Sem tag'} (`{guild_id or 'ID privado'}`)" if enabled else "Oculta"

    async def on_user_update(self, before, after):
        if not self.enabled or after.id != self.target_id:
            return
        changes = []
        if before.name != after.name:
            changes.append(("🏷️ Username", f"`{_short(before.name)}` ➔ `{_short(after.name)}`"))
            self.state.username = after.name
        if getattr(before, "global_name", None) != getattr(after, "global_name", None):
            changes.append(
                (
                    "✨ Nome de exibição",
                    f"`{_short(getattr(before, 'global_name', None))}` ➔ "
                    f"`{_short(getattr(after, 'global_name', None))}`",
                )
            )
            self.state.global_name = getattr(after, "global_name", None)
        if before.avatar != after.avatar:
            changes.append(("📸 Foto de perfil", f"[Abrir novo avatar]({after.display_avatar.url})"))
            self.state.avatar_url = str(after.display_avatar.url)
        if getattr(before, "avatar_decoration", None) != getattr(after, "avatar_decoration", None):
            decoration = getattr(after, "avatar_decoration", None)
            changes.append(
                ("🖼️ Decoração do avatar", f"[Abrir]({decoration.url})" if decoration else "Removida")
            )
        if self._primary_guild_repr(before) != self._primary_guild_repr(after):
            changes.append(
                (
                    "🏷️ Guilda principal",
                    f"{self._primary_guild_repr(before)} ➔ {self._primary_guild_repr(after)}",
                )
            )
        if changes:
            await self._dispatch_log(
                title="🎨 Perfil Atualizado",
                color=0xFAC898,
                changes=changes,
                user=after,
                event_type="profile",
            )

    async def on_presence_update(self, before, after):
        if not self.enabled or after.id != self.target_id:
            return
        now = time.monotonic()
        guild = getattr(after, "guild", None)
        source_id = guild.id if guild else 0
        self.state.guild_presence[source_id] = {
            "status": str(after.status),
            "activities": tuple(self._format_activity(item) for item in after.activities),
            "devices": tuple(self._device_statuses(after)),
            "updated_at": now,
        }
        status, activities, devices = self._select_effective_presence()
        if status is None:
            return
        if status == "offline":
            self._cancel_pending_presence()
            self._cancel_pending_offline()
            self._pending_offline_task = asyncio.create_task(
                self._debounced_emit_offline(after, self._run_id),
                name=f"tracker-offline-{self.target_id}",
            )
            return
        self._cancel_pending_offline()
        self._cancel_pending_presence()
        self._pending_presence_task = asyncio.create_task(
            self._coalesced_emit_presence(after, self._run_id),
            name=f"tracker-presence-{self.target_id}",
        )

    async def on_member_join(self, member):
        if not self.enabled or member.id != self.target_id:
            return
        self.tracked_guild_ids.add(member.guild.id)
        try:
            await member.guild.subscribe_to(members=[member])
        except Exception as error:
            self._stats["subscription_errors"] += 1
            emit_line(f"TRACKER:SUBSCRIBE_MEMBER_ERROR:{member.guild.id}:{type(error).__name__}")
        await self._dispatch_server_embed(
            title="📥 Entrou no Servidor",
            color=0x2ECC71,
            user=member,
            guild_name=member.guild.name,
            guild_id=member.guild.id,
        )

    async def on_raw_member_remove(self, payload):
        if not self.enabled or payload.user.id != self.target_id:
            return
        self.tracked_guild_ids.discard(payload.guild_id)
        self.state.guild_presence.pop(payload.guild_id, None)
        guild = self.client.get_guild(payload.guild_id)
        await self._dispatch_server_embed(
            title="📤 Saiu do Servidor",
            color=0xE74C3C,
            user=payload.user,
            guild_name=guild.name if guild else "Desconhecido",
            guild_id=payload.guild_id,
        )

    async def on_member_update(self, before, after):
        if not self.enabled or after.id != self.target_id:
            return
        changes = []
        if before.nick != after.nick:
            changes.append(("Apelido", f"`{_short(before.nick)}` ➔ `{_short(after.nick)}`"))
        before_roles = {role.id: role.name for role in before.roles[1:]}
        after_roles = {role.id: role.name for role in after.roles[1:]}
        added = [after_roles[role_id] for role_id in after_roles.keys() - before_roles.keys()]
        removed = [before_roles[role_id] for role_id in before_roles.keys() - after_roles.keys()]
        if added:
            changes.append(("Cargos adicionados", ", ".join(_short(item, 70) for item in added)))
        if removed:
            changes.append(("Cargos removidos", ", ".join(_short(item, 70) for item in removed)))
        if getattr(before, "pending", None) != getattr(after, "pending", None):
            changes.append(("Triagem do servidor", "Concluída" if not after.pending else "Pendente"))
        if getattr(before, "timed_out_until", None) != getattr(after, "timed_out_until", None):
            timeout = getattr(after, "timed_out_until", None)
            changes.append(("Timeout", discord.utils.format_dt(timeout, "F") if timeout else "Removido"))
        for attr, label in (
            ("guild_avatar", "Avatar do servidor"),
            ("guild_banner", "Banner do servidor"),
            ("guild_avatar_decoration", "Decoração no servidor"),
        ):
            if getattr(before, attr, None) != getattr(after, attr, None):
                asset = getattr(after, attr, None)
                changes.append((label, f"[Abrir]({asset.url})" if asset else "Removido"))
        if changes:
            await self._dispatch_log(
                title="🏠 Perfil no Servidor Atualizado",
                color=0x3498DB,
                changes=changes,
                user=after,
                footer=f"Servidor: {_short(after.guild.name, 150)}",
                event_type="member",
            )

    async def on_typing(self, channel, user, when):
        if not self.enabled or user.id != self.target_id:
            return
        now = time.monotonic()
        if now - self.state.last_typed_at < self.config.TYPING_THROTTLE:
            return
        self.state.last_typed_at = now
        await self._dispatch_log(
            description=f"⌨️ Foi visto digitando em `#{_short(getattr(channel, 'name', 'DM'), 100)}`",
            color=0xDCDCDC,
            footer=f"📍 Servidor: {_short(getattr(getattr(channel, 'guild', None), 'name', 'DM'), 150)}",
            event_type="typing",
        )

    async def on_voice_state_update(self, member, before, after):
        if not self.enabled:
            return
        if member.id != self.target_id:
            if before.channel == after.channel:
                return
            if after.channel and after.channel.id in self.tracked_voice_channels:
                await self._dispatch_voice_embed(
                    "👥 Entrou na Call do Alvo", 0x2ECC71, member, after.channel, member.guild
                )
            elif before.channel and before.channel.id in self.tracked_voice_channels:
                await self._dispatch_voice_embed(
                    "🚶 Saiu da Call do Alvo", 0xE74C3C, member, before.channel, member.guild
                )
            return

        titles = []
        if before.channel != after.channel:
            if before.channel:
                self.tracked_voice_channels.discard(before.channel.id)
            if after.channel:
                self.tracked_voice_channels.add(after.channel.id)
                self.state.in_voice_channel_id = after.channel.id
            else:
                self.state.in_voice_channel_id = None
            if before.channel is None:
                titles.append("🔊 Entrou no Canal de Voz")
            elif after.channel is None:
                titles.append("🔇 Saiu do Canal de Voz")
            else:
                titles.append("🔀 Mudou de Canal de Voz")

        voice_flags = (
            ("self_mute", "🤐 Mutou o microfone", "🎙️ Desmutou o microfone"),
            ("self_deaf", "🔇 Silenciou o áudio", "🔊 Ativou o áudio"),
            ("self_stream", "🎥 Iniciou uma stream", "🛑 Encerrou a stream"),
            ("self_video", "📷 Ligou a câmera", "📷 Desligou a câmera"),
            ("mute", "🛡️ Foi mutado pelo servidor", "🛡️ Foi desmutado pelo servidor"),
            ("deaf", "🛡️ Foi ensurdecido pelo servidor", "🛡️ Áudio do servidor restaurado"),
            ("suppress", "🎭 Foi movido para a audiência", "🎭 Pode falar no palco"),
        )
        for attr, enabled_label, disabled_label in voice_flags:
            if getattr(before, attr, None) != getattr(after, attr, None):
                titles.append(enabled_label if getattr(after, attr, False) else disabled_label)
        if getattr(before, "requested_to_speak_at", None) != getattr(after, "requested_to_speak_at", None):
            titles.append(
                "✋ Pediu para falar no palco"
                if getattr(after, "requested_to_speak_at", None)
                else "✋ Retirou o pedido para falar"
            )
        if getattr(before, "afk", None) != getattr(after, "afk", None):
            titles.append("💤 Movido para AFK" if getattr(after, "afk", False) else "💤 Saiu do AFK")
        if titles:
            await self._dispatch_voice_embed(
                title=" • ".join(titles),
                color=0x9DF9EF,
                member=member,
                channel=after.channel or before.channel,
                guild=member.guild,
            )

    async def _on_raw_reaction(self, payload, *, added: bool):
        if not self.enabled or payload.user_id != self.target_id:
            return
        channel = self.client.get_channel(payload.channel_id)
        guild = getattr(channel, "guild", None) if channel else None
        cached = self._msg_cache.get(payload.message_id)
        content = _short(cached.get("content"), 300, "") if cached else ""
        author = _short(cached.get("author_name"), 80, "") if cached else ""
        preview = f"> **{author}:** {content}" if content else "*[mensagem fora do cache]*"
        guild_id = guild.id if guild else "@me"
        jump_url = f"https://discord.com/channels/{guild_id}/{payload.channel_id}/{payload.message_id}"
        action = "Adicionada" if added else "Removida"
        await self._dispatch_log(
            title=f"{payload.emoji} Reação {action}",
            description=f"{preview}\n\n[↗️ Ir para a mensagem]({jump_url})",
            color=0x8E44AD if added else 0x7F8C8D,
            footer=f"#{_short(getattr(channel, 'name', 'DM'), 100)} | Servidor: {_short(getattr(guild, 'name', 'DM'), 150)}",
            event_type="reaction",
        )

    async def on_raw_reaction_add(self, payload):
        await self._on_raw_reaction(payload, added=True)

    async def on_raw_reaction_remove(self, payload):
        await self._on_raw_reaction(payload, added=False)

    async def on_message(self, message):
        if not self.enabled:
            return
        if message.author.id == self.target_id:
            data = msg_to_dict(message)
        else:
            poll = getattr(message, "poll", None)
            data = {
                "author_id": message.author.id,
                "author_name": getattr(message.author, "name", "desconhecido"),
                "content": message.content or "",
                "attachments": [],
                "embeds": [],
                "poll": (
                    {
                        "question": str(getattr(poll, "question", "")),
                        "answers": [
                            {"id": getattr(answer, "id", None), "text": str(answer)}
                            for answer in getattr(poll, "answers", [])[:10]
                        ],
                    }
                    if poll is not None
                    else None
                ),
            }
        self._cache_message(message.id, data)
        if message.author.id == self.target_id:
            self.msg_buffer.add(message.channel.id, data)

    async def _on_raw_poll_vote(self, payload, *, added: bool):
        if not self.enabled or payload.user_id != self.target_id:
            return
        cached = self._msg_cache.get(payload.message_id) or {}
        poll = cached.get("poll") or {}
        answer = next(
            (
                item.get("text")
                for item in poll.get("answers", [])
                if item.get("id") == payload.answer_id
            ),
            f"Resposta {payload.answer_id}",
        )
        channel = self.client.get_channel(payload.channel_id)
        guild = getattr(channel, "guild", None) if channel else None
        guild_id = payload.guild_id or "@me"
        action = "Voto adicionado" if added else "Voto removido"
        await self._dispatch_log(
            title=f"📊 {action}",
            color=0x5865F2 if added else 0x7F8C8D,
            changes=[
                ("Enquete", _short(poll.get("question"), 400, "Enquete fora do cache")),
                ("Resposta", _short(answer, 400)),
            ],
            description=(
                f"[↗️ Ir para a mensagem](https://discord.com/channels/"
                f"{guild_id}/{payload.channel_id}/{payload.message_id})"
            ),
            footer=f"#{_short(getattr(channel, 'name', 'DM'), 100)} | Servidor: {_short(getattr(guild, 'name', 'DM'), 150)}",
            event_type="poll_vote",
        )

    async def on_raw_poll_vote_add(self, payload):
        await self._on_raw_poll_vote(payload, added=True)

    async def on_raw_poll_vote_remove(self, payload):
        await self._on_raw_poll_vote(payload, added=False)

    def _call_involves_target(self, call) -> bool:
        users = []
        channel = getattr(call, "channel", None)
        users.extend(getattr(channel, "recipients", []) or [])
        recipient = getattr(channel, "recipient", None)
        if recipient:
            users.append(recipient)
        initiator = getattr(call, "initiator", None)
        if initiator:
            users.append(initiator)
        users.extend(getattr(call, "members", []) or [])
        users.extend(getattr(call, "ringing", []) or [])
        return any(getattr(user, "id", None) == self.target_id for user in users)

    @staticmethod
    def _call_user_ids(call, attribute: str) -> set[int]:
        return {
            user.id
            for user in (getattr(call, attribute, []) or [])
            if getattr(user, "id", None)
        }

    async def on_call_create(self, call):
        if not self.enabled or not self._call_involves_target(call):
            return
        members = self._call_user_ids(call, "members")
        ringing = self._call_user_ids(call, "ringing")
        await self._dispatch_log(
            title="📞 Chamada Privada Iniciada",
            color=0x2ECC71,
            changes=[
                ("Conectados", ", ".join(f"`{item}`" for item in sorted(members)) or "Nenhum"),
                ("Chamando", ", ".join(f"`{item}`" for item in sorted(ringing)) or "Ninguém"),
            ],
            footer=f"Canal privado: {getattr(call.channel, 'id', 'desconhecido')}",
            event_type="private_call",
        )

    async def on_call_update(self, before, after):
        if not self.enabled or not (
            self._call_involves_target(before) or self._call_involves_target(after)
        ):
            return
        changes = []
        for attribute, label in (("members", "Conectados"), ("ringing", "Chamando")):
            old_ids = self._call_user_ids(before, attribute)
            new_ids = self._call_user_ids(after, attribute)
            if old_ids != new_ids:
                changes.append(
                    (
                        label,
                        f"`{', '.join(map(str, sorted(old_ids))) or 'nenhum'}` ➔ "
                        f"`{', '.join(map(str, sorted(new_ids))) or 'nenhum'}`",
                    )
                )
        if getattr(before, "region", None) != getattr(after, "region", None):
            changes.append(("Região", f"{_short(getattr(before, 'region', None))} ➔ {_short(getattr(after, 'region', None))}"))
        if getattr(before, "unavailable", None) != getattr(after, "unavailable", None):
            changes.append(("Disponibilidade", "Indisponível" if after.unavailable else "Disponível"))
        if changes:
            await self._dispatch_log(
                title="📞 Chamada Privada Atualizada",
                color=0xF1C40F,
                changes=changes,
                footer=f"Canal privado: {getattr(after.channel, 'id', 'desconhecido')}",
                event_type="private_call",
            )

    async def on_call_delete(self, call):
        if not self.enabled or not self._call_involves_target(call):
            return
        await self._dispatch_log(
            title="📴 Chamada Privada Encerrada",
            color=0xE74C3C,
            footer=f"Canal privado: {getattr(call.channel, 'id', 'desconhecido')}",
            event_type="private_call",
        )

    @staticmethod
    def _raw_attachment(item: dict) -> dict:
        return {
            "url": item.get("url", ""),
            "filename": item.get("filename", "arquivo"),
            "content_type": item.get("content_type"),
            "size": int(item.get("size", 0) or 0),
            "spoiler": str(item.get("filename", "")).startswith("SPOILER_"),
            "duration": item.get("duration_secs"),
        }

    async def on_raw_message_edit(self, payload):
        if not self.enabled:
            return
        data = payload.data
        cached_message = payload.cached_message
        cached_data = self._msg_cache.get(payload.message_id)
        author_id = None
        if cached_message:
            author_id = cached_message.author.id
        elif cached_data:
            author_id = cached_data.get("author_id")
        elif isinstance(data.get("author"), dict):
            with suppress(TypeError, ValueError):
                author_id = int(data["author"].get("id"))
        if author_id != self.target_id:
            return

        await self.msg_buffer.flush(payload.channel_id)

        before_content = (
            cached_message.content or ""
            if cached_message
            else (cached_data or {}).get("content", "")
        )
        changes = []
        if "content" in data:
            after_content = data.get("content") or ""
            if after_content != before_content:
                changes.extend(
                    [
                        ("Antes", f">>> {_short(before_content, 950)}"),
                        ("Depois", f">>> {_short(after_content, 950)}"),
                    ]
                )
            if cached_data is not None:
                cached_data["content"] = after_content
        if "attachments" in data:
            attachments = [self._raw_attachment(item) for item in data.get("attachments", [])]
            before_count = len((cached_data or {}).get("attachments", []))
            if len(attachments) != before_count:
                changes.append(("Anexos", f"{before_count} ➔ {len(attachments)}"))
            if cached_data is not None:
                cached_data["attachments"] = attachments
        if "embeds" in data:
            before_count = len((cached_data or {}).get("embeds", []))
            after_count = len(data.get("embeds", []))
            if before_count != after_count:
                changes.append(("Embeds", f"{before_count} ➔ {after_count}"))
            if cached_data is not None:
                cached_data["embeds"] = [
                    {
                        "title": item.get("title"),
                        "description": item.get("description"),
                        "url": item.get("url"),
                    }
                    for item in data.get("embeds", [])[:5]
                    if isinstance(item, dict)
                ]
        if not changes:
            changes.append(("Metadados", "A mensagem recebeu uma atualização sem texto novo."))
        channel = self.client.get_channel(payload.channel_id)
        guild = getattr(channel, "guild", None) if channel else None
        await self._dispatch_log(
            title="Mensagem Editada",
            color=0xFDFD96,
            changes=changes,
            footer=f"📍 {_short(getattr(guild, 'name', 'DM'), 150)} ➔ #{_short(getattr(channel, 'name', 'DM'), 100)}",
            event_type="message_edit",
        )

    async def on_raw_message_delete(self, payload):
        if not self.enabled:
            return
        await self.msg_buffer.flush(payload.channel_id)
        cached_message = payload.cached_message
        cached_data = self._msg_cache.pop(payload.message_id, None)
        if cached_message:
            if cached_message.author.id != self.target_id:
                return
            content = cached_message.content or ""
            attachments = [self._raw_attachment(item.to_dict()) for item in cached_message.attachments]
        else:
            if not cached_data or cached_data.get("author_id") != self.target_id:
                return
            content = cached_data.get("content", "")
            attachments = cached_data.get("attachments", [])
        parts = [f">>> {_short(content, 3500)}"] if content else []
        parts.extend(f"📎 `{_short(item.get('filename'), 100, 'arquivo')}`" for item in attachments)
        if not parts:
            parts.append("*[sem conteúdo em cache]*")
        channel = self.client.get_channel(payload.channel_id)
        guild = getattr(channel, "guild", None) if channel else None
        await self._dispatch_log(
            title="Mensagem Excluída",
            color=0xFFB7B2,
            description="\n".join(parts),
            footer=f"📍 {_short(getattr(guild, 'name', 'DM'), 150)} ➔ #{_short(getattr(channel, 'name', 'DM'), 100)}",
            event_type="message_delete",
        )
