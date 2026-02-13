import asyncio
import discord
from discord import ui

from modals.clean_dm_modal import CleanDMModal
from modals.clean_guild_modal import CleanGuildModal
from utils.helpers import generate_progress_bar
from utils.stealth_dm import send_stealth_dm


def _require_auth(manager, user_id: int):
    return user_id in manager.active_processes


async def handle_clean_dm(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )
    await interaction.response.send_modal(CleanDMModal(manager))


async def handle_clean_all_dms(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    confirm_view = ui.View(timeout=60)

    async def confirm(inter: discord.Interaction):
        await inter.response.edit_message(
            content="🚀 Iniciando limpeza de **todas as DMs abertas**...\nIsso pode demorar bastante.",
            view=None
        )

        from utils.stealth_dm import cleanup_stealth_messages
        asyncio.create_task(cleanup_stealth_messages(inter.user, inter.client.user.id))

        async def progress(curr, total, status, extra):
            try:
                if status == "ALL_DMS_CHANNEL":
                    await inter.edit_original_response(
                        content=f"🧹 Limpando DM **{curr}/{total}**: {extra}"
                    )
                elif status == "ALL_DMS_FOUND":
                    await inter.edit_original_response(
                        content=f"🔍 Encontrado: {extra}"
                    )
                elif status == "CLEANING" and curr and total:
                    bar = generate_progress_bar(curr, total)
                    await inter.edit_original_response(
                        content=f"🧹 Deletando...\n{bar}\n`[{curr}/{total}]`"
                    )
            except Exception:
                pass

        result = await manager.clean_all_dms(user_id, update_callback=progress)
        try:
            await inter.edit_original_response(content=f"**Concluído!**\n{result}")
        except Exception:
            pass

    btn = ui.Button(label="SIM, Limpar Todas DMs Abertas", style=discord.ButtonStyle.danger, emoji="⚠️")
    btn.callback = confirm
    confirm_view.add_item(btn)

    await interaction.response.send_message(
        content=(
            "⚠️ **CONFIRMAÇÃO**\n\n"
            "Isso vai apagar suas mensagens em **TODAS** as DMs abertas.\n"
            "Pode demorar **muito** dependendo da quantidade.\n\n"
            "Deseja continuar?"
        ),
        view=confirm_view, ephemeral=True
    )


async def handle_clean_guild(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )
    await interaction.response.send_modal(CleanGuildModal(manager))


async def handle_close_dms(interaction: discord.Interaction, manager, user_id: int):
    if not _require_auth(manager, user_id):
        return await interaction.response.send_message(
            "❌ Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    action_view = ui.View(timeout=60)

    async def close_all_dm_callback(inter: discord.Interaction):
        await inter.response.edit_message(content="🚪 Fechando todas as DMs...", view=None)

        async def progress(curr, total, status, extra):
            try:
                if status in ("STARTING", "CLOSING"):
                    await inter.edit_original_response(
                        content=f"🚪 Fechando DMs... **{curr}/{total}**"
                    )
            except Exception:
                pass

        result = await manager.close_all_dms(user_id, update_callback=progress)
        try:
            await inter.edit_original_response(content=f"**Concluído!**\n{result}")
        except Exception:
            pass

    async def leave_all_groups_callback(inter: discord.Interaction):
        await inter.response.edit_message(content="🚪 Saindo de todos os Groups...", view=None)

        async def progress(curr, total, status, extra):
            try:
                if status in ("STARTING", "LEAVING"):
                    await inter.edit_original_response(
                        content=f"🚪 Saindo de Groups... **{curr}/{total}**"
                    )
            except Exception:
                pass

        result = await manager.leave_all_group_dms(user_id, update_callback=progress)
        try:
            await inter.edit_original_response(content=f"**Concluído!**\n{result}")
        except Exception:
            pass

    btn_dms = ui.Button(label="Fechar Todas DMs", style=discord.ButtonStyle.danger, emoji="💬")
    btn_dms.callback = close_all_dm_callback
    action_view.add_item(btn_dms)

    btn_groups = ui.Button(label="Sair de Todos Groups", style=discord.ButtonStyle.danger, emoji="👥")
    btn_groups.callback = leave_all_groups_callback
    action_view.add_item(btn_groups)

    await interaction.response.send_message(
        content="🚪 **O que deseja fechar?**",
        view=action_view, ephemeral=True
    )
