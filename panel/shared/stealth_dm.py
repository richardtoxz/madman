import discord
import asyncio
import logging
import time

from .sweeper import GHOST_SIGNATURE, sweep_orphaned_messages

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()


def _track(coroutine, *, name: str) -> asyncio.Task:
    task = asyncio.create_task(coroutine, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def cleanup_stealth_messages(user: discord.User, bot_id: int):
    await sweep_orphaned_messages(user, bot_id, signature=GHOST_SIGNATURE)


async def send_stealth_dm(
    interaction: discord.Interaction,
    title: str,
    description: str,
    color: int = 0x2B2D31,
    delay: int = 120,
):
    return await send_stealth_dm_to_user(
        interaction.user,
        interaction.client.user.id,
        title,
        description,
        color=color,
        delay=delay,
    )


async def send_stealth_dm_to_user(
    user: discord.User,
    bot_id: int,
    title: str,
    description: str,
    color: int = 0x2B2D31,
    delay: int = 120,
):
    """Send a temporary DM without retaining the originating interaction."""

    _track(
        cleanup_stealth_messages(user, bot_id),
        name=f"stealth-sweep-{getattr(user, 'id', 0)}",
    )

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

        _track(
            delete_after(),
            name=f"stealth-delete-{getattr(dm_message, 'id', 0)}",
        )
        return True
    except discord.Forbidden:
        return False
    except Exception as e:
        logger.error(f"Erro ao enviar DM stealth: {e}")
        return False


def schedule_stealth_cleanup(user: discord.User, bot_id: int) -> asyncio.Task:
    return _track(
        cleanup_stealth_messages(user, bot_id),
        name=f"stealth-sweep-{getattr(user, 'id', 0)}",
    )


async def shutdown_stealth_tasks() -> None:
    current = asyncio.current_task()
    pending = [
        task
        for task in tuple(_background_tasks)
        if task is not current and not task.done()
    ]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _background_tasks.clear()
