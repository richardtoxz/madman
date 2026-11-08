import json

from ..core import commands as ipc_commands


async def _run_voice_action(coro, request_id: str | None):
    await ipc_commands._run_reported_action(
        coro, request_id, "VOICE_RESULT:", "VOICE_INTERNAL_ERROR", "Falha interna ao executar a operacao de voz."
    )


async def handle_voice_join(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "VOICE_JOIN_JSON:")
        channel_id = ipc_commands._positive_id(payload.get("channel_id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        ipc_commands._emit_json(
            bot, "VOICE_RESULT:", ipc_commands._error_result("INVALID_CHANNEL_ID", "ID de canal invalido.")
        )
        return
    ipc_commands._spawn_command(
        bot, _run_voice_action(bot.voice_manager.join_voice(channel_id), ipc_commands._request_id(bot))
    )


async def handle_voice_leave(bot, cmd):
    ipc_commands._spawn_command(bot, _run_voice_action(bot.voice_manager.leave_voice(), ipc_commands._request_id(bot)))


async def handle_voice_follow(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "VOICE_FOLLOW_JSON:")
        target_id = ipc_commands._positive_id(payload.get("target_id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        ipc_commands._emit_json(
            bot, "VOICE_RESULT:", ipc_commands._error_result("INVALID_TARGET_ID", "ID de usuario invalido.")
        )
        return
    ipc_commands._spawn_command(
        bot, _run_voice_action(bot.voice_manager.start_follow(target_id), ipc_commands._request_id(bot))
    )


async def handle_voice_unfollow(bot, cmd):
    ipc_commands._spawn_command(bot, _run_voice_action(bot.voice_manager.stop_follow(), ipc_commands._request_id(bot)))


async def handle_voice_status(bot, cmd):
    ipc_commands._emit_json(bot, "VOICE_STATUS_RESULT:", await bot.voice_manager.get_status())


EXACT = {
    "VOICE_LEAVE": handle_voice_leave,
    "VOICE_UNFOLLOW": handle_voice_unfollow,
    "VOICE_STATUS": handle_voice_status,
}

PREFIX = {
    "VOICE_JOIN_JSON:": handle_voice_join,
    "VOICE_FOLLOW_JSON:": handle_voice_follow,
}
