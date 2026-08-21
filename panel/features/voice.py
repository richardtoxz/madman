import discord

from ..modals.voice_modal import (
    VoiceAFKModal,
    VoiceControlView,
    VoiceFollowModal,
    build_voice_embed,
)
from ..shared.interaction import send_modal_safely


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


async def handle_voice_control(
    interaction: discord.Interaction, manager, user_id: int
):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    status = await manager.voice_status(user_id)
    await interaction.followup.send(
        embed=build_voice_embed(status),
        view=VoiceControlView(manager, user_id, status),
        ephemeral=True,
    )
