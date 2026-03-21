import discord
import asyncio
import logging
import time

from .sweeper import GHOST_SIGNATURE, sweep_orphaned_messages

logger = logging.getLogger(__name__)


async def cleanup_stealth_messages(user: discord.User, bot_id: int):
    await sweep_orphaned_messages(user, bot_id, signature=GHOST_SIGNATURE)


async def send_stealth_dm(
    interaction: discord.Interaction,
    title: str,
    description: str,
    color: int = 0x2B2D31,
    delay: int = 120,
):
    user = interaction.user
    bot_id = interaction.client.user.id

    asyncio.create_task(cleanup_stealth_messages(user, bot_id))

    target_timestamp = int(time.time() + delay)

    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=f"Madman Services 🕵️{GHOST_SIGNATURE}")
    embed.description += (
        f"\n\n⏳ Essa mensagem será deletada em <t:{target_timestamp}:R>"
    )

    try:
        dm_message = await user.send(embed=embed)

        async def delete_after():
            await asyncio.sleep(delay)
            try:
                await dm_message.delete()
            except discord.NotFound:
                pass
            except Exception as e:
                logger.error(f"Erro ao deletar mensagem stealth: {e}")

        asyncio.create_task(delete_after())
        return True
    except discord.Forbidden:
        return False
    except Exception as e:
        logger.error(f"Erro ao enviar DM stealth: {e}")
        return False
