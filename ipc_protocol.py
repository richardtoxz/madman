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


def encode_json_payload(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def decode_json_payload(payload: str) -> dict[str, Any] | None:
    if not payload or not isinstance(payload, str):
        return None
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


_CLEANUP_DELETE_COUNTS_RE = re.compile(
    r"Deletadas\s+(?P<deleted>\d+)(?:\s+de\s+(?P<total>\d+))?",
    re.IGNORECASE,
)
_CLEANUP_FIELD_PATTERNS = {
    "missing": re.compile(r"Ja ausentes\s+(\d+)", re.IGNORECASE),
    "ignored": re.compile(r"ignoradas\s+(\d+)", re.IGNORECASE),
    "failed": re.compile(r"falhas\s+(\d+)", re.IGNORECASE),
    "remaining": re.compile(r"restantes\s+(\d+)", re.IGNORECASE),
    "unavailable_sources": re.compile(r"fontes indisponiveis\s+(\d+)", re.IGNORECASE),
}


def parse_cleanup_result(
    result: str | dict[str, Any] | None, fallback_total: int = 0
) -> dict[str, Any]:
    if isinstance(result, dict):
        data = dict(result)
    elif isinstance(result, str) and result.strip().startswith("{"):
        data = decode_json_payload(result.strip()) or {}
    elif isinstance(result, str) and "JSON:" in result:
        _, _, raw_json = result.partition("JSON:")
        data = decode_json_payload(raw_json.strip()) or {}
    else:
        data = {}

    if data:
        status = str(data.get("status") or "COMPLETADO").upper()
        deleted = int(data.get("deleted", 0) or 0)
        total = max(int(data.get("total", 0) or 0), fallback_total, deleted)
        text = str(data.get("text") or f"{status}:Deletadas {deleted} de {total}.")
        if status == "CANCELADO":
            outcome = "cancelled"
        elif status in {"ERRO", "ABORTADO"}:
            outcome = "error"
        elif status == "PARCIAL":
            outcome = "partial"
        else:
            outcome = "complete"
        return {
            "outcome": outcome,
            "status": status,
            "deleted": deleted,
            "total": total,
            "missing": int(data.get("missing", 0) or 0),
            "ignored": int(data.get("ignored", 0) or 0),
            "failed": int(data.get("failed", 0) or 0),
            "remaining": int(data.get("remaining", 0) or 0),
            "unavailable_sources": int(data.get("unavailable_sources", 0) or 0),
            "text": text,
        }

    text = str(result or "ERRO:Sem resposta do worker").strip()
    match = _CLEANUP_DELETE_COUNTS_RE.search(text)
    deleted = int(match.group("deleted")) if match else 0
    total = (
        int(match.group("total"))
        if match and match.group("total") is not None
        else max(fallback_total, deleted)
    )

    normalized = text.casefold()
    if normalized.startswith("cancelado"):
        outcome = "cancelled"
        status = "CANCELADO"
    elif normalized.startswith(("erro:", "abortado:")) or any(
        marker in normalized
        for marker in ("timeout", "nao conectado", "não conectado")
    ):
        outcome = "error"
        status = "ABORTADO" if normalized.startswith("abortado:") else "ERRO"
    elif normalized.startswith("parcial:"):
        outcome = "partial"
        status = "PARCIAL"
    else:
        outcome = "complete"
        status = "COMPLETADO"

    extracted = {}
    for key, pattern in _CLEANUP_FIELD_PATTERNS.items():
        field_match = pattern.search(text)
        extracted[key] = int(field_match.group(1)) if field_match else 0

    return {
        "outcome": outcome,
        "status": status,
        "deleted": deleted,
        "total": total,
        "text": text,
        **extracted,
    }
