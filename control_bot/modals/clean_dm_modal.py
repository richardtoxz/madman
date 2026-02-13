import asyncio
import discord
from discord import ui

from views.progress_view import ProgressView
from utils.helpers import generate_progress_bar
from utils.stealth_dm import send_stealth_dm


class CleanDMModal(ui.Modal, title='Limpar Mensagens (DM/Canal)'):
    target_id = ui.TextInput(label='ID do Usuário ou Canal', placeholder='Insira o ID aqui...', min_length=15)

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    async def on_submit(self, interaction: discord.Interaction):
        target_id_val = int(self.target_id.value)
        user_id = interaction.user.id

        embed = discord.Embed(title="🧹 Central de Limpeza Madman", color=0x2b2d31)
        embed.description = "Calculando total de mensagens..."

        view = ProgressView(user_id, self.manager)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        from utils.stealth_dm import cleanup_stealth_messages
        asyncio.create_task(cleanup_stealth_messages(interaction.user, interaction.client.user.id))

        view.channel_info = {"name": str(target_id_val), "total": 0}

        async def update_progress(current, total, status, extra_data=None):
            nonlocal embed
            channel_info = view.channel_info

            if view.cancelled_by_user:
                return

            if status == "CHANNEL_DETECTED" and extra_data:
                channel_info["name"] = extra_data
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Detectado! Contando mensagens..."
                embed.color = 0x9b59b6
            elif status == "COUNTING":
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Contando suas mensagens... 🔍"
                embed.color = 0x9b59b6
            elif status == "WAITING_START":
                if total is not None:
                    channel_info["total"] = total
                embed.description = f"**Alvo:** {channel_info['name']}\n**Total encontrado:** {channel_info['total']} mensagens.\n\nClique abaixo para iniciar a limpeza."
                embed.color = 0xf1c40f
                view.total_ready = True
                view.update_buttons()
            elif status == "CLEANING":
                if total is not None:
                    channel_info["total"] = total
                bar = generate_progress_bar(current, channel_info["total"])
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Limpando... 🧹\n\n{bar}\n`[{current}/{channel_info['total']}]`"
                embed.color = 0x3498db
                view.started = True
                view.update_buttons()
            elif status == "RATE_LIMITED":
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** ⏳ Rate limit, aguardando..."
                embed.color = 0xe74c3c
            elif status == "BATCH_PAUSE":
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** ⏸️ Pausa de segurança..."
                embed.color = 0xe67e22
            elif status == "PAUSED":
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Pausado ⏸️\n`[{current}/{channel_info['total']}]`"
                embed.color = 0xe74c3c
            elif status == "STARTED":
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Iniciando limpeza..."
                embed.color = 0x3498db
            elif status == "TARGET_TYPE" and extra_data:
                view.target_type = extra_data

            try:
                await interaction.edit_original_response(embed=embed, view=view)
            except Exception:
                pass

        asyncio.create_task(self._run_cleanup(user_id, target_id_val, update_progress, view, interaction))

    async def _run_cleanup(self, user_id, target_id, callback, view, interaction):
        result = await self.manager.send_command(user_id, target_id, update_callback=callback)

        is_error = any(kw in result.lower() for kw in ['erro', 'error', 'não encontrado', 'timeout', 'sem permissão'])

        deleted_count = "0"
        total_count = str(view.channel_info.get("total", "0"))

        if "Deletadas " in result:
            try:
                stats_part = result.split("Deletadas ")[1].split(" ")[0]
                deleted_count = stats_part.split("/")[0]
                total_count = stats_part.split("/")[1]
            except Exception:
                pass

        target_name = view.channel_info.get("name", str(target_id))
        
        # Usa o tipo detectado via IPC para formatar a menção
        mention = f"<#{target_id}>" if view.target_type == "channel" else f"<@{target_id}>"

        if is_error:
            final_text = f"❌ **Erro na limpeza do alvo ({mention}):**\n{result}"
        elif view.cancelled_by_user:
            final_text = f"⏹️ **Processo interrompido**\n**{deleted_count}** de **{total_count}** apagadas de **{target_name}** ({mention})"
        else:
            final_text = f"✅ **{target_name} ({mention}) apagada com sucesso**\nTotal: **{deleted_count}** de **{total_count}**"

            description = (
                f"**Alvo:** {target_name} ({mention})\n"
                f"**Mensagens Deletadas:** {deleted_count} de {total_count}\n"
                f"**Status:** Limpeza Concluída"
            )
            await send_stealth_dm(interaction, title="🧹 Central de Limpeza Madman", description=description, color=0x2ecc71)

        try:
            await interaction.edit_original_response(content=final_text, embed=None, view=None)
        except Exception:
            try:
                await interaction.followup.send(content=final_text, ephemeral=True)
            except Exception:
                pass