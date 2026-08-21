import discord

from ..modals.protection_modal import (
    ProtectionControlView,
    build_protection_embed,
)


async def handle_protection(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    status = await manager.protection_status(user_id)
    await interaction.followup.send(
        embed=build_protection_embed(status),
        view=ProtectionControlView(manager, user_id, status),
        ephemeral=True,
    )
