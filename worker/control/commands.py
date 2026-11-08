from ..core import commands as ipc_commands


async def handle_start(bot, cmd):
    ipc_commands._state(bot).started = True
    ipc_commands._emit_line(bot, "STATUS:STARTED")


async def handle_pause(bot, cmd):
    ipc_commands._state(bot).paused = True
    if bot.msg_manager:
        bot.msg_manager.paused = True
    ipc_commands._emit_line(bot, "STATUS:PAUSED")


async def handle_resume(bot, cmd):
    ipc_commands._state(bot).paused = False
    if bot.msg_manager:
        bot.msg_manager.paused = False
    ipc_commands._emit_line(bot, "STATUS:RESUMED")


async def handle_cancel(bot, cmd):
    ipc_commands._state(bot).cancelled = True
    if bot.msg_manager:
        bot.msg_manager.cancelled = True
    if getattr(bot, "rel_manager", None):
        bot.rel_manager.cancelled = True
    if getattr(bot, "guild_manager", None):
        bot.guild_manager.cancelled = True
    ipc_commands._emit_line(bot, "STATUS:CANCELLED")


async def handle_close(bot, cmd):
    ipc_commands._emit_line(bot, "STATUS:CLOSING")
    await bot.close()


EXACT = {
    "START": handle_start,
    "PAUSE": handle_pause,
    "RESUME": handle_resume,
    "CANCEL": handle_cancel,
    "CLOSE": handle_close,
}

PREFIX = {}
