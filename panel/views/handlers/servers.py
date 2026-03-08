import asyncio
import discord
from discord import ui

from ...utils.stealth_dm import send_stealth_dm


async def _start_guild_action(interaction: discord.Interaction, manager, user_id: int, action: str):
    stats = await manager.get_guild_stats(user_id)
    total_guilds = stats.get("total", 0) if stats else 0

    action_name = "SAIR DE TODOS" if action == "LEAVE" else "SILENCIAR TODOS"
    color = 0xe74c3c if action == "LEAVE" else 0xf1c40f

    confirm_view = ui.View(timeout=60)

    async def confirm_callback(inter: discord.Interaction):
        await inter.response.edit_message(
            content=f"Iniciando processo: {action_name}...", embed=None, view=None
        )

        from utils.stealth_dm import cleanup_stealth_messages
        asyncio.create_task(cleanup_stealth_messages(inter.user, inter.client.user.id))

        async def progress_callback(curr, total, status, name):
            embed = discord.Embed(title="Silenciando Servidores", color=color)
            if status == "STARTING":
                embed.description = f"Preparando para processar **{total}** servidores..."
            else:
                embed.description = f"Progresso: **{curr}/{total}**"

            try:
                await inter.edit_original_response(embed=embed)
            except Exception:
                pass

        result = await manager.start_guild_cleanup(user_id, action, progress_callback)
        try:
            await inter.edit_original_response(content=f"✅ **Operação concluída!**\n{result}", embed=None)
        except Exception:
            pass

        if "✅" in result or "OK" in result.upper() or "OPERACAO CONCLUIDA" in result.upper():
            await send_stealth_dm(
                inter,
                title="🏠 Servidores Madman",
                description=f"**Ação:** {action_name}\n**Resultado:** {result}",
                color=color
            )

    btn_confirm = ui.Button(label=f"Confirmar {action_name}", style=discord.ButtonStyle.danger)
    btn_confirm.callback = confirm_callback
    confirm_view.add_item(btn_confirm)

    await interaction.followup.send(
        content=(
            f"⚠️ **CONFIRMAÇÃO DE OPERAÇÃO**\n\n"
            f"Você está prestes a **{action_name}** os servidores.\n\n"
            f"**Resumo:**\n"
            f"• Servidores Atuais: **{total_guilds}**\n\n"
            f"Deseja prosseguir?"
        ),
        view=confirm_view,
        ephemeral=True
    )


async def handle_mute_servers(interaction: discord.Interaction, manager, user_id: int):
    if user_id not in manager.active_processes:
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    await _start_guild_action(interaction, manager, user_id, "MUTE")


async def handle_leave_servers(interaction: discord.Interaction, manager, user_id: int):
    if user_id not in manager.active_processes:
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    await _start_guild_action(interaction, manager, user_id, "LEAVE")
