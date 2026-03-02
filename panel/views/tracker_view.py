import logging
import time
from datetime import datetime
import discord
from discord import ui

log = logging.getLogger(__name__)

_CONTEXT_WINDOW_PADDING = 10
_CONTENT_MAX_LENGTH = 300
_EMBED_DESC_MAX_LENGTH = 4000


class TrackerContextView(ui.View):
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
        expired = [
            k for k, v in cls._context_store.items() if now - v["stored_at"] > cls._TTL
        ]
        for k in expired:
            del cls._context_store[k]

    @staticmethod
    def _build_window(
        context_msgs: list[dict], target_msgs: list[dict]
    ) -> list[dict] | None:
        target_ids = {m.get("message_id") for m in target_msgs}
        merged = {m.get("message_id"): m for m in context_msgs + target_msgs}
        ordered = sorted(merged.values(), key=lambda m: m.get("message_id", 0))

        indices = [
            i for i, m in enumerate(ordered) if m.get("message_id") in target_ids
        ]
        if not indices:
            return None

        lo = max(0, indices[0] - _CONTEXT_WINDOW_PADDING)
        hi = min(len(ordered), indices[-1] + _CONTEXT_WINDOW_PADDING + 1)
        return ordered[lo:hi]

    @staticmethod
    def _format_lines(window: list[dict], target_ids: set) -> str:
        lines: list[str] = []
        last_author: str | None = None

        for m in window:
            author = m.get("author_name", "Desconhecido")
            stickers = m.get("stickers", [])
            sticker_text = (
                " ".join(f"[Sticker: {s.get('name', '?')}]" for s in stickers)
                if stickers
                else ""
            )
            content = (
                m.get("content", "") or sticker_text or "*[Sem conteúdo em texto]*"
            )[:_CONTENT_MAX_LENGTH]
            ts = m.get("timestamp", "?")
            is_target = m.get("message_id") in target_ids

            if author != last_author:
                lines.append(f"\n👤 **{author}**")
                last_author = author

            prefix = "> 🎯 **" if is_target else "> "
            suffix = "**" if is_target else ""
            lines.append(f"{prefix}`[{ts}]` {content}{suffix}")

        text = "\n".join(lines).strip()
        if len(text) > _EMBED_DESC_MAX_LENGTH:
            text = (
                text[:_EMBED_DESC_MAX_LENGTH]
                + "\n\n*... [Contexto truncado pelo limite do Discord]*"
            )
        return text

    @ui.button(
        label="📜 Expandir Contexto",
        style=discord.ButtonStyle.secondary,
        custom_id="tracker_expand_ctx",
    )
    async def expand_context(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await self._handle_expand(interaction)
        except Exception:
            log.exception(
                "Erro ao expandir contexto (msg=%s)",
                getattr(interaction.message, "id", "?"),
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocorreu um erro inesperado ao expandir o contexto.",
                    ephemeral=True,
                )

    async def _handle_expand(self, interaction: discord.Interaction):
        ref = interaction.message
        if not ref:
            await interaction.response.send_message(
                "❌ Não foi possível localizar a mensagem de referência.",
                ephemeral=True,
            )
            return

        data = self._context_store.get(str(ref.id))
        if not data:
            await interaction.response.send_message(
                "⏳ O contexto expirou ou não está mais disponível na memória.",
                ephemeral=True,
            )
            return

        context_msgs = data.get("context", [])
        target_msgs = data.get("messages", [])

        window = self._build_window(context_msgs, target_msgs)
        if window is None:
            await interaction.response.send_message(
                "⚠️ As mensagens rastreadas não foram encontradas no histórico do contexto.",
                ephemeral=True,
            )
            return

        target_ids = {m.get("message_id") for m in target_msgs}
        description = self._format_lines(window, target_ids)

        embed = discord.Embed(
            title="📜 Contexto da Conversa",
            description=description,
            color=0x2B2D31,
        )
        now_local = datetime.now().astimezone()
        embed.set_footer(text=f"{now_local:%d/%m/%y %H:%M:%S} - Madman")

        await interaction.response.send_message(embed=embed, ephemeral=True)
