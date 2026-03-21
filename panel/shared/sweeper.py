import discord
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

GHOST_SIGNATURE = "\u200b\u200b\u200b"


async def sweep_orphaned_messages(
    user: discord.User,
    bot_id: int,
    signature: str = GHOST_SIGNATURE,
    limit: int = 50,
    min_age_seconds: int = 30,
):
    try:
        dm_channel = user.dm_channel or await user.create_dm()
        now = datetime.now(timezone.utc)

        async for message in dm_channel.history(limit=limit):
            if now - message.created_at < timedelta(seconds=min_age_seconds):
                continue

            if message.author.id == bot_id and message.embeds:
                for embed in message.embeds:
                    footer_text = str(embed.footer.text) if embed.footer else ""

                    if signature in footer_text:
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        break
    except discord.Forbidden:
        pass
    except Exception as e:
        logger.error(f"Erro no stateless sweeper da DM: {e}")
