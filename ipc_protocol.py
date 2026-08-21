"""Protocolo IPC puro compartilhado pelo painel e pelo worker."""

from __future__ import annotations

import json
import re
from typing import Any


IPC_PROTOCOL_VERSION = 1
REQUEST_ID_MARKER = "||REQ:"
MAX_IPC_LINE_LENGTH = 64 * 1024
MAX_IPC_COMMAND_LENGTH = 16 * 1024
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class IPCProtocolError(ValueError):
    """Mensagem IPC invalida ou incompatível."""


def is_valid_request_id(request_id: str | None) -> bool:
    return bool(request_id and _REQUEST_ID_RE.fullmatch(request_id))


def attach_request_id(command: str, request_id: str | None) -> str:
    if request_id is None:
        return command
    if not is_valid_request_id(request_id):
        raise IPCProtocolError("request_id invalido")
    return f"{command}{REQUEST_ID_MARKER}{request_id}"


def split_request_id(message: str) -> tuple[str, str | None]:
    command, marker, request_id = message.rpartition(REQUEST_ID_MARKER)
    if not marker or not is_valid_request_id(request_id):
        return message, None
    return command, request_id


def format_response(prefix: str, payload: str, request_id: str | None = None) -> str:
    return attach_request_id(f"{prefix}{payload}", request_id)


def format_line(message: str, request_id: str | None = None) -> str:
    return attach_request_id(message, request_id)


def encode_ready(username: str, user_id: int) -> str:
    return json.dumps(
        {
            "protocol": IPC_PROTOCOL_VERSION,
            "username": username,
            "user_id": user_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_ready(payload: str) -> tuple[str, int]:
    try:
        data: dict[str, Any] = json.loads(payload)
        protocol = int(data["protocol"])
        username = str(data["username"])
        user_id = int(data["user_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise IPCProtocolError("handshake READY invalido") from error

    if protocol != IPC_PROTOCOL_VERSION:
        raise IPCProtocolError(
            f"protocolo incompatível: worker={protocol}, painel={IPC_PROTOCOL_VERSION}"
        )
    if not username or user_id <= 0:
        raise IPCProtocolError("identidade invalida no handshake")
    return username, user_id


def encode_startup_error(code: str, message: str) -> str:
    return json.dumps(
        {"code": code, "message": message},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_startup_error(payload: str) -> tuple[str, str]:
    try:
        data: dict[str, Any] = json.loads(payload)
        code = str(data["code"])
        message = str(data["message"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise IPCProtocolError("erro de inicializacao invalido") from error

    if not code or not message:
        raise IPCProtocolError("erro de inicializacao incompleto")
    return code, message
