import discord
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Assinatura Fantasma: 3 espaços de largura zero. Totalmente invisível para humanos.
GHOST_SIGNATURE = "\u200b\u200b\u200b"

async def sweep_orphaned_messages(
    user: discord.User, 
    bot_id: int, 
    signature: str = GHOST_SIGNATURE, 
    limit: int = 50, 
    min_age_seconds: int = 30
):
    """
    Varre as últimas mensagens da DM do usuário procurando embeds enviados por este bot
    que contenham a `signature` no rodapé. Remove essas mensagens para garantir limpeza de "mensagens órfãs",
    que podem ter ficado para trás devido a um reinício abrupto do bot (quebrando os timers locais na RAM).
    """
    try:
        dm_channel = user.dm_channel or await user.create_dm()
        now = datetime.now(timezone.utc)
        
        async for message in dm_channel.history(limit=limit):
            # Ignora mensagens muito recentes para evitar race condition com a task original
            if now - message.created_at < timedelta(seconds=min_age_seconds):
                continue

            if message.author.id == bot_id and message.embeds:
                for embed in message.embeds:
                    footer_text = str(embed.footer.text) if embed.footer else ""
                    
                    # Checamos apenas a assinatura invisível
                    if signature in footer_text:
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        break  # Já deletou, não precisa olhar outros embeds da mesma msg
    except discord.Forbidden:
        pass  # Usuário fechou DMs
    except Exception as e:
        logger.error(f"Erro no stateless sweeper da DM: {e}")
