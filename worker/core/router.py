import asyncio

from ..account import commands as account
from ..control import commands as control
from ..guilds import commands as guilds
from ..messages import commands as messages
from ..presence import commands as presence
from ..protection import commands as protection
from ..quest import commands as quest
from ..relationships import commands as relationships
from ..tracker import commands as tracker
from ..voice import commands as voice
from .ipc import emit_response, split_request_id

# A ordem dos recursos reproduz a tabela de comandos original.
FEATURES = (control, relationships, guilds, messages, voice, protection, tracker, presence, quest, account)


def register_handlers(bot):
    exact, prefix = {}, {}
    for feature in FEATURES:
        exact.update(feature.EXACT)
        prefix.update(feature.PREFIX)
    return exact, prefix


async def dispatch(bot, exact_handlers, prefix_handlers, cmd_raw):
    cmd, request_id = split_request_id(cmd_raw.strip())
    cmd_upper = cmd.upper()
    previous_request_id = getattr(bot, "_current_request_id", None)
    bot._current_request_id = request_id

    try:
        handler = exact_handlers.get(cmd_upper)
        if handler:
            await handler(bot, cmd)
            return

        for pfx, handler in prefix_handlers.items():
            if cmd_upper.startswith(pfx):
                await handler(bot, cmd)
                return
        emit_response("IPC_ERROR:", "UNKNOWN_COMMAND", request_id)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        emit_response(
            "IPC_ERROR:",
            f"HANDLER_FAILED:{type(error).__name__}",
            request_id,
        )
    finally:
        bot._current_request_id = previous_request_id
