"""
Selfbot principal — orquestra managers e escuta comandos via stdin.
"""
import os
import sys

# Setup do path para a lib vendorizada (discord.py-self)
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


class MySelfbot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.msg_manager = None
        self.rel_manager = None
        self.guild_manager = None
        self._exact_handlers = {}
        self._prefix_handlers = {}

    async def on_ready(self):
        print(f'READY:{self.user}:{self.user.id}', flush=True)

        self.msg_manager = MessageManager(self, control_flags)
        self.rel_manager = RelationshipManager(self)
        self.guild_manager = GuildManager(self)

        self._exact_handlers, self._prefix_handlers = register_handlers(self)
        asyncio.create_task(self._listen_stdin())

    async def on_relationship_add(self, relationship):
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

    async def _listen_stdin(self):
        """Escuta comandos do processo pai via stdin e despacha para handlers."""
        loop = asyncio.get_event_loop()

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


# ============================================================
# MAIN
# ============================================================
async def main():
    if len(sys.argv) < 2:
        print("Usage: python run_selfbot.py <token> [status]")
        return

    token = sys.argv[1]
    status_arg = sys.argv[2] if len(sys.argv) > 2 else "online"
    status_map = {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        "invisible": discord.Status.invisible,
    }
    status = status_map.get(status_arg.lower(), discord.Status.online)
    print(f"INFO:STATUS:{status_arg}", flush=True)

    client = MySelfbot(status=status)
    try:
        await client.start(token)
    except discord.LoginFailure:
        print("RESULT:ERRO:Token invalido", flush=True)
    except Exception as e:
        print(f"RESULT:ERRO_CRITICO:{e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())