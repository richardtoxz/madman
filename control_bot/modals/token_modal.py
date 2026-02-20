import discord
from discord import ui


class AuthenticateModal(ui.Modal, title='Autenticar Selfbot'):
    token = ui.TextInput(
        label='Token da Conta',
        placeholder='Insira seu token de usuário...',
        min_length=30,
        style=discord.TextStyle.short,
    )

    def __init__(self, manager, user_id, *, on_complete=None):
        super().__init__()
        self.manager = manager
        self.user_id = user_id
        self.on_complete = on_complete

    async def on_submit(self, interaction: discord.Interaction):
        # Defer — authenticate pode demorar até 30s
        await interaction.response.defer(ephemeral=True)

        # Detecta status do usuário no guild
        status = "online"
        if interaction.guild:
            member = interaction.guild.get_member(self.user_id)
            if member and hasattr(member, 'status'):
                status_map = {
                    discord.Status.online: "online",
                    discord.Status.idle: "idle",
                    discord.Status.dnd: "dnd",
                    discord.Status.offline: "online",
                    discord.Status.invisible: "online",
                }
                status = status_map.get(member.status, "online")

        success, msg = await self.manager.authenticate(
            self.user_id, self.token.value, status=status
        )

        if success:
            await interaction.followup.send(msg, ephemeral=True)
            
            # Registrar listener para eventos de proteção
            # Queremos receber notificações de bloqueio/fechamento
            async def on_protection_event(text: str):
                # PROTECTION:BLOCKED:author_name:author_id:motivo
                # PROTECTION:CLOSED:author_name:author_id:motivo
                parts = text.split(":")
                if len(parts) >= 5:
                    event_type = parts[1] # BLOCKED ou CLOSED
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
                    # Envia DM com auto-destruição de 120s (2 min)
                    await send_stealth_dm(interaction, title, desc, delay=120)

            # Inscreve para ambos os tipos de eventos de proteção
            self.manager.subscribe_events(self.user_id, "PROTECTION:BLOCKED:", on_protection_event)
            self.manager.subscribe_events(self.user_id, "PROTECTION:CLOSED:", on_protection_event)

            # Atualiza a view principal se callback fornecido
            if self.on_complete:
                await self.on_complete(interaction)
        else:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
