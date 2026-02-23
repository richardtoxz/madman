import discord
from discord import ui
import time
from datetime import datetime, timezone, timedelta

class TrackerContextView(ui.View):
    """View persistente com botão para expandir contexto de mensagens."""

    _context_store: dict[str, dict] = {}
    _TTL = 3600

    def __init__(self):
        super().__init__(timeout=None)

    @classmethod
    def store_context(cls, key: str, context_data: list[dict], messages: list[dict]):
        cls._context_store[key] = {
            "context": context_data,
            "messages": messages,
            "stored_at": time.time(),
        }
        cls._cleanup()

    @classmethod
    def _cleanup(cls):
        now = time.time()
        expired = [k for k, v in cls._context_store.items() if now - v["stored_at"] > cls._TTL]
        for k in expired:
            del cls._context_store[k]

    @ui.button(label="📜 Expandir Contexto", style=discord.ButtonStyle.secondary, custom_id="tracker_expand_ctx")
    async def expand_context(self, interaction: discord.Interaction, button: ui.Button):
        ref = interaction.message
        if not ref:
            await interaction.response.send_message("❌ Não foi possível localizar a mensagem de referência.", ephemeral=True)
            return

        data = self._context_store.get(str(ref.id))
        if not data:
            await interaction.response.send_message("⏳ O contexto expirou ou não está mais disponível na memória.", ephemeral=True)
            return

        context_msgs = data.get("context", [])
        target_msgs  = data.get("messages", [])
        target_msg_ids = {m["message_id"] for m in target_msgs}

        # Mescla contexto + alvo e deduplica usando dictionary comprehension
        all_msgs = {m["message_id"]: m for m in context_msgs + target_msgs}
        
        # Ordena cronologicamente pelo Snowflake ID
        sorted_msgs = sorted(all_msgs.values(), key=lambda m: m["message_id"])

        # Localiza os índices das mensagens alvo no timeline
        indices = [i for i, m in enumerate(sorted_msgs) if m["message_id"] in target_msg_ids]
        if not indices:
            await interaction.response.send_message("⚠️ As mensagens rastreadas não foram encontradas no histórico do contexto.", ephemeral=True)
            return

        # Define a janela de contexto (10 antes da primeira, 10 depois da última)
        first_idx = max(0, indices[0] - 10)
        last_idx  = min(len(sorted_msgs), indices[-1] + 11)
        window    = sorted_msgs[first_idx:last_idx]

        lines = []
        last_author = None

        for m in window:
            author    = m.get("author_name", "Desconhecido")
            stickers  = m.get("stickers", [])
            sticker_label = " ".join(f"[Sticker: {s['name']}]" for s in stickers) if stickers else ""
            content   = (m.get("content", "") or sticker_label or "*[Sem conteúdo em texto]*")[:300]
            ts        = m.get("timestamp", "?")
            is_target = m["message_id"] in target_msg_ids

            # Imprime o nome do autor apenas quando muda (estilo Discord nativo)
            if author != last_author:
                lines.append(f"\n👤 **{author}**")
                last_author = author

            # Formatação da mensagem (destaque visual nas mensagens alvo)
            if is_target:
                # Usa blockquote e formatação em negrito para destacar a mensagem rastreadada
                lines.append(f"> 🎯 **`[{ts}]` {content}**")
            else:
                # Mensagens normais de contexto ficam com visual padrão
                lines.append(f"> `[{ts}]` {content}")

        # Monta o texto final e evita o limite de 4096 caracteres do Embed description
        text = "\n".join(lines).strip()
        if len(text) > 4000:
            text = text[:4000] + "\n\n*... [Contexto truncado pelo limite do Discord]*"

        embed = discord.Embed(
            title="📜 Contexto da Conversa",
            description=text,
            color=0x2b2d31, # Cor neutra (fundo escuro do Discord) para focar na leitura
        )
        now_local = datetime.now().astimezone()
        formatted = now_local.strftime("%d/%m/%y %H:%M:%S")
        embed.set_footer(text=f"{formatted} - Madman")

        await interaction.response.send_message(embed=embed, ephemeral=True)

