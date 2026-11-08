import discord
from discord import ui


class PanelLayoutView(ui.LayoutView):
    """Base LayoutView for the Madman panel — used by both persistent and ephemeral panels."""

    def __init__(self, select_items: list, *, timeout=None):
        super().__init__(timeout=timeout)

        action_rows = [ui.ActionRow(select) for select in select_items]

        self.add_item(
            ui.Container(
                ui.TextDisplay("# Madman • Painel de Controle"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("Selecione uma categoria abaixo para controlar sua conta.\n"),
                ui.TextDisplay(
                    "> <:account:1471252343386280068> **Sessão & Limpeza** — Autenticação, limpeza de DMs e canais.\n"
                    "> <:friends:1471247997307191326> **Social & Servidores** — Amigos, bloqueios, silenciar e sair de servidores.\n"
                    "> <:mic:1475141243943256115> **Ferramentas** — Voz, proteções, tracker e quests.\n"
                    "> <:configquest:1484293909923233824> **Configurações** — Webhook e integrações."
                ),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                *action_rows,
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay("-# Madman Services • v1.0"),
                accent_colour=0x531B37,
            )
        )
