from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from contextlib import suppress
from typing import Any

import discord

from ..core.ipc import emit_line
from .card import activity_items, device_items, profile_visuals
from .support import CustomStatus, msg_to_dict


def _short(value, limit: int = 250, fallback: str = "—") -> str:
    text = str(value or fallback).replace("\x00", "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# (atributo do VoiceState, título ao ligar, título ao desligar)
_VOICE_FLAG_TITLES = (
    ("self_mute", "🤐 Mutou o microfone", "🎙️ Desmutou o microfone"),
    ("self_deaf", "🔇 Silenciou o áudio", "🔊 Ativou o áudio"),
    ("self_stream", "🎥 Iniciou uma stream", "🛑 Encerrou a stream"),
    ("self_video", "📷 Ligou a câmera", "📷 Desligou a câmera"),
    ("mute", "🛡️ Foi mutado pelo servidor", "🛡️ Foi desmutado pelo servidor"),
    ("deaf", "🛡️ Foi ensurdecido pelo servidor", "🛡️ Áudio do servidor restaurado"),
    ("suppress", "🎭 Foi movido para a audiência", "🎭 Pode falar no palco"),
)


def _user_from_presence(subject):
    return getattr(subject, "user", subject)


class TrackerEventMixin:
    def _channel_allowed(self, channel, *, history: bool = False) -> bool:
        if channel is None:
            return False
        guild = getattr(channel, "guild", None)
        if guild is None:
            return bool(getattr(self.config, "INCLUDE_PRIVATE_CHANNELS", False))
        me = getattr(guild, "me", None)
        if me is None or not hasattr(channel, "permissions_for"):
            return False
        permissions = channel.permissions_for(me)
        return bool(permissions.view_channel and (not history or permissions.read_message_history))

    def _cancel_pending_offline(self):
        if self._pending_offline_task and not self._pending_offline_task.done():
            self._pending_offline_task.cancel()
        self._pending_offline_task = None

    def _cancel_pending_presence(self):
        if self._pending_presence_task and not self._pending_presence_task.done():
            self._pending_presence_task.cancel()
        self._pending_presence_task = None

    def _cancel_pending_custom_status(self):
        task = getattr(self, "_pending_custom_status_task", None)
        if task and not task.done():
            task.cancel()
        self._pending_custom_status_task = None
        self.state.pending_custom_status_key = None

    def _presence_activities(self, subject):
        activities = set()
        custom_status = None
        custom_known = True
        raw_activities = getattr(subject, "activities", None)
        if raw_activities is None:
            return (), None, False
        for activity in raw_activities:
            if isinstance(activity, discord.CustomActivity):
                custom_status = CustomStatus.from_activity(activity)
                # A blank custom-activity object has no useful status information.
                custom_known = custom_status is not None
            else:
                activities.add(self._format_activity(activity))
        return tuple(sorted(activities)), custom_status, custom_known

    def _device_statuses(self, subject) -> list[str]:
        devices = []
        for attr, label in (
            ("desktop_status", "🖥️ Desktop"),
            ("mobile_status", "📱 Mobile"),
            ("web_status", "🌐 Web"),
            ("embedded_status", "🎮 Embedded"),
        ):
            status = getattr(subject, attr, None)
            if status is None:
                continue
            text = str(status)
            if text and text != "offline":
                devices.append(f"{label}: {text}")
        return devices

    def _select_effective_snapshot(self):
        now = time.monotonic()
        fresh = [
            snapshot
            for snapshot in self.state.guild_presence.values()
            if now - snapshot["updated_at"] <= self.config.PRESENCE_SNAPSHOT_TTL
        ]
        if not fresh:
            return None
        return max(fresh, key=lambda snapshot: snapshot["updated_at"])

    def _select_effective_presence(self):
        effective = self._select_effective_snapshot()
        if effective is None:
            return None, [], []
        return (
            effective["status"],
            list(effective["activities"]),
            list(effective["devices"]),
        )

    def _observe_custom_status(self, subject, snapshot):
        if snapshot["status"] == "offline" or not snapshot.get("custom_known", False):
            self._cancel_pending_custom_status()
            return
        candidate = snapshot.get("custom_status")
        previous = self.state.custom_status
        key = candidate.key if candidate else ("", "")
        previous_key = previous.key if previous else ("", "")
        if key == previous_key:
            # An emoji rename or a different rendering of the same ID is not a change.
            self.state.custom_status = candidate
            self._cancel_pending_custom_status()
            return
        task = getattr(self, "_pending_custom_status_task", None)
        if task and not task.done() and key == self.state.pending_custom_status_key:
            return
        self._cancel_pending_custom_status()
        self.state.pending_custom_status_key = key
        losing_emoji = previous and candidate and previous.text == candidate.text and not candidate.emoji_key
        delay = (
            self.config.CUSTOM_STATUS_REMOVAL_SECONDS
            if candidate is None or losing_emoji
            else self.config.CUSTOM_STATUS_STABILITY_SECONDS
        )
        self._pending_custom_status_task = asyncio.create_task(
            self._confirm_custom_status(subject, self._run_id, key, delay),
            name=f"tracker-custom-status-{self.target_id}",
        )

    async def _confirm_custom_status(self, subject, run_id, key, delay):
        try:
            await asyncio.sleep(delay)
            if not self.enabled or run_id != self._run_id or self.state.pending_custom_status_key != key:
                return
            snapshot = self._select_effective_snapshot()
            if not snapshot or snapshot["status"] == "offline" or not snapshot.get("custom_known"):
                return
            candidate = snapshot.get("custom_status")
            if (candidate.key if candidate else ("", "")) != key:
                return
            previous = self.state.custom_status
            if previous and candidate and previous.key == candidate.key:
                return
            value = _short(candidate.display, 450) if candidate else "*Removido*"
            if self.state.custom_status_known:
                before = _short(previous.display, 450) if previous else "*Sem status*"
                value = f"**Antes:** {before}\n**Depois:** {value}"
            self.state.custom_status = candidate
            self.state.custom_status_known = True
            self.state.pending_custom_status_key = None
            await self._dispatch_log(
                title="👀 Presença Atualizada",
                color=0xA2B9EE,
                changes=[("💬 Status personalizado", value)],
                user=_user_from_presence(subject),
                event_type="presence",
            )
        except asyncio.CancelledError:
            return

    async def _emit_presence_changes(self, subject, status: str, activities: list[str], devices: list[str]):
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
            changes.append(("💻 Dispositivos", " • ".join(devices) if devices else "*Nenhum online*"))
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
                snapshot = self._select_effective_snapshot()
                if snapshot:
                    self._observe_custom_status(subject, snapshot)
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
            profile = await self.client.fetch_user_profile(
                self.target_id,
                with_mutual_guilds=False,
                with_mutual_friends=False,
            )
            if not profile:
                return
            if getattr(profile, "is_blocker", lambda: False)():
                return
            bio = getattr(profile, "bio", None)
            connections = self._profile_connections(profile)
            badges = self._profile_badges(profile)
            identity = self._profile_identity(profile)
            initialized = self.state.profile_initialized
            fields = {"bio": bio, "connections": connections, "badges": badges, "identity": identity}
            if not initialized:
                changes = self._initial_profile_fields(**fields)
            elif emit_changes:
                changes = self._profile_changes(**fields)
            else:
                changes = []
            self._store_profile(profile, **fields)
            if changes:
                await self._dispatch_log(
                    title="Perfil Público Atualizado" if initialized else "Perfil Público Inicial",
                    color=0x9B59B6,
                    changes=changes,
                    user=getattr(profile, "user", profile),
                    event_type="profile",
                )
        except Exception as error:
            self._stats["profile_errors"] = self._stats.get("profile_errors", 0) + 1
            self._stats["last_error"] = "PROFILE_FETCH_ERROR"
            emit_line(f"TRACKER:PROFILE_FETCH_ERROR:{type(error).__name__}")

    @staticmethod
    def _profile_connections(profile) -> list[str]:
        return sorted(
            {
                f"`{_short(getattr(item, 'type', None), 40)}: "
                f"{_short(getattr(item, 'name', None), 100)}`"
                + (f" [Perfil]({item.url})" if getattr(item, "url", None) else "")
                for item in (getattr(profile, "connections", None) or [])
                if getattr(item, "visible", True)
            }
        )

    @staticmethod
    def _profile_badges(profile) -> list[str]:
        return sorted(
            {
                _short(getattr(item, "description", None) or getattr(item, "id", None), 80)
                for item in (getattr(profile, "badges", None) or [])
            }
        )

    def _profile_identity(self, profile) -> dict[str, Any]:
        return {
            "Username": getattr(profile, "name", None),
            "Nome de exibição": getattr(profile, "global_name", None),
            "Avatar": str(getattr(profile, "display_avatar", "") or ""),
            "Banner": str(getattr(profile, "banner", "") or ""),
            "Pronomes": getattr(getattr(profile, "metadata", None), "pronouns", None),
            "Cor de destaque": str(getattr(profile, "accent_colour", "") or ""),
            "Assinatura Nitro": self._premium_label(profile),
            "Nitro desde": self._iso_date(getattr(profile, "premium_since", None)),
            "Impulsionando servidor desde": self._iso_date(getattr(profile, "premium_guild_since", None)),
        }

    @staticmethod
    def _initial_profile_fields(*, bio, connections, badges, identity) -> list[tuple[str, str]]:
        """Campos do primeiro registro do perfil, quando ainda não há com o que comparar."""
        return [
            *((label, _short(value, 900)) for label, value in identity.items()),
            ("Bio pública", _short(bio, 900)),
            ("Insígnias", "\n".join(badges) or "Nenhuma"),
            ("Conexões públicas", "\n".join(connections) or "Nenhuma visível"),
        ]

    def _profile_changes(self, *, bio, connections, badges, identity) -> list[tuple[str, str]]:
        """Diferenças entre o perfil buscado e o último guardado, na ordem do registro."""
        changes = []
        if bio != self.state.bio:
            changes.append(
                (
                    "Bio editada",
                    f"**Antes:**\n>>> {_short(self.state.bio, 400, '*Vazio*')}\n\n"
                    f"**Depois:**\n>>> {_short(bio, 400, '*Vazio*')}",
                )
            )
        for current, previous, added_label, removed_label in (
            (
                connections,
                self.state.connected_accounts,
                "Conexões públicas adicionadas",
                "Conexões públicas removidas",
            ),
            (badges, self.state.badges, "Insígnias adicionadas", "Insígnias removidas"),
        ):
            if current == previous:
                continue
            added = sorted(set(current) - set(previous))
            removed = sorted(set(previous) - set(current))
            if added:
                changes.append((added_label, "\n".join(added)))
            if removed:
                changes.append((removed_label, "\n".join(removed)))
        for label, value in identity.items():
            previous = self.state.profile_identity.get(label)
            if previous != value:
                changes.append((label, f"{_short(previous, 450)} ➔ {_short(value, 450)}"))
        return changes

    def _store_profile(self, profile, *, bio, connections, badges, identity) -> None:
        self.state.bio = bio
        self.state.connected_accounts = connections
        self.state.badges = badges
        self.state.profile_identity = identity
        try:
            self.state.profile_card = profile_visuals(profile)
        except Exception:
            self.state.profile_card = {}
        self.state.profile_initialized = True

    @staticmethod
    def _premium_label(profile) -> str | None:
        premium = getattr(profile, "premium_type", None)
        name = getattr(premium, "name", None)
        if not name or str(name).lower() in {"none", "0"}:
            return "Nenhuma" if premium is not None else None
        return str(name)

    @staticmethod
    def _iso_date(value) -> str | None:
        try:
            return value.strftime("%Y-%m-%d") if value is not None else None
        except AttributeError:
            return None

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
            changes.append(
                ("📸 Foto de perfil", f"[Anterior]({before.display_avatar.url}) ➔ [Atual]({after.display_avatar.url})")
            )
            self.state.avatar_url = str(after.display_avatar.url)
        if getattr(before, "avatar_decoration", None) != getattr(after, "avatar_decoration", None):
            decoration = getattr(after, "avatar_decoration", None)
            changes.append(("🖼️ Decoração do avatar", f"[Abrir]({decoration.url})" if decoration else "Removida"))
        if self._primary_guild_repr(before) != self._primary_guild_repr(after):
            changes.append(
                (
                    "🏷️ Guilda principal",
                    f"{self._primary_guild_repr(before)} ➔ {self._primary_guild_repr(after)}",
                )
            )
        if changes:
            if self.state.profile_initialized:
                self.state.profile_identity.update(
                    {
                        "Username": after.name,
                        "Nome de exibição": getattr(after, "global_name", None),
                        "Avatar": str(after.display_avatar),
                    }
                )
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
        activities, custom_status, custom_known = self._presence_activities(after)
        self.state.guild_presence[source_id] = {
            "status": str(after.status),
            "activities": activities,
            "custom_status": custom_status,
            "custom_known": custom_known,
            "devices": tuple(self._device_statuses(after)),
            "activity_items": activity_items(after),
            "device_items": device_items(after),
            "updated_at": now,
        }
        status, activities, devices = self._select_effective_presence()
        if status is None:
            return
        if status == "offline":
            self._cancel_pending_custom_status()
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
        session = self.state.voice_sessions.pop(payload.guild_id, None)
        if session:
            self.tracked_voice_channels.discard(session["channel_id"])
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
        if not self.enabled or user.id != self.target_id or not self._channel_allowed(channel):
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
        before_channel = before.channel if self._channel_allowed(before.channel) else None
        after_channel = after.channel if self._channel_allowed(after.channel) else None
        if not before_channel and not after_channel:
            return
        if member.id != self.target_id:
            await self._announce_other_member_voice(member, before, after, before_channel, after_channel)
            return

        titles, duration, partial, previous_channel = [], None, False, None
        if before.channel != after.channel:
            titles, duration, partial, previous_channel = self._move_voice_session(
                member, before, after, before_channel, after_channel
            )
        titles.extend(self._voice_flag_titles(before, after))
        if titles:
            await self._dispatch_voice_embed(
                title=" • ".join(titles),
                color=0x9DF9EF,
                member=member,
                channel=after_channel or before_channel,
                guild=member.guild,
                duration=duration,
                partial=partial,
                previous_channel=previous_channel,
            )

    async def _announce_other_member_voice(self, member, before, after, before_channel, after_channel) -> None:
        """Outra pessoa entrou ou saiu de uma call em que o alvo está."""
        if before.channel == after.channel:
            return
        if after_channel and after_channel.id in self.tracked_voice_channels:
            await self._dispatch_voice_embed("👥 Entrou na Call do Alvo", 0x2ECC71, member, after.channel, member.guild)
        elif before_channel and before_channel.id in self.tracked_voice_channels:
            await self._dispatch_voice_embed("🚶 Saiu da Call do Alvo", 0xE74C3C, member, before.channel, member.guild)

    def _move_voice_session(
        self, member, before, after, before_channel, after_channel
    ) -> tuple[list[str], int | None, bool, Any]:
        """Fecha a sessão de voz anterior, abre a nova e devolve o título da mudança.

        Devolve `(títulos, duração da sessão fechada, sessão parcial, canal anterior)`.
        """
        duration = None
        partial = False
        previous_channel = None
        if before.channel:
            self.tracked_voice_channels.discard(before.channel.id)
        session = self.state.voice_sessions.pop(member.guild.id, None)
        if session:
            duration = max(0, int(time.monotonic() - session["started"]))
            partial = session["partial"]
            self._stats["voice_seconds"] = self._stats.get("voice_seconds", 0) + duration
            previous_channel = before_channel
        if after_channel:
            self.tracked_voice_channels.add(after_channel.id)
            self.state.in_voice_channel_id = after_channel.id
            self.state.voice_sessions[member.guild.id] = {
                "channel_id": after_channel.id,
                "started": time.monotonic(),
                "partial": False,
            }
        else:
            self.state.in_voice_channel_id = None
        if before.channel is None:
            title = "🔊 Entrou no Canal de Voz"
        elif after.channel is None:
            title = "🔇 Saiu do Canal de Voz"
        else:
            title = "🔀 Mudou de Canal de Voz"
        return [title], duration, partial, previous_channel

    @staticmethod
    def _voice_flag_titles(before, after) -> list[str]:
        titles = []
        for attr, enabled_label, disabled_label in _VOICE_FLAG_TITLES:
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
        return titles

    async def _on_raw_reaction(self, payload, *, added: bool):
        if not self.enabled:
            return
        channel = self.client.get_channel(payload.channel_id)
        if not self._channel_allowed(channel):
            return
        guild = getattr(channel, "guild", None) if channel else None
        cached = self._msg_cache.get(payload.message_id)
        if payload.user_id != self.target_id and (cached or {}).get("author_id") != self.target_id:
            return
        self._stats["interactions_seen"] = self._stats.get("interactions_seen", 0) + 1
        content = _short(cached.get("content"), 300, "") if cached else ""
        author = _short(cached.get("author_name"), 80, "") if cached else ""
        preview = f"> **{author}:** {content}" if content else "*[mensagem fora do cache]*"
        guild_id = guild.id if guild else "@me"
        jump_url = f"https://discord.com/channels/{guild_id}/{payload.channel_id}/{payload.message_id}"
        action = "Adicionada" if added else "Removida"
        await self._dispatch_log(
            title=f"{payload.emoji} Reação {action}",
            description=f"Usuário: `{payload.user_id}`\n{preview}\n\n[↗️ Ir para a mensagem]({jump_url})",
            color=0x8E44AD if added else 0x7F8C8D,
            footer=f"#{_short(getattr(channel, 'name', 'DM'), 100)} | Servidor: {_short(getattr(guild, 'name', 'DM'), 150)}",
            event_type="reaction",
        )

    async def on_raw_reaction_add(self, payload):
        await self._on_raw_reaction(payload, added=True)

    async def on_raw_reaction_remove(self, payload):
        await self._on_raw_reaction(payload, added=False)

    async def on_message(self, message):
        if not self.enabled or not self._channel_allowed(message.channel):
            return
        if message.author.id == self.target_id:
            await self._capture_message(message)
            return
        if message.id not in self._msg_cache:
            poll = getattr(message, "poll", None)
            data = {
                "_captured": False,
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
        reference = getattr(message, "reference", None)
        resolved = getattr(reference, "resolved", None)
        replied_author = getattr(getattr(resolved, "author", None), "id", None)
        if replied_author is None:
            replied_author = self._msg_cache.get(getattr(reference, "message_id", None), {}).get("author_id")
        mentioned = any(user.id == self.target_id for user in getattr(message, "mentions", []))
        if replied_author == self.target_id or mentioned:
            self._stats["interactions_seen"] = self._stats.get("interactions_seen", 0) + 1
            await self._dispatch_log(
                title="↪️ Resposta ao usuário" if replied_author == self.target_id else "📣 Menção ao usuário",
                description=f"{_short(message.content, 3000)}\n\n[Mensagem]({message.jump_url})",
                user=message.author,
                footer=f"{getattr(message.guild, 'name', 'DM')} • #{getattr(message.channel, 'name', 'DM')}",
                event_type="interaction",
            )

    async def _capture_message(self, message, *, historical: bool = False):
        cached = self._msg_cache.get(message.id)
        if cached is not None and cached.get("_captured", True):
            return
        data = msg_to_dict(message)
        data["_captured"] = True
        data["historical"] = historical
        reply = data.get("reply")
        if reply and not reply.get("author_id"):
            context = self._msg_cache.get(reply["message_id"], {})
            reply.update({key: context[key] for key in ("author_id", "author_name", "content") if key in context})
        self._cache_message(message.id, data)
        key = "history_messages" if historical else "messages_seen"
        self._stats[key] = self._stats.get(key, 0) + 1
        self._stats["media_seen"] = self._stats.get("media_seen", 0) + len(data["attachments"]) + len(data["stickers"])
        if not historical:
            if reply or data["mentions"]:
                self._stats["interactions_seen"] = self._stats.get("interactions_seen", 0) + 1
            self.msg_buffer.add(message.channel.id, data)
        else:
            await self._on_buffer_flush(message.channel.id, [data], channel=message.channel)

    async def _on_raw_poll_vote(self, payload, *, added: bool):
        if not self.enabled or payload.user_id != self.target_id:
            return
        cached = self._msg_cache.get(payload.message_id) or {}
        poll = cached.get("poll") or {}
        answer = next(
            (item.get("text") for item in poll.get("answers", []) if item.get("id") == payload.answer_id),
            f"Resposta {payload.answer_id}",
        )
        channel = self.client.get_channel(payload.channel_id)
        if not self._channel_allowed(channel):
            return
        self._stats["interactions_seen"] = self._stats.get("interactions_seen", 0) + 1
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
        if not getattr(self.config, "INCLUDE_PRIVATE_CHANNELS", False):
            return False
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
        return {user.id for user in (getattr(call, attribute, []) or []) if getattr(user, "id", None)}

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
        if not self.enabled or not (self._call_involves_target(before) or self._call_involves_target(after)):
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
            changes.append(
                ("Região", f"{_short(getattr(before, 'region', None))} ➔ {_short(getattr(after, 'region', None))}")
            )
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
        channel = self.client.get_channel(payload.channel_id)
        if not self._channel_allowed(channel):
            return
        data = payload.data
        cached_message = payload.cached_message
        cached_data = self._msg_cache.get(payload.message_id)
        author_id = self._edit_author_id(cached_message, cached_data, data)
        if author_id != self.target_id:
            return

        await self.msg_buffer.flush(payload.channel_id)
        if cached_data is None:
            cached_data = msg_to_dict(cached_message) if cached_message else {"author_id": author_id}
            cached_data["_captured"] = True
            self._cache_message(payload.message_id, cached_data)

        changes = self._apply_message_edit(data, cached_data)
        if not changes:
            return
        channel = self.client.get_channel(payload.channel_id)
        guild = getattr(channel, "guild", None) if channel else None
        await self._dispatch_log(
            title="Mensagem Editada",
            color=0xFDFD96,
            changes=changes,
            description=f"[Mensagem](https://discord.com/channels/{getattr(guild, 'id', '@me')}/{payload.channel_id}/{payload.message_id})",
            footer=f"📍 {_short(getattr(guild, 'name', 'DM'), 150)} ➔ #{_short(getattr(channel, 'name', 'DM'), 100)}",
            event_type="message_edit",
        )

    @staticmethod
    def _edit_author_id(cached_message, cached_data, data) -> int | None:
        if cached_message:
            return cached_message.author.id
        if cached_data:
            return cached_data.get("author_id")
        if isinstance(data.get("author"), dict):
            with suppress(TypeError, ValueError):
                return int(data["author"].get("id"))
        return None

    @staticmethod
    def _raw_embed(item: dict) -> dict:
        return {
            "title": item.get("title"),
            "description": item.get("description"),
            "url": item.get("url"),
            "image_url": (item.get("image") or {}).get("url"),
            "video_url": (item.get("video") or {}).get("url"),
        }

    def _apply_message_edit(self, data: dict, cached_data: dict) -> list[tuple[str, str]]:
        """Compara a edição com a mensagem guardada, atualiza o cache e devolve as mudanças."""
        changes = []
        if "content" in data:
            before_content = cached_data.get("content", "")
            after_content = data.get("content") or ""
            if after_content != before_content:
                changes.extend(
                    [
                        ("Antes", f">>> {_short(before_content, 950)}"),
                        ("Depois", f">>> {_short(after_content, 950)}"),
                    ]
                )
            cached_data["content"] = after_content
        if "attachments" in data:
            attachments = [self._raw_attachment(item) for item in data.get("attachments", [])]
            before_count = len(cached_data.get("attachments", []))
            if attachments != cached_data.get("attachments", []):
                links = "\n".join(f"[{_short(item['filename'], 80)}]({item['url']})" for item in attachments)
                changes.append(("Anexos", f"{before_count} ➔ {len(attachments)}\n{links}"))
            cached_data["attachments"] = attachments
        if "embeds" in data:
            embeds = [self._raw_embed(item) for item in data.get("embeds", [])[:5] if isinstance(item, dict)]
            if embeds != cached_data.get("embeds", []):
                summary = "\n".join(
                    _short(item.get("title") or item.get("description") or item.get("url"), 300) for item in embeds
                )
                changes.append(("Embeds", summary or "Removidos"))
            cached_data["embeds"] = embeds
        return changes

    async def on_raw_message_delete(self, payload):
        if not self.enabled:
            return
        shared = getattr(self, "_shared_resources", False)
        channel = self.client.get_channel(payload.channel_id)
        if not self._channel_allowed(channel):
            if not shared:
                self._msg_cache.pop(payload.message_id, None)
            return
        cached_message = payload.cached_message
        cached_data = (
            self._msg_cache.get(payload.message_id) if shared else self._msg_cache.pop(payload.message_id, None)
        )
        if cached_data:
            if cached_data.get("author_id") != self.target_id:
                return
            if shared:
                self._msg_cache.pop(payload.message_id, None)
            content = cached_data.get("content", "")
            attachments = cached_data.get("attachments", [])
        elif cached_message:
            if cached_message.author.id != self.target_id:
                return
            content = cached_message.content or ""
            attachments = [self._raw_attachment(item.to_dict()) for item in cached_message.attachments]
        else:
            return
        await self.msg_buffer.flush(payload.channel_id)
        parts = [f">>> {_short(content, 3500)}"] if content else []
        parts.extend(
            f"📎 [{_short(item.get('filename'), 100, 'arquivo')}]({item.get('url', '')})" for item in attachments
        )
        if not parts:
            parts.append("*[sem conteúdo em cache]*")
        parts.append(f"ID da mensagem: `{payload.message_id}`")
        channel = self.client.get_channel(payload.channel_id)
        guild = getattr(channel, "guild", None) if channel else None
        await self._dispatch_log(
            title="Mensagem Excluída",
            color=0xFFB7B2,
            description="\n".join(parts),
            footer=f"📍 {_short(getattr(guild, 'name', 'DM'), 150)} ➔ #{_short(getattr(channel, 'name', 'DM'), 150)}",
            event_type="message_delete",
        )

    async def on_raw_bulk_message_delete(self, payload):
        cached = {message.id: message for message in payload.cached_messages}
        for message_id in sorted(payload.message_ids):
            await self.on_raw_message_delete(
                SimpleNamespace(
                    message_id=message_id,
                    channel_id=payload.channel_id,
                    cached_message=cached.get(message_id),
                )
            )
