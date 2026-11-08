import json

import discord
from discord import ui


async def _subscribe_protection_events(interaction, manager, user_id: int) -> None:
    notification_user = interaction.user
    bot_id = interaction.client.user.id

    async def on_protection_event(text: str):
        try:
            data = json.loads(text.removeprefix("EVENT:PROTECTION:"))
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict):
            return
        try:
            author_id = int(data.get("author_id", 0) or 0)
        except (TypeError, ValueError):
            author_id = 0

        from ..shared.stealth_dm import send_stealth_dm_to_user

        event = str(data.get("event", "ACTION"))
        action_labels = {
            "blocked": "usuario bloqueado",
            "already_blocked": "usuario ja estava bloqueado",
            "dm_closed": "DM fechada",
            "dm_already_closed": "DM ja estava fechada",
            "request_rejected": "pedido rejeitado",
            "request_already_gone": "pedido ja removido",
        }
        raw_actions = data.get("actions", [])
        if not isinstance(raw_actions, list):
            raw_actions = []
        actions = [action_labels.get(str(action), str(action)) for action in raw_actions[:10]]
        title = "🛡️ Alerta de contato protegido" if event == "TRUSTED_DETECTED" else "🛡️ Proteção executada"
        author_name = discord.utils.escape_mentions(
            discord.utils.escape_markdown(str(data.get("author_name", "Desconhecido")))
        )[:100]
        reason = discord.utils.escape_mentions(discord.utils.escape_markdown(str(data.get("reason", "Nao informado"))))[
            :300
        ]
        desc = (
            f"Tipo: **{str(data.get('threat', 'UNKNOWN'))[:50]}**\n"
            f"Usuário: **{author_name}**\n"
            f"ID: `{author_id}`\n"
            f"Motivo: {reason}\n"
            f"Ações: {', '.join(actions) if actions else 'nenhuma (contato confiavel)'}"
        )
        await send_stealth_dm_to_user(
            notification_user,
            bot_id,
            title,
            desc,
            delay=120,
        )

    set_listener = getattr(manager, "set_protection_listener", None)
    if callable(set_listener):
        set_listener(user_id, on_protection_event)
    else:
        manager.subscribe_events(user_id, "EVENT:PROTECTION:", on_protection_event)


async def authenticate_and_respond(
    interaction: discord.Interaction,
    manager,
    user_id: int,
    token: str,
    *,
    on_complete=None,
) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    success, message = await manager.authenticate(user_id, token)
    if not success:
        await interaction.followup.send(f"❌ {message}", ephemeral=True)
        return

    await interaction.followup.send(message, ephemeral=True)
    await _subscribe_protection_events(interaction, manager, user_id)
    if on_complete:
        await on_complete(interaction)


class AuthenticateModal(ui.Modal, title="Autenticar Selfbot"):
    token = ui.TextInput(
        label="Token da Conta",
        placeholder="Insira seu token de usuário...",
        min_length=30,
        max_length=512,
        style=discord.TextStyle.short,
    )

    def __init__(self, manager, user_id, *, on_complete=None):
        super().__init__()
        self.manager = manager
        self.user_id = user_id
        self.on_complete = on_complete

    async def on_submit(self, interaction: discord.Interaction):
        await authenticate_and_respond(
            interaction,
            self.manager,
            self.user_id,
            self.token.value,
            on_complete=self.on_complete,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id
