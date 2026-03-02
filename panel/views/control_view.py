import discord
from discord import ui

from utils.helpers import PainelView
from views.handlers import (
    handle_authenticate,
    handle_disconnect,
    handle_clean_dm,
    handle_clean_all_dms,
    handle_clean_guild,
    handle_close_dms,
    handle_mute_servers,
    handle_leave_servers,
    handle_pending_requests,
    handle_clean_friends,
    handle_unblock_all,
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


class ControlSelect(ui.Select):
    def __init__(self, manager):
        options = [
            discord.SelectOption(
                label="Autenticar",
                description="Conectar sua conta via token.",
                emoji="<:account:1472004352028704913>",
                value="auth",
            ),
            discord.SelectOption(
                label="Limpar DM/Canal",
                description="Deletar suas mensagens em uma DM ou canal específico.",
                emoji="<:clean:1472004349432434800>",
                value="clean_dm",
            ),
            discord.SelectOption(
                label="Limpar Todas DMs Abertas",
                description="Deletar suas mensagens em todas as DMs abertas.",
                emoji="<:cleanall:1472006170624659580>",
                value="clean_all_dms",
            ),
            discord.SelectOption(
                label="Limpar Guild",
                description="Deletar suas mensagens em todo um servidor.",
                emoji="<:guild:1472004351039111228>",
                value="clean_guild",
            ),
            discord.SelectOption(
                label="Fechar DMs/Groups",
                description="Fechar todas as DMs abertas ou sair de Groups.",
                emoji="<:closedms:1472007525556818211>",
                value="close_dms",
            ),
            discord.SelectOption(
                label="Silenciar Servidores",
                description="Silenciar notificações de todos os servidores.",
                emoji="<:mute:1472007043538747552>",
                value="mute_servers",
            ),
            discord.SelectOption(
                label="Sair de Servidores",
                description="Sair de todos os servidores.",
                emoji="<:guild:1472004351039111228>",
                value="leave_servers",
            ),
            discord.SelectOption(
                label="Farmar Voz",
                description="Conectar em canal de voz e ficar AFK.",
                emoji="<:mic:1475141243943256115>",
                value="voice_afk",
            ),
            discord.SelectOption(
                label="Seguir Usuário",
                description="Seguir um usuário em canais de voz.",
                emoji="<:follow:1475141242009551023>",
                value="voice_follow",
            ),
            discord.SelectOption(
                label="Proteções",
                description="Ativar/desativar proteções automáticas de DM e raids.",
                emoji="<:protection:1475141240420044901>",
                value="protection",
            ),
            discord.SelectOption(
                label="Tracker",
                description="Configurar e iniciar rastreio de um usuário.",
                emoji="<:eye:1475141238943650014>",
                value="tracker",
            ),
            discord.SelectOption(
                label="Pedidos Pendentes",
                description="Ver e gerenciar pedidos de amizade pendentes.",
                emoji="<:friendrequest:1472008284461465824>",
                value="pending",
            ),
            discord.SelectOption(
                label="Limpar Amigos",
                description="Remover todos os amigos.",
                emoji="<:removefriends:1472006635265462407>",
                value="clean_friends",
            ),
            discord.SelectOption(
                label="Desbloquear Todos",
                description="Desbloquear todos os usuários bloqueados.",
                emoji="<:unblock:1472007776992628839>",
                value="unblock",
            ),
            discord.SelectOption(
                label="Desconectar",
                description="Desconectar o selfbot.",
                emoji="<:unplug:1472005164989812908>",
                value="disconnect",
            ),
        ]
        super().__init__(
            placeholder="Selecione uma operação...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="madman_control_select",
        )
        self.manager = manager

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        handler = HANDLER_MAP.get(value)

        if handler:
            await handler(interaction, self.manager, interaction.user.id)


class PersistentControlView(PainelView):
    def __init__(self, manager):
        self.manager = manager
        super().__init__(select_item=ControlSelect(manager))
