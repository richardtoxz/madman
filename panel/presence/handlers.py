import discord

from .modal import RichPresenceView, build_rich_presence_embed


async def handle_rich_presence(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    status = await manager.rich_presence_status(user_id)
    await interaction.followup.send(
        embed=build_rich_presence_embed(status),
        view=RichPresenceView(manager, user_id, status),
        ephemeral=True,
    )
