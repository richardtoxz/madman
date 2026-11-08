import asyncio
import functools
import json
import os
import sys
import traceback
from contextlib import aclosing

import discord

from ipc_protocol import encode_ready, encode_startup_error
from ipc_protocol import MAX_IPC_LINE_LENGTH
from .core.config import ControlState
from .core.router import register_handlers, dispatch
from .relationships.manager import RelationshipManager
from .guilds.manager import GuildManager
from .messages.manager import MessageManager
from .voice.manager import VoiceManager
from .protection.manager import ProtectionManager
from .presence.manager import PresenceManager
from .account.manager import AccountManager
from .tracker.collection import TrackerCollection
from .quest import QuestManager
from .core.ipc import emit_line, sanitize_ipc_field
from .core.stdio import stdin_lines

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")


# curl_cffi >= 0.14 limita cada mensagem recebida a 4 MB. O READY de contas em
# muitos servidores passa disso e derrubava o gateway; o upstream (pos-2.1.0)
# usa 15 MB pelo mesmo motivo.
GATEWAY_MAX_MESSAGE_SIZE = 15 * 1024 * 1024


def raise_gateway_message_limit() -> None:
    original = discord.http.HTTPClient.ws_connect
    if getattr(original, "_madman_patched", False):
        return

    @functools.wraps(original)
    async def ws_connect(self, url, **kwargs):
        kwargs.setdefault("max_message_size", GATEWAY_MAX_MESSAGE_SIZE)
        return await original(self, url, **kwargs)

    ws_connect._madman_patched = True
    discord.http.HTTPClient.ws_connect = ws_connect


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
        self.account_manager = None
        self._exact_handlers = {}
        self._prefix_handlers = {}
        self._runtime_initialized = False
        self._shutdown_started = False
        self._stdin_task = None
        self._presence_task = None
        self._command_tasks: set[asyncio.Task] = set()
        self._dm_upsert_last: dict[int, float] = {}
        self._dm_upsert_pending: dict[int, asyncio.TimerHandle] = {}

    def _emit_dm_upsert(self, channel, *, debounce: bool = False) -> None:
        if not getattr(self, "msg_manager", None) or channel is None:
            return
        cid = getattr(channel, "id", None)
        if cid is None:
            return
        if debounce:
            try:
                now = asyncio.get_running_loop().time()
            except RuntimeError:
                now = 0.0
            last_map = getattr(self, "_dm_upsert_last", None)
            if last_map is None:
                last_map = {}
                self._dm_upsert_last = last_map
            pending = getattr(self, "_dm_upsert_pending", None)
            if pending is None:
                pending = {}
                self._dm_upsert_pending = pending
            last_sent = last_map.get(int(cid), 0.0)
            if now and (now - last_sent) < 2.0:
                # Janela ativa: agenda um unico envio ao fim dela, para o
                # painel nao ficar com a ultima mensagem de uma rajada perdida.
                if int(cid) not in pending:
                    delay = max(0.05, 2.0 - (now - last_sent))

                    def _flush(channel=channel, key=int(cid)):
                        pending.pop(key, None)
                        last_map[key] = asyncio.get_running_loop().time()
                        self._emit_dm_upsert(channel, debounce=False)

                    pending[int(cid)] = asyncio.get_running_loop().call_later(delay, _flush)
                return
            if now:
                last_map[int(cid)] = now
        try:
            serialized = self.msg_manager._serialize_private_channel(channel)
            if serialized:
                emit_line("EVENT:DM_UPSERT:" + json.dumps(serialized, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            pass

    def spawn_command_task(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine, name="worker-command")
        self._command_tasks.add(task)
        task.add_done_callback(self._command_task_done)
        return task

    def _command_task_done(self, task: asyncio.Task) -> None:
        self._command_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            emit_line(f"ERROR:CommandTask:{type(error).__name__}")

    async def _run_manager_hook(self, kind: str, method_name: str) -> None:
        manager = getattr(self, f"{kind}_manager", None)
        if manager is None:
            return
        hook = getattr(manager, method_name, None)
        if hook is None:
            return
        try:
            await hook()
        except Exception as error:
            emit_line(f"ERROR:{method_name}:{kind}:{type(error).__name__}")

    async def on_ready(self):
        if self._runtime_initialized:
            emit_line(f"EVENT:RECONNECTED:{self.user.id}")
            await self._run_manager_hook("presence", "refresh")
            await self._run_manager_hook("voice", "on_reconnect")
            await self._run_manager_hook("tracker", "on_reconnect")
            return

        try:
            self.msg_manager = MessageManager(self, self.control_state)
            self.rel_manager = RelationshipManager(self)
            self.guild_manager = GuildManager(self)
            self.voice_manager = VoiceManager(self)
            self.protection_manager = ProtectionManager(self)
            self.presence_manager = PresenceManager(self)
            self.tracker_manager = TrackerCollection(self)
            self.quest_manager = QuestManager(self)
            self.account_manager = AccountManager(self)
            self._exact_handlers, self._prefix_handlers = register_handlers(self)
            self._runtime_initialized = True
            self._stdin_task = asyncio.create_task(self._listen_stdin(), name="worker-stdin-listener")
            self._presence_task = asyncio.create_task(self.presence_manager.set_main(), name="worker-main-presence")
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
        if changes:
            emit_line(f"EVENT:REL_UPDATE:{after.user.id}:{'|'.join(changes)}")

    async def on_relationship_remove(self, relationship):
        emit_line(
            f"EVENT:REL_REMOVE:{relationship.user.id}:{relationship.type}:{sanitize_ipc_field(relationship.user.name)}"
        )

    async def on_guild_join(self, guild):
        emit_line(f"EVENT:GUILD_JOIN:{guild.id}:{sanitize_ipc_field(guild.name)}")
        if self.tracker_manager:
            await self.tracker_manager.on_guild_join(guild)

    async def on_guild_remove(self, guild):
        emit_line(f"EVENT:GUILD_REMOVE:{guild.id}:{sanitize_ipc_field(guild.name)}")
        if self.tracker_manager:
            await self.tracker_manager.on_guild_remove(guild)

    async def on_disconnect(self):
        if self.tracker_manager:
            await self.tracker_manager.on_disconnect()

    async def on_resumed(self):
        if self.tracker_manager:
            await self.tracker_manager.on_reconnect()

    async def on_guild_update(self, before, after):
        if before.name != after.name:
            emit_line(f"EVENT:GUILD_UPDATE:{after.id}:name:{sanitize_ipc_field(after.name)}")

    async def on_voice_state_update(self, member, before, after):
        if self.voice_manager:
            await self.voice_manager.on_voice_state_update(member, before, after)
        if self.tracker_manager:
            await self.tracker_manager.on_voice_state_update(member, before, after)

    async def on_private_channel_create(self, channel):
        self._emit_dm_upsert(channel, debounce=False)

    async def on_private_channel_update(self, before, after):
        self._emit_dm_upsert(after, debounce=False)

    async def on_private_channel_delete(self, channel):
        cid = getattr(channel, "id", None)
        if cid is not None:
            timer = self._dm_upsert_pending.pop(int(cid), None)
            if timer is not None:
                timer.cancel()
            self._dm_upsert_last.pop(int(cid), None)
            emit_line("EVENT:DM_REMOVE:" + json.dumps({"id": str(cid)}, ensure_ascii=False, separators=(",", ":")))

    async def on_message(self, message):
        if self.protection_manager:
            await self.protection_manager.on_message(message)
        if self.tracker_manager:
            await self.tracker_manager.on_message(message)
        channel = getattr(message, "channel", None)
        if isinstance(channel, (discord.DMChannel, discord.GroupChannel)):
            if getattr(message, "id", None) is not None:
                try:
                    channel.last_message_id = message.id
                except Exception:
                    pass
            self._emit_dm_upsert(channel, debounce=True)

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

    async def on_raw_bulk_message_delete(self, payload):
        if self.tracker_manager:
            await self.tracker_manager.on_raw_bulk_message_delete(payload)

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
                new_status = discord.Status.invisible if after.status == discord.Status.offline else after.status
                await self.change_presence(status=new_status, edit_settings=False)

    async def _listen_stdin(self):
        try:
            async with aclosing(stdin_lines(sys.stdin.buffer, limit=MAX_IPC_LINE_LENGTH)) as lines:
                async for line in lines:
                    if self.is_closed():
                        return
                    await dispatch(
                        self,
                        self._exact_handlers,
                        self._prefix_handlers,
                        line.decode("utf-8", errors="replace"),
                    )
                    if self.is_closed():
                        return
            await self.close()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            emit_line(f"ERROR:Erro no comando: {type(error).__name__}")
            await self.close()

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
        command_tasks = [task for task in tuple(self._command_tasks) if task is not current and not task.done()]
        for task in command_tasks:
            task.cancel()
        if command_tasks:
            await asyncio.gather(*command_tasks, return_exceptions=True)
        self._command_tasks.clear()

        await self._run_manager_hook("quest", "shutdown")
        await self._run_manager_hook("tracker", "shutdown")
        await self._run_manager_hook("voice", "shutdown")
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
    raise_gateway_message_limit()
    client = MySelfbot(
        sync_presence=True,
        max_messages=100,
        chunk_guilds_at_startup=False,
    )
    try:
        await client.start(token)
    except discord.LoginFailure:
        emit_line("STARTUP_ERROR:" + encode_startup_error("login_failed", "Token invalido ou revogado."))
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
        token = sys.stdin.buffer.readline(MAX_IPC_LINE_LENGTH + 1).decode("utf-8", errors="strict").strip()
    except Exception as error:
        emit_line(
            "STARTUP_ERROR:"
            + encode_startup_error(
                "credential_read_failed",
                f"Falha ao ler a credencial: {type(error).__name__}",
            )
        )
        raise SystemExit(1)

    if len(token) < 30 or len(token) > 512 or any(character in token for character in ("\n", "\r", "\x00")):
        emit_line("STARTUP_ERROR:" + encode_startup_error("invalid_credential", "Credencial invalida."))
        raise SystemExit(1)

    exit_code = 0
    try:
        asyncio.run(main(token))
    except KeyboardInterrupt:
        exit_code = 130
    except BaseException:
        traceback.print_exc()
        exit_code = 1
    # A thread de leitura do stdin fica bloqueada em readline(); deixar o Python
    # finalizar com ela viva abortava o processo com "Fatal Python error"
    # (_enter_buffered_busy). Tudo ja foi fechado: sai direto, sem finalizacao.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(exit_code)
