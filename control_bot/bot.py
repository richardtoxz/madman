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
        # Usamos uma lista vazia para não ter prefixo e sobrescrevemos on_message abaixo
        super().__init__(command_prefix=[], intents=intents)
        self.manager = manager

    async def on_message(self, message):
        # Ignora todas as mensagens de chat para o bot não tentar processar comandos
        pass

    async def setup_hook(self):
        # Registra a view persistente
        self.add_view(PersistentControlView(self.manager))

    async def on_ready(self):
        print(f'Bot de Controle {self.user} Online!')
        
        # 1. CANAL SOBRE - Components V2
        if ID_CANAL_SOBRE:
            channel_sobre = self.get_channel(ID_CANAL_SOBRE)
            if channel_sobre:
                async for message in channel_sobre.history(limit=10):
                    if message.author == self.user:
                        await message.delete()
                
                await channel_sobre.send(view=SobreView())
                print(f"Sobre enviado para o canal {ID_CANAL_SOBRE}")
            else:
                print(f"Canal SOBRE {ID_CANAL_SOBRE} não encontrado.")

        # 2. CANAL PAINEL - Utilizando Components V2 (Sem Embed)
        if ID_CANAL_PAINEL:
            channel_painel = self.get_channel(ID_CANAL_PAINEL)
            if channel_painel:
                async for message in channel_painel.history(limit=10):
                    if message.author == self.user:
                        await message.delete()
                
                # LayoutView com Components V2 — a flag IS_COMPONENTS_V2
                # é setada automaticamente pela lib ao detectar componentes V2
                await channel_painel.send(
                    view=PersistentControlView(self.manager)
                )
                print(f"Painel enviado para o canal {ID_CANAL_PAINEL}")
            else:
                print(f"Canal PAINEL {ID_CANAL_PAINEL} não encontrado.")

