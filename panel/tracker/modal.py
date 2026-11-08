from __future__ import annotations

import discord
from discord import ui
from tracker_protocol import MAX_TRACKER_TARGETS, normalize_target_ids


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
            "Acompanha mensagens, mídias, voz, perfis públicos e interações nos "
            "canais de servidores acessíveis à sua conta. Histórico inicial: últimos 7 dias, "
            "até 100 mensagens por servidor e 500 por usuário."
        )
    else:
        embed.description = f"Informe até {MAX_TRACKER_TARGETS} IDs para acompanhar usuários em servidores acessíveis."

    if status.get("target_id"):
        embed.add_field(name="Alvo", value=f"`{int(status['target_id'])}`", inline=False)
    elif status.get("target_ids"):
        embed.add_field(
            name="Usuários", value="\n".join(f"`{int(item)}`" for item in status["target_ids"]), inline=False
        )
    embed.add_field(
        name="Mensagens ao vivo / histórico / mídias",
        value=f"{status.get('messages_seen', 0)} / {status.get('history_messages', 0)} / {status.get('media_seen', 0)}",
        inline=False,
    )
    history_labels = {
        "idle": "Parado",
        "pending": "Aguardando",
        "running": "Buscando",
        "complete": "Busca concluída",
        "partial": "Busca com falhas",
        "limited": "Limite atingido",
        "cancelled": "Interrompido",
    }
    targets = status.get("targets", [status] if status.get("target_id") else [])
    if targets:
        lines = []
        for target in targets:
            label = history_labels.get(target.get("history_status"), "Aguardando")
            connections = (
                str(target.get("public_connections", 0))
                if target.get("profile_status") == "available"
                else "perfil indisponível/pendente"
            )
            lines.append(
                f"`{target['target_id']}`: {label}; {target.get('history_messages', 0)} msgs; conexões: {connections}; {target.get('history_guilds_limited', 0)} servidores no limite"
            )
        for offset in range(0, len(lines), 5):
            embed.add_field(name="Coleta por usuário", value="\n".join(lines[offset : offset + 5]), inline=False)
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
    if (
        failures
        or subscription_errors
        or status.get("history_errors")
        or status.get("profile_errors")
        or status.get("event_errors")
    ):
        embed.add_field(
            name="Falhas nesta sessão",
            value=(
                f"Webhook: **{failures}** • Assinaturas: **{subscription_errors}**\n"
                f"Histórico: **{status.get('history_errors', 0)}** • Perfil: **{status.get('profile_errors', 0)}**\n"
                f"Eventos: **{status.get('event_errors', 0)}**\n"
                f"Última: `{_safe(status.get('last_error'), 80)}`"
            ),
            inline=False,
        )
    embed.set_footer(
        text="Voz e mudanças de perfil: somente durante a observação. Mídias são links; podem expirar. DMs excluídas."
    )
    return embed


class TrackerControlView(ui.View):
    def __init__(self, manager, user_id: int, status: dict, webhook_url: str | None):
        super().__init__(timeout=300)
        self.manager = manager
        self.user_id = int(user_id)
        self.webhook_url = webhook_url
        self.target_ids = status.get("target_ids") or ([status["target_id"]] if status.get("target_id") else [])

        if status.get("enabled"):
            stop = ui.Button(label="Desativar Tracker", emoji="⏹️", style=discord.ButtonStyle.danger)
            stop.callback = self._stop
            self.add_item(stop)
            edit = ui.Button(
                label="Editar usuários", emoji="👥", style=discord.ButtonStyle.secondary, disabled=not bool(webhook_url)
            )
            edit.callback = self._start
            self.add_item(edit)
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
        await interaction.response.send_message("Este controle pertence a outro usuario.", ephemeral=True)
        return False

    async def _edit(self, interaction: discord.Interaction, result=None):
        status = await self.manager.tracker_status(self.user_id)
        await interaction.edit_original_response(
            embed=build_tracker_embed(status, result),
            view=TrackerControlView(self.manager, self.user_id, status, self.webhook_url),
        )

    async def _start(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        if not self.webhook_url:
            return await interaction.response.send_message(
                "Configure primeiro o webhook em Configurações.", ephemeral=True
            )
        await interaction.response.send_modal(
            TrackerModal(self.manager, self.user_id, self.webhook_url, self.target_ids)
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
        label="IDs dos usuários (um por linha ou vírgula)",
        style=discord.TextStyle.paragraph,
        placeholder="123456789012345678, 987654321098765432",
        required=True,
        min_length=15,
        max_length=220,
    )

    def __init__(self, manager, user_id: int, webhook_url: str, target_ids=None):
        super().__init__()
        self.manager = manager
        self.user_id = int(user_id)
        self.webhook_url = webhook_url
        self.target_id.default = "\n".join(str(item) for item in (target_ids or []))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_id = normalize_target_ids(self.target_id.value)
        except ValueError as error:
            return await interaction.response.send_message(str(error), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        result = await self.manager.tracker_start(self.user_id, target_id, self.webhook_url)
        status = await self.manager.tracker_status(self.user_id)
        await interaction.followup.send(
            embed=build_tracker_embed(status, result),
            view=TrackerControlView(self.manager, self.user_id, status, self.webhook_url),
            ephemeral=True,
        )


class TrackerStopModal(TrackerControlView):
    """Compatibilidade com caminhos antigos do painel."""

    def __init__(self, manager, user_id: int):
        super().__init__(manager, user_id, {"enabled": True}, None)
