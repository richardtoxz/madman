import asyncio
import time

import discord

from .ipc import emit_line
from .tracker_support import msg_to_dict


class TrackerEventMixin:
    async def _trigger_hybrid_fetch(self):
        run_id = self._run_id
        target_id = self.target_id
        now = time.time()
        if now - self.state.last_profile_fetch < self.config.PROFILE_FETCH_THROTTLE:
            return
        self.state.last_profile_fetch = now
        delay = float(int(time.time()) % 120 + 60)
        await asyncio.sleep(delay)

        if (
            not self.enabled
            or run_id != self._run_id
            or target_id is None
            or target_id != self.target_id
        ):
            return

        try:
            profile = await self.client.fetch_user_profile(self.target_id)
            if not profile:
                return

            changes = []
            if getattr(profile, "bio", None) != self.state.bio:
                changes.append(
                    (
                        "Bio Editada",
                        f"**Antes:**\n>>> {self.state.bio or '*Vazio*'}\n\n**Depois:**\n>>> {profile.bio or '*Vazio*'}",
                    )
                )
                self.state.bio = getattr(profile, "bio", None)

            connections = getattr(profile, "connected_accounts", [])
            conn_repr = [f"`{c.type}: {c.name}`" for c in connections]
            if conn_repr != self.state.connected_accounts:
                changes.append(
                    ("Contas Conectadas", "\n".join(conn_repr) or "*Nenhuma*")
                )
                self.state.connected_accounts = conn_repr

            if changes:
                await self._dispatch_log(
                    title="Configurações de Conta Alteradas",
                    color=0x9B59B6,
                    changes=changes,
                )
        except Exception as e:
            emit_line(f"TRACKER:PROFILE_FETCH_ERROR:{e}")

    async def on_user_update(self, before: discord.User, after: discord.User):
        if not self.enabled or after.id != self.target_id:
            return

        changes = []
        if before.name != after.name:
            changes.append(("🏷️ Username", f"`{before.name}` ➔ `{after.name}`"))
            self.state.username = after.name
        if before.global_name != after.global_name:
            changes.append(
                (
                    "✨ Nome de Exibição",
                    f"`{before.global_name}` ➔ `{after.global_name}`",
                )
            )
            self.state.global_name = after.global_name
        if before.avatar != after.avatar:
            changes.append(
                (
                    "📸 Foto de Perfil",
                    f"[Abrir Novo Avatar]({after.display_avatar.url})",
                )
            )
            self.state.avatar_url = after.display_avatar.url

        if changes:
            await self._dispatch_log(
                title="🎨 Perfil Atualizado",
                color=0xFAC898,
                changes=changes,
                user=after,
            )
            asyncio.create_task(self._trigger_hybrid_fetch())

    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if not self.enabled or after.id != self.target_id:
            return

        now = time.time()
        if now - self.state.last_presence_update < self.config.PRESENCE_THROTTLE:
            return
        self.state.last_presence_update = now

        changes = []
        status_str = str(after.status)
        if status_str != self.state.status:
            status_emojis = {"online": "🟢", "idle": "🌙", "dnd": "⛔", "offline": "⚫"}
            emoji = status_emojis.get(status_str, "⚪")
            changes.append(("Status", f"{emoji} `{status_str.title()}`"))
            self.state.status = status_str
            asyncio.create_task(self._trigger_hybrid_fetch())

        current_acts = [self._format_activity(act) for act in after.activities]
        if current_acts != self.state.activities_repr:
            new_acts = (
                "\n".join(current_acts)
                if current_acts
                else "*Nenhuma atividade no momento*"
            )
            changes.append(("🕹️ Atividades", new_acts))
            self.state.activities_repr = current_acts

        if changes:
            await self._dispatch_log(
                title="👀 Presença Atualizada",
                color=0xA2B9EE,
                changes=changes,
                user=after,
            )

    async def on_member_join(self, member: discord.Member):
        if not self.enabled or member.id != self.target_id:
            return
        try:
            await member.guild.subscribe_to(member)
        except Exception:
            pass

        await self._dispatch_server_embed(
            title="📥 Entrou no Servidor",
            color=0x2ECC71,
            user=member,
            guild_name=member.guild.name,
            guild_id=member.guild.id,
        )

    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        if not self.enabled or payload.user.id != self.target_id:
            return

        guild = self.client.get_guild(payload.guild_id)
        g_name = guild.name if guild else "Desconhecido"

        await self._dispatch_server_embed(
            title="📤 Saiu do Servidor",
            color=0xE74C3C,
            user=payload.user,
            guild_name=g_name,
            guild_id=payload.guild_id,
        )

    async def on_typing(self, channel, user, when):
        if not self.enabled or user.id != self.target_id:
            return
        now = time.time()
        if now - self.state.last_typed_at < self.config.TYPING_THROTTLE:
            return
        self.state.last_typed_at = now

        c_name = getattr(channel, "name", "DM")
        g_name = getattr(getattr(channel, "guild", None), "name", "N/A")

        await self._dispatch_log(
            description=f"⌨️ Foi visto digitando em `#{c_name}`",
            color=0xDCDCDC,
            footer=f"📍 Servidor: {g_name}",
        )

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if not self.enabled:
            return

        if member.id != self.target_id:
            if before.channel == after.channel:
                return

            if after.channel and after.channel.id in self.tracked_voice_channels:
                await self._dispatch_voice_embed(
                    title="👥 Entrou na Call do Alvo",
                    color=0x2ECC71,
                    member=member,
                    channel=after.channel,
                    guild=member.guild,
                )
            elif before.channel and before.channel.id in self.tracked_voice_channels:
                await self._dispatch_voice_embed(
                    title="🚶 Saiu da Call do Alvo",
                    color=0xE74C3C,
                    member=member,
                    channel=before.channel,
                    guild=member.guild,
                )
            return

        if before.channel != after.channel:
            if not before.channel:
                self.tracked_voice_channels.add(after.channel.id)
                self.state.in_voice_channel_id = after.channel.id
                await self._dispatch_voice_embed(
                    title="🔊 Entrou no Canal de Voz",
                    color=0x2ECC71,
                    member=member,
                    channel=after.channel,
                    guild=member.guild,
                )
            elif not after.channel:
                self.tracked_voice_channels.discard(before.channel.id)
                self.state.in_voice_channel_id = None
                await self._dispatch_voice_embed(
                    title="🔇 Saiu do Canal de Voz",
                    color=0xE74C3C,
                    member=member,
                    channel=before.channel,
                    guild=member.guild,
                )
            else:
                self.tracked_voice_channels.discard(before.channel.id)
                self.tracked_voice_channels.add(after.channel.id)
                self.state.in_voice_channel_id = after.channel.id
                await self._dispatch_voice_embed(
                    title="🔀 Mudou de Canal de Voz",
                    color=0xF39C12,
                    member=member,
                    channel=after.channel,
                    guild=member.guild,
                )

        voice_channel = after.channel or before.channel
        guild = member.guild

        if before.self_mute != after.self_mute:
            title = (
                "🤐 Mutou o Microfone" if after.self_mute else "🎙️ Desmutou o Microfone"
            )
            await self._dispatch_voice_embed(
                title=title,
                color=0x9DF9EF,
                member=member,
                channel=voice_channel,
                guild=guild,
            )

        if before.self_deaf != after.self_deaf:
            title = "🔇 Silenciou o Áudio" if after.self_deaf else "🔊 Ativou o Áudio"
            await self._dispatch_voice_embed(
                title=title,
                color=0x9DF9EF,
                member=member,
                channel=voice_channel,
                guild=guild,
            )

        if before.self_stream != after.self_stream:
            title = (
                "🎥 Iniciou uma Stream" if after.self_stream else "🛑 Encerrou a Stream"
            )
            await self._dispatch_voice_embed(
                title=title,
                color=0x9B59B6,
                member=member,
                channel=voice_channel,
                guild=guild,
            )

        if before.self_video != after.self_video:
            title = "📷 Ligou a Câmera" if after.self_video else "📷 Desligou a Câmera"
            await self._dispatch_voice_embed(
                title=title,
                color=0x9DF9EF,
                member=member,
                channel=voice_channel,
                guild=guild,
            )

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not self.enabled or payload.user_id != self.target_id:
            return

        ch = self.client.get_channel(payload.channel_id)
        channel_name = getattr(ch, "name", "DM") if ch else "DM"
        guild_name = (
            getattr(ch.guild, "name", "DM")
            if ch and hasattr(ch, "guild") and ch.guild
            else "DM"
        )
        guild_id = (
            str(ch.guild.id) if ch and hasattr(ch, "guild") and ch.guild else "@me"
        )

        reacted_data = self._msg_cache.get(payload.message_id)
        reacted_content = reacted_data.get("content", "") if reacted_data else ""
        reacted_author = reacted_data.get("author_name", "") if reacted_data else ""

        emoji_str = str(payload.emoji)
        jump_url = f"https://discord.com/channels/{guild_id}/{payload.channel_id}/{payload.message_id}"

        preview = (
            f"> **{reacted_author}:** {reacted_content[:300]}"
            if reacted_content
            else "*[mensagem fora do cache]*"
        )

        await self._dispatch_log(
            title=f"{emoji_str} Reação Adicionada",
            description=f"{preview}\n\n[↗️ Ir para a mensagem]({jump_url})",
            color=0x8E44AD,
            footer=f"#{channel_name} | Servidor: {guild_name}",
        )

    async def on_message(self, message: discord.Message):
        if not self.enabled:
            return
        self.context_ring.push(message.channel.id, msg_to_dict(message))
        if message.author.id != self.target_id:
            return
        msg_data = msg_to_dict(message)
        self.msg_buffer.add(message.channel.id, msg_data)

        self._msg_cache[message.id] = msg_data
        self._msg_cache_order.append(message.id)
        if len(self._msg_cache) > self._msg_cache_order.maxlen:
            self._msg_cache.pop(next(iter(self._msg_cache)), None)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if not self.enabled:
            return
        data = payload.data
        if "author" not in data or int(data["author"].get("id", 0)) != self.target_id:
            return
        ch = self.client.get_channel(payload.channel_id)
        channel_name = getattr(ch, "name", "DM") if ch else "DM"
        guild_name = (
            getattr(ch.guild, "name", "DM")
            if ch and hasattr(ch, "guild") and ch.guild
            else "DM"
        )

        if payload.cached_message:
            before_content = payload.cached_message.content or ""
        else:
            cached_data = self._msg_cache.get(payload.message_id)
            before_content = cached_data.get("content", "") if cached_data else ""

        after_content = data.get("content", "")
        if payload.message_id in self._msg_cache:
            self._msg_cache[payload.message_id]["content"] = after_content

        await self._dispatch_log(
            title="Mensagem Editada",
            color=0xFDFD96,
            changes=[
                ("Antes", f">>> {before_content[:1000]}" if before_content else "—"),
                ("Depois", f">>> {after_content[:1000]}" if after_content else "—"),
            ],
            footer=f"📍 {guild_name} ➔ #{channel_name}",
        )

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if not self.enabled:
            return
        cached = payload.cached_message
        if cached and cached.author.id != self.target_id:
            return

        content = ""
        attachments = []

        if not cached:
            cached_data = self._msg_cache.pop(payload.message_id, None)
            if not cached_data or cached_data.get("author_id") != self.target_id:
                return
            content = cached_data.get("content", "")
            attachments = cached_data.get("attachments", [])
        else:
            content = cached.content or ""
            attachments = [a.url for a in cached.attachments]
            self._msg_cache.pop(payload.message_id, None)

        ch = self.client.get_channel(payload.channel_id)
        channel_name = getattr(ch, "name", "DM") if ch else "DM"
        guild_name = (
            getattr(ch.guild, "name", "DM")
            if ch and hasattr(ch, "guild") and ch.guild
            else "DM"
        )

        desc_parts = []
        if content:
            desc_parts.append(f">>> {content[:3900]}")
        if attachments:
            for url in attachments:
                desc_parts.append(f"📎 {url.split('?')[0].rsplit('/', 1)[-1]}")
        if not desc_parts:
            desc_parts.append("*[sem conteúdo em cache]*")

        await self._dispatch_log(
            title="Mensagem Excluída",
            color=0xFFB7B2,
            description="\n".join(desc_parts),
            footer=f"📍 {guild_name} ➔ #{channel_name}",
        )
