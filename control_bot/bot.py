import discord
import json
from discord.ext import commands

from config import ID_CANAL_PAINEL, ID_CANAL_SOBRE
from views.control_view import PersistentControlView
from views.tracker_view import TrackerContextView
from utils import SobreView
from datetime import datetime, timezone, timedelta


class ControlBot(commands.Bot):
    def __init__(self, manager):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.presences = True  # Necessário para ver status dos membros
        intents.members = True    # Necessário para get_member()
        
        super().__init__(command_prefix=[], intents=intents)
        self.manager = manager
        self._tracker_state = {}  # {admin_id: {target_id, channel_id}}

    async def on_message(self, message):
        pass # Ignora todas as mensagens de chat

    async def setup_hook(self):
        self.add_view(PersistentControlView(self.manager))
        self.add_view(TrackerContextView())

    async def _setup_static_channel(self, channel_id: int, view: discord.ui.View, name: str):
        """Limpa as mensagens antigas do bot e envia a nova View no canal especificado."""
        if not channel_id:
            return

        channel = self.get_channel(channel_id)
        if not channel:
            print(f"Canal {name} ({channel_id}) não encontrado.")
            return

        async for message in channel.history(limit=10):
            if message.author == self.user:
                await message.delete()

        await channel.send(view=view)
        print(f"[{name}] Painel enviado para o canal {channel_id}")

    async def on_ready(self):
        print(f'Bot de Controle {self.user} Online!')
        
        # Inicialização limpa e DRY dos canais estáticos
        await self._setup_static_channel(ID_CANAL_SOBRE, SobreView(), "SOBRE")
        await self._setup_static_channel(ID_CANAL_PAINEL, PersistentControlView(self.manager), "PAINEL")

    async def _on_tracker_event(self, raw_text: str):
        """Callback IPC: recebe TRACKER_EVENT:{json} do selfbot runner."""
        try:
            payload_str = raw_text.split(":", 1)[1] if ":" in raw_text else raw_text
            data = json.loads(payload_str)
        except (json.JSONDecodeError, IndexError):
            return

        channel = self.get_channel(int(data.get("channel_id", 0)))
        if not channel:
            return

        # Mapeamento dinâmico de eventos (Dispatcher) em vez de if/elif
        event_handlers = {
            "message_batch": self._send_message_batch,
            "message_edit": self._send_message_edit,
            "message_delete": self._send_message_delete,
            "reaction_add": self._send_reaction_add,
        }

        handler = event_handlers.get(data.get("type"))
        if handler:
            await handler(channel, data)

    def _build_base_embed(self, data: dict, title: str, color: int, description: str = None, author_source: dict = None) -> discord.Embed:
        """Constrói a base padronizada do Embed para não repetir código."""
        source = author_source or data # Batch usa o primeiro index, edit/delete usam o dict base
        
        embed = discord.Embed(title=title, color=color, description=description)
        embed.set_author(
            name=source.get('author_name', 'Usuário Desconhecido'),
            icon_url=source.get("author_avatar", "") or None,
        )
        embed.add_field(name="<:servidor:1474759582151872563> Servidor", value=f"`{data.get('guild_name', 'DM')}`", inline=True)
        embed.add_field(name="<:chat:1474760371263897763> Canal", value=f"`#{data.get('channel_name', 'DM')}`", inline=True)
        now_local = datetime.now().astimezone()
        formatted = now_local.strftime("%d/%m/%y %H:%M:%S")
        embed.set_footer(text=f"{formatted} - Madman")
        
        return embed

    async def _send_message_batch(self, channel, data: dict):
        msgs = data.get("messages", [])
        if not msgs:
            return

        image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp")
        first_image_url = None
        lines = []

        for m in msgs:
            ts = m.get("timestamp", "")
            content = m.get("content", "")
            attachments = m.get("attachments", [])
            stickers = m.get("stickers", [])
            line = f"`[{ts}]` {content}" if content else f"`[{ts}]`"

            for url in attachments:
                clean = url.split("?")[0]
                if clean.lower().endswith(image_exts) and first_image_url is None:
                    first_image_url = url
                else:
                    filename = clean.rsplit("/", 1)[-1]
                    line += f"\n📎 {filename}"

            # Sticker: PNG/APNG/GIF usam set_image; Lottie (url=None) mostra texto
            for s in stickers:
                s_url = s.get("url")
                if s_url and first_image_url is None:
                    first_image_url = s_url
                else:
                    fmt = s.get("format", "")
                    label = f"🎭 Sticker: **{s['name']}**" + (" *(Lottie — sem preview)*" if not s_url else "")
                    line += f"\n{label}"

            lines.append(line)

        content_text = "\n".join(lines)
        if len(content_text) > 4000:
            content_text = content_text[:4000] + "\n\n*... [Mensagem muito longa, truncada]*"

        embed = self._build_base_embed(
            data=data,
            title="🟢 Novas Mensagens Rastreadas",
            color=0x2ecc71,
            description=content_text,
            author_source=msgs[0]
        )

        if first_image_url:
            embed.set_image(url=first_image_url)

        sent = await channel.send(embed=embed, view=TrackerContextView())
        TrackerContextView.store_context(str(sent.id), data.get("context", []), msgs)

    async def _send_message_edit(self, channel, data: dict):
        embed = self._build_base_embed(data, title="🟠 Mensagem Editada", color=0xe67e22)
        
        embed.add_field(name="📝 Antes", value=f">>> {data.get('before', '—')}"[:1024] or "—", inline=False)
        embed.add_field(name="✏️ Depois", value=f">>> {data.get('after', '—')}"[:1024] or "—", inline=False)
        
        await channel.send(embed=embed)

    async def _send_message_delete(self, channel, data: dict):
        image_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp")
        attachments = data.get("attachments", [])
        deleted_content = data.get("content", "") or ""

        # Separa imagens de outros arquivos
        first_image_url = None
        file_names = []
        for url in attachments:
            clean = url.split("?")[0]
            if clean.lower().endswith(image_exts) and first_image_url is None:
                first_image_url = url
            else:
                file_names.append(clean.rsplit("/", 1)[-1])

        desc_parts = []
        if deleted_content:
            desc_parts.append(f">>> {deleted_content[:3900]}")
        if file_names:
            desc_parts.append("\n".join(f"📎 {f}" for f in file_names))
        if not desc_parts and not first_image_url:
            desc_parts.append("*[sem conteúdo em cache]*")

        embed = self._build_base_embed(
            data=data,
            title="🔴 Mensagem Excluída",
            color=0xe74c3c,
            description="\n".join(desc_parts) if desc_parts else None,
        )

        if first_image_url:
            embed.set_image(url=first_image_url)

        await channel.send(embed=embed)

    async def _send_reaction_add(self, channel, data: dict):
        ch_id    = data.get("channel_id_src", "0")
        msg_id   = data.get("message_id", "0")
        emoji    = data.get("emoji", "?")
        guild_id = data.get("guild_id", "@me")
        reacted_content = data.get("reacted_content", "")
        reacted_author  = data.get("reacted_author", "")

        if reacted_content:
            preview = f"> **{reacted_author}:** {reacted_content[:300]}"
            if len(reacted_content) > 300:
                preview += "..."
        else:
            preview = "*[mensagem fora do cache]*"

        # guild_id/channel_id/message_id — formato correto de jump link
        jump_url = f"https://discord.com/channels/{guild_id}/{ch_id}/{msg_id}"

        embed = self._build_base_embed(
            data=data,
            title=f"{emoji} Reação Adicionada",
            color=0x8e44ad,
            description=f"{preview}\n\n[↗️ Ir para a mensagem]({jump_url})",
        )
        embed.add_field(name="🕒 Horário", value=f"`{data.get('timestamp', '?')}`", inline=True)
        await channel.send(embed=embed)

