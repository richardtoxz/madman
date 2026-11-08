import discord
from discord import ui


THUMBNAIL_URL = "https://cdn.discordapp.com/attachments/1484741994109472778/1484923627089690664/tn02jwh.png?ex=69bffe65&is=69beace5&hm=08768cce2cee7addb483a2f41c7dcbaf4e83b32a9f8bafda55eebc407b2e7d01&"


class SobreView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(
            ui.Container(
                ui.TextDisplay("# Madman • Painel de Automação para Discord"),
                ui.TextDisplay(
                    "O **Madman** é o seu painel definitivo para **automação, limpeza e segurança** da conta Discord.\n"
                    "Interface moderna, controles rápidos e tudo focado em proteger sua conta sem perder tempo."
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("### Módulos do Sistema"),
                ui.TextDisplay(
                    "> <:account:1471252343386280068> **Autenticação de Conta**\n"
                    " Conecte sua conta com segurança via token."
                ),
                ui.TextDisplay(
                    "> <:clean:1471255175795834961> **Privacidade & Limpeza**\n"
                    " Limpe mensagens em DMs, canais e servidores."
                ),
                ui.TextDisplay(
                    "> <:friends:1471247997307191326> **Relações Sociais**\n"
                    " Gestão de amigos, bloqueios e pedidos pendentes.\n"
                    "> <:guild:1471252315678834788> **Servidores**\n"
                    " Silencie ou saia de servidores em massa."
                ),
                ui.TextDisplay(
                    "> <:mic:1475141243943256115> **Voz**\n"
                    " Farm de hora AFK em canais de voz, DMs e grupos, e Coleira (segue um usuário em canais de voz).\n"
                    "> <:protection:1475141240420044901> **Proteções**\n"
                    " Filtros de DM indesejadas.\n"
                    "> <:eye:1475141238943650014> **Tracker**\n"
                    " Rastreio de interações de um usuário.\n"
                    "> <:orbs:1484293908627456141> **Quests Automáticas**\n"
                    " Faz quests do Discord automaticamente."
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("-# Madman Services • Versão v1.0"),
                accent_colour=0x531B37,
            )
        )
