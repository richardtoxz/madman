import discord
from discord import ui


class ProgressView(ui.View):
    def __init__(self, user_id, manager):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.manager = manager
        self.started = False
        self.total_ready = False
        self.cancelled_by_user = False
        self.target_type = "user"
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        if not self.started:
            btn_start = ui.Button(
                label="Iniciar Limpeza",
                style=discord.ButtonStyle.success,
                emoji="▶️",
                disabled=not self.total_ready,
            )
            btn_start.callback = self.start_cleanup
            self.add_item(btn_start)

        btn_cancel = ui.Button(
            label="Cancelar", style=discord.ButtonStyle.danger, emoji="⏹️"
        )
        btn_cancel.callback = self.cancel
        self.add_item(btn_cancel)

    async def start_cleanup(self, interaction: discord.Interaction):
        await self.manager.control_clean(self.user_id, "START")
        self.started = True
        self.update_buttons()
        await interaction.response.edit_message(view=self)

    async def cancel(self, interaction: discord.Interaction):
        self.cancelled_by_user = True

        await self.manager.control_clean(self.user_id, "CANCEL")

        stopping_embed = interaction.message.embeds[0]
        stopping_embed.title = "⏹️ Parando Limpeza..."
        stopping_embed.color = 0xE74C3C
        stopping_embed.description = (
            "Aguardando o processo finalizar para enviar o resumo..."
        )

        await interaction.response.edit_message(embed=stopping_embed, view=None)
