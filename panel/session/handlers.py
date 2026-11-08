import discord
from .modal import AuthenticateModal
from ..shared.interaction import send_modal_safely


async def handle_authenticate(interaction: discord.Interaction, manager, user_id: int):
    if manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Já existe uma conta autenticada. Desconecte-a primeiro.", ephemeral=True
        )

    return await send_modal_safely(interaction, AuthenticateModal(manager, user_id))


async def handle_disconnect(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message("Nenhuma conta autenticada para desconectar.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    success, msg = await manager.disconnect(user_id)

    if success:
        settings = getattr(interaction.client, "_settings_state", None)
        if settings is not None:
            settings.pop(user_id, None)
        await interaction.followup.send(f"{msg}", ephemeral=True)
    else:
        await interaction.followup.send(f"{msg}", ephemeral=True)
