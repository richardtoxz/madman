import os
import time
import asyncio
import aiohttp
import discord
from collections import deque
from dataclasses import dataclass, field
from discord.ext import tasks
from datetime import datetime, timezone
from config import TrackerConfig


@dataclass
class TrackerState:
    username: str | None = None
    global_name: str | None = None
    avatar_url: str | None = None
    status: str | None = None
    activities_repr: list = field(default_factory=list)
    bio: str | None = None
    connected_accounts: list = field(default_factory=list)
    in_voice_channel_id: int | None = None
    last_typed_at: float = 0.0
    last_presence_update: float = 0.0
    last_profile_fetch: float = 0.0


class MessageBuffer:
    def __init__(self, flush_callback):
        self._buffers: dict[int, list[dict]] = {}
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._flush_callback = flush_callback
        self.config = TrackerConfig()

    def add(self, channel_id: int, msg_data: dict):
        if channel_id not in self._buffers:
            self._buffers[channel_id] = []

        self._buffers[channel_id].append(msg_data)

        if channel_id in self._timers:
            self._timers[channel_id].cancel()

        loop = asyncio.get_running_loop()
        self._timers[channel_id] = loop.call_later(
            self.config.FLUSH_DELAY,
            lambda cid=channel_id: asyncio.create_task(self._flush(cid)),
        )

    async def _flush(self, channel_id: int):
        messages = self._buffers.pop(channel_id, [])
        self._timers.pop(channel_id, None)
        if messages:
            await self._flush_callback(channel_id, messages)

    def clear(self):
        for handle in self._timers.values():
            handle.cancel()
        self._buffers.clear()
        self._timers.clear()


class ContextRing:
    def __init__(self, maxlen=40):
        self._rings: dict[int, deque] = {}
        self._maxlen = maxlen

    def push(self, channel_id: int, msg_data: dict):
        if channel_id not in self._rings:
            self._rings[channel_id] = deque(maxlen=self._maxlen)
        self._rings[channel_id].append(msg_data)

    def snapshot(self, channel_id: int) -> list[dict]:
        return list(self._rings.get(channel_id, []))


def _ts_fmt(dt=None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%d/%m/%Y - %H:%M:%S")


def _msg_to_dict(message: discord.Message) -> dict:
    stickers = []
    for s in getattr(message, "stickers", []):
        fmt = getattr(s, "format", None)
        name = getattr(s, "name", "?")
        sticker_id = getattr(s, "id", None)
        if not sticker_id:
            continue

        fmt_name = fmt.name if fmt else "unknown"
        native_url = getattr(s, "url", None)

        embeddable = native_url and not native_url.endswith(".json")
        stickers.append(
            {
                "name": name,
                "url": native_url if embeddable else None,
                "format": fmt_name,
            }
        )

    return {
        "author_id": message.author.id,
        "author_name": message.author.name,
        "author_avatar": str(message.author.display_avatar.url),
        "content": message.content or "",
        "timestamp": _ts_fmt(message.created_at),
        "message_id": message.id,
        "attachments": [a.url for a in message.attachments],
        "stickers": stickers,
    }


class TrackerManager:
    def __init__(self, client):
        self.client = client
        self.target_id = None
        self.webhook_url = None
        self.enabled = False
        self.state = TrackerState()
        self._webhook_session = None
        self.config = TrackerConfig()
        self.msg_buffer = MessageBuffer(self._on_buffer_flush)
        self.context_ring = ContextRing(maxlen=self.config.CONTEXT_MAXLEN)
        self._msg_cache: dict[int, dict] = {}
        self._msg_cache_order: deque = deque(maxlen=self.config.MSG_CACHE_MAXLEN)
        self.tracked_voice_channels: set[int] = set()

        target_env = os.getenv("TRACKER_TARGET_ID")
        webhook_env = os.getenv("TRACKER_WEBHOOK_URL")
        if target_env and target_env.isdigit() and webhook_env:
            self.target_id = int(target_env)
            self.webhook_url = webhook_env
            self.enabled = True
            print(f"TRACKER:INIT:Target={self.target_id}", flush=True)
            self._subscription_loop.start()

    def start_tracking(self, target_id: str, webhook_url: str):
        if target_id.isdigit():
            self.target_id = int(target_id)
        self.webhook_url = webhook_url
        self.enabled = True
        self.state = TrackerState()
        self.msg_buffer.clear()
        self._msg_cache.clear()
        self._msg_cache_order.clear()
        self.tracked_voice_channels.clear()
        print(
            f"TRACKER:STARTED:Target={self.target_id}:Webhook={self.webhook_url[:40]}...",
            flush=True,
        )
        if not self._subscription_loop.is_running():
            self._subscription_loop.start()
        asyncio.create_task(self._subscribe_to_target())

    def stop_tracking(self):
        self.enabled = False
        self.target_id = None
        self.webhook_url = None
        self.msg_buffer.clear()
        self._msg_cache.clear()
        self._msg_cache_order.clear()
        self.tracked_voice_channels.clear()
        if self._subscription_loop.is_running():
            self._subscription_loop.stop()
        print("TRACKER:STOPPED", flush=True)

    async def _subscribe_to_target(self):
        await asyncio.sleep(1.5)
        if not self.target_id:
            return

        count = 0
        found_member = None

        for guild in self.client.guilds:
            try:
                await guild.subscribe(member_updates=True)
                member = guild.get_member(self.target_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(self.target_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        continue

                if member:
                    found_member = member
                    await guild.subscribe_to(members=[member])
                    count += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"TRACKER:SUBSCRIBE_ERR:{guild.id}:{e}", flush=True)

        print(f"TRACKER:SUBSCRIBED_GUILDS:{count}", flush=True)
        if found_member:
            await self._dispatch_initial_state(found_member)

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
                elif a_type == discord.ActivityType.streaming:
                    return f"> 🎥 Transmitindo **{act.name}**"
                elif a_type == discord.ActivityType.listening:
                    return f"> 🎧 Ouvindo **{act.name}**"
                elif a_type == discord.ActivityType.watching:
                    return f"> 📺 Assistindo **{act.name}**"
                elif a_type == discord.ActivityType.competing:
                    return f"> 🏆 Competindo em **{act.name}**"
                else:
                    return f"> 🕹️ Atividade: **{act.name}**"

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

        vs = member.voice
        if vs and vs.channel:
            changes.append(("🎙️ Conexão de Voz", f"Atualmente em `{vs.channel.name}`"))
            self.state.in_voice_channel_id = vs.channel.id
            self.tracked_voice_channels.add(vs.channel.id)

            others = [m.name for m in vs.channel.members if m.id != self.target_id]
            changes.append(
                (
                    "👥 Companhia Atual",
                    ", ".join(f"`{x}`" for x in others) or "*Sozinho*",
                )
            )

            voice_details = []
            if vs.self_mute:
                voice_details.append("Mutado 🎙️")
            if vs.self_deaf:
                voice_details.append("Silenciado 🔇")
            if vs.self_stream:
                voice_details.append("Abriu stream 🎥")
            if vs.self_video:
                voice_details.append("Ligou a camera 📷")

            if voice_details:
                changes.append(("⚙️ Conf. de Voz", " | ".join(voice_details)))

        await self._dispatch_log(
            title="🔍 Tracker Iniciado",
            color=0xD4A5A5,
            description="O alvo está no momento:",
            changes=changes,
            user=member,
            footer=f"📍 Origem: {member.guild.name}",
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
            print(f"TRACKER:WEBHOOK_ERROR:{e}", flush=True)

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
                    line += f"\n> � `{filename}`"

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
            print(f"TRACKER:WEBHOOK_ERROR:message_batch:{e}", flush=True)

    @tasks.loop(minutes=TrackerConfig.SUBSCRIPTION_INTERVAL)
    async def _subscription_loop(self):
        if not self.enabled or not self.client.is_ready():
            return
        count = 0
        for guild in self.client.guilds:
            member = guild.get_member(self.target_id)
            if member:
                try:
                    await guild.subscribe_to(members=[member])
                    count += 1
                except Exception:
                    pass
        print(f"TRACKER:SUBSCRIBED:Count={count}", flush=True)

    @_subscription_loop.before_loop
    async def _before_subscription(self):
        await self.client.wait_until_ready()

    async def _trigger_hybrid_fetch(self):
        now = time.time()
        if now - self.state.last_profile_fetch < self.config.PROFILE_FETCH_THROTTLE:
            return
        self.state.last_profile_fetch = now
        delay = float(int(time.time()) % 120 + 60)
        await asyncio.sleep(delay)

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
            print(f"TRACKER:PROFILE_FETCH_ERROR:{e}", flush=True)

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
                footer=f"📍 Servidor: {after.guild.name}",
            )

    async def on_member_join(self, member: discord.Member):
        if not self.enabled or member.id != self.target_id:
            return
        try:
            await member.guild.subscribe_to(member)
        except Exception:
            pass

        await self._dispatch_log(
            title="Entrou em um Servidor",
            description=f"Juntou-se ao **{member.guild.name}**",
            color=0x2ECC71,
            footer=f"Guild ID: {member.guild.id}",
        )

    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        if not self.enabled or payload.user.id != self.target_id:
            return

        guild = self.client.get_guild(payload.guild_id)
        g_name = guild.name if guild else "Desconhecido"

        await self._dispatch_log(
            title="Saiu de um Servidor",
            description=f"Deixou o **{g_name}**",
            color=0xE74C3C,
            footer=f"Guild ID: {payload.guild_id}",
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
            title="⌨️ Digitou...",
            description=f"Foi visto digitando em `#{c_name}`",
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
                await self._dispatch_log(
                    title="👥 Nova Companhia na Call",
                    color=0x2ECC71,
                    description=f"Novo membro entrou onde o Alvo está: <@{member.id}> (`{member.id}`)",
                    user=member,
                    footer=f"📍 Servidor: {member.guild.name} | Call: {after.channel.name}",
                )
            elif before.channel and before.channel.id in self.tracked_voice_channels:
                await self._dispatch_log(
                    title="🚶 Saída de Companhia da Call",
                    color=0xE74C3C,
                    description=f"O membro <@{member.id}> (`{member.id}`) desconectou da chamada do Alvo.",
                    user=member,
                    footer=f"📍 Servidor: {member.guild.name} | Call: {before.channel.name}",
                )
            return

        changes = []

        if before.channel != after.channel:
            if not before.channel:
                changes.append(
                    ("📞 Conexão", f"Entrou na call `🔈 {after.channel.name}`")
                )
                self.tracked_voice_channels.add(after.channel.id)
            elif not after.channel:
                changes.append(
                    ("📞 Conexão", f"Saiu da call `🔈 {before.channel.name}`")
                )
                self.tracked_voice_channels.discard(before.channel.id)
            else:
                changes.append(
                    ("📞 Conexão", f"Moveu-se para `🔈 {after.channel.name}`")
                )
                self.tracked_voice_channels.discard(before.channel.id)
                self.tracked_voice_channels.add(after.channel.id)

            if after.channel:
                others = [
                    m.name for m in after.channel.members if m.id != self.target_id
                ]
                self.state.in_voice_channel_id = after.channel.id
                changes.append(
                    (
                        "👥 Companhia Atual",
                        ", ".join(f"`{x}`" for x in others) or "*Sozinho*",
                    )
                )
            else:
                self.state.in_voice_channel_id = None

        if before.self_mute != after.self_mute:
            changes.append(
                ("🎤 Microfone", "Mutou 🤐" if after.self_mute else "Desmutou 🎙️")
            )
        if before.self_deaf != after.self_deaf:
            changes.append(
                (
                    "🎧 Fone",
                    "Silenciou-se 🔇" if after.self_deaf else "Voltou a ouvir 🔊",
                )
            )
        if before.self_stream != after.self_stream:
            changes.append(
                (
                    "📺 Transmissão",
                    "Começou uma stream 🎥"
                    if after.self_stream
                    else "Encerrou a stream 🛑",
                )
            )
        if before.self_video != after.self_video:
            changes.append(
                (
                    "📷 Câmera",
                    "Ligou a webcam 🟢" if after.self_video else "Desligou a webcam 🔴",
                )
            )

        if changes:
            await self._dispatch_log(
                title="🎙️ Atualização de Voz",
                color=0x9DF9EF,
                changes=changes,
                user=member,
                footer=f"📍 Servidor: {member.guild.name}",
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
        self.context_ring.push(message.channel.id, _msg_to_dict(message))
        if message.author.id != self.target_id:
            return
        msg_data = _msg_to_dict(message)
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
