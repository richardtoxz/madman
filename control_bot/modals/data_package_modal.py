import json
import discord
from discord import ui

from utils.helpers import generate_progress_bar


class DataPackageModal(ui.Modal, title='Deletar via Data Package'):
    json_input = ui.TextInput(
        label='JSON do Data Package',
        style=discord.TextStyle.paragraph,
        placeholder='[{"channel_id":123,"message_id":456}, ...]',
        min_length=10,
        max_length=4000
    )

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    async def on_submit(self, interaction: discord.Interaction):
        try:
            entries = json.loads(self.json_input.value.strip())
            if not isinstance(entries, list):
                raise ValueError("Deve ser uma lista")
            for e in entries:
                if "channel_id" not in e or "message_id" not in e:
                    raise ValueError("Cada item precisa de channel_id e message_id")
        except (json.JSONDecodeError, ValueError) as e:
            return await interaction.response.send_message(f"❌ JSON inválido: {e}", ephemeral=True)

        user_id = interaction.user.id
        total = len(entries)

        embed = discord.Embed(
            title="📦 Data Package Delete",
            description=f"Deletando **{total}** mensagens via Data Package...",
            color=0x9b59b6
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        async def update_progress(current, total_p, status, extra):
            if status == "DATAPACK_START":
                embed.description = f"Iniciando delete de **{total_p}** msgs..."
            elif status == "CLEANING":
                if current and total_p:
                    bar = generate_progress_bar(current, total_p)
                    embed.description = f"Deletando... 🧹\n\n{bar}\n`[{current}/{total_p}]`"
                    embed.color = 0x3498db
            elif status == "RATE_LIMITED":
                embed.description = "⏳ Rate limit, aguardando..."
                embed.color = 0xe74c3c

            try:
                await interaction.edit_original_response(embed=embed)
            except Exception:
                pass

        result = await self.manager.delete_from_data_package(user_id, entries, update_callback=update_progress)

        try:
            await interaction.edit_original_response(
                content=f"📦 **Data Package concluído!**\n{result}",
                embed=None
            )
        except Exception:
            pass