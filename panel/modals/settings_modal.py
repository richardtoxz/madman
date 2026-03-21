import discord
from discord import ui


class SettingsModal(ui.Modal, title="⚙️ Configurações Globais"):
    webhook_url = ui.TextInput(
        label="Webhook URL (Tracker + Quest logs)",
        style=discord.TextStyle.short,
        placeholder="https://discord.com/api/webhooks/...",
        required=False,
    )
    captcha_api_key = ui.TextInput(
        label="YesCaptcha API Key (Quest auto-claim)",
        style=discord.TextStyle.short,
        placeholder="chave_do_yescaptcha",
        required=False,
    )

    def __init__(self, manager, admin_id: int):
        super().__init__()
        self.manager = manager
        self.admin_id = admin_id

    async def on_submit(self, interaction: discord.Interaction):
        webhook = self.webhook_url.value.strip()
        captcha_key = self.captcha_api_key.value.strip()

        if webhook and not webhook.startswith("https://discord.com/api/webhooks/"):
            await interaction.response.send_message(
                "❌ Webhook URL inválida. Use o formato `https://discord.com/api/webhooks/...`",
                ephemeral=True,
            )
            return

        # Save to bot-level state
        state = getattr(interaction.client, "_settings_state", {})
        state[self.admin_id] = {
            "webhook_url": webhook or None,
            "has_captcha_key": bool(captcha_key),
        }
        interaction.client._settings_state = state

        # Also push captcha key to selfbot if running
        if captcha_key or webhook:
            await self.manager.quest_config(
                self.admin_id,
                webhook_url=webhook or None,
                captcha_api_key=captcha_key or None,
            )

        if not webhook and not captcha_key:
            message = (
                "✅ Configurações limpas para esta sessão.\n"
                "• Webhook: desativado\n"
                "• YesCaptcha: desativado"
            )
        else:
            wh = "✅ configurado" if webhook else "❌ desativado"
            ck = "✅ configurado" if captcha_key else "❌ desativado"
            message = (
                f"✅ Configurações atualizadas!\n"
                f"• Webhook: {wh}\n"
                f"• YesCaptcha: {ck}"
            )

        await interaction.response.send_message(message, ephemeral=True)
