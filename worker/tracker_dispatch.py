import aiohttp
import discord

from .ipc import emit_line


class TrackerDispatchMixin:
    def _format_activity(self, act) -> str:
        match act:
            case discord.CustomActivity():
                return f"> 💭 `{act.name}`"
            case discord.Spotify():
                return f"> 🎵 Ouvindo **{act.title}** de *{act.artist}*"
            case _:
                a_type = getattr(act, "type", None)
                if a_type == discord.ActivityType.playing:
                    return f"> 🎮 Jogando **{act.name}**"
                if a_type == discord.ActivityType.streaming:
                    return f"> 🎥 Transmitindo **{act.name}**"
                if a_type == discord.ActivityType.listening:
                    return f"> 🎧 Ouvindo **{act.name}**"
                if a_type == discord.ActivityType.watching:
                    return f"> 📺 Assistindo **{act.name}**"
                if a_type == discord.ActivityType.competing:
                    return f"> 🏆 Competindo em **{act.name}**"
                return f"> 🕹️ Atividade: **{act.name}**"

    async def _dispatch_voice_embed(
        self,
        title: str,
        color: int,
        member: discord.Member,
        channel: discord.VoiceChannel | None,
        guild: discord.Guild,
    ):
        if not self.enabled or not self.webhook_url:
            return

        embed = discord.Embed(
            title=title, color=color, timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=member.name, icon_url=member.display_avatar.url)

        embed.add_field(name="👤 Usuário", value=f"`{member.id}`", inline=False)

        if channel:
            embed.add_field(
                name="🏠 Servidor",
                value=f"**{guild.name}**\n`{guild.id}`",
                inline=True,
            )
            embed.add_field(
                name="🔊 Canal de voz",
                value=f"**{channel.name}**\n`{channel.id}`",
                inline=True,
            )

            others = [m for m in channel.members if m.id != member.id]
            member_list = (
                "\n".join(f"• `{m.id}`" for m in others) if others else "*Vazio*"
            )
            embed.add_field(
                name=f"👥 Usuários no canal ({len(others)})",
                value=member_list,
                inline=False,
            )

            link = f"https://discord.com/channels/{guild.id}/{channel.id}"
            embed.add_field(
                name="🔗 Link Do Canal",
                value=f"[Clique para acessar]({link})",
                inline=False,
            )
        else:
            embed.add_field(
                name="🏠 Servidor",
                value=f"**{guild.name}**\n`{guild.id}`",
                inline=False,
            )

        embed.set_footer(text="Madman Tracker")

        try:
            session = await self._get_webhook_session()
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(embed=embed, username="Madman Tracker")
        except Exception as e:
            emit_line(f"TRACKER:WEBHOOK_ERROR:{e}")

    async def _dispatch_server_embed(
        self,
        title: str,
        color: int,
        user: discord.User | discord.Member,
        guild_name: str,
        guild_id: int,
    ):
        if not self.enabled or not self.webhook_url:
            return

        embed = discord.Embed(
            title=title, color=color, timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=user.name, icon_url=user.display_avatar.url)

        embed.add_field(
            name="👤 Usuário",
            value=f"`{user.name} ({user.id})`",
            inline=False,
        )
        embed.add_field(
            name="🏠 Servidor",
            value=f"**{guild_name}**\n`{guild_id}`",
            inline=False,
        )

        embed.set_footer(text="Madman Tracker")

        try:
            session = await self._get_webhook_session()
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(embed=embed, username="Madman Tracker")
        except Exception as e:
            emit_line(f"TRACKER:WEBHOOK_ERROR:{e}")

    async def _dispatch_initial_state(self, member: discord.Member):
        changes = []

        status_str = str(member.status)
        status_emojis = {"online": "🟢", "idle": "🌙", "dnd": "⛔", "offline": "⚫"}
        emoji = status_emojis.get(status_str, "⚪")
        changes.append(("Situação Atual", f"{emoji} `{status_str.title()}`"))
        self.state.status = status_str

        current_acts = [self._format_activity(act) for act in member.activities]
        if current_acts:
            acts_text = "\n".join(current_acts)
            changes.append(("Atividades", acts_text))
            self.state.activities_repr = current_acts

        await self._dispatch_log(
            title="🔍 Tracker Iniciado",
            color=0xD4A5A5,
            description="O alvo está no momento:",
            changes=changes,
            user=member,
            footer=f"📍 Origem: {member.guild.name}",
        )

        vs = member.voice
        if vs and vs.channel:
            self.state.in_voice_channel_id = vs.channel.id
            self.tracked_voice_channels.add(vs.channel.id)

            await self._dispatch_voice_embed(
                title="🔊 Conectado no Canal de Voz",
                color=0x9DF9EF,
                member=member,
                channel=vs.channel,
                guild=member.guild,
            )

    async def _get_webhook_session(self):
        if self._webhook_session is None or self._webhook_session.closed:
            self._webhook_session = aiohttp.ClientSession()
        return self._webhook_session

    async def _dispatch_log(
        self,
        title: str,
        color: int,
        changes: list = None,
        description: str = None,
        user: discord.User = None,
        footer: str = None,
    ):
        if not self.enabled or not self.webhook_url:
            return

        embed = discord.Embed(
            title=title,
            color=color,
            description=description,
            timestamp=discord.utils.utcnow(),
        )

        if user:
            embed.set_author(name=user.name, icon_url=user.display_avatar.url)

        if changes:
            for name, value in changes:
                embed.add_field(name=name, value=value, inline=False)

        f_text = "Madman Tracker"
        if footer:
            f_text = f"{footer} • {f_text}"
        embed.set_footer(text=f_text)

        try:
            session = await self._get_webhook_session()
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(embed=embed, username="Madman Tracker")
        except Exception as e:
            emit_line(f"TRACKER:WEBHOOK_ERROR:{e}")

    async def _on_buffer_flush(self, channel_id: int, messages: list[dict]):
        if not self.enabled or not self.webhook_url or not messages:
            return

        ch = self.client.get_channel(channel_id)
        channel_name = getattr(ch, "name", "DM") if ch else "DM"
        guild_name = (
            getattr(ch.guild, "name", "DM")
            if ch and hasattr(ch, "guild") and ch.guild
            else "DM"
        )

        image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp")
        first_image_url = None
        lines = []

        for m in messages:
            ts = m.get("timestamp", "")
            content = m.get("content", "")
            attachments = m.get("attachments", [])
            stickers = m.get("stickers", [])

            line = f"`{ts}` {content}" if content else f"`{ts}`"

            for url in attachments:
                clean = url.split("?")[0]
                if clean.lower().endswith(image_exts) and first_image_url is None:
                    first_image_url = url
                else:
                    filename = clean.rsplit("/", 1)[-1]
                    line += f"\n> 📎 `{filename}`"

            for s in stickers:
                s_url = s.get("url")
                if s_url and first_image_url is None:
                    first_image_url = s_url
                else:
                    label = f"> 🎭 **[{s['name']}]**" + (
                        " *(Lottie)*" if not s_url else ""
                    )
                    line += f"\n{label}"

            lines.append(line)

        content_text = "\n".join(lines)
        if len(content_text) > 3500:
            content_text = content_text[:3500] + "\n\n*... [histórico truncado]*"

        first_msg = messages[0]
        embed = discord.Embed(
            title="Mensagem Recebida",
            color=0xA8E6CF,
            description=content_text,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=first_msg.get("author_name", "Unknown"),
            icon_url=first_msg.get("author_avatar", "") or None,
        )
        embed.add_field(
            name="📍 Local", value=f"`{guild_name}` ➔ `#{channel_name}`", inline=False
        )

        if first_image_url:
            embed.set_image(url=first_image_url)

        embed.set_footer(text="Madman Tracker")

        try:
            session = await self._get_webhook_session()
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(embed=embed, username="Madman Tracker")
        except Exception as e:
            emit_line(f"TRACKER:WEBHOOK_ERROR:message_batch:{e}")
