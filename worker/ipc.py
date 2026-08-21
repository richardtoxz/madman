from ipc_protocol import (
    REQUEST_ID_MARKER,
    attach_request_id,
    format_line,
    format_response,
    split_request_id,
)


def split_response_metadata(message: str) -> tuple[str, str | None]:
    return split_request_id(message)


def emit_response(prefix: str, payload: str, request_id: str | None = None) -> None:
    print(format_response(prefix, payload, request_id), flush=True)


def emit_line(message: str, request_id: str | None = None) -> None:
    print(format_line(message, request_id), flush=True)


def sanitize_ipc_field(value, *, limit: int = 100) -> str:
    """Impede que dados vindos do Discord alterem o protocolo de uma linha."""
    text = " ".join(str(value or "Desconhecido").split())
    text = text.replace(REQUEST_ID_MARKER, "REQ:").replace(":", "：")
    return text[:limit] or "Desconhecido"
