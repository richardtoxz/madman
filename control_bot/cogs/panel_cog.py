import discord
from discord.ext import commands
from discord import app_commands
from views.ephemeral_panel import EphemeralPanelView


class PanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="painel", 
        description="Abre o painel de controle de forma invisível para os outros."
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def painel(self, interaction: discord.Interaction):
        # Envia de forma ephemera (apenas o autor vê) usando nossa view categorizada
        panel_view = EphemeralPanelView(self.bot.manager, original_interaction=interaction)
        
        embed = discord.Embed(
            title="<:engrenagem:1472004351039111228> Painel de Controle (Privado)",
            description="Selecione uma categoria abaixo para controlar sua conta.\n"
                        "*Esta mensagem é visível apenas para você e as opções podem ser executadas múltiplas vezes.*",
            color=0x2b2d31
        )
        embed.set_footer(text="Madman Selfbot • Re-execução ativada")
        
        await interaction.response.send_message(embed=embed, view=panel_view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(PanelCog(bot))
