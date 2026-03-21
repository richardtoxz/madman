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

    def __init__(self, manager, admin_id: int, webhook_url: str):
        super().__init__()
        self.manager = manager
        self.admin_id = admin_id
        self.webhook_url = webhook_url

    async def on_submit(self, interaction: discord.Interaction):
        target = self.target_id.value.strip()

        if not target.isdigit():
            await interaction.response.send_message(
                "❌ O ID do Alvo deve ser numérico.", ephemeral=True
            )
            return

        command = f"TRACKER START {target} {self.webhook_url}"
        success = await self.manager.send_raw_command(self.admin_id, command)

        if success:
            bot = interaction.client
            tracker_state = getattr(bot, "_tracker_state", {})
            tracker_state[self.admin_id] = {
                "target_id": int(target),
                "webhook_url": self.webhook_url,
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
