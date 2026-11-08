import json

from ..core import commands as ipc_commands


async def handle_target(bot, cmd):
    tid = ipc_commands._id_arg(cmd)
    presence_token = await ipc_commands._presence_token(bot, "Limpando mensagens")
    ipc_commands._spawn_safe(
        bot,
        bot.msg_manager.clean_channel(tid, request_id=ipc_commands._request_id(bot)),
        presence_task_token=presence_token,
    )


async def handle_msg_clean_channel(bot, cmd):
    cid = ipc_commands._id_arg(cmd)
    presence_token = await ipc_commands._presence_token(bot, "Limpando canal de texto")
    ipc_commands._spawn_safe(
        bot,
        bot.msg_manager.clean_channel(cid, request_id=ipc_commands._request_id(bot)),
        presence_task_token=presence_token,
    )


async def handle_msg_clean_channel_now(bot, cmd):
    cid = ipc_commands._id_arg(cmd)
    presence_token = await ipc_commands._presence_token(bot, "Limpando canal de texto")
    ipc_commands._spawn_safe(
        bot,
        bot.msg_manager.clean_channel(cid, request_id=ipc_commands._request_id(bot), auto_start=True),
        presence_task_token=presence_token,
    )


async def handle_msg_preview(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "MSG_PREVIEW_JSON:")
        target_id = ipc_commands._positive_id(payload.get("target_id"))
        guild = bool(payload.get("guild", False))
    except (TypeError, ValueError, json.JSONDecodeError):
        ipc_commands._emit_json(
            bot,
            "MSG_PREVIEW_RESULT:",
            ipc_commands._error_result("INVALID_INPUT", "ID invalido."),
        )
        return
    ipc_commands._spawn_safe(
        bot, bot.msg_manager.preview_clean(target_id, guild=guild, request_id=ipc_commands._request_id(bot))
    )


async def handle_msg_clean_all_dms(bot, cmd):
    presence_token = await ipc_commands._presence_token(bot, "Limpando todas as DMs")
    ipc_commands._spawn_safe(
        bot,
        bot.msg_manager.clean_all_dms(request_id=ipc_commands._request_id(bot)),
        presence_task_token=presence_token,
    )


async def handle_msg_clean_batch(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "MSG_CLEAN_BATCH_JSON:")
        raw_ids = payload.get("target_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
            raise ValueError("lista de alvos invalida")
        target_ids = [ipc_commands._positive_id(cid) for cid in raw_ids]
    except (TypeError, ValueError, json.JSONDecodeError):
        ipc_commands._emit_line(bot, "MSG_CLEAN_ALL_DMS_RESULT:ERRO:IDs invalidos para limpeza em fila.")
        return
    presence_token = await ipc_commands._presence_token(bot, "Limpando fila de conversas")
    ipc_commands._spawn_safe(
        bot,
        bot.msg_manager.clean_channels_batch(target_ids, request_id=ipc_commands._request_id(bot)),
        presence_task_token=presence_token,
    )


async def handle_msg_clean_guild(bot, cmd):
    gid = ipc_commands._id_arg(cmd)
    presence_token = await ipc_commands._presence_token(bot, "Limpando servidor")
    ipc_commands._spawn_safe(
        bot,
        bot.msg_manager.clean_guild(gid, request_id=ipc_commands._request_id(bot)),
        presence_task_token=presence_token,
    )


async def handle_msg_clean_guild_now(bot, cmd):
    gid = ipc_commands._id_arg(cmd)
    presence_token = await ipc_commands._presence_token(bot, "Limpando servidor")
    ipc_commands._spawn_safe(
        bot,
        bot.msg_manager.clean_guild(gid, request_id=ipc_commands._request_id(bot), auto_start=True),
        presence_task_token=presence_token,
    )


async def handle_msg_count(bot, cmd):
    cid = ipc_commands._id_arg(cmd)
    bot.msg_manager.cancelled = False
    ipc_commands._spawn_safe(bot, bot.msg_manager.count_messages(cid, request_id=ipc_commands._request_id(bot)))


async def handle_msg_scan_guilds(bot, cmd):
    bot.msg_manager.cancelled = False
    ipc_commands._spawn_safe(bot, bot.msg_manager.scan_guilds_for_messages(request_id=ipc_commands._request_id(bot)))


async def handle_dm_close(bot, cmd):
    cid = ipc_commands._id_arg(cmd)
    ipc_commands._spawn_safe(bot, bot.msg_manager.close_dm(cid, request_id=ipc_commands._request_id(bot)))


async def handle_dm_close_all(bot, cmd):
    bot.msg_manager.cancelled = False
    ipc_commands._spawn_safe(bot, bot.msg_manager.close_all_dms(request_id=ipc_commands._request_id(bot)))


async def handle_dm_list(bot, cmd):
    refresh = cmd.upper().strip() == "DM_LIST_REFRESH"
    await bot.msg_manager.list_dms(request_id=ipc_commands._request_id(bot), refresh=refresh)


async def handle_group_dm_leave(bot, cmd):
    cid = ipc_commands._id_arg(cmd)
    ipc_commands._spawn_safe(bot, bot.msg_manager.leave_group_dm(cid, request_id=ipc_commands._request_id(bot)))


async def handle_group_dm_leave_all(bot, cmd):
    bot.msg_manager.cancelled = False
    ipc_commands._spawn_safe(bot, bot.msg_manager.leave_all_group_dms(request_id=ipc_commands._request_id(bot)))


EXACT = {
    "MSG_CLEAN_ALL_DMS": handle_msg_clean_all_dms,
    "MSG_SCAN_GUILDS": handle_msg_scan_guilds,
    "DM_CLOSE_ALL": handle_dm_close_all,
    "DM_LIST": handle_dm_list,
    "DM_LIST_REFRESH": handle_dm_list,
    "GROUP_DM_LEAVE_ALL": handle_group_dm_leave_all,
}

PREFIX = {
    "TARGET:": handle_target,
    "MSG_PREVIEW_JSON:": handle_msg_preview,
    "MSG_CLEAN_BATCH_JSON:": handle_msg_clean_batch,
    "MSG_CLEAN_CHANNEL:": handle_msg_clean_channel,
    "MSG_CLEAN_CHANNEL_NOW:": handle_msg_clean_channel_now,
    "MSG_CLEAN_GUILD:": handle_msg_clean_guild,
    "MSG_CLEAN_GUILD_NOW:": handle_msg_clean_guild_now,
    "MSG_COUNT:": handle_msg_count,
    "DM_CLOSE:": handle_dm_close,
    "GROUP_DM_LEAVE:": handle_group_dm_leave,
}
