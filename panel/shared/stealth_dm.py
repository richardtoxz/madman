import discord
import asyncio
import logging
import time

from .sweeper import (
    ACTIVE_TEMPORARY_MESSAGE_IDS,
    GHOST_SIGNATURE,
    sweep_orphaned_messages,
)

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()
RESULT_TTL_SECONDS = 120
CHOICE_TTL_SECONDS = 300
PROGRESS_THROTTLE_SECONDS = 1.5


def _track(coroutine, *, name: str) -> asyncio.Task:
    task = asyncio.create_task(coroutine, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def cleanup_stealth_messages(user: discord.User, bot_id: int):
    await sweep_orphaned_messages(user, bot_id, signature=GHOST_SIGNATURE)


class TemporaryDM:
    """One bot message for the whole operation, with expiry after completion."""

    def __init__(self, message, embed, view=None):
        self.message = message
        self.embed = embed.copy()
        self.view = view
        self._expires_at = None
        self._delete_task = None
        self._last_progress_edit_at = 0.0
        ACTIVE_TEMPORARY_MESSAGE_IDS.add(message.id)

    def _render(self):
        embed = self.embed.copy()
        footer = str(embed.footer.text or "Madman Services")
        if GHOST_SIGNATURE not in footer:
            footer += GHOST_SIGNATURE
        embed.set_footer(text=footer)
        if self._expires_at is not None:
            embed.description = (embed.description or "")[:3900] + (
                f"\n\n⏳ Essa mensagem será deletada em <t:{self._expires_at}:R>"
            )
        return embed

    @classmethod
    async def send(cls, user, bot_id, *, embed, view=None):
        message = await user.send(embed=embed, view=view)
        dm = cls(message, embed, view)
        await dm.edit()
        schedule_stealth_cleanup(user, bot_id)
        return dm

    def hold(self):
        if self._delete_task is not None:
            self._delete_task.cancel()
            self._delete_task = None
        self._expires_at = None
        self._last_progress_edit_at = 0.0
        ACTIVE_TEMPORARY_MESSAGE_IDS.add(self.message.id)

    async def edit(
        self,
        *,
        content=None,
        embed=None,
        view=discord.utils.MISSING,
        throttle: bool = False,
    ):
        if embed is not None:
            self.embed = embed.copy()
        if content is not None:
            self.embed = discord.Embed(
                title=self.embed.title, description=content[:3900], color=self.embed.color
            )
        if view is not discord.utils.MISSING:
            if self.view is not None and self.view is not view:
                self.view.stop()
            self.view = view

        if throttle:
            now = time.monotonic()
            if (
                self._last_progress_edit_at > 0.0
                and (now - self._last_progress_edit_at) < PROGRESS_THROTTLE_SECONDS
            ):
                return True
            self._last_progress_edit_at = now
        else:
            self._last_progress_edit_at = 0.0

        try:
            await self.message.edit(content=None, embed=self._render(), view=self.view)
            return True
        except discord.HTTPException:
            logger.warning("Falha ao atualizar DM temporaria %s", self.message.id, exc_info=True)
            return False

    async def expire_after(self, delay=RESULT_TTL_SECONDS):
        if self._delete_task is not None:
            self._delete_task.cancel()
        self._expires_at = int(time.time() + delay)
        await self.edit()
        ACTIVE_TEMPORARY_MESSAGE_IDS.discard(self.message.id)

        async def delete_after():
            try:
                await asyncio.sleep(delay)
                await self.message.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                logger.warning("Falha ao apagar DM temporaria %s", self.message.id, exc_info=True)
            if self.view is not None:
                self.view.stop()
            ACTIVE_TEMPORARY_MESSAGE_IDS.discard(self.message.id)

        self._delete_task = _track(delete_after(), name=f"temporary-dm-delete-{self.message.id}")

    async def finish(self, *, content=None, embed=None):
        await self.edit(content=content, embed=embed, view=None)
        await self.expire_after()


async def open_temporary_dm(interaction, *, embed, view=None):
    """Acknowledge immediately and establish the DM before starting any work."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        dm = await TemporaryDM.send(
            interaction.user, interaction.client.user.id, embed=embed, view=view
        )
    except discord.HTTPException:
        await interaction.edit_original_response(
            content="❌ Não consegui enviar a DM. Abra suas DMs e tente novamente. Nada foi iniciado.",
            embed=None, view=None,
        )
        return None

    try:
        await interaction.edit_original_response(
            content=f"Acompanhe a operação na sua DM com o bot: {dm.message.jump_url}",
            embed=None, view=None,
        )
    except discord.HTTPException:
        logger.debug("Falha ao enviar aviso de acompanhamento da DM", exc_info=True)

    async def remove_notice():
        await asyncio.sleep(5)
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass

    _track(remove_notice(), name=f"temporary-dm-notice-{dm.message.id}")
    return dm


async def send_stealth_dm(
    interaction: discord.Interaction,
    title: str,
    description: str,
    color: int = 0x2B2D31,
    delay: int = RESULT_TTL_SECONDS,
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
    delay: int = RESULT_TTL_SECONDS,
):
    """Send a temporary DM without retaining the originating interaction."""

    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Madman Services 🕵️")

    try:
        dm = await TemporaryDM.send(user, bot_id, embed=embed)
        await dm.expire_after(delay)
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
    ACTIVE_TEMPORARY_MESSAGE_IDS.clear()
