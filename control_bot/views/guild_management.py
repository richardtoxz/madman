import discord
import asyncio
from discord import ui
from typing import List, Callable, Awaitable
from utils.stealth_dm import send_stealth_dm

class GuildWhitelistModal(ui.Modal, title='Gerenciar Whitelist de Servidores'):
    guild_id_input = ui.TextInput(
        label='ID do Servidor',
        placeholder='Insira o ID do servidor para proteger...',
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
            val = self.guild_id_input.value.strip()
            if not val.isdigit():
                return await interaction.response.send_message("❌ O ID deve conter apenas números!", ephemeral=True)
            
            guild_id = int(val)
            added = await self.manager.toggle_guild_whitelist(self.user_id, guild_id)
            msg = "adicionado à" if added else "removido da"
            
            await interaction.response.send_message(f"✅ Servidor ID `{guild_id}` {msg} whitelist de servidores!", ephemeral=True)
            
            if self.update_callback:
                await self.update_callback(interaction)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

class GuildManagementView(ui.View):
    def __init__(self, manager, user_id):
        super().__init__(timeout=180)
        self.manager = manager
        self.user_id = user_id
        self.stats = None
        self.last_interaction = None
        self._is_subscribed = False

    async def _handle_backend_event(self, text: str):
        """Callback acionado por eventos do backend."""
        if not self.last_interaction:
            return
        try:
            await self.refresh_stats()
            embed = self._get_embed(self.last_interaction)
            await self.last_interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            pass

    def _ensure_subscription(self, interaction: discord.Interaction):
        """Garante a inscrição nos eventos."""
        self.last_interaction = interaction
        if not self._is_subscribed:
            self.manager.subscribe_events(self.user_id, "EVENT:GUILD_", self._handle_backend_event)
            self._is_subscribed = True

    async def on_timeout(self):
        """Limpeza ao expirar."""
        if self._is_subscribed:
            self.manager.unsubscribe_events(self.user_id, "EVENT:GUILD_", self._handle_backend_event)
            self._is_subscribed = False

    async def refresh_stats(self):
        self.stats = await self.manager.get_guild_stats(self.user_id)

    def _get_embed(self, interaction: discord.Interaction):
        self._ensure_subscription(interaction)
        is_active = self.user_id in self.manager.active_processes
        whitelist = self.manager.get_guild_whitelist(self.user_id)
        
        if not is_active:
            return discord.Embed(
                title="🏠 Gerenciamento de Servidores",
                description="❌ Seu selfbot precisa estar ligado para gerenciar servidores.",
                color=0xe74c3c
            )
            
        total_guilds = self.stats.get("total", "?") if self.stats else "Carregando..."
        whitelist_text = "\n".join([f"• `{uid}`" for uid in whitelist]) if whitelist else "*Nenhum ID configurado*"
        
        embed = discord.Embed(
            title="🏠 Gerenciamento de Servidores",
            description=f"Gerencie seus servidores e notificações de forma privada.",
            color=0x5865F2
        )
        embed.add_field(name="📊 Estatísticas", value=f"🌐 **Servidores:** {total_guilds}", inline=False)
        embed.add_field(name="🛡️ Whitelist (Protegidos)", value=f"{whitelist_text}", inline=False)
        embed.set_footer(text="A Whitelist protege servidores contra saída e silenciamento.")
        
        return embed

    @ui.button(label="Whitelist", style=discord.ButtonStyle.primary, emoji="🛡️")
    async def whitelist_button(self, interaction: discord.Interaction, button: ui.Button):
        async def update_after_modal(modal_interaction: discord.Interaction):
            await interaction.edit_original_response(embed=self._get_embed(interaction), view=self)
            
        await interaction.response.send_modal(GuildWhitelistModal(self.manager, self.user_id, update_after_modal))

    @ui.button(label="Sair de Tudo", style=discord.ButtonStyle.danger, emoji="🧹")
    async def leave_button(self, interaction: discord.Interaction, button: ui.Button):
        await self._start_cleanup(interaction, "LEAVE")

    @ui.button(label="Silenciar Todos", style=discord.ButtonStyle.danger, emoji="🔕")
    async def mute_button(self, interaction: discord.Interaction, button: ui.Button):
        await self._start_cleanup(interaction, "MUTE")

    async def _start_cleanup(self, interaction: discord.Interaction, action: str):
        if not self.stats:
            await self.refresh_stats()
            
        total_guilds = self.stats.get("total", 0) if self.stats else 0
        whitelist = self.manager.get_guild_whitelist(self.user_id)
        
        action_name = "SAIR DE TODOS" if action == "LEAVE" else "SILENCIAR TODOS"
        color = 0xe74c3c if action == "LEAVE" else 0xf1c40f
        
        # Confirmação
        confirm_view = ui.View(timeout=60)
        
        async def confirm_callback(interaction_confirm: discord.Interaction):
            await interaction_confirm.response.edit_message(content=f"🚀 Iniciando processo: {action_name}...", embed=None, view=None)
            
            # Limpeza proativa de DMs orfãs
            from utils.stealth_dm import cleanup_stealth_messages
            asyncio.create_task(cleanup_stealth_messages(interaction_confirm.user, interaction_confirm.client.user.id))

            async def progress_callback(curr, total, status, name):
                embed = discord.Embed(title=f"🧹 Processando Servidores", color=color)
                
                if status == "STARTING":
                    embed.description = f"Preparando para processar **{total}** servidores..."
                else:
                    embed.description = f"Progresso: **{curr}/{total}**"
                
                await interaction_confirm.edit_original_response(embed=embed)

            result = await self.manager.start_guild_cleanup(self.user_id, action, progress_callback)
            await self.refresh_stats()
            await interaction_confirm.edit_original_response(content=f"✅ **Operação concluída!**\n{result}", embed=None)
            
            # Modo Stealth
            if "✅" in result or "OK" in result.upper() or "OPERACAO CONCLUIDA" in result.upper():
                await send_stealth_dm(
                    interaction_confirm,
                    title="🏠 Servidores Madman",
                    description=f"**Ação:** {action_name}\n**Resultado:** {result}",
                    color=color
                )

        btn_confirm = ui.Button(label=f"Confirmar {action_name}", style=discord.ButtonStyle.danger)
        btn_confirm.callback = confirm_callback
        confirm_view.add_item(btn_confirm)
        
        await interaction.response.send_message(
            content=f"⚠️ **CONFIRMAÇÃO DE OPERAÇÃO**\n\n"
                    f"Você está prestes a **{action_name}** os servidores, exceto os protegidos.\n\n"
                    f"📊 **Resumo:**\n"
                    f"• Servidores Atuais: **{total_guilds}**\n"
                    f"• Whitelist: **{len(whitelist)}** protegidos\n"
                    f"• **Nota:** O processo terá um delay de 2s por servidor para segurança.\n\n"
                    f"Deseja prosseguir?",
            view=confirm_view,
            ephemeral=True
        )
