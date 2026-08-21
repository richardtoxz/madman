import logging

import discord
from discord.ext import commands

from .config import ID_CANAL_PAINEL, ID_CANAL_SOBRE
from .views.control_view import PersistentControlView
from .views.sobre_view import SobreView
from .shared.stealth_dm import shutdown_stealth_tasks
from .shared.tasks import cancel_panel_tasks

log = logging.getLogger(__name__)


class ControlBot(commands.Bot):
    def __init__(self, manager):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.presences = True
        intents.members = True

        super().__init__(command_prefix=[], intents=intents)
        self.manager = manager

    async def setup_hook(self):
        await self.load_extension("panel.cogs.panel_cog")
        await self.tree.sync()

    async def _setup_static_channel(
        self,
        channel_id: int | None,
        view: discord.ui.View,
        label: str,
    ) -> None:
        if channel_id is None:
            log.warning("[%s] ID do canal não configurado — pulando.", label)
            return

        channel = self.get_channel(channel_id)
        if not channel:
            log.warning("[%s] Canal %s não encontrado.", label, channel_id)
            return

        self.add_view(view)
        is_layout = isinstance(view, discord.ui.LayoutView)

        # Delete old bot messages so the new view replaces them
        async for msg in channel.history(limit=20):
            if msg.author == self.user:
                if is_layout or msg.embeds:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                    break

        if is_layout:
            await channel.send(view=view)
        else:
            embed = (
                view.build_embed()
                if hasattr(view, "build_embed")
                else discord.Embed(
                    title=f"Painel {label}",
                    description="Use o menu abaixo para controlar sua conta.",
                    color=0x2B2D31,
                )
            )
            await channel.send(embed=embed, view=view)

        log.info("[%s] Painel enviado para o canal %s.", label, channel_id)

    async def on_ready(self):
        log.info("Bot de Controle %s Online!", self.user)

        for channel_id, view, label in (
            (ID_CANAL_SOBRE, SobreView(), "SOBRE"),
            (ID_CANAL_PAINEL, PersistentControlView(self.manager), "PAINEL"),
        ):
            try:
                await self._setup_static_channel(channel_id, view, label)
            except Exception:
                log.exception("[%s] Falha ao configurar painel.", label)

    async def close(self):
        shutdown = getattr(self.manager, "shutdown", None)
        try:
            if shutdown is not None:
                await shutdown()
        finally:
            try:
                await cancel_panel_tasks(self)
                await shutdown_stealth_tasks()
                sessions = getattr(self, "_quest_sessions", None)
                if sessions is not None:
                    sessions.clear()
            finally:
                await super().close()
