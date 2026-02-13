import discord
from discord import ui

from utils.helpers import PainelView



from views.account_management import AccountManagementView
from views.relationship_management import RelationshipManagementView
from views.guild_management import GuildManagementView
from views.message_cleanup_view import MessageCleanupView


class ControlSelect(ui.Select):
    def __init__(self, manager):
        options = [
            discord.SelectOption(
                label="Gerenciar Conta",
                description="Autenticar/Desconectar selfbot.",
                emoji="<:account:1471252343386280068>",
                value="account"
            ),
            discord.SelectOption(
                label="Gerenciar Relações",
                description="Amigos, bloqueios e pedidos pendentes.",
                emoji="<:friends:1471247997307191326>",
                value="rel"
            ),
            discord.SelectOption(
                label="Gerenciar Servidores",
                description="Sair de servidores e silenciar notificações.",
                emoji="<:guild:1471252315678834788>",
                value="guild"
            ),
            discord.SelectOption(
                label="Limpar Mensagens",
                description="Purge de DMs, Canais e Data Package.",
                emoji="<:clean:1471255175795834961>",
                value="clear"
            ),
        ]
        super().__init__(
            placeholder="Selecione uma operação...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="madman_control_select"
        )
        self.manager = manager

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        
        # O usuário solicitou não resetar o select (não chamar edit_message com a view)
        # await interaction.response.edit_message(view=self.view)
        
        # Deferimos a resposta para garantir que não expire (opcional, mas seguro)
        await interaction.response.defer(ephemeral=False)

        if value == "account":
            view = AccountManagementView(self.manager, interaction.user.id)
            view._update_buttons()
            await interaction.followup.send(
                embed=view._get_status_embed(interaction),
                view=view,
                ephemeral=False
            )

        elif value == "rel":
            view = RelationshipManagementView(self.manager, interaction.user.id)
            await view.refresh_stats()
            await interaction.followup.send(
                embed=view._get_embed(interaction),
                view=view,
                ephemeral=False
            )

        elif value == "guild":
            view = GuildManagementView(self.manager, interaction.user.id)
            await view.refresh_stats()
            await interaction.followup.send(
                embed=view._get_embed(interaction),
                view=view,
                ephemeral=False
            )

        elif value == "clear":
            if interaction.user.id not in self.manager.active_processes:
                return await interaction.followup.send(
                    "❌ Você não está conectado! Selecione **Gerenciar Conta** -> **Autenticar** primeiro.",
                    ephemeral=False
                )
            view = MessageCleanupView(self.manager, interaction.user.id)
            await interaction.followup.send(
                embed=view._get_embed(),
                view=view,
                ephemeral=False
            )


class PersistentControlView(PainelView):
    """View persistente do painel — herda o layout de PainelView."""
    def __init__(self, manager):
        self.manager = manager
        super().__init__(select_item=ControlSelect(manager))
