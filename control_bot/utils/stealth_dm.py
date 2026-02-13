import discord
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

async def cleanup_stealth_messages(user: discord.User, bot_id: int):
    """
    Varre as últimas mensagens da DM dele e deleta mensagens do Modo Stealth
    que podem ter ficado para trás devido a quedas ou crashes do bot.
    """
    try:
        # Abre a DM se necessário
        dm_channel = user.dm_channel or await user.create_dm()
        
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        
        # Busca as últimas 50 mensagens enviadas pelo bot
        async for message in dm_channel.history(limit=50):
            # Ignora mensagens muito recentes (menos de 30 segundos) para evitar race conditions
            if now - message.created_at < timedelta(seconds=30):
                continue

            if message.author.id == bot_id and message.embeds:
                for embed in message.embeds:
                    # Verifica se é uma mensagem do modo stealth pelo rodapé ou descrição
                    footer_text = str(embed.footer.text) if embed.footer else ""
                    description_text = str(embed.description) if embed.description else ""
                    
                    if "Auto-destruição" in description_text:
                        try:
                            await message.delete()
                        except:
                            pass
    except discord.Forbidden:
        pass # DMs fechadas
    except Exception as e:
        logger.error(f"Erro na limpeza proativa de DMs stealth: {e}")

async def send_stealth_dm(interaction: discord.Interaction, title: str, description: str, color: int = 0x2b2d31, delay: int = 600):
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
    embed.set_footer(text=f"Madman Services")
    embed.description += f"\n\n**Auto-destruição:** <t:{target_timestamp}:R>"
    
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
