import discord
from discord import ui

from utils.helpers import generate_progress_bar
from modals.clean_dm_modal import CleanDMModal
from modals.clean_guild_modal import CleanGuildModal
from modals.data_package_modal import DataPackageModal


class MessageCleanupView(ui.View):
    """Menu completo de limpeza de mensagens."""

    def __init__(self, manager, user_id):
        super().__init__(timeout=300)
        self.manager = manager
        self.user_id = user_id

    def _check_active(self):
        return self.user_id in self.manager.active_processes

    def _get_embed(self):
        embed = discord.Embed(
            title="🧹 Central de Limpeza de Mensagens",
            description=(
                "Escolha o tipo de limpeza desejado.\n"
                "Todas as operações respeitam rate limits automaticamente.\n\n"
                "**Delay adaptativo:** ~600ms entre msgs (sobe se tomar 429, desce se fluir)\n"
                "**Proteção:** Auto-abort em rate limit excessivo"
            ),
            color=0x5865F2
        )
        embed.add_field(
            name="📋 Opções",
            value=(
                "🧹 **Limpar DM/Canal** — Suas msgs em 1 DM ou canal\n"
                "🔥 **Todas as DMs** — Suas msgs em todas as DMs abertas\n"
                "🏠 **Limpar Guild** — Suas msgs em todo um servidor\n"
                "📦 **Data Package** — Deleta usando export do Discord\n"
                "🔍 **Scan Guilds** — Descobre em quais guilds você tem msgs\n"
                "📨 **Listar DMs** — Mostra DMs e Groups abertos\n"
                "🚪 **Fechar DMs** — Fecha todas as DMs abertas"
            ),
            inline=False
        )
        return embed

    # =============================================
    # ROW 0: Limpezas principais
    # =============================================

    @ui.button(label="Limpar DM/Canal", style=discord.ButtonStyle.primary, emoji="🧹", row=0)
    async def clean_single(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)
        await interaction.response.send_modal(CleanDMModal(self.manager))

    @ui.button(label="Todas as DMs", style=discord.ButtonStyle.danger, emoji="🔥", row=0)
    async def clean_all_dms(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)

        confirm_view = ui.View(timeout=60)

        async def confirm(inter):
            await inter.response.edit_message(
                content="🚀 Iniciando limpeza de **todas as DMs**...\nIsso pode demorar bastante.",
                view=None
            )

            async def progress(curr, total, status, extra):
                try:
                    if status == "ALL_DMS_CHANNEL":
                        await inter.edit_original_response(
                            content=f"🧹 Limpando DM **{curr}/{total}**: {extra}"
                        )
                    elif status == "ALL_DMS_FOUND":
                        await inter.edit_original_response(
                            content=f"🔍 Encontrado: {extra}"
                        )
                    elif status == "CLEANING" and curr and total:
                        bar = generate_progress_bar(curr, total)
                        await inter.edit_original_response(
                            content=f"🧹 Deletando...\n{bar}\n`[{curr}/{total}]`"
                        )
                    elif status == "ALL_DMS_SKIP":
                        pass  # Silencioso para DMs sem msgs
                except Exception:
                    pass

            result = await self.manager.clean_all_dms(self.user_id, update_callback=progress)
            try:
                await inter.edit_original_response(content=f"✅ **Concluído!**\n{result}")
            except Exception:
                pass

        btn = ui.Button(label="SIM, Limpar Todas DMs", style=discord.ButtonStyle.danger, emoji="⚠️")
        btn.callback = confirm
        confirm_view.add_item(btn)

        await interaction.response.send_message(
            content=(
                "⚠️ **CONFIRMAÇÃO**\n\n"
                "Isso vai apagar suas mensagens em **TODAS** as DMs abertas.\n"
                "Pode demorar **muito** dependendo da quantidade.\n\n"
                "Deseja continuar?"
            ),
            view=confirm_view, ephemeral=True
        )

    @ui.button(label="Limpar Guild", style=discord.ButtonStyle.primary, emoji="🏠", row=0)
    async def clean_guild(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)
        await interaction.response.send_modal(CleanGuildModal(self.manager))


    # =============================================
    # ROW 1: Data Package, Scan, Listar, Fechar
    # =============================================

    @ui.button(label="Data Package", style=discord.ButtonStyle.secondary, emoji="📦", row=1)
    async def data_package(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)
        await interaction.response.send_modal(DataPackageModal(self.manager))

    @ui.button(label="Scan Guilds", style=discord.ButtonStyle.secondary, emoji="🔍", row=1)
    async def scan_guilds(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        async def progress(curr, total, status, extra):
            try:
                if status == "SCAN_CHECKING":
                    await interaction.edit_original_response(
                        content=f"🔍 Verificando guild **{curr}/{total}**: {extra}..."
                    )
                elif status == "SCAN_FOUND":
                    await interaction.edit_original_response(
                        content=f"✅ Encontrado msgs em: **{extra}**"
                    )
            except Exception:
                pass

        results = await self.manager.scan_guilds_for_messages(self.user_id, update_callback=progress)

        if not results:
            return await interaction.edit_original_response(
                content="✅ Nenhuma mensagem sua encontrada em nenhuma guild!"
            )

        text = "🔍 **Guilds onde você tem mensagens:**\n\n"
        for r in results:
            text += f"• **{r['guild_name']}** (`{r['guild_id']}`)\n"
        text += f"\n📊 Total: **{len(results)}** guilds com mensagens"

        await interaction.edit_original_response(content=text)

    @ui.button(label="Listar DMs", style=discord.ButtonStyle.secondary, emoji="📨", row=1)
    async def list_dms(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        dms = await self.manager.list_dms(self.user_id)

        if not dms:
            return await interaction.followup.send("📨 Nenhuma DM aberta.", ephemeral=True)

        dm_list = [d for d in dms if d["type"] == "dm"]
        group_list = [d for d in dms if d["type"] == "group"]

        embeds = []

        if dm_list:
            text = "\n".join([f"• **{d['name']}** (`{d['id']}`)" for d in dm_list[:25]])
            if len(dm_list) > 25:
                text += f"\n... e mais {len(dm_list) - 25}"
            embeds.append(discord.Embed(
                title=f"💬 DMs ({len(dm_list)})", description=text, color=0x5865F2
            ))

        if group_list:
            text = "\n".join([f"• **{g['name']}** ({g['members']} membros) (`{g['id']}`)" for g in group_list[:25]])
            if len(group_list) > 25:
                text += f"\n... e mais {len(group_list) - 25}"
            embeds.append(discord.Embed(
                title=f"👥 Groups ({len(group_list)})", description=text, color=0x57F287
            ))

        await interaction.followup.send(embeds=embeds, ephemeral=True)

    @ui.button(label="Fechar DMs", style=discord.ButtonStyle.danger, emoji="🚪", row=1)
    async def close_all_dms(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)

        # Sub-menu para escolher o que fechar
        action_view = ui.View(timeout=60)

        async def close_all_dm_callback(inter):
            await inter.response.edit_message(content="🚪 Fechando todas as DMs...", view=None)

            async def progress(curr, total, status, extra):
                try:
                    if status in ("STARTING", "CLOSING"):
                        await inter.edit_original_response(
                            content=f"🚪 Fechando DMs... **{curr}/{total}**"
                        )
                except Exception:
                    pass

            result = await self.manager.close_all_dms(self.user_id, update_callback=progress)
            try:
                await inter.edit_original_response(content=f"✅ **Concluído!**\n{result}")
            except Exception:
                pass

        async def leave_all_groups_callback(inter):
            await inter.response.edit_message(content="🚪 Saindo de todos os Groups...", view=None)

            async def progress(curr, total, status, extra):
                try:
                    if status in ("STARTING", "LEAVING"):
                        await inter.edit_original_response(
                            content=f"🚪 Saindo de Groups... **{curr}/{total}**"
                        )
                except Exception:
                    pass

            result = await self.manager.leave_all_group_dms(self.user_id, update_callback=progress)
            try:
                await inter.edit_original_response(content=f"✅ **Concluído!**\n{result}")
            except Exception:
                pass

        btn_dms = ui.Button(label="Fechar Todas DMs", style=discord.ButtonStyle.danger, emoji="💬")
        btn_dms.callback = close_all_dm_callback
        action_view.add_item(btn_dms)

        btn_groups = ui.Button(label="Sair de Todos Groups", style=discord.ButtonStyle.danger, emoji="👥")
        btn_groups.callback = leave_all_groups_callback
        action_view.add_item(btn_groups)

        await interaction.response.send_message(
            content="🚪 **O que deseja fechar?**",
            view=action_view, ephemeral=True
        )