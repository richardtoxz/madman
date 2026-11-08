import discord

from .modal import TrackerControlView, build_tracker_embed


async def handle_tracker(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        return await interaction.response.send_message(
            "Você precisa estar autenticado! Use **Autenticar** primeiro.", ephemeral=True
        )

    webhook_url = None
    get_webhook = getattr(manager, "get_webhook_url", None)
    if callable(get_webhook):
        webhook_url = get_webhook(user_id)
    if not webhook_url:
        settings = getattr(interaction.client, "_settings_state", {}).get(user_id, {})
        webhook_url = settings.get("webhook_url")

    await interaction.response.defer(ephemeral=True)
    status = await manager.tracker_status(user_id)
    embed = build_tracker_embed(status)
    if not status.get("enabled") and not webhook_url:
        embed.description = "Configure primeiro um webhook em **Configurações → Webhook**."
    await interaction.followup.send(
        embed=embed,
        view=TrackerControlView(manager, user_id, status, webhook_url),
        ephemeral=True,
    )
