import asyncio
import discord
from discord import ui
from typing import Callable, Awaitable
from views.friends_view import FriendsListView
from utils.stealth_dm import send_stealth_dm


class WhitelistModal(ui.Modal, title='Gerenciar Whitelist'):
    user_id_input = ui.TextInput(
        label='ID do Usuário',
        placeholder='Insira o ID para proteger/desproteger...',
        min_length=15,
        max_length=21
    )

    def __init__(self, manager, user_id, update_callback: Callable[[discord.Interaction], Awaitable[None]]):
        super().__init__()
        self.manager = manager
        self.user_id = user_id
        self.update_callback = update_callback

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = self.user_id_input.value.strip()
            if not val.isdigit():
                return await interaction.response.send_message("❌ O ID deve conter apenas números!", ephemeral=True)

            target_id = int(val)
            added = await self.manager.toggle_whitelist(self.user_id, target_id)
            msg = "adicionado à" if added else "removido da"

            # Como é uma ação de usuário, usamos <@ID>
            await interaction.response.send_message(f"✅ <@{target_id}> (`{target_id}`) {msg} whitelist!", ephemeral=True)

            if self.update_callback:
                await self.update_callback(interaction)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)





class RelationshipManagementView(ui.View):
    def __init__(self, manager, user_id):
        super().__init__(timeout=300)
        self.manager = manager
        self.user_id = user_id
        self.stats = None
        self.last_interaction = None
        self._is_subscribed = False

    async def _handle_backend_event(self, text: str):
        """Callback acionado por eventos do backend (process.py)."""
        if not self.last_interaction:
            return

        try:
            # Refresh nas stats e atualiza a mensagem
            await self.refresh_stats()
            embed = self._get_embed(self.last_interaction)
            await self.last_interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            pass

    def _ensure_subscription(self, interaction: discord.Interaction):
        """Garante que estamos inscritos nos eventos do backend."""
        self.last_interaction = interaction
        if not self._is_subscribed:
            self.manager.subscribe_events(self.user_id, "EVENT:REL_", self._handle_backend_event)
            self._is_subscribed = True

    async def on_timeout(self):
        """Limpa a inscrição ao expirar a view."""
        if self._is_subscribed:
            self.manager.unsubscribe_events(self.user_id, "EVENT:REL_", self._handle_backend_event)
            self._is_subscribed = False

    async def refresh_stats(self):
        self.stats = await self.manager.get_rel_stats(self.user_id)

    def _check_active(self):
        return self.user_id in self.manager.active_processes

    def _get_embed(self, interaction: discord.Interaction):
        # Garante a inscrição sempre que o embed é gerado/atualizado
        self._ensure_subscription(interaction)
        
        whitelist = self.manager.get_whitelist(self.user_id)

        if not self._check_active():
            return discord.Embed(
                title="👥 Gerenciamento de Relações",
                description="❌ Seu selfbot precisa estar ligado para ver as estatísticas.",
                color=0xe74c3c
            )

        s = self.stats or {}
        whitelist_text = "\n".join([f"• <@{uid}>" for uid in whitelist]) if whitelist else "*Nenhum ID configurado*"

        embed = discord.Embed(
            title="👥 Gerenciamento de Relações",
            description="Gerencie amigos, bloqueios e pedidos de forma segura.",
            color=0x5865F2
        )

        friends_count = s.get("friends", "?")
        online_count = s.get("online_friends", "?")
        blocked_count = s.get("blocked", "?")
        incoming_count = s.get("incoming_requests", "?")
        outgoing_count = s.get("outgoing_requests", "?")

        embed.add_field(
            name="📊 Estatísticas",
            value=(
                f"👫 **Amigos:** {friends_count} (🟢 {online_count} online)\n"
                f"🚫 **Bloqueados:** {blocked_count}\n"
                f"📨 **Pedidos recebidos:** {incoming_count}\n"
                f"📤 **Pedidos enviados:** {outgoing_count}"
            ),
            inline=False
        )
        embed.add_field(name="🛡️ Whitelist (Protegidos)", value=whitelist_text, inline=False)
        embed.set_footer(text="⚠️ Operações em massa respeitam rate limits e podem demorar.")

        return embed

    # =============================================
    # ROW 0: Consultas
    # =============================================

    @ui.button(label="Amigos", style=discord.ButtonStyle.primary, emoji="<:friends:1471247997307191326>", row=0)
    async def list_friends_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        friends = await self.manager.get_friends_list(self.user_id)

        if not friends:
            return await interaction.followup.send("⚠️ Nenhum amigo encontrado.", ephemeral=True)

        view = FriendsListView(friends, self.user_id)
        await interaction.followup.send(embeds=view._get_page_content(), view=view, ephemeral=True)

    @ui.button(label="Pendentes", style=discord.ButtonStyle.secondary, emoji="📨", row=0)
    async def pending_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        data = await self.manager.get_pending_requests(self.user_id)

        embed = discord.Embed(title="📨 Pedidos Pendentes", color=0xfaa61a)

        incoming = data.get("incoming", [])
        outgoing = data.get("outgoing", [])

        incoming_text = "\n".join([f"• **{r['name']}** (`{r['id']}`)" for r in incoming[:25]]) or "*Nenhum*"
        outgoing_text = "\n".join([f"• **{r['name']}** (`{r['id']}`)" for r in outgoing[:25]]) or "*Nenhum*"

        embed.add_field(name=f"📥 Recebidos ({len(incoming)})", value=incoming_text, inline=False)
        embed.add_field(name=f"📤 Enviados ({len(outgoing)})", value=outgoing_text, inline=False)
        embed.set_footer(text="⚠️ Aceitar/Negar em massa respeita rate limits (~5s entre cada)")

        action_view = ui.View(timeout=120)

        if incoming:
            async def accept_all(inter):
                await inter.response.edit_message(
                    content="🚀 Aceitando pedidos... Isso pode demorar. Respeita rate limits.",
                    embed=None, view=None
                )
                # Limpeza proativa de DMs orfãs
                from utils.stealth_dm import cleanup_stealth_messages
                asyncio.create_task(cleanup_stealth_messages(inter.user, inter.client.user.id))

                result = await self.manager.accept_all_requests(self.user_id)
                try:
                    await inter.edit_original_response(content=f"✅ **Concluído!**\n{result}")
                    
                    # Modo Stealth
                    if "✅" in result or "OK" in result.upper():
                        await send_stealth_dm(
                            inter,
                            title="👥 Amizades Madman",
                            description=f"**Ação:** Aceitar todos os pedidos\n**Resultado:** {result}",
                            color=0x2ecc71
                        )
                except Exception:
                    pass

            btn_accept = ui.Button(label=f"Aceitar Todos ({len(incoming)})", style=discord.ButtonStyle.green, emoji="✅")
            btn_accept.callback = accept_all
            action_view.add_item(btn_accept)

        if incoming or outgoing:
            async def deny_all(inter):
                await inter.response.edit_message(
                    content="🚀 Negando pedidos... Isso pode demorar. Respeita rate limits.",
                    embed=None, view=None
                )
                # Limpeza proativa de DMs orfãs
                from utils.stealth_dm import cleanup_stealth_messages
                asyncio.create_task(cleanup_stealth_messages(inter.user, inter.client.user.id))

                result = await self.manager.deny_all_requests(self.user_id)
                try:
                    await inter.edit_original_response(content=f"✅ **Concluído!**\n{result}")
                    
                    # Modo Stealth
                    if "✅" in result or "OK" in result.upper():
                        await send_stealth_dm(
                            inter,
                            title="👥 Amizades Madman",
                            description=f"**Ação:** Negar/Remover todos os pedidos\n**Resultado:** {result}",
                            color=0x2ecc71
                        )
                except Exception:
                    pass

            btn_deny = ui.Button(
                label=f"Negar Todos ({len(incoming) + len(outgoing)})",
                style=discord.ButtonStyle.danger, emoji="❌"
            )
            btn_deny.callback = deny_all
            action_view.add_item(btn_deny)

        await interaction.followup.send(embed=embed, view=action_view, ephemeral=True)

    @ui.button(label="Bloqueados", style=discord.ButtonStyle.secondary, emoji="🚫", row=0)
    async def blocked_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        blocked = await self.manager.get_blocked_list(self.user_id)

        embed = discord.Embed(title=f"🚫 Bloqueados ({len(blocked)})", color=0xe74c3c)
        text = "\n".join([f"• **{b['name']}** (`{b['id']}`)" for b in blocked[:25]])
        if len(blocked) > 25:
            text += f"\n... e mais {len(blocked) - 25}"
        embed.description = text or "*Nenhum bloqueado*"

        est_min = max(1, len(blocked) * 4 // 60) if blocked else 0
        est_max = max(1, len(blocked) * 7 // 60) if blocked else 0

        if blocked:
            embed.set_footer(text=f"⏱️ Desbloquear todos: ~{est_min}-{est_max} minutos")

        action_view = ui.View(timeout=120)

        if blocked:
            async def unblock_confirm(inter):
                confirm_view = ui.View(timeout=60)

                async def unblock_execute(inter2):
                    await inter2.response.edit_message(
                        content=(
                            f"🚀 Desbloqueando {len(blocked)} usuários...\n"
                            f"⚠️ Estimativa: ~{est_min}-{est_max} minutos.\n"
                            f"Respeita rate limits automaticamente."
                        ),
                        embed=None, view=None
                    )
                    # Limpeza proativa de DMs orfãs
                    from utils.stealth_dm import cleanup_stealth_messages
                    asyncio.create_task(cleanup_stealth_messages(inter2.user, inter2.client.user.id))

                    result = await self.manager.unblock_all(self.user_id)
                    try:
                        await inter2.edit_original_response(content=f"✅ **Concluído!**\n{result}")
                        
                        # Modo Stealth
                        if "✅" in result or "OK" in result.upper():
                            await send_stealth_dm(
                                inter2,
                                title="🚫 Bloqueios Madman",
                                description=f"**Ação:** Desbloquear todos os usuários\n**Resultado:** {result}",
                                color=0x2ecc71
                            )
                    except Exception:
                        pass

                btn_confirm = ui.Button(label="SIM, Desbloquear Todos", style=discord.ButtonStyle.danger, emoji="⚠️")
                btn_confirm.callback = unblock_execute
                confirm_view.add_item(btn_confirm)

                await inter.response.edit_message(
                    content=(
                        f"⚠️ **TEM CERTEZA?**\n\n"
                        f"Você vai desbloquear **{len(blocked)}** usuários.\n"
                        f"Isso **não pode ser desfeito** facilmente.\n"
                        f"Estimativa: **{est_min}-{est_max} minutos**"
                    ),
                    embed=None, view=confirm_view
                )

            btn = ui.Button(label=f"Desbloquear Todos ({len(blocked)})", style=discord.ButtonStyle.green, emoji="🔓")
            btn.callback = unblock_confirm
            action_view.add_item(btn)

        await interaction.followup.send(embed=embed, view=action_view, ephemeral=True)

    # =============================================
    # ROW 1: Ações individuais
    # =============================================

    @ui.button(label="Whitelist", style=discord.ButtonStyle.primary, emoji="🛡️", row=1)
    async def whitelist_button(self, interaction: discord.Interaction, button: ui.Button):
        async def update_after_modal(modal_interaction: discord.Interaction):
            await interaction.edit_original_response(embed=self._get_embed(interaction), view=self)

        await interaction.response.send_modal(WhitelistModal(self.manager, self.user_id, update_after_modal))



    # =============================================
    # ROW 2: Operações em massa
    # =============================================

    @ui.button(label="Limpar Amigos", style=discord.ButtonStyle.danger, emoji="🧹", row=1)
    async def cleanup_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self._check_active():
            return await interaction.response.send_message("❌ Ligue o selfbot primeiro!", ephemeral=True)

        if not self.stats or self.stats.get("friends", 0) == 0:
            await self.refresh_stats()
            if not self.stats or self.stats.get("friends", 0) == 0:
                return await interaction.response.send_message(
                    "❌ Ninguém para remover ou estatísticas não carregadas.", ephemeral=True
                )

        whitelist = self.manager.get_whitelist(self.user_id)
        friends_total = self.stats.get("friends", 0)
        removable = max(0, friends_total - len(whitelist))
        est_min = max(1, removable * 4 // 60)
        est_max = max(1, removable * 7 // 60)

        confirm_view = ui.View(timeout=60)

        async def confirm_callback(interaction_confirm: discord.Interaction):
            await interaction_confirm.response.edit_message(
                content=(
                    f"🚀 Iniciando limpeza de amigos...\n"
                    f"⚠️ Estimativa: ~{est_min}-{est_max} minutos para ~{removable} amigos.\n"
                    f"Respeita rate limits automaticamente."
                ),
                embed=None, view=None
            )
            # Limpeza proativa de DMs orfãs
            from utils.stealth_dm import cleanup_stealth_messages
            asyncio.create_task(cleanup_stealth_messages(interaction_confirm.user, interaction_confirm.client.user.id))

            async def progress_callback(curr, total, status, name):
                try:
                    from utils.helpers import generate_progress_bar
                    embed = discord.Embed(title="🧹 Limpando Amizades", color=0xf1c40f)

                    if status == "STARTING":
                        embed.description = f"Preparando para remover **{total}** amigos..."
                    elif status == "REMOVING":
                        bar = generate_progress_bar(curr, total)
                        embed.description = (
                            f"Removendo: **{name}**\n"
                            f"Progresso: **{curr}/{total}**\n\n{bar}"
                        )
                    elif status == "BATCH_PAUSE":
                        embed.description = f"⏸️ {name}\nProgresso: **{curr}/{total}**"
                        embed.color = 0xe67e22
                    elif status == "RATE_LIMITED":
                        embed.description = f"⏳ {name}"
                        embed.color = 0xe74c3c
                    elif status == "ERROR":
                        embed.description = f"⚠️ Erro: {name}"
                        embed.color = 0xe74c3c
                    else:
                        return

                    await interaction_confirm.edit_original_response(embed=embed)
                except Exception:
                    pass

            result = await self.manager.start_rel_cleanup(self.user_id, progress_callback)

            await self.refresh_stats()
            try:
                await interaction_confirm.edit_original_response(
                    content=f"✅ **Limpeza concluída!**\n{result}", embed=None
                )
                
                # Modo Stealth
                if "✅" in result or "COMPLETADO" in result.upper():
                    await send_stealth_dm(
                        interaction_confirm,
                        title="🧹 Limpeza de Amigos Madman",
                        description=f"**Resultado:** {result}",
                        color=0x2ecc71
                    )
            except Exception:
                pass

        btn_confirm = ui.Button(label="Confirmar Exclusão em Massa", style=discord.ButtonStyle.danger)
        btn_confirm.callback = confirm_callback
        confirm_view.add_item(btn_confirm)

        await interaction.response.send_message(
            content=(
                f"⚠️ **CONFIRMAÇÃO DE LIMPEZA**\n\n"
                f"Você está prestes a remover **todos os seus amigos** exceto os protegidos.\n\n"
                f"📊 **Resumo:**\n"
                f"• Total de amigos: **{friends_total}**\n"
                f"• Whitelist: **{len(whitelist)}** protegidos\n"
                f"• A remover: **~{removable}**\n"
                f"• ⏱️ Estimativa: **{est_min}-{est_max} minutos**\n\n"
                f"⚠️ Operação respeita rate limits automaticamente.\n"
                f"Deseja prosseguir?"
            ),
            view=confirm_view,
            ephemeral=True
        )