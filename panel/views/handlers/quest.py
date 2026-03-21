import asyncio
import logging
import re
import time

import discord
from discord import ui

from ...modals.quest_config_modal import QuestConfigModal
from ...utils.interaction import send_modal_safely

logger = logging.getLogger(__name__)

PROGRESS_EDIT_STEP_PERCENT = 10
NON_PROGRESS_EDIT_COOLDOWN_SECONDS = 6.0


def _extract_progress_percent(text: str) -> int | None:
    match = re.search(r"\((\d+)%\)", text)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except Exception:
        return None
    return max(0, min(100, value))


class QuestConfigStartView(ui.View):
    def __init__(self, manager, user_id: int):
        super().__init__(timeout=120)
        self.manager = manager
        self.user_id = user_id

    @ui.button(label="Abrir Configuração", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Somente quem abriu pode configurar.", ephemeral=True
            )
            return

        await send_modal_safely(
            interaction,
            QuestConfigModal(self.manager, self.user_id),
        )


async def handle_quest(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "❌ O selfbot não está rodando!", ephemeral=True
        )

    if not manager._get_proc(user_id):
        return await interaction.response.send_message(
            "❌ Selfbot não disponível.", ephemeral=True
        )

    await interaction.response.send_message(
        "🎯 **Quest Completer ativado!**\n-# Progresso será enviado via DM.",
        ephemeral=True,
    )

    user = interaction.user
    embed = discord.Embed(
        title="🎯 Quest Completer",
        description="Iniciando...",
        color=0x5865F2,
    )
    embed.set_footer(text="Madman Quest Completer")

    try:
        dm_msg = await user.send(embed=embed)
    except discord.Forbidden:
        dm_msg = None

    success = await manager.quest_auto(user_id)
    if not success:
        if dm_msg:
            embed.description = "❌ Falha ao iniciar."
            embed.color = 0xED4245
            try:
                await dm_msg.edit(embed=embed)
            except Exception:
                pass
        return

    last_edit = [0.0]
    last_progress_bucket = [-1]
    finished = [False]

    async def on_quest_event(text: str):
        if not dm_msg or finished[0]:
            return

        content = ""
        if text.startswith("QUEST:AUTO:"):
            content = text[len("QUEST:AUTO:") :]
        elif text.startswith("QUEST:PROGRESS:"):
            content = text[len("QUEST:PROGRESS:") :]
        elif text.startswith("QUEST:ERRO:"):
            content = "❌ " + text[len("QUEST:ERRO:") :]
        else:
            return

        is_final = any(
            kw in text
            for kw in ["processadas", "Cancelado", "pendente", "Nenhuma quest"]
        )

        if is_final:
            finished[0] = True
            embed.description = content
            embed.color = 0x57F287 if "processadas" in text else 0xFEE75C
            delete_at = int(time.time() + 120)
            embed.description += f"\n\n⏳ Deletando <t:{delete_at}:R>"
            try:
                await dm_msg.edit(embed=embed)
            except Exception:
                pass
            manager.unsubscribe_events(user_id, "QUEST:", on_quest_event)
            await asyncio.sleep(120)
            try:
                await dm_msg.delete()
            except Exception:
                pass
            return

        is_progress = text.startswith("QUEST:PROGRESS:")
        if is_progress:
            pct = _extract_progress_percent(text)
            if pct is not None:
                bucket = pct // PROGRESS_EDIT_STEP_PERCENT
                if bucket <= last_progress_bucket[0] and pct < 100:
                    return
                last_progress_bucket[0] = bucket
            else:
                now = time.time()
                if (now - last_edit[0]) < NON_PROGRESS_EDIT_COOLDOWN_SECONDS:
                    return
                last_edit[0] = now
        else:
            now = time.time()
            if (now - last_edit[0]) < NON_PROGRESS_EDIT_COOLDOWN_SECONDS:
                return
            last_edit[0] = now

        embed.description = content
        try:
            await dm_msg.edit(embed=embed)
        except Exception:
            pass

    manager.subscribe_events(user_id, "QUEST:", on_quest_event)

    async def unsub_later():
        await asyncio.sleep(7200)
        if not finished[0]:
            manager.unsubscribe_events(user_id, "QUEST:", on_quest_event)

    asyncio.create_task(unsub_later())


async def handle_quest_config(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        await interaction.response.send_message(
            "❌ O selfbot não está rodando!", ephemeral=True
        )
        return

    state = getattr(interaction.client, "_quest_config_state", {})
    user_state = state.get(user_id, {})
    webhook_on = bool(user_state.get("webhook_url"))
    captcha_on = bool(user_state.get("has_captcha_key"))

    webhook_status = "🟢 Ligado" if webhook_on else "🔴 Desligado"
    captcha_status = "🟢 Ligado" if captcha_on else "🔴 Desligado"

    message = (
        "**Configuração do Quest Completer**\n"
        f"- Webhook: {webhook_status}\n"
        f"- YesCaptcha: {captcha_status}\n\n"
        "**Para que serve cada campo?**\n"
        "- `WEBHOOK_URL`: envia logs para acompanhar o progresso das missões.\n"
        "- `YES_CAPTCHA_API_KEY`: tenta resolver captcha no claim para automatizar o resgate.\n\n"
        "Se a API do YesCaptcha não for configurada, o resgate da missão terá que ser feito manualmente."
    )

    await interaction.response.send_message(
        message,
        view=QuestConfigStartView(manager, user_id),
        ephemeral=True,
    )
