import json

from ..core import commands as ipc_commands


async def handle_rel_stats(bot, cmd):
    ipc_commands._emit(bot, "REL_STATS_RESULT:", json.dumps(await bot.rel_manager.get_stats()))


async def handle_rel_friends_list(bot, cmd):
    page, page_size, sort = (0, 20, "status")
    if cmd.upper().startswith("REL_FRIENDS_PAGE_JSON:"):
        page, page_size = ipc_commands._page_payload(cmd, "REL_FRIENDS_PAGE_JSON:", 20)
        sort = str(ipc_commands._json_command_payload(cmd, "REL_FRIENDS_PAGE_JSON:").get("sort") or "status")
    ipc_commands._emit(
        bot,
        "REL_FRIENDS_RESULT:",
        json.dumps(await bot.rel_manager.get_friends_list(page, page_size, sort)),
    )


async def handle_rel_closest(bot, cmd):
    payload = ipc_commands._json_command_payload(cmd, "REL_CLOSEST_JSON:")
    limit = int(payload.get("limit", 10))
    if limit < 1 or limit > 50:
        raise ValueError("limite invalido")
    ipc_commands._emit_json(bot, "REL_CLOSEST_RESULT:", await bot.rel_manager.get_closest_friends(limit))


async def handle_rel_pending_list(bot, cmd):
    page, page_size = (0, 10)
    if cmd.upper().startswith("REL_PENDING_PAGE_JSON:"):
        page, page_size = ipc_commands._page_payload(cmd, "REL_PENDING_PAGE_JSON:", 10)
    ipc_commands._emit(
        bot,
        "REL_PENDING_RESULT:",
        json.dumps(await bot.rel_manager.get_pending_requests(page, page_size)),
    )


async def handle_rel_blocked_list(bot, cmd):
    page, page_size = (0, 20)
    if cmd.upper().startswith("REL_BLOCKED_PAGE_JSON:"):
        page, page_size = ipc_commands._page_payload(cmd, "REL_BLOCKED_PAGE_JSON:", 20)
    ipc_commands._emit(
        bot,
        "REL_BLOCKED_RESULT:",
        json.dumps(await bot.rel_manager.get_blocked_list(page, page_size)),
    )


async def handle_rel_remove_all(bot, cmd):
    wl = [int(i.strip()) for i in cmd.upper().split(":")[1].split(",") if i.strip().isdigit()]
    bot.rel_manager.cancelled = False
    ipc_commands._spawn_safe(bot, bot.rel_manager.remove_all_friends(wl, request_id=ipc_commands._request_id(bot)))


async def handle_rel_unblock_all(bot, cmd):
    bot.rel_manager.cancelled = False
    ipc_commands._spawn_safe(bot, bot.rel_manager.unblock_all(request_id=ipc_commands._request_id(bot)))


async def handle_rel_accept_all(bot, cmd):
    bot.rel_manager.cancelled = False
    ipc_commands._spawn_safe(bot, bot.rel_manager.accept_all_requests(request_id=ipc_commands._request_id(bot)))


async def handle_rel_deny_all(bot, cmd):
    bot.rel_manager.cancelled = False
    ipc_commands._spawn_safe(bot, bot.rel_manager.deny_all_requests(request_id=ipc_commands._request_id(bot)))


async def handle_rel_nickname(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "REL_NICKNAME_JSON:")
        target_id = ipc_commands._positive_id(payload.get("target_id"))
        nickname = payload.get("nickname")
        if nickname is not None:
            if not isinstance(nickname, str) or len(nickname) > 32:
                raise ValueError("apelido invalido")
            nickname = nickname or None
        result = await bot.rel_manager.set_friend_nickname(target_id, nickname)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = ipc_commands._error_result("INVALID_INPUT", "ID ou apelido invalido.")
    ipc_commands._emit_json(bot, "REL_NICKNAME_RESULT:", result)


async def handle_rel_add_friend(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "REL_ADD_FRIEND_JSON:")
        username = payload.get("username")
        if not isinstance(username, str):
            raise ValueError("username invalido")
        username = username.strip()
        if not username or len(username) > 100:
            raise ValueError("username invalido")
        result = await bot.rel_manager.send_friend_request_by_name(username)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = ipc_commands._error_result("INVALID_INPUT", "Nome de usuario invalido.")
    ipc_commands._emit_json(bot, "REL_ADD_FRIEND_RESULT:", result)


async def _rel_target_action(bot, cmd, prefix: str, result_prefix: str, action):
    try:
        payload = ipc_commands._json_command_payload(cmd, prefix)
        target_id = ipc_commands._positive_id(payload.get("target_id"))
        result = await action(target_id)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = ipc_commands._error_result("INVALID_INPUT", "ID de usuario invalido.")
    ipc_commands._emit_json(bot, result_prefix, result)


async def handle_rel_block(bot, cmd):
    await _rel_target_action(
        bot, cmd, "REL_BLOCK_JSON:", "REL_BLOCK_RESULT:", lambda target_id: bot.rel_manager.block_user(target_id)
    )


async def handle_rel_unblock(bot, cmd):
    await _rel_target_action(
        bot, cmd, "REL_UNBLOCK_JSON:", "REL_UNBLOCK_RESULT:", lambda target_id: bot.rel_manager.unblock_user(target_id)
    )


EXACT = {
    "REL_STATS": handle_rel_stats,
    "REL_FRIENDS_LIST": handle_rel_friends_list,
    "REL_PENDING_LIST": handle_rel_pending_list,
    "REL_BLOCKED_LIST": handle_rel_blocked_list,
    "REL_UNBLOCK_ALL": handle_rel_unblock_all,
    "REL_ACCEPT_ALL": handle_rel_accept_all,
    "REL_DENY_ALL": handle_rel_deny_all,
}

PREFIX = {
    "REL_REMOVE_ALL:": handle_rel_remove_all,
    "REL_FRIENDS_PAGE_JSON:": handle_rel_friends_list,
    "REL_CLOSEST_JSON:": handle_rel_closest,
    "REL_PENDING_PAGE_JSON:": handle_rel_pending_list,
    "REL_BLOCKED_PAGE_JSON:": handle_rel_blocked_list,
    "REL_NICKNAME_JSON:": handle_rel_nickname,
    "REL_ADD_FRIEND_JSON:": handle_rel_add_friend,
    "REL_BLOCK_JSON:": handle_rel_block,
    "REL_UNBLOCK_JSON:": handle_rel_unblock,
}
