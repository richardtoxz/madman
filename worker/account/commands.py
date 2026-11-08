import json

from ..core import commands as ipc_commands


def _account(bot):
    return getattr(bot, "account_manager", None)


def _account_unavailable() -> dict:
    return ipc_commands._error_result("UNAVAILABLE", "Recursos da conta indisponíveis.")


def _account_invalid(message: str = "Dados inválidos.") -> dict:
    return ipc_commands._error_result("INVALID_INPUT", message)


async def _run_account_action(coro, request_id: str | None):
    await ipc_commands._run_reported_action(
        coro, request_id, "ACCOUNT_RESULT:", "ACCOUNT_INTERNAL_ERROR", "Falha interna ao executar a operação da conta."
    )


def _account_spawn(bot, factory):
    manager = _account(bot)
    if manager is None:
        ipc_commands._emit_json(bot, "ACCOUNT_RESULT:", _account_unavailable())
        return
    ipc_commands._spawn_command(bot, _run_account_action(factory(manager), ipc_commands._request_id(bot)))


async def handle_account_status(bot, cmd):
    manager = _account(bot)
    ipc_commands._emit_json(bot, "ACCOUNT_RESULT:", manager.get_custom_status() if manager else _account_unavailable())


async def handle_account_profile(bot, cmd):
    refresh = cmd.upper() == "ACCOUNT_PROFILE_REFRESH"
    _account_spawn(bot, lambda m: m.get_profile(refresh=refresh))


async def handle_account_status_set(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "ACCOUNT_STATUS_SET_JSON:")
    except (TypeError, ValueError, json.JSONDecodeError):
        ipc_commands._emit_json(bot, "ACCOUNT_RESULT:", _account_invalid())
        return
    _account_spawn(bot, lambda m: m.set_custom_status(payload))


async def handle_account_sessions(bot, cmd):
    _account_spawn(bot, lambda m: m.list_sessions())


async def handle_account_mentions(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "ACCOUNT_MENTIONS_JSON:")
        limit = int(payload.get("limit", 25))
        before = payload.get("before")
        before = ipc_commands._positive_id(before) if before not in (None, "") else None
    except (TypeError, ValueError, json.JSONDecodeError):
        ipc_commands._emit_json(bot, "ACCOUNT_RESULT:", _account_invalid())
        return
    _account_spawn(bot, lambda m: m.list_mentions(limit=limit, before=before))


async def handle_account_mention_delete(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "ACCOUNT_MENTION_DELETE_JSON:")
        raw_ids = payload.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 100:
            raise ValueError("ids invalidos")
        ids = [ipc_commands._positive_id(value) for value in raw_ids]
    except (TypeError, ValueError, json.JSONDecodeError):
        ipc_commands._emit_json(bot, "ACCOUNT_RESULT:", _account_invalid("IDs de menção inválidos."))
        return
    _account_spawn(bot, lambda m: m.delete_mentions(ids))


async def handle_account_unread(bot, cmd):
    manager = _account(bot)
    ipc_commands._emit_json(bot, "ACCOUNT_RESULT:", manager.unread_summary() if manager else _account_unavailable())


async def handle_account_ack_all(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "ACCOUNT_ACK_ALL_JSON:")
        scope = str(payload.get("scope") or "all")
        if scope not in ("all", "private", "guilds"):
            raise ValueError("escopo invalido")
    except (TypeError, ValueError, json.JSONDecodeError):
        ipc_commands._emit_json(bot, "ACCOUNT_RESULT:", _account_invalid())
        return
    _account_spawn(bot, lambda m: m.ack_all(scope))


EXACT = {
    "ACCOUNT_STATUS": handle_account_status,
    "ACCOUNT_PROFILE": handle_account_profile,
    "ACCOUNT_PROFILE_REFRESH": handle_account_profile,
    "ACCOUNT_SESSIONS": handle_account_sessions,
    "ACCOUNT_UNREAD": handle_account_unread,
}

PREFIX = {
    "ACCOUNT_STATUS_SET_JSON:": handle_account_status_set,
    "ACCOUNT_MENTIONS_JSON:": handle_account_mentions,
    "ACCOUNT_MENTION_DELETE_JSON:": handle_account_mention_delete,
    "ACCOUNT_ACK_ALL_JSON:": handle_account_ack_all,
}
