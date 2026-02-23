import os
import sys

_lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")

for mod_name in list(sys.modules.keys()):
    if mod_name.startswith('discord'):
        del sys.modules[mod_name]

if os.path.exists(_lib_path):
    sys.path.insert(0, _lib_path)
    print(f"DEBUG:LIB_PATH:{_lib_path}", flush=True)
else:
    print(f"ERROR:LIB_NOT_FOUND:{_lib_path}", flush=True)
    sys.exit(1)

import discord
print(f"DEBUG:DISCORD_VERSION:{discord.__version__}", flush=True)
import asyncio
import json

sys.stdout.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')

from config import control_flags
from command_router import register_handlers, dispatch
from relationship_manager import RelationshipManager
from guild_manager import GuildManager
from message_manager import MessageManager
from voice_manager import VoiceManager
from protection_manager import ProtectionManager
from presence_manager import PresenceManager
from tracker_manager import TrackerManager


class MySelfbot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.msg_manager = None
        self.rel_manager = None
        self.guild_manager = None
        self.voice_manager = None
        self.protection_manager = None
        self.presence_manager = None
        self.tracker_manager = None
        self._exact_handlers = {}
        self._prefix_handlers = {}

    async def on_ready(self):
        print(f'READY:{self.user}:{self.user.id}', flush=True)

        self.msg_manager = MessageManager(self, control_flags)
        self.rel_manager = RelationshipManager(self)
        self.guild_manager = GuildManager(self)
        self.voice_manager = VoiceManager(self)
        self.protection_manager = ProtectionManager(self)
        self.presence_manager = PresenceManager(self)
        self.tracker_manager = TrackerManager(self)

        self._exact_handlers, self._prefix_handlers = register_handlers(self)
        asyncio.create_task(self._listen_stdin())
        asyncio.create_task(self.presence_manager.set_main())

    async def on_relationship_add(self, relationship):
        if self.protection_manager:
            await self.protection_manager.on_relationship_add(relationship)

        type_names = {
            discord.RelationshipType.friend: "FRIEND",
            discord.RelationshipType.blocked: "BLOCKED",
            discord.RelationshipType.incoming_request: "INCOMING_REQUEST",
            discord.RelationshipType.outgoing_request: "OUTGOING_REQUEST",
        }
        print(
            f"EVENT:REL_ADD:{relationship.user.id}"
            f":{type_names.get(relationship.type, str(relationship.type))}"
            f":{relationship.user.name}",
            flush=True,
        )

    async def on_relationship_update(self, before, after):
        changes = []
        if before.type != after.type:
            changes.append(f"type:{before.type}->{after.type}")
        if before.nick != after.nick:
            changes.append(f"nick:{before.nick}->{after.nick}")
        print(
            f"EVENT:REL_UPDATE:{after.user.id}:{'|'.join(changes) if changes else 'presence'}",
            flush=True,
        )

    async def on_relationship_remove(self, relationship):
        print(
            f"EVENT:REL_REMOVE:{relationship.user.id}:{relationship.type}:{relationship.user.name}",
            flush=True,
        )

    async def on_guild_join(self, guild):
        print(f"EVENT:GUILD_JOIN:{guild.id}:{guild.name}", flush=True)

    async def on_guild_remove(self, guild):
        print(f"EVENT:GUILD_REMOVE:{guild.id}:{guild.name}", flush=True)

    async def on_guild_update(self, before, after):
        if before.name != after.name:
            print(f"EVENT:GUILD_UPDATE:{after.id}:name:{after.name}", flush=True)

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

    async def on_user_update(self, before, after):
        if self.tracker_manager: await self.tracker_manager.on_user_update(before, after)

    async def on_presence_update(self, before, after):
        if self.tracker_manager: await self.tracker_manager.on_presence_update(before, after)

    async def on_member_join(self, member):
        if self.tracker_manager: await self.tracker_manager.on_member_join(member)

    async def on_raw_member_remove(self, payload):
        if self.tracker_manager: await self.tracker_manager.on_raw_member_remove(payload)

    async def on_typing(self, channel, user, when):
        if self.tracker_manager: await self.tracker_manager.on_typing(channel, user, when)

    async def on_raw_message_edit(self, payload):
        if self.tracker_manager: await self.tracker_manager.on_raw_message_edit(payload)

    async def on_raw_message_delete(self, payload):
        if self.tracker_manager: await self.tracker_manager.on_raw_message_delete(payload)

    async def on_raw_reaction_add(self, payload):
        if self.tracker_manager: await self.tracker_manager.on_raw_reaction_add(payload)

    async def on_settings_update(self, before, after):
        if hasattr(after, 'status') and after.status is not None:
            if before.status != after.status:
                new_status = (
                    discord.Status.invisible
                    if after.status == discord.Status.offline
                    else after.status
                )
                await self.change_presence(status=new_status, edit_settings=False)
                print(f"STATUS_SYNC:{new_status}", flush=True)

    async def _listen_stdin(self):
        """Escuta comandos do processo pai via stdin e despacha para handlers."""
        loop = asyncio.get_running_loop()

        while not self.is_closed():
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                await dispatch(self, self._exact_handlers, self._prefix_handlers, line)
            except Exception as e:
                print(f"ERROR:Erro no comando: {e}", flush=True)

    async def _safe_exec(self, coro):
        """Wrapper que captura exceções em tasks assíncronas."""
        try:
            await coro
        except Exception as e:
            print(f"ERROR:Execucao falhou: {e}", flush=True)
        finally:
            if self.presence_manager:
                await self.presence_manager.set_main()

async def main():
    try:
        input_line = await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
        token = input_line.strip()
    except Exception as e:
        print(f"RESULT:ERRO:Falha ao ler token do stdin: {e}", flush=True)
        return

    if not token:
        print("RESULT:ERRO:Token vazio recebido", flush=True)
        return

    print("INFO:SYNC_PRESENCE:True (Automatic)", flush=True)

    client = MySelfbot(sync_presence=True)
    try:
        await client.start(token)
    except discord.LoginFailure:
        print("RESULT:ERRO:Token invalido", flush=True)
    except Exception as e:
        print(f"RESULT:ERRO_CRITICO:{e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())