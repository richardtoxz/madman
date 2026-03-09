import discord


def is_unknown_interaction_error(error: Exception) -> bool:
    return isinstance(error, discord.HTTPException) and getattr(error, "code", None) == 10062


def is_already_acknowledged_error(error: Exception) -> bool:
    return isinstance(error, discord.HTTPException) and getattr(error, "code", None) == 40060


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


async def send_message_safely(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = False,
) -> bool:
    if interaction.response.is_done():
        try:
            await interaction.followup.send(
                content=content,
                embed=embed,
                view=view,
                ephemeral=ephemeral,
            )
            return True
        except discord.HTTPException as error:
            if is_unknown_interaction_error(error):
                return False
            raise

    try:
        await interaction.response.send_message(
            content=content,
            embed=embed,
            view=view,
            ephemeral=ephemeral,
        )
        return True
    except discord.HTTPException as error:
        if is_unknown_interaction_error(error):
            return False
        if is_already_acknowledged_error(error):
            try:
                await interaction.followup.send(
                    content=content,
                    embed=embed,
                    view=view,
                    ephemeral=ephemeral,
                )
                return True
            except discord.HTTPException as followup_error:
                if is_unknown_interaction_error(followup_error):
                    return False
                raise
        raise