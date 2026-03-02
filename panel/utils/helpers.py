import discord
from discord import ui


def generate_progress_bar(current, total, size=15):
    if current is None:
        current = 0
    if total is None or total == 0:
        total = 1
    percentage = current / total
    filled = int(size * percentage)
    bar = "▰" * filled + "▱" * (size - filled)
    return bar


class SobreView(ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(
            ui.Container(
                ui.TextDisplay("# Madman "),
                ui.TextDisplay(
                    "O **Madman** é o seu painel definitivo de automação para o Discord. Com uma interface **intuitiva**, ele oferece ferramentas avançadas de **gestão, limpeza e segurança** — tudo a um clique de distância, projetado para maximizar sua produtividade e proteger sua conta."
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("### Módulos do Sistema"),
                ui.TextDisplay(
                    "> <:account:1471252343386280068> **Autenticação de Conta**\n"
                    " *Autenticação segura da conta.*"
                ),
                ui.TextDisplay(
                    "> <:friends:1471247997307191326> **Relações Sociais**\n"
                    " *Limpeza de amigos, gestão de bloqueios e pedidos pendentes.*"
                ),
                ui.TextDisplay(
                    "> <:guild:1471252315678834788> **Servidores**\n"
                    " *Silenciar servidores e sair de servidores.*"
                ),
                ui.TextDisplay(
                    "> <:clean:1471255175795834961> **Privacidade & Limpeza**\n"
                    " *Limpeza de mensagens em (DMs/Canais).*"
                ),
                ui.TextDisplay(
                    "> <:protection:1475141240420044901> **Proteções**\n"
                    " *Proteções contra DMs e raids indesejadas.*"
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("-#  `v1.0`"),
                accent_colour=0x242429,
            )
        )


class PainelView(ui.LayoutView):
    def __init__(self, select_item):
        super().__init__(timeout=None)

        self.add_item(
            ui.Container(
                ui.TextDisplay("# PAINEL"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("Configure sua conta via **token** para usar o painel."),
                ui.TextDisplay("-# Selecione uma opção abaixo para começar."),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.ActionRow(select_item),
                accent_colour=0x242429,
            )
        )
