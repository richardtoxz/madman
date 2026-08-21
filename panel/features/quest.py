from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress

import discord

from ..shared.tasks import create_panel_task

logger = logging.getLogger(__name__)

PROGRESS_EDIT_STEP_PERCENT = 10
SESSION_TIMEOUT_SECONDS = 7200


def _extract_progress_percent(text: str) -> int | None:
    match = re.search(r"\((\d+)%\)", text)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


async def handle_quest(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id) or not manager._get_proc(user_id):
        await interaction.response.send_message(
            "❌ O selfbot não está rodando!", ephemeral=True
        )
        return

    sessions = getattr(interaction.client, "_quest_sessions", None)
    if sessions is None:
        sessions = {}
        interaction.client._quest_sessions = sessions
    if user_id in sessions:
        await interaction.response.send_message(
            "🎯 Já existe uma fila de Quests sendo acompanhada na sua DM.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title="🎯 Quest Completer",
        description="Preparando a fila...",
        color=0x5865F2,
    )
    embed.set_footer(text="Madman Quest Completer")
    try:
        dm_message = await interaction.user.send(embed=embed)
    except discord.Forbidden:
        await interaction.edit_original_response(
            content="❌ Abra suas DMs para receber o progresso das Quests. Nada foi iniciado."
        )
        return
    except Exception:
        await interaction.edit_original_response(
            content="❌ Não consegui criar a mensagem de acompanhamento. Nada foi iniciado."
        )
        return

    session: dict = {"finished": False, "last_bucket": -1}
    sessions[user_id] = session

    def finish_session() -> None:
        if sessions.get(user_id) is session:
            sessions.pop(user_id, None)
        manager.unsubscribe_events(user_id, "QUEST:", on_quest_event)

    async def on_quest_event(text: str):
        if session["finished"]:
            return
        content: str | None = None
        color: int | None = None
        final = False

        if text.startswith("QUEST:RESULT:"):
            try:
                result = json.loads(text.removeprefix("QUEST:RESULT:"))
            except (json.JSONDecodeError, TypeError):
                result = {
                    "ok": False,
                    "message": "O worker retornou um resultado inválido.",
                }
            completed = int(result.get("completed", 0) or 0)
            failed = int(result.get("failed", 0) or 0)
            manual_enroll = int(result.get("manual_enroll", 0) or 0)
            manual_claim = int(result.get("manual_claim", 0) or 0)
            unsupported = int(result.get("unsupported", 0) or 0)
            lines = [str(result.get("message") or "Fila finalizada.")]
            if completed:
                lines.append(f"✅ Concluídas: **{completed}**")
            if failed:
                lines.append(f"❌ Não confirmadas: **{failed}**")
            if manual_enroll:
                lines.append(f"⏸️ Precisam ser aceitas no Discord: **{manual_enroll}**")
            if manual_claim:
                lines.append(f"🎁 Aguardam resgate manual: **{manual_claim}**")
            if unsupported:
                lines.append(f"⏭️ Exigem execução manual: **{unsupported}**")
            content = "\n".join(lines)
            color = 0x57F287 if result.get("ok") else 0xFEE75C
            final = True
        elif text.startswith("QUEST:AUTO:"):
            content = text.removeprefix("QUEST:AUTO:")
        elif text.startswith("QUEST:PROGRESS:"):
            pct = _extract_progress_percent(text)
            if pct is not None:
                bucket = pct // PROGRESS_EDIT_STEP_PERCENT
                if bucket <= session["last_bucket"] and pct < 100:
                    return
                session["last_bucket"] = bucket
            content = text.removeprefix("QUEST:PROGRESS:")
        elif text.startswith("QUEST:ERRO:"):
            content = "❌ " + text.removeprefix("QUEST:ERRO:")
            color = 0xED4245

        if content is None:
            return
        embed.description = content[:4000]
        if color is not None:
            embed.color = color
        try:
            await dm_message.edit(embed=embed)
        except Exception:
            logger.debug("Falha ao atualizar DM de Quest", exc_info=True)

        if final:
            session["finished"] = True
            finish_session()

    manager.subscribe_events(user_id, "QUEST:", on_quest_event)

    result = await manager.quest_auto(user_id)
    if not result.get("ok"):
        session["finished"] = True
        finish_session()
        embed.description = "❌ " + str(result.get("message") or "Falha ao iniciar.")
        embed.color = 0xED4245
        with suppress(Exception):
            await dm_message.edit(embed=embed)
        await interaction.edit_original_response(content=embed.description)
        return

    # A queue containing only manual/unsupported entries can finish between
    # the worker acknowledgement and this coroutine resuming.  Preserve the
    # final event instead of replacing it with the temporary "started" state.
    if session["finished"]:
        await interaction.edit_original_response(
            content="🎯 Fila verificada. Confira o resultado na sua DM."
        )
        return

    embed.description = (
        "Fila iniciada em modo sequencial. Somente Quests já aceitas serão processadas; "
        "o resgate da recompensa continua manual."
    )
    with suppress(Exception):
        await dm_message.edit(embed=embed)
    await interaction.edit_original_response(
        content="🎯 Fila iniciada. Acompanhe o progresso na sua DM."
    )

    async def expire_session():
        await asyncio.sleep(SESSION_TIMEOUT_SECONDS)
        if sessions.get(user_id) is session and not session["finished"]:
            session["finished"] = True
            finish_session()

    create_panel_task(
        interaction.client,
        expire_session(),
        name=f"quest-panel-session-{user_id}",
    )
