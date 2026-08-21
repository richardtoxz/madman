from __future__ import annotations

import discord
from discord import ui


def _safe(value, limit: int = 100) -> str:
    text = " ".join(str(value or "Desconhecido").split())
    text = discord.utils.escape_mentions(discord.utils.escape_markdown(text))
    return text[:limit] or "Desconhecido"


def build_voice_embed(status: dict, result: dict | None = None) -> discord.Embed:
    ok = bool(status.get("ok"))
    mode = str(status.get("mode", "IDLE")).upper()
    colors = {"FOLLOW": 0x9B59B6, "AFK": 0x3498DB, "IDLE": 0x95A5A6}
    labels = {
        "FOLLOW": "Seguindo usuario",
        "AFK": "AFK em voz",
        "IDLE": "Fora da voz",
    }
    embed = discord.Embed(
        title="🎙️ Controle de Voz",
        color=colors.get(mode, 0x2ECC71 if ok else 0xE74C3C),
    )

    if result:
        marker = "✅" if result.get("ok") else "⚠️"
        embed.description = f"{marker} {_safe(result.get('message'), 500)}"
    elif not ok:
        embed.description = f"❌ {_safe(status.get('message'), 500)}"

    embed.add_field(name="Modo", value=labels.get(mode, mode), inline=True)
    embed.add_field(
        name="Conectado",
        value="Sim" if status.get("in_voice") else "Nao",
        inline=True,
    )
    if status.get("channel_id"):
        embed.add_field(
            name="Canal",
            value=(
                f"**{_safe(status.get('channel_name'))}**\n"
                f"`{int(status['channel_id'])}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Servidor",
            value=_safe(status.get("guild_name")),
            inline=False,
        )
    elif mode == "FOLLOW":
        embed.add_field(
            name="Estado",
            value="Aguardando o alvo entrar em um canal compartilhado.",
            inline=False,
        )

    if status.get("target_id"):
        embed.add_field(
            name="Alvo do follow",
            value=f"`{int(status['target_id'])}`",
            inline=False,
        )

    if status.get("self_mute") is not None:
        flags = (
            f"Microfone: {'desativado' if status.get('self_mute') else 'ativo'} • "
            f"Audio: {'desativado' if status.get('self_deaf') else 'ativo'}"
        )
        embed.set_footer(text=flags)
    return embed


class VoiceControlView(ui.View):
    def __init__(self, manager, user_id: int, status: dict):
        super().__init__(timeout=300)
        self.manager = manager
        self.user_id = int(user_id)

        if status.get("following"):
            stop = ui.Button(
                label="Parar de seguir",
                emoji="⏹️",
                style=discord.ButtonStyle.danger,
            )
            stop.callback = self._stop_follow
            self.add_item(stop)
        elif status.get("in_voice"):
            leave = ui.Button(
                label="Desconectar da voz",
                emoji="🔌",
                style=discord.ButtonStyle.danger,
            )
            leave.callback = self._leave
            self.add_item(leave)

        refresh = ui.Button(
            label="Atualizar estado",
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
        status = await self.manager.voice_status(self.user_id)
        await interaction.edit_original_response(
            embed=build_voice_embed(status, result),
            view=VoiceControlView(self.manager, self.user_id, status),
        )

    async def _leave(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await interaction.response.defer()
        result = await self.manager.voice_leave(self.user_id)
        await self._edit(interaction, result)

    async def _stop_follow(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await interaction.response.defer()
        result = await self.manager.voice_unfollow(self.user_id)
        await self._edit(interaction, result)

    async def _refresh_status(self, interaction: discord.Interaction):
        if not await self._owned(interaction):
            return
        await interaction.response.defer()
        await self._edit(interaction)


class VoiceAFKModal(ui.Modal, title="Farmar Voz"):
    channel_id = ui.TextInput(
        label="ID do canal",
        placeholder="ID do canal de voz para conectar...",
        min_length=15,
        max_length=25,
        style=discord.TextStyle.short,
    )

    def __init__(self, manager, user_id):
        super().__init__()
        self.manager = manager
        self.user_id = int(user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            channel_id = int(self.channel_id.value.strip())
            if channel_id <= 0 or channel_id >= 2**64:
                raise ValueError
        except ValueError:
            return await interaction.followup.send(
                "ID de canal invalido.", ephemeral=True
            )

        result = await self.manager.voice_join(self.user_id, channel_id)
        status = await self.manager.voice_status(self.user_id)
        await interaction.followup.send(
            embed=build_voice_embed(status, result),
            view=VoiceControlView(self.manager, self.user_id, status),
            ephemeral=True,
        )


class VoiceFollowModal(ui.Modal, title="Seguir Usuario"):
    target_id = ui.TextInput(
        label="ID do usuario",
        placeholder="ID do usuario para seguir em voz...",
        min_length=15,
        max_length=25,
        style=discord.TextStyle.short,
    )

    def __init__(self, manager, user_id):
        super().__init__()
        self.manager = manager
        self.user_id = int(user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = int(self.target_id.value.strip())
            if target_id <= 0 or target_id >= 2**64:
                raise ValueError
        except ValueError:
            return await interaction.followup.send(
                "ID de usuario invalido.", ephemeral=True
            )

        result = await self.manager.voice_follow(self.user_id, target_id)
        status = await self.manager.voice_status(self.user_id)
        await interaction.followup.send(
            embed=build_voice_embed(status, result),
            view=VoiceControlView(self.manager, self.user_id, status),
            ephemeral=True,
        )
