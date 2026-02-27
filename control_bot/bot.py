import discord
from discord.ext import commands

from config import ID_CANAL_PAINEL, ID_CANAL_SOBRE
from views.control_view import PersistentControlView
from utils import SobreView


class ControlBot(commands.Bot):
    def __init__(self, manager):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.presences = True  # Necessário para ver status dos membros
        intents.members = True    # Necessário para get_member()
        
        super().__init__(command_prefix=[], intents=intents)
        self.manager = manager

    async def setup_hook(self):
        await self.load_extension("cogs.panel_cog")
        print("Cogs carregados.")
        synced = await self.tree.sync()
        print(f"Comandos sincronizados globalmente.")

    async def _setup_static_channel(self, channel_id: int, view: discord.ui.View, label: str):
        channel = self.get_channel(channel_id)
        if not channel:
            print(f"[{label}] Canal {channel_id} não encontrado.")
            return

        self.add_view(view)
        is_layout = isinstance(view, discord.ui.LayoutView)

        async for msg in channel.history(limit=20):
            if msg.author == self.user:
                if is_layout or msg.embeds:
                    return

        if is_layout:
            await channel.send(view=view)
        else:
            embed = view.build_embed() if hasattr(view, 'build_embed') else discord.Embed(
                title=f"Painel {label}",
                description="Use o menu abaixo para controlar sua conta.",
                color=0x2b2d31
            )
            await channel.send(embed=embed, view=view)
        print(f"[{label}] Painel enviado para o canal {channel_id}")

    async def on_ready(self):
        print(f'Bot de Controle {self.user} Online!')
        
        # Inicialização limpa e DRY dos canais estáticos
        await self._setup_static_channel(ID_CANAL_SOBRE, SobreView(), "SOBRE")
        await self._setup_static_channel(ID_CANAL_PAINEL, PersistentControlView(self.manager), "PAINEL")
