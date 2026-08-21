import re
from urllib.parse import urlparse

import discord
from discord import ui


def _looks_like_discord_webhook(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname in {"discord.com", "discordapp.com"}
        and parsed.port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and re.fullmatch(
            r"/api(?:/v\d+)?/webhooks/\d{5,25}/[A-Za-z0-9._-]{20,}",
            parsed.path,
        )
    )


class SettingsModal(ui.Modal, title="⚙️ Configurações Globais"):
    webhook_url = ui.TextInput(
        label="Webhook URL (Tracker + Quest logs)",
        style=discord.TextStyle.short,
        placeholder="https://discord.com/api/webhooks/...",
        required=False,
    )

    def __init__(self, manager, admin_id: int):
        super().__init__()
        self.manager = manager
        self.admin_id = int(admin_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin_id

    async def on_submit(self, interaction: discord.Interaction):
        webhook = self.webhook_url.value.strip()

        if webhook and not _looks_like_discord_webhook(webhook):
            await interaction.response.send_message(
                "❌ Webhook URL inválida. Use o formato `https://discord.com/api/webhooks/...`",
                ephemeral=True,
            )
            return

        result = await self.manager.quest_config(
            self.admin_id,
            webhook_url=webhook or None,
        )
        if not result.get("ok"):
            await interaction.response.send_message(
                "❌ " + str(result.get("message") or "Falha ao salvar a configuração."),
                ephemeral=True,
            )
            return

        state = getattr(interaction.client, "_settings_state", {})
        state[self.admin_id] = {"webhook_url": webhook or None}
        interaction.client._settings_state = state

        wh = "✅ configurado" if webhook else "❌ desativado"
        message = f"✅ Configurações atualizadas!\n• Webhook: {wh}"

        await interaction.response.send_message(message, ephemeral=True)
