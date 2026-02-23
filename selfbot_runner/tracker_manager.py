import os
import time
import json
import asyncio
import aiohttp
import discord
from collections import deque
from discord.ext import tasks
from datetime import datetime, timezone

from config import TrackerConfig

class TrackerState:
    def __init__(self):
        self.username = None
        self.global_name = None
        self.avatar_url = None
        self.status = None
        self.activities_repr = []
        self.bio = None
        self.connected_accounts = []
        self.in_voice_channel_id = None
        self.last_typed_at = 0.0
        self.last_presence_update = 0.0
        self.last_profile_fetch = 0.0

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
            self.config.FLUSH_DELAY, lambda cid=channel_id: asyncio.create_task(self._flush(cid))
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
    """Ring buffer de contexto por canal — guarda últimas N mensagens de QUALQUER autor."""
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
    for s in getattr(message, 'stickers', []):
        fmt = getattr(s, 'format', None)
        name = getattr(s, 'name', '?')
        sticker_id = getattr(s, 'id', None)
        if not sticker_id:
            continue
        # Lottie (.json) não pode ser embutido como imagem num embed.
        # O CDN serve um PNG de preview para qualquer sticker via /stickers/{id}.png
        # mesmo que o formato original seja Lottie ou APNG.
        fmt_name = fmt.name if fmt else "unknown"
        native_url = getattr(s, 'url', None)

        # Lottie stickers (.json) não têm versão de imagem estática no CDN do Discord.
        # Testado: 404 em .png, .gif, .webp para stickers Lottie.
        # PNG/APNG/GIF funcionam normalmente com a URL nativa da lib.
        embeddable = native_url and not native_url.endswith(".json")
        stickers.append({
            "name": name,
            "url": native_url if embeddable else None,
            "format": fmt_name,
        })

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

# --- Tracker Manager Principal ---

class TrackerManager:
    def __init__(self, client):
        self.client = client
        self.target_id = None
        self.channel_id = None
        self.webhook_url = os.getenv("TRACKER_WEBHOOK_URL")
        self.enabled = False
        self.state = TrackerState()
        self._webhook_session = None
        self.config = TrackerConfig()
        self.msg_buffer = MessageBuffer(self._on_buffer_flush)
        self.context_ring = ContextRing(maxlen=self.config.CONTEXT_MAXLEN)
        self._msg_cache: dict[int, dict] = {} 
        self._msg_cache_order: deque = deque(maxlen=self.config.MSG_CACHE_MAXLEN) 

        target_env = os.getenv("TRACKER_TARGET_ID")
        if target_env and target_env.isdigit():
            self.target_id = int(target_env)

        channel_env = os.getenv("TRACKER_CHANNEL_ID")
        if channel_env and channel_env.isdigit():
            self.channel_id = int(channel_env)

        if self.target_id and (self.channel_id or self.webhook_url):
            self.enabled = True
            print(f"TRACKER:INIT:Target={self.target_id}", flush=True)
            self._subscription_loop.start()

    def start_tracking(self, target_id: str, channel_id: str):
        if target_id.isdigit():
            self.target_id = int(target_id)
        if channel_id.isdigit():
            self.channel_id = int(channel_id)
        self.enabled = True
        self.state = TrackerState()
        self.msg_buffer.clear()
        self._msg_cache.clear()
        self._msg_cache_order.clear()
        print(f"TRACKER:STARTED:Target={self.target_id}:Channel={self.channel_id}", flush=True)
        if not self._subscription_loop.is_running():
            self._subscription_loop.start()
        asyncio.create_task(self._subscribe_to_target())


    def stop_tracking(self):
        self.enabled = False
        self.target_id = None
        self.channel_id = None
        self.msg_buffer.clear()
        self._msg_cache.clear()
        self._msg_cache_order.clear()
        if self._subscription_loop.is_running():
            self._subscription_loop.stop()
        print("TRACKER:STOPPED", flush=True)

    async def _subscribe_to_target(self):
        await asyncio.sleep(1.5)
        if not self.target_id:
            return
        count = 0
        for guild in self.client.guilds:
            try:
                await guild.subscribe(member_updates=True)
                member = guild.get_member(self.target_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(self.target_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        continue
                await guild.subscribe_to(members=[member])
                count += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"TRACKER:SUBSCRIBE_ERR:{guild.id}:{e}", flush=True)
        print(f"TRACKER:SUBSCRIBED_GUILDS:{count}", flush=True)

    # ── Webhook Helpers ──────────────────


    async def _get_webhook_session(self):
        if self._webhook_session is None or self._webhook_session.closed:
            self._webhook_session = aiohttp.ClientSession()
        return self._webhook_session

    async def _dispatch_log(self, title: str, color: int, changes: list = None, description: str = None, user: discord.User = None, footer: str = None):
        """Construtor DRY para centralizar o design dos logs via Webhook."""
        if not self.enabled or not self.webhook_url:
            return

        embed = discord.Embed(title=title, color=color, description=description, timestamp=discord.utils.utcnow())
        
        if user:
            embed.set_author(name=user.name, icon_url=user.display_avatar.url)
        
        if changes:
            for name, value in changes:
                embed.add_field(name=name, value=value, inline=False)
                
        if footer:
            embed.set_footer(text=footer)

        try:
            session = await self._get_webhook_session()
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(embed=embed, username="Tracker Automático")
        except Exception as e:
            print(f"TRACKER:WEBHOOK_ERROR:{e}", flush=True)

    # ── IPC ───────────────────

    def _emit_ipc(self, event_type: str, data: dict):
        payload = {"type": event_type, "target_id": self.target_id, "channel_id": self.channel_id, **data}
        print(f"TRACKER_EVENT:{json.dumps(payload, ensure_ascii=False)}", flush=True)

    async def _on_buffer_flush(self, channel_id: int, messages: list[dict]):
        context = self.context_ring.snapshot(channel_id)
        guild_name, channel_name = "DM", "DM"

        ch = self.client.get_channel(channel_id)
        if ch:
            channel_name = getattr(ch, 'name', 'DM')
            if hasattr(ch, 'guild') and ch.guild:
                guild_name = ch.guild.name

        guild_id = str(ch.guild.id) if ch and hasattr(ch, 'guild') and ch.guild else "@me"
        first_ts = messages[0]["timestamp"] if messages else ""
        last_ts = messages[-1]["timestamp"] if messages else ""

        self._emit_ipc("message_batch", {
            "guild_name": guild_name,
            "guild_id": guild_id,
            "channel_name": channel_name,
            "channel_id_src": channel_id,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "messages": messages,
            "context": context,
        })

    # ── Subscription Loop ────────────────────────────────────

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

    # ── Hybrid Profile Fetch ─────────────────────────────────

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
            if getattr(profile, 'bio', None) != self.state.bio:
                changes.append(("Bio Editada", f"**Antes:**\n>>> {self.state.bio or '*Vazio*'}\n\n**Depois:**\n>>> {profile.bio or '*Vazio*'}"))
                self.state.bio = getattr(profile, 'bio', None)
                
            connections = getattr(profile, 'connected_accounts', [])
            conn_repr = [f"`{c.type}: {c.name}`" for c in connections]
            if conn_repr != self.state.connected_accounts:
                changes.append(("Contas Conectadas", "\n".join(conn_repr) or "*Nenhuma*"))
                self.state.connected_accounts = conn_repr
                
            if changes:
                await self._dispatch_log(
                    title="Configurações de Conta Alteradas",
                    color=0x9b59b6, # Roxo
                    changes=changes,
                    footer=f"Target ID: {self.target_id}"
                )
        except Exception as e:
            print(f"TRACKER:PROFILE_FETCH_ERROR:{e}", flush=True)

    # ==============================================================
    # Event Handlers Otimizados
    # ==============================================================

    async def on_user_update(self, before: discord.User, after: discord.User):
        if not self.enabled or after.id != self.target_id:
            return
            
        changes = []
        if before.name != after.name:
            changes.append(("Username", f"`{before.name}` ➔ `{after.name}`"))
            self.state.username = after.name
        if before.global_name != after.global_name:
            changes.append(("Nome de Exibição", f"`{before.global_name}` ➔ `{after.global_name}`"))
            self.state.global_name = after.global_name
        if before.avatar != after.avatar:
            changes.append(("Foto de Perfil", f"[Abrir Novo Avatar]({after.display_avatar.url})"))
            self.state.avatar_url = after.display_avatar.url
            
        if changes:
            await self._dispatch_log(
                title="Perfil Atualizado",
                color=0xf1c40f,
                changes=changes,
                user=after
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
            changes.append(("Status", f"`{self.state.status}` ➔ `{status_str}`"))
            self.state.status = status_str
            asyncio.create_task(self._trigger_hybrid_fetch())
            
        current_acts = []
        for act in after.activities:
            if isinstance(act, discord.CustomActivity):
                current_acts.append(f"💭 {act.name}")
            elif isinstance(act, discord.Spotify):
                current_acts.append(f"🎵 Ouvindo **{act.title}** de {act.artist}")
            else:
                current_acts.append(f"🎮 Jogando **{act.name}**")
                
        if current_acts != self.state.activities_repr:
            new_acts = "\n".join(current_acts) or "*Nenhuma atividade*"
            changes.append(("Atividade Atual", new_acts))
            self.state.activities_repr = current_acts
            
        if changes:
            await self._dispatch_log(
                title="Atividade Atualizada",
                color=0x3498db,
                changes=changes,
                user=after,
                footer=f"Detectado no servidor: {after.guild.name}"
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
            color=0x2ecc71,
            footer=f"Guild ID: {member.guild.id}"
        )

    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        if not self.enabled or payload.user.id != self.target_id:
            return
            
        guild = self.client.get_guild(payload.guild_id)
        g_name = guild.name if guild else "Desconhecido"
        
        await self._dispatch_log(
            title="Saiu de um Servidor",
            description=f"Deixou o **{g_name}**",
            color=0xe74c3c,
            footer=f"Guild ID: {payload.guild_id}"
        )

    async def on_typing(self, channel, user, when):
        if not self.enabled or user.id != self.target_id:
            return
        now = time.time()
        if now - self.state.last_typed_at < self.config.TYPING_THROTTLE:
            return
        self.state.last_typed_at = now
        
        c_name = getattr(channel, 'name', 'DM')
        g_name = getattr(getattr(channel, 'guild', None), 'name', 'N/A')
        
        await self._dispatch_log(
            title="Digitando...",
            description=f"Visto digitando em **#{c_name}**",
            color=0x95a5a6,
            footer=f"Servidor: {g_name} | Channel ID: {channel.id}"
        )

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not self.enabled or member.id != self.target_id:
            return
            
        changes = []
        
        if before.channel != after.channel:
            if not before.channel:
                changes.append(("Conexão", f"Entrou em **{after.channel.name}**"))
            elif not after.channel:
                changes.append(("Conexão", f"Saiu de **{before.channel.name}**"))
            else:
                changes.append(("Conexão", f"Moveu-se para **{after.channel.name}**"))
                
            if after.channel:
                others = [m.name for m in after.channel.members if m.id != self.target_id]
                self.state.in_voice_channel_id = after.channel.id
                changes.append(("Companhia na Call", ", ".join(others) or "*Sozinho*"))
            else:
                self.state.in_voice_channel_id = None
                
        if before.self_mute != after.self_mute:
            changes.append(("Microfone", "Mutado 🔇" if after.self_mute else "Desmutado 🎙️"))
        if before.self_deaf != after.self_deaf:
            changes.append(("Fones", "Surdo 🎧" if after.self_deaf else "Ouvindo 🔊"))
        if before.self_stream != after.self_stream:
            changes.append(("Transmissão", "Começou uma stream 📺" if after.self_stream else "Encerrou a stream 🛑"))

        if changes:
            await self._dispatch_log(
                title="Atualização de Voz",
                color=0x1abc9c,
                changes=changes,
                user=member,
                footer=f"Servidor: {member.guild.name}"
            )

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not self.enabled or payload.user_id != self.target_id:
            return

        ch = self.client.get_channel(payload.channel_id)
        channel_name = getattr(ch, 'name', 'DM') if ch else 'DM'
        guild_name = getattr(ch.guild, 'name', 'DM') if ch and hasattr(ch, 'guild') and ch.guild else 'DM'
        guild_id = str(ch.guild.id) if ch and hasattr(ch, 'guild') and ch.guild else "@me"

        member = None
        if ch and hasattr(ch, 'guild') and ch.guild:
            member = ch.guild.get_member(self.target_id)
        author_avatar = str(member.display_avatar.url) if member else ""
        author_name = member.name if member else str(self.target_id)

        # Busca conteúdo da mensagem reagida no cache
        reacted_data = self._msg_cache.get(payload.message_id)
        reacted_content = reacted_data.get("content", "") if reacted_data else ""
        reacted_author = reacted_data.get("author_name", "") if reacted_data else ""

        emoji_str = str(payload.emoji)
        self._emit_ipc("reaction_add", {
            "guild_name": guild_name,
            "guild_id": guild_id,
            "channel_name": channel_name,
            "channel_id_src": payload.channel_id,
            "message_id": payload.message_id,
            "emoji": emoji_str,
            "reacted_content": reacted_content,
            "reacted_author": reacted_author,
            "timestamp": _ts_fmt(),
            "author_name": author_name,
            "author_avatar": author_avatar,
        })

        if self.webhook_url:
            await self._dispatch_log(
                title="Reação Adicionada",
                description=f"Reagiu com **{emoji_str}** em uma mensagem.",
                color=0x8e44ad,
                footer=f"#{channel_name} | Servidor: {guild_name}"
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
        if 'author' not in data or int(data['author'].get('id', 0)) != self.target_id:
            return
        ch = self.client.get_channel(payload.channel_id)
        channel_name = getattr(ch, 'name', 'DM') if ch else 'DM'
        guild_name = getattr(ch.guild, 'name', 'DM') if ch and hasattr(ch, 'guild') and ch.guild else 'DM'

        if payload.cached_message:
            before_content = payload.cached_message.content or ""
            author_avatar = str(payload.cached_message.author.display_avatar.url)
        else:
            cached_data = self._msg_cache.get(payload.message_id)
            before_content = cached_data.get("content", "") if cached_data else ""
            author_avatar = cached_data.get("author_avatar", "") if cached_data else ""

        if payload.message_id in self._msg_cache:
            self._msg_cache[payload.message_id]["content"] = data.get('content', '')

        self._emit_ipc("message_edit", {
            "guild_name": guild_name,
            "channel_name": channel_name,
            "channel_id_src": payload.channel_id,
            "message_id": payload.message_id,
            "before": before_content,
            "after": data.get('content', ''),
            "timestamp": _ts_fmt(),
            "author_name": data['author'].get('username', '?'),
            "author_avatar": author_avatar,
        })

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if not self.enabled:
            return
        cached = payload.cached_message
        if cached and cached.author.id != self.target_id:
            return
        if not cached:
            cached_data = self._msg_cache.pop(payload.message_id, None)
            if not cached_data or cached_data.get("author_id") != self.target_id:
                return
            ch = self.client.get_channel(payload.channel_id)
            channel_name, guild_name = getattr(ch, 'name', 'DM') if ch else 'DM', getattr(ch.guild, 'name', 'DM') if ch and hasattr(ch, 'guild') and ch.guild else 'DM'
            self._emit_ipc("message_delete", {
                "guild_name": guild_name, "channel_name": channel_name, "channel_id_src": payload.channel_id,
                "message_id": payload.message_id, "content": cached_data.get("content", ""),
                "attachments": cached_data.get("attachments", []), "timestamp": _ts_fmt(),
                "author_name": cached_data.get("author_name", "?"), "author_avatar": cached_data.get("author_avatar", ""),
            })
            return
        ch = self.client.get_channel(payload.channel_id)
        channel_name, guild_name = getattr(ch, 'name', 'DM') if ch else 'DM', getattr(ch.guild, 'name', 'DM') if ch and hasattr(ch, 'guild') and ch.guild else 'DM'
        self._msg_cache.pop(payload.message_id, None)
        self._emit_ipc("message_delete", {
            "guild_name": guild_name, "channel_name": channel_name, "channel_id_src": payload.channel_id,
            "message_id": payload.message_id, "content": cached.content or "", "attachments": [a.url for a in cached.attachments],
            "timestamp": _ts_fmt(), "author_name": cached.author.name, "author_avatar": str(cached.author.display_avatar.url),
        })