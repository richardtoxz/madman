import asyncio
import discord
from discord import ui

from ..access import ACCESS_DENIED_MESSAGE, is_user_allowed
from ..shared.interaction import is_unknown_interaction_error


class BaseSelect(ui.Select):
    """Base select with shared callback logic — routes to HANDLER_MAP."""

    def __init__(
        self,
        manager,
        placeholder: str,
        options: list[discord.SelectOption],
        row: int = 0,
        *,
        custom_id: str | None = None,
    ):
        kwargs = dict(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            row=row,
        )
        if custom_id:
            kwargs["custom_id"] = custom_id

        super().__init__(**kwargs)
        self.manager = manager

    async def callback(self, interaction: discord.Interaction):
        from .registry import HANDLER_MAP

        # Vale para o painel efêmero e para o fixo no canal: os dois usam este select.
        if not is_user_allowed(interaction.user.id):
            await interaction.response.send_message(ACCESS_DENIED_MESSAGE, ephemeral=True)
            return

        value = self.values[0]
        handler = HANDLER_MAP.get(value)
        if not handler:
            return

        try:
            handled = await handler(interaction, self.manager, interaction.user.id)
        except discord.HTTPException as error:
            if is_unknown_interaction_error(error):
                return
            raise

        if handled is False:
            return

        for opt in self.options:
            opt.default = False

        if hasattr(self.view, "original_interaction") and self.view.original_interaction:
            await asyncio.sleep(0.5)
            try:
                await self.view.original_interaction.edit_original_response(view=self.view)
            except discord.HTTPException:
                pass


class SessionCleanSelect(BaseSelect):
    def __init__(self, manager, *, custom_id: str | None = None):
        options = [
            discord.SelectOption(
                label="Autenticar",
                description="Conectar via token.",
                emoji="<:account:1472004352028704913>",
                value="auth",
            ),
            discord.SelectOption(
                label="Desconectar",
                description="Desconectar o selfbot.",
                emoji="<:unplug:1472005164989812908>",
                value="disconnect",
            ),
            discord.SelectOption(
                label="Limpar DM/Canal",
                description="Deletar suas mensagens aqui.",
                emoji="<:clean:1472004349432434800>",
                value="clean_dm",
            ),
            discord.SelectOption(
                label="Limpar Conversas Privadas",
                description="Deletar em DMs e grupos.",
                emoji="<:cleanall:1472006170624659580>",
                value="clean_all_dms",
            ),
            discord.SelectOption(
                label="Limpar Guild",
                description="Deletar de todo o servidor.",
                emoji="<:guild:1472004351039111228>",
                value="clean_guild",
            ),
            discord.SelectOption(
                label="Fechar DMs/Groups",
                description="Fechar abas de DMs/Groups.",
                emoji="<:closedms:1472007525556818211>",
                value="close_dms",
            ),
        ]
        super().__init__(
            manager,
            placeholder="🔐 Sessão & Limpeza...",
            options=options,
            row=0,
            custom_id=custom_id,
        )


class SocialServersSelect(BaseSelect):
    def __init__(self, manager, *, custom_id: str | None = None):
        options = [
            discord.SelectOption(
                label="Silenciar Servidores",
                description="Silenciar notificações gerais.",
                emoji="<:mute:1472007043538747552>",
                value="mute_servers",
            ),
            discord.SelectOption(
                label="Sair de Servidores",
                description="Sair de todos os servers.",
                emoji="<:guild:1472004351039111228>",
                value="leave_servers",
            ),
            discord.SelectOption(
                label="Whitelist de Servidores",
                description="Escolher servidores protegidos.",
                emoji="🛡️",
                value="guild_whitelist",
            ),
            discord.SelectOption(
                label="Pedidos Pendentes",
                description="Ver/gerenciar amizades pendentes.",
                emoji="<:friendrequest:1472008284461465824>",
                value="pending",
            ),
            discord.SelectOption(
                label="Limpar Amigos",
                description="Remover todos amigos.",
                emoji="<:removefriends:1472006635265462407>",
                value="clean_friends",
            ),
            discord.SelectOption(
                label="Desbloquear Todos",
                description="Desbloquear bloqueados.",
                emoji="<:unblock:1472007776992628839>",
                value="unblock",
            ),
        ]
        super().__init__(
            manager,
            placeholder="👥 Social & Servidores...",
            options=options,
            row=1,
            custom_id=custom_id,
        )


class ToolsSelect(BaseSelect):
    def __init__(self, manager, *, custom_id: str | None = None):
        options = [
            discord.SelectOption(
                label="Farmar Voz",
                description="Ficar AFK em voz, DM ou grupo.",
                emoji="<:mic:1475141243943256115>",
                value="voice_afk",
            ),
            discord.SelectOption(
                label="Seguir Usuário",
                description="Seguir usuário em canais.",
                emoji="<:follow:1475141242009551023>",
                value="voice_follow",
            ),
            discord.SelectOption(
                label="Controle de Voz",
                description="Ver estado, sair ou parar follow.",
                emoji="🎙️",
                value="voice_control",
            ),
            discord.SelectOption(
                label="Proteções",
                description="Configurar protecoes anti-raid/DM.",
                emoji="<:protection:1475141240420044901>",
                value="protection",
            ),
            discord.SelectOption(
                label="Rich Presence",
                description="Editar ou desligar a atividade do Madman.",
                emoji="🎮",
                value="rich_presence",
            ),
            discord.SelectOption(
                label="Tracker",
                description="Configurar rastreio.",
                emoji="<:eye:1475141238943650014>",
                value="tracker",
            ),
            discord.SelectOption(
                label="Quest Completer",
                description="Completar quests automaticamente.",
                emoji="<:orbs:1484293908627456141>",
                value="quest",
            ),
        ]
        super().__init__(
            manager,
            placeholder="🛠️ Ferramentas...",
            options=options,
            row=2,
            custom_id=custom_id,
        )


class SettingsSelect(BaseSelect):
    def __init__(self, manager, *, custom_id: str | None = None):
        options = [
            discord.SelectOption(
                label="Webhook",
                description="Configurar logs do Tracker e Quest Completer.",
                emoji="<:configquest:1484293909923233824>",
                value="settings",
            ),
        ]
        super().__init__(
            manager,
            placeholder="⚙️ Configurações...",
            options=options,
            row=3,
            custom_id=custom_id,
        )
