from ..core import commands as ipc_commands


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


_UNAVAILABLE = ("PRESENCE_UNAVAILABLE", "A presença ainda não está disponível neste worker.")


async def handle_rich_presence_status(bot, cmd):
    presence = getattr(bot, "presence_manager", None)
    if not presence:
        ipc_commands._emit_json(bot, "RICH_PRESENCE_RESULT:", ipc_commands._error_result(*_UNAVAILABLE))
        return
    ipc_commands._emit_json(bot, "RICH_PRESENCE_RESULT:", presence.status())


async def handle_rich_presence_set(bot, cmd):
    presence = getattr(bot, "presence_manager", None)
    if not presence:
        ipc_commands._emit_json(bot, "RICH_PRESENCE_RESULT:", ipc_commands._error_result(*_UNAVAILABLE))
        return
    try:
        payload = ipc_commands._json_command_payload(cmd, "RICH_PRESENCE_SET_JSON:")
    except ValueError:
        payload = None
    enabled = payload.get("enabled") if payload is not None else None
    if not isinstance(enabled, bool):
        result = ipc_commands._error_result("INVALID_RICH_PRESENCE", "Configuração de Rich Presence inválida.")
    else:
        try:
            result = await presence.configure(enabled, payload.get("custom"))
        except ValueError as exc:
            result = ipc_commands._error_result("INVALID_RICH_PRESENCE", str(exc) or "Rich Presence inválido.")
    ipc_commands._emit_json(bot, "RICH_PRESENCE_RESULT:", result)


EXACT = {
    "PRESENCE_MAIN": handle_presence_main,
    "RICH_PRESENCE_STATUS": handle_rich_presence_status,
}

PREFIX = {
    "PRESENCE_TASK:": handle_presence_task,
    "PRESENCE_UPDATE:": handle_presence_update,
    "RICH_PRESENCE_SET_JSON:": handle_rich_presence_set,
}
