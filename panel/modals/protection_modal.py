from __future__ import annotations

import re

import discord
from discord import ui


def _safe(value, limit: int = 500) -> str:
    text = " ".join(str(value or "Desconhecido").split())
    text = discord.utils.escape_mentions(discord.utils.escape_markdown(text))
    return text[:limit] or "Desconhecido"


def build_protection_embed(status: dict, result: dict | None = None) -> discord.Embed:
    connected = bool(status.get("ok"))
    enabled = bool(status.get("enabled"))
    embed = discord.Embed(
        title=f"🛡️ Protecoes {'Ativas' if enabled else 'Desativadas'}",
        color=0x2ECC71 if enabled else (0x95A5A6 if connected else 0xE74C3C),
    )

    if result:
        marker = "✅" if result.get("ok") else "⚠️"
        embed.description = f"{marker} {_safe(result.get('message'))}"
    elif not connected:
        embed.description = f"❌ {_safe(status.get('message'))}"
    else:
        embed.description = (
            "Links e anexos de alto risco sao analisados em DMs. Amigos, bots do "
            "sistema e IDs da whitelist nunca sofrem acao automatica."
        )

    embed.add_field(
        name="Deteccoes / acoes",
        value=(
            f"**{int(status.get('threats_detected', 0))}** / "
            f"**{int(status.get('actions_taken', 0))}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Golpes bloqueados",
        value=str(int(status.get("scams_blocked", 0))),
        inline=True,
    )
    embed.add_field(
        name="Spam de convite",
        value=str(int(status.get("spam_closed", 0))),
        inline=True,
    )
    embed.add_field(
        name="Floods de DM / pedidos",
        value=(
            f"**{int(status.get('dm_floods', 0))}** / "
            f"**{int(status.get('fr_floods', 0))}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Pedidos rejeitados",
        value=str(int(status.get("fr_rejected", 0))),
        inline=True,
    )
    embed.add_field(
        name="Whitelist",
        value=f"{int(status.get('whitelist_count', 0))} usuario(s)",
        inline=True,
    )

    limits = status.get("limits") or {}
    if limits:
        embed.add_field(
            name="Limites automaticos",
            value=(
                f"DM: **{int(limits.get('dm_burst_messages', 0))}** mensagens / "
                f"**{int(limits.get('dm_burst_seconds', 0))}s** ou "
                f"**{int(limits.get('dm_raid_users', 0))}** remetentes / "
                f"**{int(limits.get('dm_raid_seconds', 0))}s**\n"
                f"Convites: **{int(limits.get('invite_messages', 0))}** / "
                f"**{int(limits.get('invite_seconds', 0))}s** • Pedidos: "
                f"**{int(limits.get('friend_requests', 0))}** / "
                f"**{int(limits.get('friend_request_seconds', 0))}s**"
            ),
            inline=False,
        )

    errors = int(status.get("errors", 0))
    if errors:
        embed.add_field(
            name="Falhas de API nesta sessao",
            value=f"{errors} — ultima: `{_safe(status.get('last_error'), 100)}`",
            inline=False,
        )
    embed.set_footer(
        text="Um TLD incomum ou um unico convite, isoladamente, nao dispara bloqueio."
    )
    return embed


class ProtectionControlView(ui.View):
    def __init__(self, manager, user_id: int, status: dict):
        super().__init__(timeout=300)
        self.manager = manager
        self.user_id = int(user_id)

        enable = ui.Button(
            label="Ativar",
            emoji="✅",
            style=discord.ButtonStyle.success,
            disabled=bool(status.get("enabled")),
        )
        enable.callback = self._enable
        self.add_item(enable)

        disable = ui.Button(
            label="Desativar",
            emoji="⏹️",
            style=discord.ButtonStyle.danger,
            disabled=not bool(status.get("enabled")),
        )
        disable.callback = self._disable
        self.add_item(disable)

        whitelist = ui.Button(
            label="Editar whitelist",
            emoji="📝",
            style=discord.ButtonStyle.secondary,
        )
        whitelist.callback = self._whitelist
        self.add_item(whitelist)

        refresh = ui.Button(
            label="Atualizar",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
        )
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
        status = await self.manager.protection_status(self.user_id)
        await interaction.edit_original_response(
            embed=build_protection_embed(status, result),
            view=ProtectionControlView(self.manager, self.user_id, status),
        )

    async def _enable(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await interaction.response.defer()
        result = await self.manager.protection_enable(self.user_id)
        await self._edit(interaction, result)

    async def _disable(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await interaction.response.defer()
        result = await self.manager.protection_disable(self.user_id)
        await self._edit(interaction, result)

    async def _whitelist(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        current = self.manager.get_whitelist(self.user_id)
        await interaction.response.send_modal(
            ProtectionWhitelistModal(self.manager, self.user_id, current)
        )

    async def _refresh_status(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await interaction.response.defer()
        await self._edit(interaction)


class ProtectionWhitelistModal(ui.Modal, title="Whitelist das Protecoes"):
    user_ids = ui.TextInput(
        label="IDs protegidos",
        placeholder="Um ID por linha, ou separados por virgula. Vazio limpa a lista.",
        required=False,
        max_length=4000,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, manager, user_id: int, current):
        super().__init__()
        self.manager = manager
        self.user_id = int(user_id)
        self.user_ids.default = "\n".join(str(target_id) for target_id in current)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_submit(self, interaction: discord.Interaction):
        parts = [
            part
            for part in re.split(r"[\s,;]+", self.user_ids.value.strip())
            if part
        ]
        try:
            whitelist = sorted({int(part) for part in parts})
            if len(whitelist) > 150 or any(
                target_id <= 0 or target_id >= 2**64 for target_id in whitelist
            ):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "Use no maximo 150 IDs numericos validos.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        result = await self.manager.protection_configure(self.user_id, whitelist)
        status = await self.manager.protection_status(self.user_id)
        await interaction.followup.send(
            embed=build_protection_embed(status, result),
            view=ProtectionControlView(self.manager, self.user_id, status),
            ephemeral=True,
        )
