from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest


# Panel and worker use different Discord libraries. Run these scenarios in a
# fresh panel process, as production does, even during full test discovery.
SCENARIOS = textwrap.dedent("""
    import asyncio
    import json
    import sys
    import time
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock, patch

    import panel
    import discord
    from panel.shared.stealth_dm import TemporaryDM, send_stealth_dm_to_user, shutdown_stealth_tasks
    from panel.shared.sweeper import (
        ACTIVE_TEMPORARY_MESSAGE_IDS, GHOST_SIGNATURE, sweep_orphaned_messages,
    )
    from panel.shared.tasks import cancel_panel_tasks
    from panel.cleanup.handlers import handle_clean_all_dms, handle_close_dms
    from panel.social.handlers import (
        handle_clean_friends, handle_pending_requests, handle_unblock_all,
        handle_mute_servers, handle_leave_servers,
    )
    from panel.quest.handlers import handle_quest
    from panel.cleanup.modals import CleanDMModal, CleanGuildModal
    from panel.cleanup.progress_view import ProgressView

    class Message:
        def __init__(self, message_id=101, author_id=456):
            self.id = message_id
            self.author = NS(id=author_id)
            self.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
            self.jump_url = "https://discord.com/channels/@me/1/101"
            self.embeds = []
            self.view = None
            self.edit = AsyncMock(side_effect=self.update)
            self.delete = AsyncMock()

        async def update(self, **kwargs):
            if "embed" in kwargs:
                self.embeds = [kwargs["embed"]] if kwargs["embed"] else []
            if "view" in kwargs:
                self.view = kwargs["view"]
            return self

    def setup():
        message = Message()
        async def history(**kwargs):
            yield message
        user = NS(id=123, send=AsyncMock(return_value=message), dm_channel=NS(history=history))
        client = NS(user=NS(id=456))
        interaction = NS(
            user=user, client=client, message=message,
            response=NS(is_done=lambda: False, defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(), delete_original_response=AsyncMock(),
            followup=NS(send=AsyncMock()),
        )
        manager = NS(active_processes={123: object()}, control_clean=AsyncMock(return_value=True))
        return message, interaction, manager

    def expire(interaction):
        # Any use of the old token after setup must fail this scenario.
        interaction.edit_original_response.side_effect = AssertionError("expired interaction token used")

    async def blocked():
        message, inter, manager = setup()
        response = NS(status=403, reason="Forbidden")
        inter.user.send.side_effect = discord.Forbidden(response, {"code": 50007, "message": "closed DMs"})
        manager.list_open_dms = AsyncMock()
        await handle_clean_all_dms(inter, manager, 123)
        manager.list_open_dms.assert_not_awaited()
        assert "Nada foi iniciado" in inter.edit_original_response.await_args.kwargs["content"]
        inter.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)

    async def cleanup(guild=False):
        message, inter, manager = setup()
        async def clean(*args, update_callback):
            expire(inter)
            await update_callback(0, 2, "WAITING_START")
            assert message.view.timeout is None
            await update_callback(2, 2, "CLEANING")
            return "COMPLETADO:Deletadas 2 de 2; restantes 0."
        manager.send_command = AsyncMock(side_effect=clean)
        manager.clean_guild_messages = AsyncMock(side_effect=clean)
        modal = CleanGuildModal(manager, 123) if guild else CleanDMModal(manager, 123)
        field = modal.guild_id_input if guild else modal.target_id
        field._value = "123456789012345678"
        await modal.on_submit(inter)
        await asyncio.gather(*tuple(inter.client._panel_background_tasks))
        inter.user.send.assert_awaited_once()
        assert message.view is None
        assert "2" in message.embeds[0].description
        assert "Essa mensagem será deletada" in message.embeds[0].description
        assert message.id not in ACTIVE_TEMPORARY_MESSAGE_IDS
        modal.stop()

    async def all_dms(close_single=False):
        message, inter, manager = setup()
        manager.list_open_dms = AsyncMock(return_value=[{"id": 1, "user_id": 1, "name": "Alvo"}])
        manager.close_single_dm = AsyncMock(return_value=(True, "OK"))
        async def clean(*args, update_callback):
            await update_callback(1, 1, "ALL_DMS_START", None)
            await update_callback(1, 1, "ALL_DMS_CHANNEL", "Alvo")
            await update_callback(1, 1, "ALL_DMS_FOUND", "Alvo:2")
            await update_callback(2, 2, "CLEANING", None)
            return "COMPLETADO:Deletadas 2 de 2."
        manager.clean_all_dms = AsyncMock(side_effect=clean)
        await handle_clean_all_dms(inter, manager, 123)
        expire(inter)
        button = next(item for item in message.view.children if (
            item.label == "Fechar esta DM" if close_single else (item.label or "").startswith("Limpar ")
        ))
        await button.callback(inter)
        assert message.view is None
        inter.user.send.assert_awaited_once()
        assert "Essa mensagem será deletada" in message.embeds[0].description

    async def close_conversations(groups=False):
        message, inter, manager = setup()
        async def close(*args, update_callback):
            await update_callback(2, 2, "LEAVING" if groups else "CLOSING", None)
            return "COMPLETADO:2 conversas."
        manager.close_all_dms = AsyncMock(side_effect=close)
        manager.leave_all_group_dms = AsyncMock(side_effect=close)
        await handle_close_dms(inter, manager, 123)
        expire(inter)
        await message.view.children[1 if groups else 0].callback(inter)
        assert message.view is None
        assert "2 conversas" in message.embeds[0].description
        inter.user.send.assert_awaited_once()

    async def social(kind):
        message, inter, manager = setup()
        manager.get_rel_stats = AsyncMock(return_value={"friends": 2, "fresh": True})
        manager.get_pending_requests = AsyncMock(return_value={"incoming_total": 2, "outgoing_total": 1})
        manager.get_blocked_list = AsyncMock(return_value={"total": 2, "fresh": True})
        manager.get_guild_stats = AsyncMock(return_value={"total": 2, "fresh": True, "guild_ids": [1, 2]})
        manager.get_guild_whitelist = lambda user_id: []
        async def run(*args):
            progress = args[-1]
            assert message.view.timeout is None
            await progress(1, 2, "CLEANING", "Alvo")
            return "COMPLETADO:2 itens; restantes 0."
        for name in ("start_rel_cleanup", "accept_all_requests", "deny_all_requests", "unblock_all", "start_guild_cleanup"):
            setattr(manager, name, AsyncMock(side_effect=run))
        handlers = {
            "friends": handle_clean_friends, "accept": handle_pending_requests,
            "deny": handle_pending_requests, "unblock": handle_unblock_all,
            "mute": handle_mute_servers, "leave": handle_leave_servers,
        }
        await handlers[kind](inter, manager, 123)
        expire(inter)
        if kind == "deny":
            button = next(item for item in message.view.children if (item.label or "").startswith("Remover Todos"))
        else:
            button = message.view.children[0]
        await button.callback(inter)
        if kind == "unblock":
            await message.view.children[0].callback(inter)
        assert message.view is None
        assert "2 itens" in message.embeds[0].description
        assert "Essa mensagem será deletada" in message.embeds[0].description
        inter.user.send.assert_awaited_once()

    async def quest():
        message, inter, manager = setup()
        manager.is_authenticated = lambda user_id: True
        manager._get_proc = lambda user_id: object()
        callbacks = []
        manager.subscribe_events = lambda user_id, prefix, callback: callbacks.append(callback)
        manager.unsubscribe_events = lambda *args: None
        async def start(user_id):
            expire(inter)
            await callbacks[0]("QUEST:RESULT:" + json.dumps({"ok": True, "message": "Fila finalizada.", "completed": 1}))
            return {"ok": True}
        manager.quest_auto = AsyncMock(side_effect=start)
        await handle_quest(inter, manager, 123)
        assert "Fila finalizada" in message.embeds[0].description
        assert "Fila iniciada" not in message.embeds[0].description
        assert 123 not in inter.client._quest_sessions
        assert message.id not in ACTIVE_TEMPORARY_MESSAGE_IDS

    async def controls():
        message, inter, manager = setup()
        view = ProgressView(123, manager)
        dm = TemporaryDM(message, discord.Embed(description="Aguardando"), view)
        view.dm = dm
        await dm.edit()
        async def control(*args):
            assert inter.response.defer.await_count > 0
            return True
        manager.control_clean.side_effect = control
        expire(inter)
        await view.start_cleanup(inter)
        assert view.started and message.view is view
        await view.cancel(inter)
        assert view.cancelled_by_user and dm.view is None and message.view is None
        await dm.finish(content="CANCELADO")

    async def failure():
        message, inter, manager = setup()
        manager.get_rel_stats = AsyncMock(return_value={"friends": 2, "fresh": True})
        manager.start_rel_cleanup = AsyncMock(side_effect=RuntimeError("worker failed"))
        await handle_clean_friends(inter, manager, 123)
        expire(inter)
        try:
            await message.view.children[0].callback(inter)
        except RuntimeError:
            pass
        else:
            raise AssertionError("worker error was swallowed")
        assert message.view is None
        assert "Falha inesperada" in message.embeds[0].description
        assert "Essa mensagem será deletada" in message.embeds[0].description
        assert message.id not in ACTIVE_TEMPORARY_MESSAGE_IDS

    async def lifecycle():
        message, inter, manager = setup()
        view = discord.ui.View(timeout=None)
        dm = TemporaryDM(message, discord.Embed(description="Em execução"), view)
        await dm.edit()
        assert dm._delete_task is None
        await sweep_orphaned_messages(inter.user, 456)
        message.delete.assert_not_awaited()
        await dm.expire_after(300)
        pending = dm._delete_task
        dm.hold()
        await asyncio.gather(pending, return_exceptions=True)
        assert not view.is_finished()
        assert message.id in ACTIVE_TEMPORARY_MESSAGE_IDS
        with patch("panel.shared.stealth_dm.asyncio.sleep", new=AsyncMock()) as sleep:
            await dm.finish(content="Concluído")
            await dm._delete_task
        sleep.assert_awaited_once_with(120)
        message.delete.assert_awaited_once()
        assert view.is_finished() and message.view is None

    async def sweeper():
        now = int(time.time())
        messages = [Message(index) for index in range(1, 5)]
        for message in messages:
            embed = discord.Embed(description="Resumo")
            embed.set_footer(text="Madman" + GHOST_SIGNATURE)
            message.embeds = [embed]
        ACTIVE_TEMPORARY_MESSAGE_IDS.add(1)
        messages[1].embeds[0].description += f"\\n\\n⏳ Essa mensagem será deletada em <t:{now + 300}:R>"
        messages[2].embeds[0].description += f"\\n\\n⏳ Essa mensagem será deletada em <t:{now - 1}:R>"
        messages[3].author.id = 999
        async def history(**kwargs):
            for message in messages:
                yield message
        await sweep_orphaned_messages(NS(dm_channel=NS(history=history)), 456)
        for index in (0, 1, 3):
            messages[index].delete.assert_not_awaited()
        messages[2].delete.assert_awaited_once()

    async def notification():
        message, inter, manager = setup()
        assert await send_stealth_dm_to_user(inter.user, 456, "Aviso", "Resultado", delay=600)
        await sweep_orphaned_messages(inter.user, 456)
        message.delete.assert_not_awaited()
        assert "Resultado" in message.embeds[0].description
        assert "Essa mensagem será deletada" in message.embeds[0].description
        inter.user.send.assert_awaited_once()

    async def main():
        scenario = sys.argv[1]
        try:
            if scenario.startswith("social_"):
                await social(scenario.removeprefix("social_"))
            elif scenario == "guild_cleanup":
                await cleanup(guild=True)
            elif scenario == "close_single":
                await all_dms(close_single=True)
            elif scenario == "leave_groups":
                await close_conversations(groups=True)
            else:
                await globals()[scenario]()
        finally:
            await shutdown_stealth_tasks()
    asyncio.run(main())
""")


class TemporaryDMTests(unittest.TestCase):
    def _check(self, scenario):
        result = subprocess.run(
            [sys.executable, "-c", SCENARIOS, scenario],
            capture_output=True,
            encoding="utf-8",
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blocked_dms_do_not_start_work(self):
        self._check("blocked")

    def test_message_cleanup_survives_expired_interaction(self):
        self._check("cleanup")

    def test_guild_cleanup_survives_expired_interaction(self):
        self._check("guild_cleanup")

    def test_all_dms_keep_progress_and_result_in_one_message(self):
        self._check("all_dms")

    def test_closing_last_dm_finishes_the_temporary_message(self):
        self._check("close_single")

    def test_closing_conversations_survives_expired_interaction(self):
        self._check("close_conversations")

    def test_leaving_groups_survives_expired_interaction(self):
        self._check("leave_groups")

    def test_all_social_bulk_actions_survive_expired_interaction(self):
        for kind in ("friends", "accept", "deny", "unblock", "mute", "leave"):
            with self.subTest(kind=kind):
                self._check("social_" + kind)

    def test_quest_preserves_result_that_arrives_before_acknowledgement(self):
        self._check("quest")

    def test_start_and_cancel_acknowledge_before_worker_calls(self):
        self._check("controls")

    def test_expiry_starts_after_completion_and_holding_cancels_previous_timer(self):
        self._check("lifecycle")

    def test_sweeper_preserves_active_and_unexpired_messages(self):
        self._check("sweeper")

    def test_worker_failure_finishes_and_expires_the_dm(self):
        self._check("failure")

    def test_existing_notifications_share_expiry_and_survive_early_sweeping(self):
        self._check("notification")
