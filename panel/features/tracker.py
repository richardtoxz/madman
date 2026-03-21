import discord

from ..modals.tracker_modal import TrackerModal, TrackerStopModal
from ..shared.interaction import send_modal_safely


async def handle_tracker(interaction: discord.Interaction, manager, admin_id: int):
    if not manager.is_authenticated(admin_id):
        await interaction.response.send_message("❌ O selfbot não está rodando!", ephemeral=True)
        return

    tracker_state = getattr(interaction.client, '_tracker_state', {})
    is_active = admin_id in tracker_state

    if is_active:
        view = TrackerStopModal(manager, admin_id)
        await interaction.response.send_message(
            f"🎯 **Tracker Ativo**\n"
            f"Alvo: `{tracker_state[admin_id]['target_id']}`\n"
            f"Logs: Via Webhook ✅",
            view=view,
            ephemeral=True
        )
    else:
        settings_state = getattr(interaction.client, '_settings_state', {})
        user_settings = settings_state.get(admin_id, {})
        webhook_url = user_settings.get("webhook_url")

        if not webhook_url:
            await interaction.response.send_message(
                "❌ **Webhook não configurado!**\n"
                "Use **⚙️ Configurações → Webhook & Captcha** para definir o webhook antes de usar o Tracker.",
                ephemeral=True,
            )
            return

        return await send_modal_safely(
            interaction, TrackerModal(manager, admin_id, webhook_url)
        )
