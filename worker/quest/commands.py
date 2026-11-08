import json

from ..core import commands as ipc_commands


def _quest_unavailable() -> dict:
    return ipc_commands._error_result("UNAVAILABLE", "Quest Manager indisponível.")


async def handle_quest_list(bot, cmd):
    manager = getattr(bot, "quest_manager", None)
    result = await manager.list_quests() if manager else _quest_unavailable()
    ipc_commands._emit_json(bot, "QUEST_LIST_RESULT:", result)


async def handle_quest_start(bot, cmd):
    quest_id = cmd.split(":", 1)[1].strip() if ":" in cmd else ""
    if not quest_id.isdigit() or int(quest_id) <= 0:
        ipc_commands._emit_json(
            bot,
            "QUEST_START_RESULT:",
            ipc_commands._error_result("INVALID_QUEST_ID", "ID de Quest inválido."),
        )
        return
    manager = getattr(bot, "quest_manager", None)
    result = (
        await manager.start_quest(quest_id, request_id=ipc_commands._request_id(bot))
        if manager
        else _quest_unavailable()
    )
    ipc_commands._emit_json(bot, "QUEST_START_RESULT:", result)


async def handle_quest_cancel(bot, cmd):
    manager = getattr(bot, "quest_manager", None)
    cancelled = await manager.cancel() if manager else False
    ipc_commands._emit_json(
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
    result = await manager.start_auto(request_id=ipc_commands._request_id(bot)) if manager else _quest_unavailable()
    ipc_commands._emit_json(bot, "QUEST_AUTO_RESULT:", result)


async def handle_quest_config(bot, cmd):
    payload_raw = cmd.split(":", 1)[1].strip() if ":" in cmd else ""
    if not payload_raw:
        ipc_commands._emit_json(
            bot, "QUEST:CONFIG_RESULT:", ipc_commands._error_result("INVALID_PAYLOAD", "Payload de configuração vazio.")
        )
        return

    try:
        payload = json.loads(payload_raw)
    except (json.JSONDecodeError, TypeError):
        ipc_commands._emit_json(
            bot,
            "QUEST:CONFIG_RESULT:",
            ipc_commands._error_result("INVALID_PAYLOAD", "Payload de configuração inválido."),
        )
        return

    if not isinstance(payload, dict):
        ipc_commands._emit_json(
            bot,
            "QUEST:CONFIG_RESULT:",
            ipc_commands._error_result("INVALID_PAYLOAD", "Payload de configuração deve ser um objeto."),
        )
        return

    webhook_url = payload.get("webhook_url")

    manager = getattr(bot, "quest_manager", None)
    if manager:
        await manager.set_runtime_config(
            webhook_url=webhook_url,
            request_id=ipc_commands._request_id(bot),
        )
    else:
        ipc_commands._emit_json(bot, "QUEST:CONFIG_RESULT:", _quest_unavailable())


EXACT = {
    "QUEST_LIST": handle_quest_list,
    "QUEST_CANCEL": handle_quest_cancel,
    "QUEST_AUTO": handle_quest_auto,
}

PREFIX = {
    "QUEST_START:": handle_quest_start,
    "QUEST_CONFIG:": handle_quest_config,
}
