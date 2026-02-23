import discord
from discord import ui


class TrackerModal(ui.Modal, title="Configurar Tracker"):
    target_id = ui.TextInput(
        label="ID do Alvo",
        style=discord.TextStyle.short,
        placeholder="123456789012345678",
        required=True,
        min_length=17,
        max_length=20
    )

    channel_id = ui.TextInput(
        label="Channel ID (para logs de mensagens)",
        style=discord.TextStyle.short,
        placeholder="123456789012345678",
        required=True,
        min_length=17,
        max_length=20
    )

    def __init__(self, manager, admin_id: int):
        super().__init__()
        self.manager = manager
        self.admin_id = admin_id

    async def on_submit(self, interaction: discord.Interaction):
        target = self.target_id.value.strip()
        channel = self.channel_id.value.strip()

        if not target.isdigit():
            await interaction.response.send_message("❌ O ID do Alvo deve ser numérico.", ephemeral=True)
            return

        if not channel.isdigit():
            await interaction.response.send_message("❌ O Channel ID deve ser numérico.", ephemeral=True)
            return

        command = f"TRACKER START {target} {channel}"
        success = await self.manager.send_raw_command(self.admin_id, command)

        if success:
            # Registra a subscription de eventos no control bot
            from views.tracker_view import TrackerContextView
            bot = interaction.client

            tracker_state = getattr(bot, '_tracker_state', {})
            tracker_state[self.admin_id] = {
                "target_id": int(target),
                "channel_id": int(channel),
            }
            bot._tracker_state = tracker_state

            self.manager.subscribe_events(self.admin_id, "TRACKER_EVENT:", bot._on_tracker_event)

            await interaction.response.send_message(
                f"✅ Tracker ativado!\n"
                f"**Alvo:** `{target}`\n"
                f"**Canal de Logs:** <#{channel}>\n\n"
                f"Mensagens serão agrupadas e enviadas com contexto expansível.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ Selfbot não está rodando.", ephemeral=True)


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
            # Remove o estado do tracker para que o painel mostre como inativo
            tracker_state = getattr(bot, '_tracker_state', {})
            tracker_state.pop(self.admin_id, None)
            bot._tracker_state = tracker_state
            # Cancela a escuta dos eventos de tracker
            try:
                self.manager.unsubscribe_events(self.admin_id, "TRACKER_EVENT:", bot._on_tracker_event)
            except Exception:
                pass
            await interaction.response.send_message("🛑 Tracker desativado.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Falha ao comunicar com o selfbot.", ephemeral=True)

