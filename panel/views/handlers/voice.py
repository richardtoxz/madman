import discord

from ...modals.voice_modal import VoiceAFKModal, VoiceFollowModal
from ...utils.interaction import send_modal_safely


async def handle_voice_afk(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    return await send_modal_safely(interaction, VoiceAFKModal(manager, user_id))


async def handle_voice_follow(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    return await send_modal_safely(interaction, VoiceFollowModal(manager, user_id))
