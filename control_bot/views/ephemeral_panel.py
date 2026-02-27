import asyncio
import discord
from discord import ui
from views.handlers import (
    handle_authenticate, handle_disconnect,
    handle_clean_dm, handle_clean_all_dms, handle_clean_guild, handle_close_dms,
    handle_mute_servers, handle_leave_servers,
    handle_pending_requests, handle_clean_friends, handle_unblock_all,
)
from views.handlers.voice import handle_voice_afk, handle_voice_follow
from views.handlers.protection import handle_protection
from views.handlers.tracker import handle_tracker


HANDLER_MAP = {
    "auth": handle_authenticate,
    "clean_dm": handle_clean_dm,
    "clean_all_dms": handle_clean_all_dms,
    "clean_guild": handle_clean_guild,
    "close_dms": handle_close_dms,
    "mute_servers": handle_mute_servers,
    "leave_servers": handle_leave_servers,
    "voice_afk": handle_voice_afk,
    "voice_follow": handle_voice_follow,
    "protection": handle_protection,
    "tracker": handle_tracker,
    "pending": handle_pending_requests,
    "clean_friends": handle_clean_friends,
    "unblock": handle_unblock_all,
    "disconnect": handle_disconnect,
}

class BaseEphemeralSelect(ui.Select):
    """
    Classe base para os selects efêmeros.
    Implementa a lógica de callback e o "pulso de reset" para permitir re-selecionar o mesmo item.
    """
    def __init__(self, manager, placeholder: str, options: list[discord.SelectOption], row: int = 0):
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            row=row
        )
        self.manager = manager
        self.original_placeholder = placeholder

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        handler = HANDLER_MAP.get(value)
        if not handler:
            return

        # 1. Executa o handler normalmente — ele consome interaction.response como quiser
        #    (send_modal, send_message, defer, etc.)
        await handler(interaction, self.manager, interaction.user.id)

        # 2. Reseta o estado dos selects para permitir re-seleção da mesma opção
        for opt in self.options:
            opt.default = False

        # 3. Reset silencioso via webhook do /painel original
        #    Isso edita APENAS a mensagem do slash command, não a resposta do handler
        #    O delay evita "flash" visual no Discord client
        if hasattr(self.view, 'original_interaction') and self.view.original_interaction:
            await asyncio.sleep(0.5)
            try:
                await self.view.original_interaction.edit_original_response(view=self.view)
            except discord.HTTPException:
                pass


class SessionCleanSelect(BaseEphemeralSelect):
    """Select para Sessão e Limpeza"""
    def __init__(self, manager):
        options = [
            discord.SelectOption(label="Autenticar", description="Conectar via token.", emoji="<:account:1472004352028704913>", value="auth"),
            discord.SelectOption(label="Desconectar", description="Desconectar o selfbot.", emoji="<:unplug:1472005164989812908>", value="disconnect"),
            discord.SelectOption(label="Limpar DM/Canal", description="Deletar suas mensagens aqui.", emoji="<:clean:1472004349432434800>", value="clean_dm"),
            discord.SelectOption(label="Limpar Todas DMs", description="Deletar em todas as DMs.", emoji="<:cleanall:1472006170624659580>", value="clean_all_dms"),
            discord.SelectOption(label="Limpar Guild", description="Deletar de todo o servidor.", emoji="<:guild:1472004351039111228>", value="clean_guild"),
            discord.SelectOption(label="Fechar DMs/Groups", description="Fechar abas de DMs/Groups.", emoji="<:closedms:1472007525556818211>", value="close_dms"),
        ]
        super().__init__(manager, placeholder="Sessão & Limpeza...", options=options, row=0)


class ActionsSocialSelect(BaseEphemeralSelect):
    """Select para Ações Gerais e Social"""
    def __init__(self, manager):
        options = [
            discord.SelectOption(label="Silenciar Servidores", description="Silenciar notificações gerais.", emoji="<:mute:1472007043538747552>", value="mute_servers"),
            discord.SelectOption(label="Sair de Servidores", description="Sair de todos os servers.", emoji="<:guild:1472004351039111228>", value="leave_servers"),
            discord.SelectOption(label="Pedidos Pendentes", description="Ver/gerenciar amizades pendentes.", emoji="<:friendrequest:1472008284461465824>", value="pending"),
            discord.SelectOption(label="Limpar Amigos", description="Remover todos amigos.", emoji="<:removefriends:1472006635265462407>", value="clean_friends"),
            discord.SelectOption(label="Desbloquear Todos", description="Desbloquear bloqueados.", emoji="<:unblock:1472007776992628839>", value="unblock"),
        ]
        super().__init__(manager, placeholder="Ações & Social...", options=options, row=1)


class VoiceProtectionSelect(BaseEphemeralSelect):
    """Select para Voz, Segurança e Tracker"""
    def __init__(self, manager):
        options = [
            discord.SelectOption(label="Farmar Voz", description="Ficar AFK em voz.", emoji="<:mic:1475141243943256115>", value="voice_afk"),
            discord.SelectOption(label="Seguir Usuário", description="Seguir usuário em canais.", emoji="<:follow:1475141242009551023>", value="voice_follow"),
            discord.SelectOption(label="Proteções", description="Ativar proteções (Anti-raid/DM).", emoji="<:protection:1475141240420044901>", value="protection"),
            discord.SelectOption(label="Tracker", description="Configurar rastreio.", emoji="<:eye:1475141238943650014>", value="tracker"),
        ]
        super().__init__(manager, placeholder="Voz & Segurança...", options=options, row=2)


class EphemeralPanelView(ui.View):
    """
    View principal ephemera gerada pelo /painel
    Possui multiplos Selects distribuídos por categorias (rows)
    """
    def __init__(self, manager, original_interaction: discord.Interaction = None):
        super().__init__(timeout=None) # Ephemeral views podem não precisar de timeout longo, mas mantemos
        
        self.original_interaction = original_interaction
        
        # Adiciona os selects categorizados
        self.add_item(SessionCleanSelect(manager))
        self.add_item(ActionsSocialSelect(manager))
        self.add_item(VoiceProtectionSelect(manager))
