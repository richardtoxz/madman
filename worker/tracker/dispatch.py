from __future__ import annotations

import discord
from .style import (
    channel_ref,
    discord_time,
    footer_text,
    is_short_field,
    small,
    user_ref,
)
from .support import CustomStatus


def _clip(value, limit: int, fallback: str = "") -> str:
    text = str(value or fallback).replace("\x00", "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _asset_url(value) -> str | None:
    return str(value) if value else None


def _message_chunks(text: str, limit: int = 3800) -> list[str]:
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        chunks.append(current)
    return chunks or ["[mensagem sem texto]"]


class TrackerDispatchMixin:
    def _format_activity(self, activity) -> str:
        if isinstance(activity, discord.CustomActivity):
            status = CustomStatus.from_activity(activity)
            return _clip(f"> {status.display}", 250) if status else ""
        if isinstance(activity, discord.Spotify):
            return _clip(f"> 🎵 Ouvindo **{activity.title}** de *{activity.artist}*", 250)
        if isinstance(activity, getattr(discord, "HangActivity", ())):
            return _clip(f"> 🫂 Hang: **{getattr(activity, 'text', '') or 'Ativo'}**", 250)

        activity_type = getattr(activity, "type", None)
        labels = {
            discord.ActivityType.playing: "🎮 Jogando",
            discord.ActivityType.streaming: "🎥 Transmitindo",
            discord.ActivityType.listening: "🎧 Ouvindo",
            discord.ActivityType.watching: "📺 Assistindo",
            discord.ActivityType.competing: "🏆 Competindo em",
            getattr(discord.ActivityType, "hang", None): "🫂 Hang",
        }
        label = labels.get(activity_type, "🕹️ Atividade")
        name = _clip(getattr(activity, "name", None), 150, "Desconhecida")
        detail = _clip(getattr(activity, "details", None), 100)
        suffix = f" — {detail}" if detail else ""
        return _clip(f"> {label} **{name}**{suffix}", 250)

    async def _dispatch_voice_embed(
        self,
        title: str,
        color: int,
        member,
        channel,
        guild,
        duration=None,
        partial=False,
        previous_channel=None,
    ):
        if not self.enabled:
            return
        embed = discord.Embed(title=_clip(title, 256), color=color, timestamp=discord.utils.utcnow())
        embed.set_author(
            name=_clip(getattr(member, "name", None), 256, "Usuario"),
            icon_url=_asset_url(getattr(member, "display_avatar", None)),
        )
        if channel:
            embed.add_field(name="Servidor", value=_clip(guild.name, 200), inline=True)
            embed.add_field(name="Canal de voz", value=channel_ref(channel.id), inline=True)
            others = [item for item in getattr(channel, "members", []) if item.id != member.id]
            if duration is not None:
                label = "Tempo observado (início desconhecido)" if partial else "Tempo no canal anterior"
                embed.add_field(
                    name=label,
                    value=f"{duration // 3600:02d}:{duration // 60 % 60:02d}:{duration % 60:02d}",
                    inline=True,
                )
            if previous_channel and channel != previous_channel:
                embed.add_field(name="Canal anterior", value=channel_ref(previous_channel.id), inline=True)
            visible = others[:25]
            member_list = " ".join(user_ref(item.id) for item in visible)
            if len(others) > len(visible):
                member_list += f" … e mais {len(others) - len(visible)}"
            embed.add_field(
                name=f"No canal agora ({len(others)})",
                value=_clip(member_list, 1024, "Ninguém além do alvo"),
                inline=False,
            )
        else:
            embed.add_field(name="Servidor", value=_clip(guild.name, 200), inline=True)
            if duration is not None:
                label = "Tempo observado (início desconhecido)" if partial else "Tempo no canal anterior"
                embed.add_field(
                    name=label,
                    value=f"{duration // 3600:02d}:{duration // 60 % 60:02d}:{duration % 60:02d}",
                    inline=True,
                )
            if previous_channel:
                embed.add_field(name="Canal anterior", value=channel_ref(previous_channel.id), inline=True)
        embed.set_footer(text=footer_text(self.target_id))
        self._queue_webhook(embed, "voice")

    async def _dispatch_server_embed(
        self,
        title: str,
        color: int,
        user,
        guild_name: str,
        guild_id: int,
    ):
        if not self.enabled:
            return
        embed = discord.Embed(title=_clip(title, 256), color=color, timestamp=discord.utils.utcnow())
        embed.set_author(
            name=_clip(getattr(user, "name", None), 256, "Usuario"),
            icon_url=_asset_url(getattr(user, "display_avatar", None)),
        )
        embed.add_field(name="Servidor", value=_clip(guild_name, 200), inline=True)
        embed.set_footer(text=footer_text(self.target_id, f"ID do servidor {guild_id}"))
        self._queue_webhook(embed, "server")

    async def _dispatch_initial_state(self, member):
        if self.state.initial_state_sent:
            return
        self.state.initial_state_sent = True
        # A gateway update may have arrived while the subscription scan was running.
        if self.state.status is not None:
            await self._dispatch_log(
                title="📡 Estado Inicial Detectado",
                color=0xD4A5A5,
                changes=[("Situação atual", f"`{self.state.status.title()}`")],
                user=member,
            )
            return
        status = str(member.status)
        status_emojis = {"online": "🟢", "idle": "🌙", "dnd": "⛔", "offline": "⚫"}
        changes = [("Situacao atual", f"{status_emojis.get(status, '⚪')} `{status.title()}`")]
        self.state.status = status

        activities, custom_status, _ = self._presence_activities(member)
        activities = list(activities)
        if activities:
            changes.append(("Atividades", "\n".join(activities)))
        self.state.activities_repr = activities
        devices = self._device_statuses(member)
        self.state.devices_repr = devices
        if devices:
            changes.append(("Dispositivos", " • ".join(devices)))
        if custom_status and status != "offline":
            self.state.custom_status = custom_status
            self.state.custom_status_known = True
            changes.append(("💬 Status personalizado", custom_status.display))

        await self._dispatch_log(
            title="📡 Estado Inicial Detectado",
            color=0xD4A5A5,
            changes=changes,
            user=member,
        )

    async def _dispatch_log(
        self,
        title: str = "Madman Tracker",
        color: int = 0x95A5A6,
        changes: list | None = None,
        description: str | None = None,
        user=None,
        footer: str | None = None,
        event_type: str = "general",
    ):
        if not self.enabled:
            return
        embed = discord.Embed(
            title=_clip(title, 256),
            color=color,
            description=_clip(description, 4096) or None,
            timestamp=discord.utils.utcnow(),
        )
        if user:
            embed.set_author(
                name=_clip(getattr(user, "name", None), 256, "Usuario"),
                icon_url=_asset_url(getattr(user, "display_avatar", None)),
            )
        footer_line = footer_text(self.target_id, _clip(footer, 300) if footer else None)
        embed.set_footer(text=footer_line)
        for name, value in (changes or [])[:25]:
            name = _clip(name, 256, "Alteracao")
            value = _clip(value, 1024, "—")
            if len(embed) + len(name) + len(value) > 5900:
                self._queue_webhook(embed, event_type)
                embed = discord.Embed(title=_clip(title, 256), color=color, timestamp=discord.utils.utcnow())
                embed.set_footer(text=footer_line)
            embed.add_field(
                name=name,
                value=value,
                inline=is_short_field(name, value),
            )
        self._queue_webhook(embed, event_type)

    async def _on_buffer_flush(self, channel_id: int, messages: list[dict], *, channel=None):
        if not self.enabled or not messages:
            return
        # Each message is rendered separately so a burst cannot truncate its tail.
        if len(messages) > 1:
            for message in messages:
                await self._on_buffer_flush(channel_id, [message], channel=channel)
            return
        channel = channel or self.client.get_channel(channel_id)
        if not self._channel_allowed(channel):
            return
        guild = getattr(channel, "guild", None) if channel else None
        guild_name = getattr(guild, "name", "DM")
        first_image_url = None
        lines = []

        for message in messages:
            moment = message.get("created_unix")
            line_parts = [
                small(f"{discord_time(moment, 'f')} · {discord_time(moment, 'R')}")
                if moment
                else small(str(message.get("timestamp", "")))
            ]
            reply = message.get("reply") or {}
            if reply:
                reply_author = _clip(reply.get("author_name"), 40, "mensagem")
                reply_content = _clip(reply.get("content"), 140, "sem cache")
                line_parts.append(f"↪️ Em resposta a **{reply_author}**: {reply_content}")
            content = str(message.get("content") or "")
            if content:
                line_parts.append(content)

            for attachment in message.get("attachments", []):
                url = attachment.get("url", "")
                content_type = str(attachment.get("content_type") or "")
                filename = _clip(attachment.get("filename"), 100, "arquivo")
                if content_type.startswith("image/") and first_image_url is None and not attachment.get("spoiler"):
                    first_image_url = url
                line_parts.append(f"> 📎 [{filename}]({url}) ({attachment.get('size', 0)} bytes)")
            for sticker in message.get("stickers", []):
                if sticker.get("url") and first_image_url is None:
                    first_image_url = sticker["url"]
                line_parts.append(f"> 🎭 Sticker: **{_clip(sticker.get('name'), 80)}**")
            for item in message.get("embeds", []):
                summary = _clip(item.get("title") or item.get("description"), 180)
                if summary:
                    line_parts.append(f"> 🧩 Embed: {summary}")
                urls = (item.get("url"), item.get("image_url"), item.get("video_url"))
                for url in dict.fromkeys(filter(None, urls)):
                    line_parts.append(f"> [Mídia/link incorporado]({url})")
            poll = message.get("poll")
            if poll:
                line_parts.append(f"> 📊 Enquete: **{_clip(poll.get('question'), 180)}**")
            if message.get("mentions"):
                line_parts.append("Menções: " + ", ".join(f"`{item}`" for item in message["mentions"]))
            if message.get("jump_url"):
                line_parts.append(f"[↗️ Mensagem original]({message['jump_url']})")
            if len(line_parts) == 1:
                line_parts.append("*[mensagem sem texto]*")
            lines.append("\n".join(line_parts))

        content_text = "\n\n".join(lines)
        chunks = _message_chunks(content_text)
        first = messages[0]
        place = f"{_clip(guild_name, 120)} › {channel_ref(channel_id) if channel else 'DM'}"
        embed = discord.Embed(
            title="Mensagem antiga" if first.get("historical") else "Nova mensagem",
            description=f"{small('📍 ' + place)}\n{chunks[0]}",
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=_clip(first.get("author_name"), 256, "Usuario"),
            icon_url=first.get("author_avatar") or None,
        )
        if first_image_url:
            embed.set_image(url=first_image_url)
        embed.set_footer(text=footer_text(self.target_id))
        event_type = "history_message" if first.get("historical") else "message_batch"
        self._queue_webhook(embed, event_type)
        for chunk in chunks[1:]:
            continuation = discord.Embed(title="Continuação", description=chunk)
            continuation.set_footer(text=footer_text(self.target_id, f"Mensagem {first.get('message_id')}"))
            self._queue_webhook(continuation, event_type)
