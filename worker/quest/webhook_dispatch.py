from __future__ import annotations

import aiohttp
import discord

from ..ipc import emit_line


class QuestWebhookDispatchMixin:
    async def _get_webhook_session(self):
        if self._webhook_session is None or self._webhook_session.closed:
            self._webhook_session = aiohttp.ClientSession()
        return self._webhook_session

    async def close_webhook_session(self):
        if self._webhook_session is not None and not self._webhook_session.closed:
            await self._webhook_session.close()
        self._webhook_session = None

    async def _dispatch_webhook_embed(
        self,
        *,
        title: str,
        color: int,
        description: str | None = None,
        fields: list[tuple[str, str, bool]] | None = None,
    ):
        if not self.webhook_url:
            return

        embed = discord.Embed(
            title=title,
            color=color,
            description=description,
            timestamp=discord.utils.utcnow(),
        )
        if fields:
            for name, value, inline in fields:
                embed.add_field(
                    name=str(name)[:256],
                    value=str(value)[:1024] or "-",
                    inline=inline,
                )

        embed.set_footer(text="Madman Quest Logger")

        try:
            session = await self._get_webhook_session()
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(
                embed=embed,
                username="Madman Quest",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            emit_line(f"QUEST:WEBHOOK_ERROR:{type(exc).__name__}")

    async def dispatch_quest_start(self, quest, task_name: str):
        await self._dispatch_webhook_embed(
            title="Quest Iniciada",
            color=0x5865F2,
            fields=[
                ("Quest", f"{quest.quest_name}\n`{quest.id}`", False),
                ("Aplicacao", quest.application_name, True),
                ("Tipo", task_name, True),
            ],
        )

    async def dispatch_quest_progress(
        self,
        *,
        quest,
        task_name: str,
        progress: int,
        target: int,
    ):
        pct = min(100, int((progress / target) * 100)) if target else 0
        await self._dispatch_webhook_embed(
            title="Quest Progresso",
            color=0xFEE75C,
            fields=[
                ("Quest", f"{quest.quest_name}\n`{quest.id}`", False),
                ("Tipo", task_name, True),
                ("Progresso", f"{progress}/{target} ({pct}%)", True),
            ],
        )

    async def dispatch_quest_complete(self, *, quest, task_name: str):
        await self._dispatch_webhook_embed(
            title="Quest Completa",
            color=0x57F287,
            fields=[
                ("Quest", f"{quest.quest_name}\n`{quest.id}`", False),
                ("Tipo", task_name, True),
            ],
        )

    async def dispatch_quest_error(self, *, quest_name: str, detail: str):
        await self._dispatch_webhook_embed(
            title="Erro em Quest",
            color=0xED4245,
            description=detail[:900],
            fields=[("Quest", quest_name, False)],
        )
