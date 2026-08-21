from __future__ import annotations

import discord
from discord import ui


def _safe(value, limit: int = 500) -> str:
    text = " ".join(str(value or "Desconhecido").split())
    text = discord.utils.escape_mentions(discord.utils.escape_markdown(text))
    return text[:limit] or "Desconhecido"


def build_tracker_embed(status: dict, result: dict | None = None) -> discord.Embed:
    connected = bool(status.get("ok"))
    enabled = bool(status.get("enabled"))
    embed = discord.Embed(
        title=f"🎯 Tracker {'Ativo' if enabled else 'Desativado'}",
        color=0x2ECC71 if enabled else (0x95A5A6 if connected else 0xE74C3C),
    )
    if result:
        embed.description = f"{'✅' if result.get('ok') else '⚠️'} {_safe(result.get('message'))}"
    elif not connected:
        embed.description = f"❌ {_safe(status.get('message'))}"
    elif enabled:
        embed.description = (
            "Capturas são processadas por uma fila limitada; rajadas não bloqueiam "
            "a conexão do Discord."
        )
    else:
        embed.description = "Informe o ID do alvo para iniciar o monitoramento."

    if status.get("target_id"):
        embed.add_field(name="Alvo", value=f"`{int(status['target_id'])}`", inline=False)
    embed.add_field(
        name="Eventos / enviados",
        value=f"**{int(status.get('events_captured', 0))}** / **{int(status.get('webhooks_sent', 0))}**",
        inline=True,
    )
    embed.add_field(
        name="Na fila / descartados",
        value=f"**{int(status.get('queue_size', 0))}** / **{int(status.get('events_dropped', 0))}**",
        inline=True,
    )
    embed.add_field(
        name="Servidores em comum",
        value=str(int(status.get("shared_guilds", 0))),
        inline=True,
    )
    failures = int(status.get("webhooks_failed", 0))
    subscription_errors = int(status.get("subscription_errors", 0))
    if failures or subscription_errors:
        embed.add_field(
            name="Falhas nesta sessão",
            value=(
                f"Webhook: **{failures}** • Assinaturas: **{subscription_errors}**\n"
                f"Última: `{_safe(status.get('last_error'), 80)}`"
            ),
            inline=False,
        )
    embed.set_footer(
        text="Presença depende das assinaturas e da visibilidade disponibilizada pelo Discord."
    )
    return embed


class TrackerControlView(ui.View):
    def __init__(self, manager, user_id: int, status: dict, webhook_url: str | None):
        super().__init__(timeout=300)
        self.manager = manager
        self.user_id = int(user_id)
        self.webhook_url = webhook_url

        if status.get("enabled"):
            stop = ui.Button(
                label="Desativar Tracker", emoji="⏹️", style=discord.ButtonStyle.danger
            )
            stop.callback = self._stop
            self.add_item(stop)
        else:
            start = ui.Button(
                label="Ativar Tracker",
                emoji="🎯",
                style=discord.ButtonStyle.success,
                disabled=not bool(webhook_url),
            )
            start.callback = self._start
            self.add_item(start)

        refresh = ui.Button(label="Atualizar", emoji="🔄", style=discord.ButtonStyle.secondary)
        refresh.callback = self._refresh_status
        self.add_item(refresh)

    async def _owned(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "Este controle pertence a outro usuario.", ephemeral=True
        )
        return False

    async def _edit(self, interaction: discord.Interaction, result=None):
        status = await self.manager.tracker_status(self.user_id)
        await interaction.edit_original_response(
            embed=build_tracker_embed(status, result),
            view=TrackerControlView(
                self.manager, self.user_id, status, self.webhook_url
            ),
        )

    async def _start(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        if not self.webhook_url:
            return await interaction.response.send_message(
                "Configure primeiro o webhook em Configurações.", ephemeral=True
            )
        await interaction.response.send_modal(
            TrackerModal(self.manager, self.user_id, self.webhook_url)
        )

    async def _stop(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await interaction.response.defer()
        result = await self.manager.tracker_stop(self.user_id)
        await self._edit(interaction, result)

    async def _refresh_status(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await interaction.response.defer()
        await self._edit(interaction)


class TrackerModal(ui.Modal, title="Configurar Tracker"):
    target_id = ui.TextInput(
        label="ID do alvo",
        style=discord.TextStyle.short,
        placeholder="123456789012345678",
        required=True,
        min_length=15,
        max_length=20,
    )

    def __init__(self, manager, user_id: int, webhook_url: str):
        super().__init__()
        self.manager = manager
        self.user_id = int(user_id)
        self.webhook_url = webhook_url

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_id = int(self.target_id.value.strip())
            if target_id <= 0 or target_id >= 2**64:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "O ID do alvo deve ser numérico e válido.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        result = await self.manager.tracker_start(
            self.user_id, target_id, self.webhook_url
        )
        status = await self.manager.tracker_status(self.user_id)
        await interaction.followup.send(
            embed=build_tracker_embed(status, result),
            view=TrackerControlView(
                self.manager, self.user_id, status, self.webhook_url
            ),
            ephemeral=True,
        )


class TrackerStopModal(TrackerControlView):
    """Compatibilidade com caminhos antigos do painel."""

    def __init__(self, manager, user_id: int):
        super().__init__(manager, user_id, {"enabled": True}, None)
