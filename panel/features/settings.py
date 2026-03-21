import discord

from ..modals.settings_modal import SettingsModal
from ..shared.interaction import send_modal_safely


async def handle_settings(interaction: discord.Interaction, manager, user_id: int):
    if not manager.is_authenticated(user_id):
        await interaction.response.send_message(
            "❌ O selfbot não está rodando!", ephemeral=True
        )
        return

    state = getattr(interaction.client, "_settings_state", {})
    user_state = state.get(user_id, {})
    webhook_on = bool(user_state.get("webhook_url"))
    captcha_on = bool(user_state.get("has_captcha_key"))

    webhook_status = "🟢 Configurado" if webhook_on else "🔴 Não configurado"
    captcha_status = "🟢 Configurado" if captcha_on else "🔴 Não configurado"

    embed = discord.Embed(
        title="⚙️ Configurações Globais",
        description=(
            "Configurações compartilhadas entre Tracker e Quest Completer.\n\n"
            f"**Webhook:** {webhook_status}\n"
            f"**YesCaptcha:** {captcha_status}\n\n"
            "**Para que serve cada campo?**\n"
            "• `Webhook URL` — Envia logs e notificações (usado por Tracker e Quest).\n"
            "• `YesCaptcha API Key` — Resolve captcha automaticamente no resgate de quests."
        ),
        color=0x2B2D31,
    )
    embed.set_footer(text="Clique no botão abaixo para alterar.")

    view = SettingsConfigView(manager, user_id)
    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True,
    )


class SettingsConfigView(discord.ui.View):
    def __init__(self, manager, user_id: int):
        super().__init__(timeout=120)
        self.manager = manager
        self.user_id = user_id

    @discord.ui.button(label="Configurar", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Somente quem abriu pode configurar.", ephemeral=True
            )
            return

        await send_modal_safely(
            interaction,
            SettingsModal(self.manager, self.user_id),
        )
