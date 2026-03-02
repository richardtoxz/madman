import discord
from discord import ui


class VoiceAFKModal(ui.Modal, title="Farmar Voz"):
    channel_id = ui.TextInput(
        label="Channel ID",
        placeholder="ID do canal de voz para conectar...",
        min_length=15,
        max_length=25,
        style=discord.TextStyle.short,
    )

    def __init__(self, manager, user_id):
        super().__init__()
        self.manager = manager
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            cid = int(self.channel_id.value.strip())
        except ValueError:
            return await interaction.followup.send("ID inválido.", ephemeral=True)

        result = await self.manager.voice_join(self.user_id, cid)
        await interaction.followup.send(result, ephemeral=True)


class VoiceFollowModal(ui.Modal, title="Seguir Usuário"):
    target_id = ui.TextInput(
        label="User ID",
        placeholder="ID do usuário para seguir em voice...",
        min_length=15,
        max_length=25,
        style=discord.TextStyle.short,
    )

    def __init__(self, manager, user_id):
        super().__init__()
        self.manager = manager
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            tid = int(self.target_id.value.strip())
        except ValueError:
            return await interaction.followup.send("ID inválido.", ephemeral=True)

        result = await self.manager.voice_follow(self.user_id, tid)
        await interaction.followup.send(result, ephemeral=True)
