from ..core import commands as ipc_commands


async def handle_protection_enable(bot, cmd):
    whitelist = None
    if cmd.upper().startswith("PROTECTION_ENABLE_JSON:"):
        whitelist = ipc_commands._whitelist_payload(cmd, "PROTECTION_ENABLE_JSON:", max_items=150)
    result = bot.protection_manager.enable(whitelist)
    if result.get("ok") and getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_protection(True)
    ipc_commands._emit_json(bot, "PROTECTION_RESULT:", result)


async def handle_protection_disable(bot, cmd):
    result = bot.protection_manager.disable()
    if result.get("ok") and getattr(bot, "presence_manager", None):
        await bot.presence_manager.set_protection(False)
    ipc_commands._emit_json(bot, "PROTECTION_RESULT:", result)


async def handle_protection_status(bot, cmd):
    ipc_commands._emit_json(bot, "PROTECTION_STATUS_RESULT:", bot.protection_manager.get_status())


async def handle_protection_configure(bot, cmd):
    whitelist = ipc_commands._whitelist_payload(cmd, "PROTECTION_CONFIG_JSON:", max_items=150)
    ipc_commands._emit_json(bot, "PROTECTION_RESULT:", bot.protection_manager.configure(whitelist))


EXACT = {
    "PROTECTION_ENABLE": handle_protection_enable,
    "PROTECTION_DISABLE": handle_protection_disable,
    "PROTECTION_STATUS": handle_protection_status,
}

PREFIX = {
    "PROTECTION_ENABLE_JSON:": handle_protection_enable,
    "PROTECTION_CONFIG_JSON:": handle_protection_configure,
}
