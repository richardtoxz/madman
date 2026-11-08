import discord
from discord import ui
from ipc_protocol import parse_cleanup_result
from .progress_view import ProgressView
from ..shared.helpers import generate_progress_bar
from ..shared.stealth_dm import open_temporary_dm
from ..shared.tasks import create_panel_task


def _cleanup_summary(result: str, fallback_total: int = 0) -> tuple[str, int, int]:
    parsed = parse_cleanup_result(result, fallback_total=fallback_total)
    return parsed["outcome"], int(parsed["deleted"]), int(parsed["total"])


class CleanDMModal(ui.Modal, title="Limpar Mensagens (DM/Canal)"):
    target_id = ui.TextInput(label="ID do Usuário ou Canal", placeholder="Insira o ID aqui...", min_length=15)

    def __init__(self, manager, user_id: int):
        super().__init__()
        self.manager = manager
        self.user_id = int(user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_submit(self, interaction: discord.Interaction):
        raw_id = self.target_id.value.strip()

        if not raw_id.isdigit():
            return await interaction.response.send_message(
                "❌ ID do Usuário ou Canal deve ser apenas números!", ephemeral=True
            )

        if not (17 <= len(raw_id) <= 21):
            return await interaction.response.send_message(
                "❌ ID inválido! O tamanho deve ser entre 17 e 21 dígitos.",
                ephemeral=True,
            )

        target_id_val = int(raw_id)
        user_id = interaction.user.id

        embed = discord.Embed(title="🧹 Central de Limpeza Madman", color=0x2B2D31)
        embed.description = "Calculando total de mensagens..."

        view = ProgressView(user_id, self.manager)
        dm = await open_temporary_dm(interaction, embed=embed, view=view)
        if dm is None:
            view.stop()
            return
        view.dm = dm

        view.channel_info = {"name": str(target_id_val), "total": 0}

        async def update_progress(current, total, status, extra_data=None):
            nonlocal embed
            channel_info = view.channel_info

            if view.cancelled_by_user:
                return

            if status == "CHANNEL_DETECTED" and extra_data:
                channel_info["name"] = extra_data
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Detectado! Contando mensagens..."
                embed.color = 0x9B59B6
            elif status == "COUNTING":
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Contando suas mensagens... 🔍"
                embed.color = 0x9B59B6
            elif status == "WAITING_START":
                if total is not None:
                    channel_info["total"] = total
                embed.description = (
                    f"**Alvo:** {channel_info['name']}\n"
                    f"**Estimativa inicial:** {channel_info['total']} mensagens.\n"
                    "O total pode ser ajustado durante a limpeza.\n\n"
                    "Clique abaixo para iniciar."
                )
                embed.color = 0xF1C40F
                view.total_ready = True
                view.update_buttons()
            elif status == "CLEANING":
                if total is not None:
                    channel_info["total"] = total
                bar = generate_progress_bar(current, channel_info["total"])
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Limpando... 🧹\n\n{bar}\n`[{current}/{channel_info['total']}]`"
                embed.color = 0x3498DB
                view.started = True
                view.update_buttons()
            elif status == "RATE_LIMITED":
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** ⏳ Rate limit, aguardando..."
                embed.color = 0xE74C3C
            elif status == "PAUSED":
                embed.description = (
                    f"**Alvo:** {channel_info['name']}\n**Status:** Pausado ⏸️\n`[{current}/{channel_info['total']}]`"
                )
                embed.color = 0xE74C3C
            elif status == "STARTED":
                embed.description = f"**Alvo:** {channel_info['name']}\n**Status:** Iniciando limpeza..."
                embed.color = 0x3498DB
            elif status == "TARGET_TYPE" and extra_data:
                view.target_type = extra_data

            should_throttle = bool(
                status == "CLEANING"
                and current is not None
                and channel_info["total"]
                and current < channel_info["total"]
            )
            try:
                await dm.edit(embed=embed, view=view, throttle=should_throttle)
            except Exception:
                pass

        create_panel_task(
            interaction.client,
            self._run_cleanup(user_id, target_id_val, update_progress, view, dm),
            name=f"panel-clean-target-{user_id}-{target_id_val}",
        )

    async def _run_cleanup(self, user_id, target_id, callback, view, dm):
        try:
            result = await self.manager.send_command(user_id, target_id, update_callback=callback)
        except Exception:
            await dm.finish(content="❌ Falha inesperada ao acompanhar a limpeza.")
            raise
        outcome, deleted_count, total_count = _cleanup_summary(result, int(view.channel_info.get("total", 0) or 0))

        target_name = view.channel_info.get("name", str(target_id))
        mention = f"<#{target_id}>" if view.target_type == "channel" else f"<@{target_id}>"

        if outcome == "error":
            final_text = f"❌ **Erro na limpeza do alvo ({mention}):**\n{result}"
        elif view.cancelled_by_user or outcome == "cancelled":
            final_text = f"⏹️ **Processo interrompido**\n**{deleted_count}** de **{total_count}** apagadas de **{target_name}** ({mention})"
        elif outcome == "partial":
            final_text = (
                f"⚠️ **Limpeza parcial em {target_name} ({mention})**\n"
                f"**{deleted_count}** de **{total_count}** apagadas.\n{result}"
            )
        else:
            final_text = (
                f"✅ **{target_name} ({mention}) apagada com sucesso**\nTotal: **{deleted_count}** de **{total_count}**"
            )

        await dm.finish(content=final_text)


class CleanGuildModal(ui.Modal, title="Limpar Mensagens em Servidor"):
    guild_id_input = ui.TextInput(
        label="ID do Servidor",
        placeholder="Guild ID para limpar suas msgs...",
        min_length=15,
        max_length=21,
    )

    def __init__(self, manager, user_id: int):
        super().__init__()
        self.manager = manager
        self.user_id = int(user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            guild_id = int(self.guild_id_input.value.strip())
        except ValueError:
            return await interaction.response.send_message("❌ ID deve ser numérico!", ephemeral=True)

        user_id = interaction.user.id
        embed = discord.Embed(title="🏠 Limpeza de Guild", color=0x2B2D31)
        embed.description = f"Buscando suas mensagens na guild `{guild_id}`..."
        view = ProgressView(user_id, self.manager)
        dm = await open_temporary_dm(interaction, embed=embed, view=view)
        if dm is None:
            view.stop()
            return
        view.dm = dm
        view.channel_info = {"name": str(guild_id), "total": 0}

        async def update_progress(current, total, status, extra_data=None):
            nonlocal embed
            ci = view.channel_info

            if view.cancelled_by_user:
                return

            if status == "CHANNEL_DETECTED" and extra_data:
                ci["name"] = extra_data
                embed.description = f"**Guild:** {ci['name']}\n**Status:** Contando mensagens..."
                embed.color = 0x9B59B6
            elif status == "COUNTING":
                embed.description = f"**Guild:** {ci['name']}\n**Status:** Contando... 🔍"
                embed.color = 0x9B59B6
            elif status == "WAITING_START":
                if total is not None:
                    ci["total"] = total
                embed.description = (
                    f"**Guild:** {ci['name']}\n**Total:** {ci['total']} mensagens\n\nClique ▶️ para iniciar."
                )
                embed.color = 0xF1C40F
                view.total_ready = True
                view.update_buttons()
            elif status == "CLEANING":
                if total is not None:
                    ci["total"] = total
                bar = generate_progress_bar(current, ci["total"])
                embed.description = (
                    f"**Guild:** {ci['name']}\n**Status:** Limpando... 🧹\n\n{bar}\n`[{current}/{ci['total']}]`"
                )
                embed.color = 0x3498DB
                view.started = True
                view.update_buttons()
            elif status == "RATE_LIMITED":
                embed.description = f"**Guild:** {ci['name']}\n**Status:** ⏳ Rate limit..."
                embed.color = 0xE74C3C
            elif status == "STARTED":
                embed.description = f"**Guild:** {ci['name']}\n**Status:** Iniciando limpeza..."
                embed.color = 0x3498DB
            elif status == "TARGET_TYPE" and extra_data:
                view.target_type = extra_data

            should_throttle = bool(
                status == "CLEANING" and current is not None and ci["total"] and current < ci["total"]
            )
            try:
                await dm.edit(embed=embed, view=view, throttle=should_throttle)
            except Exception:
                pass

        create_panel_task(
            interaction.client,
            self._run(user_id, guild_id, update_progress, view, dm),
            name=f"panel-clean-guild-{user_id}-{guild_id}",
        )

    async def _run(self, user_id, guild_id, callback, view, dm):
        try:
            result = await self.manager.clean_guild_messages(user_id, guild_id, update_callback=callback)
        except Exception:
            await dm.finish(content="❌ Falha inesperada ao acompanhar a limpeza.")
            raise
        outcome, deleted_count, total_count = _cleanup_summary(result, int(view.channel_info.get("total", 0) or 0))

        name = view.channel_info.get("name", str(guild_id))

        if view.target_type == "channel":
            mention = f"<#{guild_id}>"
        elif view.target_type == "user":
            mention = f"<@{guild_id}>"
        else:
            mention = f"ID `{guild_id}`"

        if view.cancelled_by_user or outcome == "cancelled":
            final = f"⏹️ **Interrompido**\n**{deleted_count}** de **{total_count}** apagadas em **{name}** ({mention})"
        elif outcome == "error":
            final = f"❌ **Erro:** {result}"
        elif outcome == "partial":
            final = (
                f"⚠️ **Limpeza parcial em {name} ({mention})**\n"
                f"**{deleted_count}** de **{total_count}** deletadas.\n{result}"
            )
        else:
            final = f"✅ **Guild {name} ({mention}) limpa**\n**{deleted_count}** de **{total_count}** deletadas"

        await dm.finish(content=final)
