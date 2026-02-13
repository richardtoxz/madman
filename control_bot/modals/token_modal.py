import discord
from discord import ui


class AuthenticateModal(ui.Modal, title='Autenticar Selfbot'):
    token = ui.TextInput(
        label='Token da Conta',
        placeholder='Insira seu token de usuário...',
        min_length=30,
        style=discord.TextStyle.short,
    )

    def __init__(self, manager, user_id, *, on_complete=None):
        super().__init__()
        self.manager = manager
        self.user_id = user_id
        self.on_complete = on_complete

    async def on_submit(self, interaction: discord.Interaction):
        # Defer — authenticate pode demorar até 30s
        await interaction.response.defer(ephemeral=True)

        # Detecta status do usuário no guild
        status = "online"
        if interaction.guild:
            member = interaction.guild.get_member(self.user_id)
            if member and hasattr(member, 'status'):
                status_map = {
                    discord.Status.online: "online",
                    discord.Status.idle: "idle",
                    discord.Status.dnd: "dnd",
                    discord.Status.offline: "online",
                    discord.Status.invisible: "online",
                }
                status = status_map.get(member.status, "online")

        success, msg = await self.manager.authenticate(
            self.user_id, self.token.value, status=status
        )

        if success:
            await interaction.followup.send(msg, ephemeral=True)
            # Atualiza a view principal se callback fornecido
            if self.on_complete:
                await self.on_complete(interaction)
        else:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
