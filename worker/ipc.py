REQUEST_ID_MARKER = "||REQ:"


def attach_request_id(command: str, request_id: str | None) -> str:
    if not request_id:
        return command
    return f"{command}{REQUEST_ID_MARKER}{request_id}"


def split_request_id(message: str) -> tuple[str, str | None]:
    command, marker, request_id = message.rpartition(REQUEST_ID_MARKER)
    if not marker:
        return message, None
    return command, request_id or None


def format_response(prefix: str, payload: str, request_id: str | None = None) -> str:
    text = f"{prefix}{payload}"
    if request_id:
        return f"{text}{REQUEST_ID_MARKER}{request_id}"
    return text


def format_line(message: str, request_id: str | None = None) -> str:
    if request_id:
        return f"{message}{REQUEST_ID_MARKER}{request_id}"
    return message


def split_response_metadata(message: str) -> tuple[str, str | None]:
    return split_request_id(message)


def emit_response(prefix: str, payload: str, request_id: str | None = None) -> None:
    print(format_response(prefix, payload, request_id), flush=True)


def emit_line(message: str, request_id: str | None = None) -> None:
    print(format_line(message, request_id), flush=True)