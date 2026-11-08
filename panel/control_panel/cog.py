import discord
from discord.ext import commands
from discord import app_commands

from ..access import ACCESS_DENIED_MESSAGE, is_user_allowed
from .ephemeral_panel import EphemeralPanelView


class PanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="painel", description="Abre o painel de controle do Madman.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def painel(self, interaction: discord.Interaction):
        if not is_user_allowed(interaction.user.id):
            await interaction.response.send_message(ACCESS_DENIED_MESSAGE, ephemeral=True)
            return

        panel_view = EphemeralPanelView(self.bot.manager, original_interaction=interaction)

        await interaction.response.send_message(
            view=panel_view,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(PanelCog(bot))
