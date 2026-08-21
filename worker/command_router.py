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


def _emit_json(bot, prefix: str, payload: dict):
    _emit(
        bot,
        prefix,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


def _spawn_command(bot, coroutine):
    spawn = getattr(bot, "spawn_command_task", None)
    if spawn is not None:
        return spawn(coroutine)
    return asyncio.create_task(coroutine)


def _json_command_payload(cmd: str, prefix: str) -> dict:
    if not cmd.upper().startswith(prefix):
        raise ValueError("prefixo invalido")
    payload = json.loads(cmd[len(prefix) :])
    if not isinstance(payload, dict):
        raise ValueError("payload invalido")
    return payload


def _positive_id(value) -> int:
    user_id = int(value)
    if user_id <= 0 or user_id >= 2**64:
        raise ValueError("id invalido")
    return user_id


def _page_payload(cmd: str, prefix: str, default_size: int) -> tuple[int, int]:
    payload = _json_command_payload(cmd, prefix)
    page = int(payload.get("page", 0))
    page_size = int(payload.get("page_size", default_size))
    if page < 0 or page > 100_000 or page_size < 1 or page_size > 25:
        raise ValueError("pagina invalida")
    return page, page_size


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
    page, page_size = (0, 20)
    if cmd.upper().startswith("REL_FRIENDS_PAGE_JSON:"):
        page, page_size = _page_payload(cmd, "REL_FRIENDS_PAGE_JSON:", 20)
    _emit(
        bot,
        "REL_FRIENDS_RESULT:",
        json.dumps(await bot.rel_manager.get_friends_list(page, page_size)),
    )


async def handle_rel_pending_list(bot, cmd):
    page, page_size = (0, 10)
    if cmd.upper().startswith("REL_PENDING_PAGE_JSON:"):
        page, page_size = _page_payload(cmd, "REL_PENDING_PAGE_JSON:", 10)
    _emit(
        bot,
        "REL_PENDING_RESULT:",
        json.dumps(await bot.rel_manager.get_pending_requests(page, page_size)),
    )


async def handle_rel_blocked_list(bot, cmd):
    page, page_size = (0, 20)
    if cmd.upper().startswith("REL_BLOCKED_PAGE_JSON:"):
        page, page_size = _page_payload(cmd, "REL_BLOCKED_PAGE_JSON:", 20)
    _emit(
        bot,
        "REL_BLOCKED_RESULT:",
        json.dumps(await bot.rel_manager.get_blocked_list(page, page_size)),
    )


async def handle_rel_remove_all(bot, cmd):
    wl = [
        int(i.strip())
        for i in cmd.upper().split(":")[1].split(",")
        if i.strip().isdigit()
    ]
    bot.rel_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(
            bot.rel_manager.remove_all_friends(wl, request_id=_request_id(bot))
        )
    )


async def handle_rel_unblock_all(bot, cmd):
    bot.rel_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(bot.rel_manager.unblock_all(request_id=_request_id(bot)))
    )


async def handle_rel_accept_all(bot, cmd):
    bot.rel_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(bot.rel_manager.accept_all_requests(request_id=_request_id(bot)))
    )


async def handle_rel_deny_all(bot, cmd):
    bot.rel_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(bot.rel_manager.deny_all_requests(request_id=_request_id(bot)))
    )


async def handle_rel_nickname(bot, cmd):
    try:
        payload = _json_command_payload(cmd, "REL_NICKNAME_JSON:")
        target_id = _positive_id(payload.get("target_id"))
        nickname = payload.get("nickname")
        if nickname is not None:
            if not isinstance(nickname, str) or len(nickname) > 32:
                raise ValueError("apelido invalido")
            nickname = nickname or None
        result = await bot.rel_manager.set_friend_nickname(target_id, nickname)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": "ID ou apelido invalido.",
        }
    _emit_json(bot, "REL_NICKNAME_RESULT:", result)


async def handle_rel_add_friend(bot, cmd):
    try:
        payload = _json_command_payload(cmd, "REL_ADD_FRIEND_JSON:")
        username = payload.get("username")
        if not isinstance(username, str):
            raise ValueError("username invalido")
        username = username.strip()
        if not username or len(username) > 100:
            raise ValueError("username invalido")
        result = await bot.rel_manager.send_friend_request_by_name(username)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": "Nome de usuario invalido.",
        }
    _emit_json(bot, "REL_ADD_FRIEND_RESULT:", result)


async def handle_rel_block(bot, cmd):
    try:
        payload = _json_command_payload(cmd, "REL_BLOCK_JSON:")
        target_id = _positive_id(payload.get("target_id"))
        result = await bot.rel_manager.block_user(target_id)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": "ID de usuario invalido.",
        }
    _emit_json(bot, "REL_BLOCK_RESULT:", result)


async def handle_rel_unblock(bot, cmd):
    try:
        payload = _json_command_payload(cmd, "REL_UNBLOCK_JSON:")
        target_id = _positive_id(payload.get("target_id"))
        result = await bot.rel_manager.unblock_user(target_id)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": "ID de usuario invalido.",
        }
    _emit_json(bot, "REL_UNBLOCK_RESULT:", result)


async def handle_guild_stats(bot, cmd):
    _emit_json(bot, "GUILD_STATS_RESULT:", await bot.guild_manager.get_stats())


async def handle_guild_list(bot, cmd):
    page, page_size = _page_payload(cmd, "GUILD_LIST_PAGE_JSON:", 20)
    _emit_json(
        bot,
        "GUILD_LIST_RESULT:",
        await bot.guild_manager.get_guilds_page(page, page_size),
    )


def _guild_whitelist_payload(cmd: str, prefix: str) -> list[int]:
    payload = _json_command_payload(cmd, prefix)
    raw_whitelist = payload.get("whitelist", [])
    if not isinstance(raw_whitelist, list) or len(raw_whitelist) > 1000:
        raise ValueError("whitelist invalida")
    return sorted({_positive_id(guild_id) for guild_id in raw_whitelist})


async def handle_guild_leave_all(bot, cmd):
    wl = _guild_whitelist_payload(cmd, "GUILD_LEAVE_ALL_JSON:")
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Saindo de servidores")
    bot.guild_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(
            bot.guild_manager.leave_all_guilds(wl, request_id=_request_id(bot)),
            presence_task_token=presence_token,
        )
    )


async def handle_guild_mute_all(bot, cmd):
    wl = _guild_whitelist_payload(cmd, "GUILD_MUTE_ALL_JSON:")
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Silenciando servidores")
    bot.guild_manager.cancelled = False
    _spawn_command(bot,
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
    _spawn_command(bot,
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
    _spawn_command(bot,
        bot._safe_exec(
            bot.msg_manager.clean_channel(cid, request_id=_request_id(bot)),
            presence_task_token=presence_token,
        )
    )


async def handle_msg_clean_all_dms(bot, cmd):
    presence_token = None
    if getattr(bot, "presence_manager", None):
        presence_token = await bot.presence_manager.set_task("Limpando todas as DMs")
    _spawn_command(bot,
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
    _spawn_command(bot,
        bot._safe_exec(
            bot.msg_manager.clean_guild(gid, request_id=_request_id(bot)),
            presence_task_token=presence_token,
        )
    )


async def handle_msg_count(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    bot.msg_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(bot.msg_manager.count_messages(cid, request_id=_request_id(bot)))
    )


async def handle_msg_scan_guilds(bot, cmd):
    bot.msg_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(
            bot.msg_manager.scan_guilds_for_messages(request_id=_request_id(bot))
        )
    )


async def handle_dm_close(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    _spawn_command(bot,
        bot._safe_exec(bot.msg_manager.close_dm(cid, request_id=_request_id(bot)))
    )


async def handle_dm_close_all(bot, cmd):
    bot.msg_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(bot.msg_manager.close_all_dms(request_id=_request_id(bot)))
    )


async def handle_dm_list(bot, cmd):
    await bot.msg_manager.list_dms(request_id=_request_id(bot))


async def handle_group_dm_leave(bot, cmd):
    cid = int(cmd.upper().split(":")[1])
    _spawn_command(bot,
        bot._safe_exec(bot.msg_manager.leave_group_dm(cid, request_id=_request_id(bot)))
    )


async def handle_group_dm_leave_all(bot, cmd):
    bot.msg_manager.cancelled = False
    _spawn_command(bot,
        bot._safe_exec(bot.msg_manager.leave_all_group_dms(request_id=_request_id(bot)))
    )


async def _run_voice_action(coro, request_id: str | None):
    try:
        result = await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        result = {
            "ok": False,
            "code": "VOICE_INTERNAL_ERROR",
            "message": "Falha interna ao executar a operacao de voz.",
        }
    emit_response(
        "VOICE_RESULT:",
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        request_id,
    )


async def handle_voice_join(bot, cmd):
    try:
        payload = _json_command_payload(cmd, "VOICE_JOIN_JSON:")
        channel_id = _positive_id(payload.get("channel_id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        _emit_json(
            bot,
            "VOICE_RESULT:",
            {
                "ok": False,
                "code": "INVALID_CHANNEL_ID",
                "message": "ID de canal invalido.",
            },
        )
        return
    _spawn_command(bot,
        _run_voice_action(bot.voice_manager.join_voice(channel_id), _request_id(bot))
    )


async def handle_voice_leave(bot, cmd):
    _spawn_command(bot,
        _run_voice_action(bot.voice_manager.leave_voice(), _request_id(bot))
    )


async def handle_voice_follow(bot, cmd):
    try:
        payload = _json_command_payload(cmd, "VOICE_FOLLOW_JSON:")
        target_id = _positive_id(payload.get("target_id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        _emit_json(
            bot,
            "VOICE_RESULT:",
            {
                "ok": False,
                "code": "INVALID_TARGET_ID",
                "message": "ID de usuario invalido.",
            },
        )
        return
    _spawn_command(bot,
        _run_voice_action(bot.voice_manager.start_follow(target_id), _request_id(bot))
    )


async def handle_voice_unfollow(bot, cmd):
    _spawn_command(bot,
        _run_voice_action(bot.voice_manager.stop_follow(), _request_id(bot))
    )


async def handle_voice_status(bot, cmd):
    _emit_json(bot, "VOICE_STATUS_RESULT:", await bot.voice_manager.get_status())


def _protection_whitelist_payload(cmd: str, prefix: str) -> list[int]:
    payload = _json_command_payload(cmd, prefix)
    raw_whitelist = payload.get("whitelist", [])
    if not isinstance(raw_whitelist, list) or len(raw_whitelist) > 150:
        raise ValueError("whitelist invalida")
    return sorted({_positive_id(user_id) for user_id in raw_whitelist})


async def handle_protection_enable(bot, cmd):
    whitelist = None
    if cmd.upper().startswith("PROTECTION_ENABLE_JSON:"):
        whitelist = _protection_whitelist_payload(cmd, "PROTECTION_ENABLE_JSON:")
    result = bot.protection_manager.enable(whitelist)
    if result.get("ok") and getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_protection(True)
    _emit_json(bot, "PROTECTION_RESULT:", result)


async def handle_protection_disable(bot, cmd):
    result = bot.protection_manager.disable()
    if result.get("ok") and getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_protection(False)
    _emit_json(bot, "PROTECTION_RESULT:", result)


async def handle_protection_status(bot, cmd):
    _emit_json(bot, "PROTECTION_STATUS_RESULT:", bot.protection_manager.get_status())


async def handle_protection_configure(bot, cmd):
    whitelist = _protection_whitelist_payload(cmd, "PROTECTION_CONFIG_JSON:")
    _emit_json(bot, "PROTECTION_RESULT:", bot.protection_manager.configure(whitelist))


async def handle_tracker_start(bot, cmd):
    try:
        payload = _json_command_payload(cmd, "TRACKER_START_JSON:")
        target_id = _positive_id(payload.get("target_id"))
        webhook_url = payload.get("webhook_url")
        if not isinstance(webhook_url, str):
            raise ValueError("webhook invalido")
        result = await bot.tracker_manager.start_tracking(target_id, webhook_url)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": "ID do alvo ou webhook invalido.",
            "enabled": False,
        }
    _emit_json(bot, "TRACKER_RESULT:", result)


async def handle_tracker_stop(bot, cmd):
    result = await bot.tracker_manager.stop_tracking()
    _emit_json(bot, "TRACKER_RESULT:", result)


async def handle_tracker_status(bot, cmd):
    _emit_json(bot, "TRACKER_STATUS_RESULT:", bot.tracker_manager.get_status())


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
    manager = getattr(bot, "quest_manager", None)
    result = (
        await manager.list_quests()
        if manager
        else {"ok": False, "code": "UNAVAILABLE", "message": "Quest Manager indisponível."}
    )
    _emit_json(bot, "QUEST_LIST_RESULT:", result)


async def handle_quest_start(bot, cmd):
    quest_id = cmd.split(":", 1)[1].strip() if ":" in cmd else ""
    if not quest_id.isdigit() or int(quest_id) <= 0:
        _emit_json(
            bot,
            "QUEST_START_RESULT:",
            {
                "ok": False,
                "code": "INVALID_QUEST_ID",
                "message": "ID de Quest inválido.",
            },
        )
        return
    manager = getattr(bot, "quest_manager", None)
    result = (
        await manager.start_quest(quest_id, request_id=_request_id(bot))
        if manager
        else {"ok": False, "code": "UNAVAILABLE", "message": "Quest Manager indisponível."}
    )
    _emit_json(bot, "QUEST_START_RESULT:", result)


async def handle_quest_cancel(bot, cmd):
    manager = getattr(bot, "quest_manager", None)
    cancelled = await manager.cancel() if manager else False
    _emit_json(
        bot,
        "QUEST_CANCEL_RESULT:",
        {
            "ok": manager is not None,
            "code": "CANCELLED" if cancelled else ("IDLE" if manager else "UNAVAILABLE"),
            "message": (
                "Fila de Quests cancelada."
                if cancelled
                else ("Nenhuma Quest estava em execução." if manager else "Quest Manager indisponível.")
            ),
        },
    )


async def handle_quest_auto(bot, cmd):
    manager = getattr(bot, "quest_manager", None)
    result = (
        await manager.start_auto(request_id=_request_id(bot))
        if manager
        else {"ok": False, "code": "UNAVAILABLE", "message": "Quest Manager indisponível."}
    )
    _emit_json(bot, "QUEST_AUTO_RESULT:", result)


async def handle_quest_config(bot, cmd):
    payload_raw = cmd.split(":", 1)[1].strip() if ":" in cmd else ""
    if not payload_raw:
        _emit_json(bot, "QUEST:CONFIG_RESULT:", {"ok": False, "code": "INVALID_PAYLOAD", "message": "Payload de configuração vazio."})
        return

    try:
        payload = json.loads(payload_raw)
    except (json.JSONDecodeError, TypeError):
        _emit_json(bot, "QUEST:CONFIG_RESULT:", {"ok": False, "code": "INVALID_PAYLOAD", "message": "Payload de configuração inválido."})
        return

    if not isinstance(payload, dict):
        _emit_json(bot, "QUEST:CONFIG_RESULT:", {"ok": False, "code": "INVALID_PAYLOAD", "message": "Payload de configuração deve ser um objeto."})
        return

    webhook_url = payload.get("webhook_url")

    manager = getattr(bot, "quest_manager", None)
    if manager:
        await manager.set_runtime_config(
            webhook_url=webhook_url,
            request_id=_request_id(bot),
        )
    else:
        _emit_json(bot, "QUEST:CONFIG_RESULT:", {"ok": False, "code": "UNAVAILABLE", "message": "Quest Manager indisponível."})


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
        "TRACKER_STOP": handle_tracker_stop,
        "TRACKER_STATUS": handle_tracker_status,
        "PRESENCE_MAIN": handle_presence_main,
        "QUEST_LIST": handle_quest_list,
        "QUEST_CANCEL": handle_quest_cancel,
        "QUEST_AUTO": handle_quest_auto,
    }

    prefix = {
        "REL_REMOVE_ALL:": handle_rel_remove_all,
        "REL_FRIENDS_PAGE_JSON:": handle_rel_friends_list,
        "REL_PENDING_PAGE_JSON:": handle_rel_pending_list,
        "REL_BLOCKED_PAGE_JSON:": handle_rel_blocked_list,
        "REL_NICKNAME_JSON:": handle_rel_nickname,
        "REL_ADD_FRIEND_JSON:": handle_rel_add_friend,
        "REL_BLOCK_JSON:": handle_rel_block,
        "REL_UNBLOCK_JSON:": handle_rel_unblock,
        "GUILD_LIST_PAGE_JSON:": handle_guild_list,
        "GUILD_LEAVE_ALL_JSON:": handle_guild_leave_all,
        "GUILD_MUTE_ALL_JSON:": handle_guild_mute_all,
        "TARGET:": handle_target,
        "MSG_CLEAN_CHANNEL:": handle_msg_clean_channel,
        "MSG_CLEAN_GUILD:": handle_msg_clean_guild,
        "MSG_COUNT:": handle_msg_count,
        "DM_CLOSE:": handle_dm_close,
        "GROUP_DM_LEAVE:": handle_group_dm_leave,
        "VOICE_JOIN_JSON:": handle_voice_join,
        "VOICE_FOLLOW_JSON:": handle_voice_follow,
        "PROTECTION_ENABLE_JSON:": handle_protection_enable,
        "PROTECTION_CONFIG_JSON:": handle_protection_configure,
        "TRACKER_START_JSON:": handle_tracker_start,
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
