import asyncio
import json

from ipc_protocol import encode_json_payload

from .ipc import emit_line, emit_response


def _state(bot):
    return bot.control_state


def _request_id(bot):
    return getattr(bot, "_current_request_id", None)


def _emit(bot, prefix: str, payload: str):
    emit_response(prefix, payload, _request_id(bot))


def _emit_line(bot, message: str):
    emit_line(message, _request_id(bot))


def _error_result(code: str, message: str, **extra) -> dict:
    return {"ok": False, "code": code, "message": message, **extra}


def _emit_json(bot, prefix: str, payload: dict):
    _emit(bot, prefix, encode_json_payload(payload))


async def _run_reported_action(coro, request_id: str | None, prefix: str, failure_code: str, failure_message: str):
    try:
        result = await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        result = _error_result(failure_code, failure_message)
    emit_response(prefix, encode_json_payload(result), request_id)


def _spawn_command(bot, coroutine):
    spawn = getattr(bot, "spawn_command_task", None)
    if spawn is not None:
        return spawn(coroutine)
    return asyncio.create_task(coroutine)


def _spawn_safe(bot, coroutine, **exec_kwargs):
    return _spawn_command(bot, bot._safe_exec(coroutine, **exec_kwargs))


async def _presence_token(bot, label: str):
    if getattr(bot, "presence_manager", None):
        return await bot.presence_manager.set_task(label)
    return None


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


def _id_arg(cmd: str) -> int:
    return int(cmd.upper().split(":")[1])


def _page_payload(cmd: str, prefix: str, default_size: int) -> tuple[int, int]:
    payload = _json_command_payload(cmd, prefix)
    page = int(payload.get("page", 0))
    page_size = int(payload.get("page_size", default_size))
    if page < 0 or page > 100_000 or page_size < 1 or page_size > 25:
        raise ValueError("pagina invalida")
    return page, page_size


def _whitelist_payload(cmd: str, prefix: str, max_items: int) -> list[int]:
    payload = _json_command_payload(cmd, prefix)
    raw_whitelist = payload.get("whitelist", [])
    if not isinstance(raw_whitelist, list) or len(raw_whitelist) > max_items:
        raise ValueError("whitelist invalida")
    return sorted({_positive_id(item_id) for item_id in raw_whitelist})
