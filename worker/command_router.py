import asyncio
import json
from config import control_flags


async def handle_start(bot, cmd):
    control_flags["started"] = True
    print("STATUS:STARTED", flush=True)


async def handle_pause(bot, cmd):
    control_flags["paused"] = True
    if bot.msg_manager:
        bot.msg_manager.paused = True
    print("STATUS:PAUSED", flush=True)


async def handle_resume(bot, cmd):
    control_flags["paused"] = False
    if bot.msg_manager:
        bot.msg_manager.paused = False
    print("STATUS:RESUMED", flush=True)


async def handle_cancel(bot, cmd):
    control_flags["cancelled"] = True
    if bot.msg_manager:
        bot.msg_manager.cancelled = True
    if getattr(bot, "rel_manager", None):
        bot.rel_manager.cancelled = True
    if getattr(bot, "guild_manager", None):
        bot.guild_manager.cancelled = True
    print("STATUS:CANCELLED", flush=True)


async def handle_close(bot, cmd):
    print("STATUS:CLOSING", flush=True)
    await bot.close()


async def handle_rel_stats(bot, cmd):
    print(
        f"REL_STATS_RESULT:{json.dumps(await bot.rel_manager.get_stats())}", flush=True
    )


async def handle_rel_friends_list(bot, cmd):
    print(
        f"REL_FRIENDS_RESULT:{json.dumps(await bot.rel_manager.get_friends_list())}",
        flush=True,
    )


async def handle_rel_pending_list(bot, cmd):
    print(
        f"REL_PENDING_RESULT:{json.dumps(await bot.rel_manager.get_pending_requests())}",
        flush=True,
    )


async def handle_rel_blocked_list(bot, cmd):
    print(
        f"REL_BLOCKED_RESULT:{json.dumps(await bot.rel_manager.get_blocked_list())}",
        flush=True,
    )


async def handle_rel_remove_all(bot, cmd):
    wl = [
        int(i.strip())
        for i in cmd.upper().split(":")[1].split(",")
        if i.strip().isdigit()
    ]
    bot.rel_manager.cancelled = False
    asyncio.create_task(bot._safe_exec(bot.rel_manager.remove_all_friends(wl)))


async def handle_rel_unblock_all(bot, cmd):
    bot.rel_manager.cancelled = False
    asyncio.create_task(bot._safe_exec(bot.rel_manager.unblock_all()))


async def handle_rel_accept_all(bot, cmd):
    bot.rel_manager.cancelled = False
    asyncio.create_task(bot._safe_exec(bot.rel_manager.accept_all_requests()))


async def handle_rel_deny_all(bot, cmd):
    bot.rel_manager.cancelled = False
    asyncio.create_task(bot._safe_exec(bot.rel_manager.deny_all_requests()))


async def handle_rel_nickname(bot, cmd):
    parts = cmd.split(":", 2)
    if len(parts) >= 3:
        tid, nick = int(parts[1].strip()), parts[2].strip() or None
        ok = await bot.rel_manager.set_friend_nickname(tid, nick)
        print(f"REL_NICKNAME_RESULT:{'OK' if ok else 'FAIL'}", flush=True)


async def handle_rel_add_friend(bot, cmd):
    username = cmd.split(":", 1)[1].strip()
    try:
        await bot.rel_manager.send_friend_request_by_name(username)
        print(f"REL_ADD_FRIEND_RESULT:OK:{username}", flush=True)
    except Exception as e:
        print(f"REL_ADD_FRIEND_RESULT:FAIL:{e}", flush=True)


async def handle_rel_block(bot, cmd):
    tid = int(cmd.upper().split(":")[1].strip())
    try:
        await bot.rel_manager.block_user(tid)
        print(f"REL_BLOCK_RESULT:OK:{tid}", flush=True)
    except Exception as e:
        print(f"REL_BLOCK_RESULT:FAIL:{e}", flush=True)


async def handle_rel_unblock(bot, cmd):
    tid = int(cmd.upper().split(":")[1].strip())
    try:
        await bot.rel_manager.unblock_user(tid)
        print(f"REL_UNBLOCK_RESULT:OK:{tid}", flush=True)
    except Exception as e:
        print(f"REL_UNBLOCK_RESULT:FAIL:{e}", flush=True)


async def handle_guild_stats(bot, cmd):
    print(
        f"GUILD_STATS_RESULT:{json.dumps(await bot.guild_manager.get_stats())}",
        flush=True,
    )


async def handle_guild_leave_all(bot, cmd):
    wl = [
        int(i.strip())
        for i in cmd.upper().split(":")[1].split(",")
        if i.strip().isdigit()
    ]
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Saindo de servidores")
    bot.guild_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(
            bot.guild_manager.leave_all_guilds(wl), presence_task_token=presence_token
        )
    )


async def handle_guild_mute_all(bot, cmd):
    wl = [
        int(i.strip())
        for i in cmd.upper().split(":")[1].split(",")
        if i.strip().isdigit()
    ]
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Silenciando servidores")
    bot.guild_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(
            bot.guild_manager.mute_all_guilds(wl), presence_task_token=presence_token
        )
    )


async def handle_target(bot, cmd):
    tid = int(cmd.upper().split(":")[1])
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando mensagens")
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.clean_channel(tid), presence_task_token=presence_token
        )
    )


async def handle_msg_clean_channel(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando canal de texto")
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.clean_channel(cid), presence_task_token=presence_token
        )
    )


async def handle_msg_clean_all_dms(bot, cmd):
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando todas as DMs")
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.clean_all_dms(), presence_task_token=presence_token
        )
    )


async def handle_msg_clean_guild(bot, cmd):
    gid = int(cmd.upper().split(":")[1])
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando servidor")
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.clean_guild(gid), presence_task_token=presence_token
        )
    )


async def handle_msg_count(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    bot.msg_manager.cancelled = False
    asyncio.create_task(bot._safe_exec(bot.msg_manager.count_messages(cid)))


async def handle_msg_scan_guilds(bot, cmd):
    bot.msg_manager.cancelled = False
    asyncio.create_task(bot._safe_exec(bot.msg_manager.scan_guilds_for_messages()))


async def handle_msg_datapack(bot, cmd):
    data_json = cmd.split(":", 1)[1]
    try:
        entries = json.loads(data_json)
        bot.msg_manager.cancelled = False
        bot.msg_manager.paused = False
        asyncio.create_task(
            bot._safe_exec(bot.msg_manager.delete_from_data_package(entries))
        )
    except json.JSONDecodeError as e:
        print(f"MSG_DATAPACK_RESULT:ERRO:JSON invalido: {e}", flush=True)


async def handle_dm_close(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    asyncio.create_task(bot._safe_exec(bot.msg_manager.close_dm(cid)))


async def handle_dm_close_all(bot, cmd):
    bot.msg_manager.cancelled = False
    asyncio.create_task(bot._safe_exec(bot.msg_manager.close_all_dms()))


async def handle_dm_list(bot, cmd):
    await bot.msg_manager.list_dms()


async def handle_group_dm_leave(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    asyncio.create_task(bot._safe_exec(bot.msg_manager.leave_group_dm(cid)))


async def handle_group_dm_leave_all(bot, cmd):
    bot.msg_manager.cancelled = False
    asyncio.create_task(bot._safe_exec(bot.msg_manager.leave_all_group_dms()))


async def handle_voice_join(bot, cmd):
    cid = int(cmd.split(":")[1].strip())
    asyncio.create_task(bot._safe_exec(bot.voice_manager.join_voice(cid)))


async def handle_voice_leave(bot, cmd):
    asyncio.create_task(bot._safe_exec(bot.voice_manager.leave_voice()))


async def handle_voice_follow(bot, cmd):
    uid = int(cmd.split(":")[1].strip())
    asyncio.create_task(bot._safe_exec(bot.voice_manager.start_follow(uid)))


async def handle_voice_unfollow(bot, cmd):
    asyncio.create_task(bot._safe_exec(bot.voice_manager.stop_follow()))


async def handle_voice_status(bot, cmd):
    vm = bot.voice_manager
    if vm.is_following:
        print(
            f"VOICE_STATUS:FOLLOWING:{vm.follow_target_id}:{vm.current_channel_id}",
            flush=True,
        )
    elif vm.is_in_voice:
        print(f"VOICE_STATUS:JOINED:{vm.current_channel_id}", flush=True)
    else:
        print("VOICE_STATUS:IDLE", flush=True)


async def handle_protection_enable(bot, cmd):
    if getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_protection(True)
    bot.protection_manager.enable()


async def handle_protection_disable(bot, cmd):
    if getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_protection(False)
    bot.protection_manager.disable()


async def handle_protection_status(bot, cmd):
    bot.protection_manager.get_status()


async def handle_tracker_start(bot, cmd):
    parts = cmd.split(" ", 3)
    if len(parts) >= 4:
        target_id = parts[2]
        webhook_url = parts[3]
        if getattr(bot, "tracker_manager", None):
            bot.tracker_manager.start_tracking(target_id, webhook_url)


async def handle_tracker_stop(bot, cmd):
    if getattr(bot, "tracker_manager", None):
        bot.tracker_manager.stop_tracking()


async def handle_presence_main(bot, cmd):
    if getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_main()


async def handle_presence_task(bot, cmd):
    if getattr(bot, "presence_manager", None):
        parts = cmd.split(":", 2)
        name = parts[1] if len(parts) > 1 else "Tarefa"
        detail = parts[2] if len(parts) > 2 else None
        await bot.presence_manager.set_task(name, detail)


async def handle_presence_update(bot, cmd):
    if getattr(bot, "presence_manager", None):
        detail = cmd.split(":", 1)[1] if ":" in cmd else ""
        await bot.presence_manager.update_detail(detail)


def register_handlers(bot):
    exact = {
        "START": handle_start,
        "PAUSE": handle_pause,
        "RESUME": handle_resume,
        "CANCEL": handle_cancel,
        "CLOSE": handle_close,
        "REL_STATS": handle_rel_stats,
        "REL_FRIENDS_LIST": handle_rel_friends_list,
        "REL_PENDING_LIST": handle_rel_pending_list,
        "REL_BLOCKED_LIST": handle_rel_blocked_list,
        "REL_UNBLOCK_ALL": handle_rel_unblock_all,
        "REL_ACCEPT_ALL": handle_rel_accept_all,
        "REL_DENY_ALL": handle_rel_deny_all,
        "GUILD_STATS": handle_guild_stats,
        "MSG_CLEAN_ALL_DMS": handle_msg_clean_all_dms,
        "MSG_SCAN_GUILDS": handle_msg_scan_guilds,
        "DM_CLOSE_ALL": handle_dm_close_all,
        "DM_LIST": handle_dm_list,
        "GROUP_DM_LEAVE_ALL": handle_group_dm_leave_all,
        "VOICE_LEAVE": handle_voice_leave,
        "VOICE_UNFOLLOW": handle_voice_unfollow,
        "VOICE_STATUS": handle_voice_status,
        "PROTECTION_ENABLE": handle_protection_enable,
        "PROTECTION_DISABLE": handle_protection_disable,
        "PROTECTION_STATUS": handle_protection_status,
        "TRACKER STOP": handle_tracker_stop,
        "PRESENCE_MAIN": handle_presence_main,
    }

    prefix = {
        "REL_REMOVE_ALL:": handle_rel_remove_all,
        "REL_NICKNAME:": handle_rel_nickname,
        "REL_ADD_FRIEND:": handle_rel_add_friend,
        "REL_BLOCK:": handle_rel_block,
        "REL_UNBLOCK:": handle_rel_unblock,
        "GUILD_LEAVE_ALL:": handle_guild_leave_all,
        "GUILD_MUTE_ALL:": handle_guild_mute_all,
        "TARGET:": handle_target,
        "MSG_CLEAN_CHANNEL:": handle_msg_clean_channel,
        "MSG_CLEAN_GUILD:": handle_msg_clean_guild,
        "MSG_COUNT:": handle_msg_count,
        "MSG_DATAPACK:": handle_msg_datapack,
        "DM_CLOSE:": handle_dm_close,
        "GROUP_DM_LEAVE:": handle_group_dm_leave,
        "VOICE_JOIN:": handle_voice_join,
        "VOICE_FOLLOW:": handle_voice_follow,
        "TRACKER START": handle_tracker_start,
        "PRESENCE_TASK:": handle_presence_task,
        "PRESENCE_UPDATE:": handle_presence_update,
    }

    return exact, prefix


async def dispatch(bot, exact_handlers, prefix_handlers, cmd_raw):

    cmd_upper = cmd_raw.strip().upper()

    handler = exact_handlers.get(cmd_upper)
    if handler:
        await handler(bot, cmd_raw.strip())
        return

    for pfx, handler in prefix_handlers.items():
        if cmd_upper.startswith(pfx):
            await handler(bot, cmd_raw.strip())
            return
