import discord
from discord import ui
from modals.token_modal import AuthenticateModal


class AccountManagementView(ui.View):
    def __init__(self, manager, user_id):
        super().__init__(timeout=180)
        self.manager = manager
        self.user_id = user_id

    def _get_status_embed(self, interaction: discord.Interaction):
        is_active = self.manager.is_authenticated(self.user_id)

        if is_active:
            proc = self.manager.active_processes.get(self.user_id)
            username = proc.username if proc else "desconhecido"
            status_text = f"🟢 **ATIVO** como `{username}`"
            color = 0x57F287  # Verde Discord
        else:
            status_text = "🔴 **INATIVO**"
            color = 0x2b2d31

        embed = discord.Embed(
            title="<:account:1471252343386280068> Gerenciamento de Conta",
            description=f"Gerencie o estado do seu selfbot.\n\n"
                        f"📡 **Status:** {status_text}",
            color=color
        )
        return embed

    def _update_buttons(self):
        is_active = self.manager.is_authenticated(self.user_id)

        for item in self.children:
            if isinstance(item, ui.Button):
                if item.custom_id == "ephemeral_authenticate":
                    item.disabled = is_active
                elif item.custom_id == "ephemeral_disconnect":
                    item.disabled = not is_active

    @ui.button(label="Autenticar", style=discord.ButtonStyle.success, emoji="🔑", custom_id="ephemeral_authenticate", row=1)
    async def authenticate_button(self, interaction: discord.Interaction, button: ui.Button):
        if self.manager.is_authenticated(self.user_id):
            return await interaction.response.send_message(
                "⚠️ Selfbot já está autenticado. Desconecte primeiro.", ephemeral=True
            )

        async def on_complete(modal_interaction: discord.Interaction):
            """Callback chamado quando a autenticação completar com sucesso."""
            self._update_buttons()
            try:
                await interaction.edit_original_response(
                    embed=self._get_status_embed(interaction), view=self
                )
            except discord.HTTPException:
                pass

        await interaction.response.send_modal(
            AuthenticateModal(self.manager, self.user_id, on_complete=on_complete)
        )

    @ui.button(label="Desconectar", style=discord.ButtonStyle.danger, emoji="🔌", custom_id="ephemeral_disconnect", row=1)
    async def disconnect_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self.manager.is_authenticated(self.user_id):
            return await interaction.response.send_message(
                "⚠️ Nenhum selfbot ativo para desconectar.", ephemeral=True
            )

        await interaction.response.defer()

        success, msg = await self.manager.disconnect(self.user_id)

        self._update_buttons()
        await interaction.edit_original_response(
            embed=self._get_status_embed(interaction), view=self
        )
