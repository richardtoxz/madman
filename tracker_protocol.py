"""Validation shared by the tracker panel, engine and worker."""

import re
from urllib.parse import urlsplit


MAX_TRACKER_TARGETS = 10

_WEBHOOK_PATH = re.compile(
    r"^/api(?:/v\d+)?/webhooks/(?P<id>\d{15,25})/(?P<token>[A-Za-z0-9._-]{20,})/?$"
)
_WEBHOOK_HOSTS = {
    "discord.com",
    "canary.discord.com",
    "ptb.discord.com",
    "discordapp.com",
}


def normalize_webhook_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 500:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _WEBHOOK_HOSTS
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not _WEBHOOK_PATH.fullmatch(parsed.path)
    ):
        return None
    return text.rstrip("/")


def normalize_target_ids(value) -> list[int]:
    if isinstance(value, str):
        value = re.split(r"[\s,;]+", value.strip())
    elif isinstance(value, int) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("Informe pelo menos um ID de usuário.")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise ValueError("Os IDs devem ser números inteiros válidos.")
        if isinstance(item, str) and not re.fullmatch(r"[0-9]+", item):
            raise ValueError("Os IDs devem ser números inteiros válidos.")
        target_id = int(item)
        if not 0 < target_id < 2**64:
            raise ValueError("ID de usuário inválido.")
        if target_id not in result:
            result.append(target_id)
    if len(result) > MAX_TRACKER_TARGETS:
        raise ValueError(f"O limite é de {MAX_TRACKER_TARGETS} usuários por conta.")
    return result
