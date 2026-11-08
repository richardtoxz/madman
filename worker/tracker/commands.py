import json

from tracker_protocol import normalize_target_ids

from ..core import commands as ipc_commands


async def handle_tracker_start(bot, cmd):
    try:
        payload = ipc_commands._json_command_payload(cmd, "TRACKER_START_JSON:")
        target_id = normalize_target_ids(payload.get("target_ids", payload.get("target_id")))
        if "target_ids" not in payload:
            target_id = target_id[0]
        webhook_url = payload.get("webhook_url")
        if not isinstance(webhook_url, str):
            raise ValueError("webhook invalido")
        result = await bot.tracker_manager.start_tracking(target_id, webhook_url)
    except (TypeError, ValueError, json.JSONDecodeError):
        result = ipc_commands._error_result("INVALID_INPUT", "ID do alvo ou webhook invalido.", enabled=False)
    ipc_commands._emit_json(bot, "TRACKER_RESULT:", result)


async def handle_tracker_stop(bot, cmd):
    result = await bot.tracker_manager.stop_tracking()
    ipc_commands._emit_json(bot, "TRACKER_RESULT:", result)


async def handle_tracker_status(bot, cmd):
    ipc_commands._emit_json(bot, "TRACKER_STATUS_RESULT:", bot.tracker_manager.get_status())


EXACT = {
    "TRACKER_STOP": handle_tracker_stop,
    "TRACKER_STATUS": handle_tracker_status,
}

PREFIX = {
    "TRACKER_START_JSON:": handle_tracker_start,
}
