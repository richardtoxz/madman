from __future__ import annotations

import discord


def _clip(value, limit: int, fallback: str = "") -> str:
    text = str(value or fallback).replace("\x00", "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _asset_url(value) -> str | None:
    return str(value) if value else None


class TrackerDispatchMixin:
    def _format_activity(self, activity) -> str:
        if isinstance(activity, discord.CustomActivity):
            emoji = str(getattr(activity, "emoji", "") or "💭")
            return _clip(f"> {emoji} {_clip(activity.name, 180, 'Status personalizado')}", 250)
        if isinstance(activity, discord.Spotify):
            return _clip(
                f"> 🎵 Ouvindo **{activity.title}** de *{activity.artist}*", 250
            )
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
    ):
        if not self.enabled:
            return
        embed = discord.Embed(
            title=_clip(title, 256), color=color, timestamp=discord.utils.utcnow()
        )
        embed.set_author(
            name=_clip(getattr(member, "name", None), 256, "Usuario"),
            icon_url=_asset_url(getattr(member, "display_avatar", None)),
        )
        embed.add_field(name="👤 Usuario", value=f"`{member.id}`", inline=False)

        if channel:
            embed.add_field(
                name="🏠 Servidor",
                value=f"**{_clip(guild.name, 200)}**\n`{guild.id}`",
                inline=True,
            )
            embed.add_field(
                name="🔊 Canal de voz",
                value=f"**{_clip(channel.name, 200)}**\n`{channel.id}`",
                inline=True,
            )
            others = [item for item in getattr(channel, "members", []) if item.id != member.id]
            visible = others[:25]
            member_list = "\n".join(
                f"• {_clip(getattr(item, 'display_name', item.name), 45)} (`{item.id}`)"
                for item in visible
            )
            if len(others) > len(visible):
                member_list += f"\n• … e mais {len(others) - len(visible)}"
            embed.add_field(
                name=f"👥 Usuarios no canal ({len(others)})",
                value=_clip(member_list, 1024, "*Vazio*"),
                inline=False,
            )
            embed.add_field(
                name="🔗 Link do canal",
                value=f"[Clique para acessar](https://discord.com/channels/{guild.id}/{channel.id})",
                inline=False,
            )
        else:
            embed.add_field(
                name="🏠 Servidor",
                value=f"**{_clip(guild.name, 200)}**\n`{guild.id}`",
                inline=False,
            )
        embed.set_footer(text="Madman Tracker")
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
        embed = discord.Embed(
            title=_clip(title, 256), color=color, timestamp=discord.utils.utcnow()
        )
        embed.set_author(
            name=_clip(getattr(user, "name", None), 256, "Usuario"),
            icon_url=_asset_url(getattr(user, "display_avatar", None)),
        )
        embed.add_field(
            name="👤 Usuario",
            value=f"`{_clip(getattr(user, 'name', None), 100)} ({user.id})`",
            inline=False,
        )
        embed.add_field(
            name="🏠 Servidor",
            value=f"**{_clip(guild_name, 200)}**\n`{guild_id}`",
            inline=False,
        )
        embed.set_footer(text="Madman Tracker")
        self._queue_webhook(embed, "server")

    async def _dispatch_initial_state(self, member):
        status = str(member.status)
        status_emojis = {"online": "🟢", "idle": "🌙", "dnd": "⛔", "offline": "⚫"}
        changes = [("Situacao atual", f"{status_emojis.get(status, '⚪')} `{status.title()}`")]
        self.state.status = status

        activities = [self._format_activity(activity) for activity in member.activities]
        if activities:
            changes.append(("Atividades", "\n".join(activities)))
        self.state.activities_repr = activities
        devices = self._device_statuses(member)
        self.state.devices_repr = devices
        if devices:
            changes.append(("Dispositivos", " • ".join(devices)))

        await self._dispatch_log(
            title="📡 Estado Inicial Detectado",
            color=0xD4A5A5,
            changes=changes,
            user=member,
        )
        voice_state = member.voice
        if voice_state and voice_state.channel:
            self.state.in_voice_channel_id = voice_state.channel.id
            self.tracked_voice_channels.add(voice_state.channel.id)
            await self._dispatch_voice_embed(
                title="🔊 Conectado no Canal de Voz",
                color=0x9DF9EF,
                member=member,
                channel=voice_state.channel,
                guild=member.guild,
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
        for name, value in (changes or [])[:25]:
            embed.add_field(
                name=_clip(name, 256, "Alteracao"),
                value=_clip(value, 1024, "—"),
                inline=False,
            )
        footer_text = f"{_clip(footer, 1950)} • Madman Tracker" if footer else "Madman Tracker"
        embed.set_footer(text=footer_text)
        self._queue_webhook(embed, event_type)

    async def _on_buffer_flush(self, channel_id: int, messages: list[dict]):
        if not self.enabled or not messages:
            return
        channel = self.client.get_channel(channel_id)
        channel_name = getattr(channel, "name", "DM") if channel else "DM"
        guild = getattr(channel, "guild", None) if channel else None
        guild_name = getattr(guild, "name", "DM")
        first_image_url = None
        lines = []

        for message in messages:
            line_parts = [f"`{message.get('timestamp', '')}`"]
            reply = message.get("reply") or {}
            if reply:
                reply_author = _clip(reply.get("author_name"), 40, "mensagem")
                reply_content = _clip(reply.get("content"), 140, "sem cache")
                line_parts.append(f"↪️ Em resposta a **{reply_author}**: {reply_content}")
            content = _clip(message.get("content"), 900)
            if content:
                line_parts.append(content)

            for attachment in message.get("attachments", []):
                url = attachment.get("url", "")
                content_type = str(attachment.get("content_type") or "")
                filename = _clip(attachment.get("filename"), 100, "arquivo")
                if content_type.startswith("image/") and first_image_url is None:
                    first_image_url = url
                line_parts.append(f"> 📎 `{filename}` ({attachment.get('size', 0)} bytes)")
            for sticker in message.get("stickers", []):
                if sticker.get("url") and first_image_url is None:
                    first_image_url = sticker["url"]
                line_parts.append(f"> 🎭 Sticker: **{_clip(sticker.get('name'), 80)}**")
            for item in message.get("embeds", [])[:2]:
                summary = _clip(item.get("title") or item.get("description"), 180)
                if summary:
                    line_parts.append(f"> 🧩 Embed: {summary}")
            poll = message.get("poll")
            if poll:
                line_parts.append(f"> 📊 Enquete: **{_clip(poll.get('question'), 180)}**")
            if len(line_parts) == 1:
                line_parts.append("*[mensagem sem texto]*")
            lines.append("\n".join(line_parts))

        content_text = _clip("\n\n".join(lines), 3800)
        first = messages[0]
        embed = discord.Embed(
            title="Mensagem Enviada",
            color=0xA8E6CF,
            description=content_text,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=_clip(first.get("author_name"), 256, "Usuario"),
            icon_url=first.get("author_avatar") or None,
        )
        embed.add_field(
            name="📍 Local",
            value=f"`{_clip(guild_name, 150)}` ➔ `#{_clip(channel_name, 150)}`",
            inline=False,
        )
        if first_image_url:
            embed.set_image(url=first_image_url)
        embed.set_footer(text=f"{len(messages)} mensagem(ns) • Madman Tracker")
        self._queue_webhook(embed, "message_batch")
