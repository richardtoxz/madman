import discord
from discord import ui


class AuthenticateModal(ui.Modal, title="Autenticar Selfbot"):
    token = ui.TextInput(
        label="Token da Conta",
        placeholder="Insira seu token de usuário...",
        min_length=30,
        style=discord.TextStyle.short,
    )

    def __init__(self, manager, user_id, *, on_complete=None):
        super().__init__()
        self.manager = manager
        self.user_id = user_id
        self.on_complete = on_complete

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        success, msg = await self.manager.authenticate(self.user_id, self.token.value)

        if success:
            await interaction.followup.send(msg, ephemeral=True)

            async def on_protection_event(text: str):
                parts = text.split(":", 4)
                if len(parts) >= 5:
                    event_type = parts[1]
                    author_name = parts[2]
                    author_id = parts[3]
                    reason = parts[4]

                    from utils.stealth_dm import send_stealth_dm

                    title = "🛡️ Proteção Ativada"
                    desc = (
                        f"Ação: **{event_type}**\n"
                        f"Usuário: `{author_name}`\n"
                        f"ID: `{author_id}`\n"
                        f"Motivo: {reason}"
                    )
                    await send_stealth_dm(interaction, title, desc, delay=120)

            self.manager.subscribe_events(
                self.user_id, "PROTECTION:BLOCKED:", on_protection_event
            )
            self.manager.subscribe_events(
                self.user_id, "PROTECTION:CLOSED:", on_protection_event
            )

            if self.on_complete:
                await self.on_complete(interaction)
        else:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
