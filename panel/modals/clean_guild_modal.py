import asyncio
import discord
from discord import ui
from ..views.progress_view import ProgressView
from ..utils.helpers import generate_progress_bar
from ..utils.stealth_dm import send_stealth_dm


class CleanGuildModal(ui.Modal, title="Limpar Mensagens em Servidor"):
    guild_id_input = ui.TextInput(
        label="ID do Servidor",
        placeholder="Guild ID para limpar suas msgs...",
        min_length=15,
        max_length=21,
    )

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    async def on_submit(self, interaction: discord.Interaction):
        try:
            guild_id = int(self.guild_id_input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                "❌ ID deve ser numérico!", ephemeral=True
            )

        user_id = interaction.user.id
        embed = discord.Embed(title="🏠 Limpeza de Guild", color=0x2B2D31)
        embed.description = f"Buscando suas mensagens na guild `{guild_id}`..."
        view = ProgressView(user_id, self.manager)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.channel_info = {"name": str(guild_id), "total": 0}

        async def update_progress(current, total, status, extra_data=None):
            nonlocal embed
            ci = view.channel_info

            if view.cancelled_by_user:
                return

            if status == "CHANNEL_DETECTED" and extra_data:
                ci["name"] = extra_data
                embed.description = (
                    f"**Guild:** {ci['name']}\n**Status:** Contando mensagens..."
                )
                embed.color = 0x9B59B6
            elif status == "COUNTING":
                embed.description = (
                    f"**Guild:** {ci['name']}\n**Status:** Contando... 🔍"
                )
                embed.color = 0x9B59B6
            elif status == "WAITING_START":
                if total is not None:
                    ci["total"] = total
                embed.description = f"**Guild:** {ci['name']}\n**Total:** {ci['total']} mensagens\n\nClique ▶️ para iniciar."
                embed.color = 0xF1C40F
                view.total_ready = True
                view.update_buttons()
            elif status == "CLEANING":
                if total is not None:
                    ci["total"] = total
                bar = generate_progress_bar(current, ci["total"])
                embed.description = f"**Guild:** {ci['name']}\n**Status:** Limpando... 🧹\n\n{bar}\n`[{current}/{ci['total']}]`"
                embed.color = 0x3498DB
                view.started = True
                view.update_buttons()
            elif status == "RATE_LIMITED":
                embed.description = (
                    f"**Guild:** {ci['name']}\n**Status:** ⏳ Rate limit..."
                )
                embed.color = 0xE74C3C
            elif status == "BATCH_PAUSE":
                embed.description = (
                    f"**Guild:** {ci['name']}\n**Status:** ⏸️ Pausa de segurança..."
                )
                embed.color = 0xE67E22

            elif status == "STARTED":
                embed.description = (
                    f"**Guild:** {ci['name']}\n**Status:** Iniciando limpeza..."
                )
                embed.color = 0x3498DB
            elif status == "TARGET_TYPE" and extra_data:
                view.target_type = extra_data

            try:
                await interaction.edit_original_response(embed=embed, view=view)
            except Exception:
                pass

        asyncio.create_task(
            self._run(user_id, guild_id, update_progress, view, interaction)
        )

    async def _run(self, user_id, guild_id, callback, view, interaction):
        result = await self.manager.clean_guild_messages(
            user_id, guild_id, update_callback=callback
        )

        deleted_count = "0"
        total_count = str(view.channel_info.get("total", "0"))
        if "Deletadas " in result:
            try:
                after_del = result.split("Deletadas ")[1]
                deleted_count = after_del.split(" ")[0].strip()
                if "/" in after_del:
                    total_count = after_del.split("/")[1].split(" ")[0].strip()
                elif deleted_count.isdigit():
                    total_count = deleted_count
            except Exception:
                pass

        name = view.channel_info.get("name", str(guild_id))

        if view.target_type == "channel":
            mention = f"<#{guild_id}>"
        elif view.target_type == "user":
            mention = f"<@{guild_id}>"
        else:
            mention = f"ID `{guild_id}`"

        if view.cancelled_by_user:
            final = f"⏹️ **Interrompido**\n**{deleted_count}** de **{total_count}** apagadas em **{name}** ({mention})"
        elif "erro" in result.lower():
            final = f"❌ **Erro:** {result}"
        else:
            final = f"✅ **Guild {name} ({mention}) limpa**\n**{deleted_count}** de **{total_count}** deletadas"

            description = (
                f"**Alvo:** {name} ({mention})\n"
                f"**Mensagens Deletadas:** {deleted_count} de {total_count}\n"
                f"**Status:** Limpeza Concluída"
            )
            await send_stealth_dm(
                interaction,
                title="🧹 Central de Limpeza Madman",
                description=description,
                color=0x2ECC71,
            )

        try:
            await interaction.delete_original_response()
        except Exception:
            try:
                await interaction.edit_original_response(
                    content=final, embed=None, view=None
                )
            except Exception:
                pass
