import discord
from discord import ui


class TrackerModal(ui.Modal, title="Configurar Tracker"):
    target_id = ui.TextInput(
        label="ID do Alvo",
        style=discord.TextStyle.short,
        placeholder="123456789012345678",
        required=True,
        min_length=17,
        max_length=20,
    )
    webhook_url = ui.TextInput(
        label="Webhook URL (para logs)",
        style=discord.TextStyle.short,
        placeholder="https://discord.com/api/webhooks/...",
        required=True,
    )

    def __init__(self, manager, admin_id: int):
        super().__init__()
        self.manager = manager
        self.admin_id = admin_id

    async def on_submit(self, interaction: discord.Interaction):
        target = self.target_id.value.strip()
        webhook = self.webhook_url.value.strip()

        if not target.isdigit():
            await interaction.response.send_message(
                "❌ O ID do Alvo deve ser numérico.", ephemeral=True
            )
            return

        if not webhook.startswith("https://discord.com/api/webhooks/"):
            await interaction.response.send_message(
                "❌ Webhook inválido. Use o formato `https://discord.com/api/webhooks/...`",
                ephemeral=True,
            )
            return

        command = f"TRACKER START {target} {webhook}"
        success = await self.manager.send_raw_command(self.admin_id, command)

        if success:
            bot = interaction.client
            tracker_state = getattr(bot, "_tracker_state", {})
            tracker_state[self.admin_id] = {
                "target_id": int(target),
                "webhook_url": webhook,
            }
            bot._tracker_state = tracker_state

            await interaction.response.send_message(
                f"✅ Tracker ativado!\n"
                f"**Alvo:** `{target}`\n"
                f"**Logs:** Webhook configurado\n\n"
                f"Todas as atividades serão logadas via webhook.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Selfbot não está rodando.", ephemeral=True
            )


class TrackerStopModal(ui.View):
    def __init__(self, manager, admin_id: int):
        super().__init__(timeout=60)
        self.manager = manager
        self.admin_id = admin_id

    @ui.button(label="Desativar Tracker", style=discord.ButtonStyle.danger)
    async def stop_tracker(self, interaction: discord.Interaction, button: ui.Button):
        success = await self.manager.send_raw_command(self.admin_id, "TRACKER STOP")

        if success:
            bot = interaction.client
            tracker_state = getattr(bot, "_tracker_state", {})
            tracker_state.pop(self.admin_id, None)
            bot._tracker_state = tracker_state
            await interaction.response.send_message(
                "🛑 Tracker desativado.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Falha ao comunicar com o selfbot.", ephemeral=True
            )
