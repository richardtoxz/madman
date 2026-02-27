import discord
import asyncio
import logging
import time

from utils.sweeper import GHOST_SIGNATURE, sweep_orphaned_messages

logger = logging.getLogger(__name__)

async def cleanup_stealth_messages(user: discord.User, bot_id: int):
    """
    Wrapper for the generic sweeper. Cleans messages using the ghost signature.
    """
    await sweep_orphaned_messages(user, bot_id, signature=GHOST_SIGNATURE)

async def send_stealth_dm(interaction: discord.Interaction, title: str, description: str, color: int = 0x2b2d31, delay: int = 120):
    """
    Envia uma mensagem de confirmação para a DM do usuário que se auto-destrói após o delay.
    Usa timestamps dinâmicos do Discord para um contador em tempo real.
    """
    user = interaction.user
    bot_id = interaction.client.user.id
    
    # Limpeza proativa de possíveis mensagens "orfãs"
    asyncio.create_task(cleanup_stealth_messages(user, bot_id))
    
    # Calcula o timestamp Unix de quando a mensagem será deletada
    target_timestamp = int(time.time() + delay)
    
    # Prepara o Embed
    embed = discord.Embed(title=title, description=description, color=color)
    
    # Usa o formato <t:timestamp:R> para o contador dinâmico do Discord
    # Adicionamos a assinatura fantasma (GHOST_SIGNATURE) de forma invisível no footer.
    embed.set_footer(text=f"Madman Services 🕵️{GHOST_SIGNATURE}")
    embed.description += f"\n\n⏳ Essa mensagem será deletada em <t:{target_timestamp}:R>"
    
    try:
        # Envia a DM
        dm_message = await user.send(embed=embed)
        
        # Agendamento da exclusão em background
        async def delete_after():
            await asyncio.sleep(delay)
            try:
                await dm_message.delete()
            except discord.NotFound:
                pass # Mensagem já foi deletada pelo usuário
            except Exception as e:
                logger.error(f"Erro ao deletar mensagem stealth: {e}")
                
        asyncio.create_task(delete_after())
        return True
    except discord.Forbidden:
        # Usuário com DMs fechadas, não podemos forçar
        return False
    except Exception as e:
        logger.error(f"Erro ao enviar DM stealth: {e}")
        return False
