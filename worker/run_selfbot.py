import asyncio
import sys
from contextlib import suppress

import discord

from ipc_protocol import encode_ready, encode_startup_error
from ipc_protocol import MAX_IPC_LINE_LENGTH
from .config import ControlState
from .command_router import register_handlers, dispatch
from .relationship_manager import RelationshipManager
from .guild_manager import GuildManager
from .message_manager import MessageManager
from .voice_manager import VoiceManager
from .protection_manager import ProtectionManager
from .presence_manager import PresenceManager
from .tracker_manager import TrackerManager
from .quest import QuestManager
from .ipc import emit_line, sanitize_ipc_field

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")


class MySelfbot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_state = ControlState()
        self.msg_manager = None
        self.rel_manager = None
        self.guild_manager = None
        self.voice_manager = None
        self.protection_manager = None
        self.presence_manager = None
        self.tracker_manager = None
        self.quest_manager = None
        self._exact_handlers = {}
        self._prefix_handlers = {}
        self._runtime_initialized = False
        self._shutdown_started = False
        self._stdin_task = None
        self._presence_task = None
        self._command_tasks: set[asyncio.Task] = set()

    def spawn_command_task(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine, name="worker-command")
        self._command_tasks.add(task)
        task.add_done_callback(self._command_task_done)
        return task

    def _command_task_done(self, task: asyncio.Task) -> None:
        self._command_tasks.discard(task)
        if task.cancelled():
            return
        with suppress(Exception):
            task.result()

    async def on_ready(self):
        if self._runtime_initialized:
            emit_line(f"EVENT:RECONNECTED:{self.user.id}")
            if self.voice_manager:
                with suppress(Exception):
                    await self.voice_manager.on_reconnect()
            if self.tracker_manager:
                with suppress(Exception):
                    await self.tracker_manager.on_reconnect()
            return

        try:
            self.msg_manager = MessageManager(self, self.control_state)
            self.rel_manager = RelationshipManager(self)
            self.guild_manager = GuildManager(self)
            self.voice_manager = VoiceManager(self)
            self.protection_manager = ProtectionManager(self)
            self.presence_manager = PresenceManager(self)
            self.tracker_manager = TrackerManager(self)
            self.quest_manager = QuestManager(self)
            self._exact_handlers, self._prefix_handlers = register_handlers(self)
            self._runtime_initialized = True
            self._stdin_task = asyncio.create_task(
                self._listen_stdin(), name="worker-stdin-listener"
            )
            self._presence_task = asyncio.create_task(
                self.presence_manager.set_main(), name="worker-main-presence"
            )
        except Exception as error:
            emit_line(
                "STARTUP_ERROR:"
                + encode_startup_error(
                    "initialization_failed",
                    f"Falha ao preparar o worker: {type(error).__name__}",
                )
            )
            await self.close()
            return

        emit_line(f"READY:{encode_ready(str(self.user), self.user.id)}")

    async def on_relationship_add(self, relationship):
        if self.protection_manager:
            await self.protection_manager.on_relationship_add(relationship)

        type_names = {
            discord.RelationshipType.friend: "FRIEND",
            discord.RelationshipType.blocked: "BLOCKED",
            discord.RelationshipType.incoming_request: "INCOMING_REQUEST",
            discord.RelationshipType.outgoing_request: "OUTGOING_REQUEST",
        }
        emit_line(
            f"EVENT:REL_ADD:{relationship.user.id}"
            f":{type_names.get(relationship.type, str(relationship.type))}"
            f":{sanitize_ipc_field(relationship.user.name)}"
        )

    async def on_relationship_update(self, before, after):
        changes = []
        if before.type != after.type:
            changes.append(f"type:{before.type}->{after.type}")
        if before.nick != after.nick:
            changes.append("nick:changed")
        emit_line(
            f"EVENT:REL_UPDATE:{after.user.id}:{'|'.join(changes) if changes else 'presence'}"
        )

    async def on_relationship_remove(self, relationship):
        emit_line(
            f"EVENT:REL_REMOVE:{relationship.user.id}:{relationship.type}:"
            f"{sanitize_ipc_field(relationship.user.name)}"
        )

    async def on_guild_join(self, guild):
        emit_line(f"EVENT:GUILD_JOIN:{guild.id}:{sanitize_ipc_field(guild.name)}")

    async def on_guild_remove(self, guild):
        emit_line(f"EVENT:GUILD_REMOVE:{guild.id}:{sanitize_ipc_field(guild.name)}")

    async def on_guild_update(self, before, after):
        if before.name != after.name:
            emit_line(
                f"EVENT:GUILD_UPDATE:{after.id}:name:"
                f"{sanitize_ipc_field(after.name)}"
            )

    async def on_voice_state_update(self, member, before, after):
        if self.voice_manager:
            await self.voice_manager.on_voice_state_update(member, before, after)
        if self.tracker_manager:
            await self.tracker_manager.on_voice_state_update(member, before, after)

    async def on_message(self, message):
        if self.protection_manager:
            await self.protection_manager.on_message(message)
        if self.tracker_manager:
            await self.tracker_manager.on_message(message)

    async def on_message_edit(self, before, after):
        if self.protection_manager:
            await self.protection_manager.on_message_edit(before, after)

    async def on_user_update(self, before, after):
        if self.tracker_manager:
            await self.tracker_manager.on_user_update(before, after)

    async def on_presence_update(self, before, after):
        if self.tracker_manager:
            await self.tracker_manager.on_presence_update(before, after)

    async def on_member_join(self, member):
        if self.tracker_manager:
            await self.tracker_manager.on_member_join(member)

    async def on_member_update(self, before, after):
        if self.tracker_manager:
            await self.tracker_manager.on_member_update(before, after)

    async def on_raw_member_remove(self, payload):
        if self.tracker_manager:
            await self.tracker_manager.on_raw_member_remove(payload)

    async def on_typing(self, channel, user, when):
        if self.tracker_manager:
            await self.tracker_manager.on_typing(channel, user, when)

    async def on_raw_message_edit(self, payload):
        if self.tracker_manager:
            await self.tracker_manager.on_raw_message_edit(payload)

    async def on_raw_message_delete(self, payload):
        if self.tracker_manager:
            await self.tracker_manager.on_raw_message_delete(payload)

    async def on_raw_reaction_add(self, payload):
        if self.tracker_manager:
            await self.tracker_manager.on_raw_reaction_add(payload)

    async def on_raw_reaction_remove(self, payload):
        if self.tracker_manager:
            await self.tracker_manager.on_raw_reaction_remove(payload)

    async def on_raw_poll_vote_add(self, payload):
        if self.tracker_manager:
            await self.tracker_manager.on_raw_poll_vote_add(payload)

    async def on_raw_poll_vote_remove(self, payload):
        if self.tracker_manager:
            await self.tracker_manager.on_raw_poll_vote_remove(payload)

    async def on_call_create(self, call):
        if self.tracker_manager:
            await self.tracker_manager.on_call_create(call)

    async def on_call_update(self, before, after):
        if self.tracker_manager:
            await self.tracker_manager.on_call_update(before, after)

    async def on_call_delete(self, call):
        if self.tracker_manager:
            await self.tracker_manager.on_call_delete(call)

    async def on_settings_update(self, before, after):
        if hasattr(after, "status") and after.status is not None:
            if before.status != after.status:
                new_status = (
                    discord.Status.invisible
                    if after.status == discord.Status.offline
                    else after.status
                )
                await self.change_presence(status=new_status, edit_settings=False)

    async def _listen_stdin(self):
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=MAX_IPC_LINE_LENGTH)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        try:
            while not self.is_closed():
                line = await reader.readline()
                if not line:
                    await self.close()
                    return
                await dispatch(
                    self,
                    self._exact_handlers,
                    self._prefix_handlers,
                    line.decode("utf-8", errors="replace"),
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            emit_line(f"ERROR:Erro no comando: {type(error).__name__}")
        finally:
            transport.close()

    async def _safe_exec(self, coro, presence_task_token: int | None = None):
        try:
            await coro
        except Exception as error:
            emit_line(f"ERROR:Execucao falhou:{type(error).__name__}")
        finally:
            if self.presence_manager:
                await self.presence_manager.complete_task(presence_task_token)

    async def close(self):
        if self._shutdown_started:
            return
        self._shutdown_started = True

        current = asyncio.current_task()
        command_tasks = [
            task
            for task in tuple(self._command_tasks)
            if task is not current and not task.done()
        ]
        for task in command_tasks:
            task.cancel()
        if command_tasks:
            await asyncio.gather(*command_tasks, return_exceptions=True)
        self._command_tasks.clear()

        if self.quest_manager:
            with suppress(Exception):
                await self.quest_manager.shutdown()
        if self.tracker_manager:
            with suppress(Exception):
                await self.tracker_manager.shutdown()
        if self.voice_manager:
            with suppress(Exception):
                await self.voice_manager.shutdown()
        background_tasks = [
            task
            for task in (self._stdin_task, self._presence_task)
            if task is not None and task is not current and not task.done()
        ]
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

        await super().close()


async def main(token: str):
    client = MySelfbot(sync_presence=True)
    try:
        await client.start(token)
    except discord.LoginFailure:
        emit_line(
            "STARTUP_ERROR:"
            + encode_startup_error("login_failed", "Token invalido ou revogado.")
        )
    except Exception as error:
        safe_message = str(error).replace(token, "[REDACTED]")[:500]
        emit_line(
            "STARTUP_ERROR:"
            + encode_startup_error(
                "discord_connection_failed",
                safe_message or type(error).__name__,
            )
        )
    finally:
        token = ""


if __name__ == "__main__":
    try:
        token = sys.stdin.buffer.readline(MAX_IPC_LINE_LENGTH + 1).decode(
            "utf-8", errors="strict"
        ).strip()
    except Exception as error:
        emit_line(
            "STARTUP_ERROR:"
            + encode_startup_error(
                "credential_read_failed",
                f"Falha ao ler a credencial: {type(error).__name__}",
            )
        )
        raise SystemExit(1)

    if (
        len(token) < 30
        or len(token) > 512
        or any(character in token for character in ("\n", "\r", "\x00"))
    ):
        emit_line(
            "STARTUP_ERROR:"
            + encode_startup_error("invalid_credential", "Credencial invalida.")
        )
        raise SystemExit(1)

    asyncio.run(main(token))
