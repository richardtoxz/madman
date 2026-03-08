import discord


def is_unknown_interaction_error(error: Exception) -> bool:
    return isinstance(error, discord.HTTPException) and getattr(error, "code", None) == 10062


async def send_modal_safely(
    interaction: discord.Interaction,
    modal: discord.ui.Modal,
) -> bool:
    if interaction.response.is_done():
        return False

    try:
        await interaction.response.send_modal(modal)
        return True
    except discord.HTTPException as error:
        if is_unknown_interaction_error(error):
            return False
        raise