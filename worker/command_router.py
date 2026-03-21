import asyncio
import json

from .ipc import emit_line, emit_response, split_request_id


def _state(bot):
    return bot.control_state


def _request_id(bot):
    return getattr(bot, "_current_request_id", None)


def _emit(bot, prefix: str, payload: str):
    emit_response(prefix, payload, _request_id(bot))


def _emit_line(bot, message: str):
    emit_line(message, _request_id(bot))


async def handle_start(bot, cmd):
    _state(bot).started = True
    _emit_line(bot, "STATUS:STARTED")


async def handle_pause(bot, cmd):
    _state(bot).paused = True
    if bot.msg_manager:
        bot.msg_manager.paused = True
    _emit_line(bot, "STATUS:PAUSED")


async def handle_resume(bot, cmd):
    _state(bot).paused = False
    if bot.msg_manager:
        bot.msg_manager.paused = False
    _emit_line(bot, "STATUS:RESUMED")


async def handle_cancel(bot, cmd):
    _state(bot).cancelled = True
    if bot.msg_manager:
        bot.msg_manager.cancelled = True
    if getattr(bot, "rel_manager", None):
        bot.rel_manager.cancelled = True
    if getattr(bot, "guild_manager", None):
        bot.guild_manager.cancelled = True
    _emit_line(bot, "STATUS:CANCELLED")


async def handle_close(bot, cmd):
    _emit_line(bot, "STATUS:CLOSING")
    await bot.close()


async def handle_rel_stats(bot, cmd):
    _emit(bot, "REL_STATS_RESULT:", json.dumps(await bot.rel_manager.get_stats()))


async def handle_rel_friends_list(bot, cmd):
    _emit(
        bot,
        "REL_FRIENDS_RESULT:",
        json.dumps(await bot.rel_manager.get_friends_list()),
    )


async def handle_rel_pending_list(bot, cmd):
    _emit(
        bot,
        "REL_PENDING_RESULT:",
        json.dumps(await bot.rel_manager.get_pending_requests()),
    )


async def handle_rel_blocked_list(bot, cmd):
    _emit(
        bot,
        "REL_BLOCKED_RESULT:",
        json.dumps(await bot.rel_manager.get_blocked_list()),
    )


async def handle_rel_remove_all(bot, cmd):
    wl = [
        int(i.strip())
        for i in cmd.upper().split(":")[1].split(",")
        if i.strip().isdigit()
    ]
    bot.rel_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(
            bot.rel_manager.remove_all_friends(wl, request_id=_request_id(bot))
        )
    )


async def handle_rel_unblock_all(bot, cmd):
    bot.rel_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(bot.rel_manager.unblock_all(request_id=_request_id(bot)))
    )


async def handle_rel_accept_all(bot, cmd):
    bot.rel_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(bot.rel_manager.accept_all_requests(request_id=_request_id(bot)))
    )


async def handle_rel_deny_all(bot, cmd):
    bot.rel_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(bot.rel_manager.deny_all_requests(request_id=_request_id(bot)))
    )


async def handle_rel_nickname(bot, cmd):
    parts = cmd.split(":", 2)
    if len(parts) >= 3:
        tid, nick = int(parts[1].strip()), parts[2].strip() or None
        ok = await bot.rel_manager.set_friend_nickname(tid, nick)
        _emit(bot, "REL_NICKNAME_RESULT:", "OK" if ok else "FAIL")


async def handle_rel_add_friend(bot, cmd):
    username = cmd.split(":", 1)[1].strip()
    try:
        await bot.rel_manager.send_friend_request_by_name(username)
        _emit(bot, "REL_ADD_FRIEND_RESULT:", f"OK:{username}")
    except Exception as e:
        _emit(bot, "REL_ADD_FRIEND_RESULT:", f"FAIL:{e}")


async def handle_rel_block(bot, cmd):
    tid = int(cmd.upper().split(":")[1].strip())
    try:
        await bot.rel_manager.block_user(tid)
        _emit(bot, "REL_BLOCK_RESULT:", f"OK:{tid}")
    except Exception as e:
        _emit(bot, "REL_BLOCK_RESULT:", f"FAIL:{e}")


async def handle_rel_unblock(bot, cmd):
    tid = int(cmd.upper().split(":")[1].strip())
    try:
        await bot.rel_manager.unblock_user(tid)
        _emit(bot, "REL_UNBLOCK_RESULT:", f"OK:{tid}")
    except Exception as e:
        _emit(bot, "REL_UNBLOCK_RESULT:", f"FAIL:{e}")


async def handle_guild_stats(bot, cmd):
    _emit(bot, "GUILD_STATS_RESULT:", json.dumps(await bot.guild_manager.get_stats()))


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
            bot.guild_manager.leave_all_guilds(wl, request_id=_request_id(bot)),
            presence_task_token=presence_token,
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
            bot.guild_manager.mute_all_guilds(wl, request_id=_request_id(bot)),
            presence_task_token=presence_token,
        )
    )


async def handle_target(bot, cmd):
    tid = int(cmd.upper().split(":")[1])
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando mensagens")
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.clean_channel(tid, request_id=_request_id(bot)),
            presence_task_token=presence_token,
        )
    )


async def handle_msg_clean_channel(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando canal de texto")
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.clean_channel(cid, request_id=_request_id(bot)),
            presence_task_token=presence_token,
        )
    )


async def handle_msg_clean_all_dms(bot, cmd):
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando todas as DMs")
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.clean_all_dms(request_id=_request_id(bot)),
            presence_task_token=presence_token,
        )
    )


async def handle_msg_clean_guild(bot, cmd):
    gid = int(cmd.upper().split(":")[1])
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando servidor")
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.clean_guild(gid, request_id=_request_id(bot)),
            presence_task_token=presence_token,
        )
    )


async def handle_msg_count(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    bot.msg_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(bot.msg_manager.count_messages(cid, request_id=_request_id(bot)))
    )


async def handle_msg_scan_guilds(bot, cmd):
    bot.msg_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(
            bot.msg_manager.scan_guilds_for_messages(request_id=_request_id(bot))
        )
    )


async def handle_dm_close(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    asyncio.create_task(
        bot._safe_exec(bot.msg_manager.close_dm(cid, request_id=_request_id(bot)))
    )


async def handle_dm_close_all(bot, cmd):
    bot.msg_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(bot.msg_manager.close_all_dms(request_id=_request_id(bot)))
    )


async def handle_dm_list(bot, cmd):
    await bot.msg_manager.list_dms(request_id=_request_id(bot))


async def handle_group_dm_leave(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    asyncio.create_task(
        bot._safe_exec(bot.msg_manager.leave_group_dm(cid, request_id=_request_id(bot)))
    )


async def handle_group_dm_leave_all(bot, cmd):
    bot.msg_manager.cancelled = False
    asyncio.create_task(
        bot._safe_exec(bot.msg_manager.leave_all_group_dms(request_id=_request_id(bot)))
    )


async def handle_voice_join(bot, cmd):
    cid = int(cmd.split(":")[1].strip())
    asyncio.create_task(
        bot._safe_exec(bot.voice_manager.join_voice(cid, request_id=_request_id(bot)))
    )


async def handle_voice_leave(bot, cmd):
    asyncio.create_task(
        bot._safe_exec(bot.voice_manager.leave_voice(request_id=_request_id(bot)))
    )


async def handle_voice_follow(bot, cmd):
    uid = int(cmd.split(":")[1].strip())
    asyncio.create_task(
        bot._safe_exec(bot.voice_manager.start_follow(uid, request_id=_request_id(bot)))
    )


async def handle_voice_unfollow(bot, cmd):
    asyncio.create_task(
        bot._safe_exec(bot.voice_manager.stop_follow(request_id=_request_id(bot)))
    )


async def handle_voice_status(bot, cmd):
    vm = bot.voice_manager
    if vm.is_following:
        _emit(
            bot,
            "VOICE_STATUS:",
            f"FOLLOWING:{vm.follow_target_id}:{vm.current_channel_id}",
        )
    elif vm.is_in_voice:
        _emit(bot, "VOICE_STATUS:", f"JOINED:{vm.current_channel_id}")
    else:
        _emit(bot, "VOICE_STATUS:", "IDLE")


async def handle_protection_enable(bot, cmd):
    if getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_protection(True)
    bot.protection_manager.enable(request_id=_request_id(bot))


async def handle_protection_disable(bot, cmd):
    if getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_protection(False)
    bot.protection_manager.disable(request_id=_request_id(bot))


async def handle_protection_status(bot, cmd):
    bot.protection_manager.get_status(request_id=_request_id(bot))


async def handle_tracker_start(bot, cmd):
    parts = cmd.split(" ", 3)
    if len(parts) >= 4:
        target_id = parts[2]
        webhook_url = parts[3]
        if getattr(bot, "tracker_manager", None):
            bot.tracker_manager.start_tracking(target_id, webhook_url)


async def handle_tracker_stop(bot, cmd):
    if getattr(bot, "tracker_manager", None):
        await bot.tracker_manager.stop_tracking()


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


async def handle_quest_list(bot, cmd):
    if getattr(bot, "quest_manager", None):
        await bot.quest_manager.list_quests(request_id=_request_id(bot))


async def handle_quest_start(bot, cmd):
    quest_id = cmd.split(":", 1)[1].strip() if ":" in cmd else ""
    if not quest_id:
        _emit_line(bot, "QUEST:ERRO:quest_id nao fornecido")
        return
    if getattr(bot, "quest_manager", None):
        presence_token = None
        if getattr(bot, "presence_manager", None):
            presence_token = await bot.presence_manager.set_task("Quest Completer")
        asyncio.create_task(
            bot._safe_exec(
                bot.quest_manager.complete_quest(quest_id, request_id=_request_id(bot)),
                presence_task_token=presence_token,
            )
        )


async def handle_quest_cancel(bot, cmd):
    if getattr(bot, "quest_manager", None):
        bot.quest_manager.cancel()
        _emit_line(bot, "QUEST:CANCELLED")


async def handle_quest_auto(bot, cmd):
    if getattr(bot, "quest_manager", None):
        asyncio.create_task(
            bot.quest_manager.auto_complete_all(request_id=_request_id(bot))
        )


async def handle_quest_config(bot, cmd):
    payload_raw = cmd.split(":", 1)[1].strip() if ":" in cmd else ""
    if not payload_raw:
        _emit_line(bot, "QUEST:ERRO:payload de configuracao vazio")
        return

    try:
        payload = json.loads(payload_raw)
    except Exception:
        _emit_line(bot, "QUEST:ERRO:payload de configuracao invalido")
        return

    webhook_url = payload.get("webhook_url")
    captcha_api_key = payload.get("captcha_api_key")

    if getattr(bot, "quest_manager", None):
        await bot.quest_manager.set_runtime_config(
            webhook_url=webhook_url,
            captcha_api_key=captcha_api_key,
            request_id=_request_id(bot),
        )


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
        "QUEST_LIST": handle_quest_list,
        "QUEST_CANCEL": handle_quest_cancel,
        "QUEST_AUTO": handle_quest_auto,
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
        "DM_CLOSE:": handle_dm_close,
        "GROUP_DM_LEAVE:": handle_group_dm_leave,
        "VOICE_JOIN:": handle_voice_join,
        "VOICE_FOLLOW:": handle_voice_follow,
        "TRACKER START": handle_tracker_start,
        "PRESENCE_TASK:": handle_presence_task,
        "PRESENCE_UPDATE:": handle_presence_update,
        "QUEST_START:": handle_quest_start,
        "QUEST_CONFIG:": handle_quest_config,
    }

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
    finally:
        bot._current_request_id = previous_request_id
