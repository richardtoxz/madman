from ..core import commands as ipc_commands


async def handle_guild_stats(bot, cmd):
    ipc_commands._emit_json(bot, "GUILD_STATS_RESULT:", await bot.guild_manager.get_stats())


async def handle_guild_list(bot, cmd):
    page, page_size = ipc_commands._page_payload(cmd, "GUILD_LIST_PAGE_JSON:", 20)
    ipc_commands._emit_json(
        bot,
        "GUILD_LIST_RESULT:",
        await bot.guild_manager.get_guilds_page(page, page_size),
    )


async def handle_guild_leave_all(bot, cmd):
    wl = ipc_commands._whitelist_payload(cmd, "GUILD_LEAVE_ALL_JSON:", max_items=1000)
    presence_token = await ipc_commands._presence_token(bot, "Saindo de servidores")
    bot.guild_manager.cancelled = False
    ipc_commands._spawn_safe(
        bot,
        bot.guild_manager.leave_all_guilds(wl, request_id=ipc_commands._request_id(bot)),
        presence_task_token=presence_token,
    )


async def handle_guild_mute_all(bot, cmd):
    wl = ipc_commands._whitelist_payload(cmd, "GUILD_MUTE_ALL_JSON:", max_items=1000)
    presence_token = await ipc_commands._presence_token(bot, "Silenciando servidores")
    bot.guild_manager.cancelled = False
    ipc_commands._spawn_safe(
        bot,
        bot.guild_manager.mute_all_guilds(wl, request_id=ipc_commands._request_id(bot)),
        presence_task_token=presence_token,
    )


EXACT = {
    "GUILD_STATS": handle_guild_stats,
}

PREFIX = {
    "GUILD_LIST_PAGE_JSON:": handle_guild_list,
    "GUILD_LEAVE_ALL_JSON:": handle_guild_leave_all,
    "GUILD_MUTE_ALL_JSON:": handle_guild_mute_all,
}
