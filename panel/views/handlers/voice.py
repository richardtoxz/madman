import discord

from modals.voice_modal import VoiceAFKModal, VoiceFollowModal


async def handle_voice_afk(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.send_modal(VoiceAFKModal(manager, user_id))


async def handle_voice_follow(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.send_modal(VoiceFollowModal(manager, user_id))
