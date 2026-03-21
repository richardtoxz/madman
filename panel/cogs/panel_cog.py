import discord
from discord.ext import commands
from discord import app_commands

from ..views.ephemeral_panel import EphemeralPanelView


class PanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="painel", description="Abre o painel de controle do Madman."
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def painel(self, interaction: discord.Interaction):
        panel_view = EphemeralPanelView(
            self.bot.manager, original_interaction=interaction
        )

        await interaction.response.send_message(
            view=panel_view,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(PanelCog(bot))
