"""Regras do Rich Presence editável, compartilhadas entre o worker e o engine."""

from __future__ import annotations

from urllib.parse import urlparse

ACTIVITY_TYPES = ("playing", "streaming", "listening", "watching", "competing")
TIMERS = ("none", "since_start", "since_save", "duration")
STATUS_DISPLAYS = ("name", "state", "details")
PLATFORMS = ("desktop", "xbox", "ps4", "ps5", "android", "ios")
MAX_TEXT = 128
MAX_URL = 512
MAX_LINK = 256
MAX_BUTTONS = 2
MAX_BUTTON_LABEL = 32
MAX_DURATION_MINUTES = 1440
MAX_PARTY = 99
_STREAM_HOSTS = ("twitch.tv", "youtube.com", "youtu.be")

_TEXT_FIELDS = ("details", "state")
_IMAGE_FIELDS = (("large_image", "large_text"), ("small_image", "small_text"))
# Link de clique de cada texto/imagem: só vale quando o campo de origem existe.
_LINK_FIELDS = (
    ("details", "details_url"),
    ("state", "state_url"),
    ("large_image", "large_url"),
    ("small_image", "small_url"),
)
_CUSTOM_KEYS = frozenset(
    {"type", "name", "details", "state", "timer", "buttons", "status_display", "stream_url"}
    | {"duration_minutes", "party_current", "party_max", "platform"}
    | {key for pair in _IMAGE_FIELDS for key in pair}
    | {link for _, link in _LINK_FIELDS}
)
_FIELD_NAMES = {
    "name": "Nome",
    "details": "Detalhes",
    "state": "Estado",
    "large_image": "Imagem grande",
    "large_text": "Texto da imagem grande",
    "small_image": "Imagem pequena",
    "small_text": "Texto da imagem pequena",
    "details_url": "Link dos detalhes",
    "state_url": "Link do estado",
    "large_url": "Link da imagem grande",
    "small_url": "Link da imagem pequena",
}


def _text(raw: dict, key: str, label: str, limit: int) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} precisa ser texto.")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{label} pode ter no máximo {limit} caracteres.")
    return value or None


def _url(raw: dict, key: str, label: str, limit: int = MAX_URL) -> str | None:
    value = _text(raw, key, label, limit)
    if value is not None and not value.lower().startswith("https://"):
        raise ValueError(f"{label} precisa ser um link https://.")
    return value


def _int(raw: dict, key: str, label: str, low: int, high: int) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} precisa ser um número de {low} a {high}.")
    return value


def _buttons(raw: object) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("Botões precisam ser uma lista.")
    if len(raw) > MAX_BUTTONS:
        raise ValueError(f"No máximo {MAX_BUTTONS} botões.")
    buttons = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or set(item) - {"label", "url"}:
            raise ValueError(f"Botão {index} inválido.")
        label = _text(item, "label", f"Rótulo do botão {index}", MAX_BUTTON_LABEL)
        if label is None:
            raise ValueError(f"O botão {index} precisa de um rótulo.")
        url = _url(item, "url", f"Link do botão {index}")
        if url is None:
            raise ValueError(f"O botão {index} precisa de um link.")
        buttons.append({"label": label, "url": url})
    return buttons


def _stream_url(raw: dict, activity_type: str) -> str | None:
    if activity_type != "streaming":
        return None
    url = _url(raw, "stream_url", "Link da transmissão")
    host = (urlparse(url).hostname or "").lower() if url else ""
    if not any(host == allowed or host.endswith("." + allowed) for allowed in _STREAM_HOSTS):
        raise ValueError("Transmitindo precisa de um link https:// da Twitch ou do YouTube.")
    return url


def _choice(raw: dict, key: str, options: tuple, default, message: str):
    value = raw.get(key, default)
    if value is None and default is None:
        return None
    if value not in options:
        raise ValueError(message)
    return value


def validate_custom(raw: object) -> dict:
    """Normaliza um Rich Presence personalizado; lança ValueError com mensagem ao usuário."""
    if not isinstance(raw, dict):
        raise ValueError("Rich Presence inválido.")
    unknown = set(raw) - _CUSTOM_KEYS
    if unknown:
        raise ValueError(f"Campo desconhecido: {sorted(unknown)[0]}.")
    activity_type = _choice(
        raw, "type", ACTIVITY_TYPES, "", "Tipo inválido: use Jogando, Transmitindo, Ouvindo, Assistindo ou Competindo."
    )
    timer = _choice(raw, "timer", TIMERS, "none", "Cronômetro inválido.")
    name = _text(raw, "name", _FIELD_NAMES["name"], MAX_TEXT)
    if name is None:
        raise ValueError("O nome é obrigatório.")

    custom: dict = {"type": activity_type, "name": name}
    for key in _TEXT_FIELDS:
        custom[key] = _text(raw, key, _FIELD_NAMES[key], MAX_TEXT)
    custom["timer"] = timer
    for image_key, text_key in _IMAGE_FIELDS:
        image = _url(raw, image_key, _FIELD_NAMES[image_key])
        text = _text(raw, text_key, _FIELD_NAMES[text_key], MAX_TEXT)
        custom[image_key] = image
        custom[text_key] = text if image else None
    custom["buttons"] = _buttons(raw.get("buttons"))
    custom["status_display"] = _choice(
        raw, "status_display", STATUS_DISPLAYS, "name", "Texto da lista de membros inválido."
    )
    for source, link in _LINK_FIELDS:
        url = _url(raw, link, _FIELD_NAMES[link], MAX_LINK)
        custom[link] = url if custom[source] else None
    custom["stream_url"] = _stream_url(raw, activity_type)
    duration = _int(raw, "duration_minutes", "A duração (minutos)", 1, MAX_DURATION_MINUTES)
    if timer == "duration" and duration is None:
        raise ValueError("Informe a duração em minutos (1 a 1440).")
    custom["duration_minutes"] = duration if timer == "duration" else None
    current = _int(raw, "party_current", "O grupo atual", 1, MAX_PARTY)
    maximum = _int(raw, "party_max", "O tamanho do grupo", 1, MAX_PARTY)
    if (current is None) != (maximum is None):
        raise ValueError("Informe o grupo atual e o tamanho do grupo juntos.")
    if current is not None and current > maximum:
        raise ValueError("O grupo atual não pode passar do tamanho do grupo.")
    custom["party_current"], custom["party_max"] = current, maximum
    custom["platform"] = _choice(raw, "platform", PLATFORMS, None, "Plataforma inválida.")
    return custom


def validate_rich_presence_payload(raw: object) -> tuple[bool, dict]:
    """Valida `{enabled, custom}`. Retorna (True, payload normalizado) ou (False, erro)."""
    if not isinstance(raw, dict) or set(raw) - {"enabled", "custom"}:
        return False, _error("Configuração de Rich Presence inválida.")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        return False, _error("Informe se o Rich Presence fica ligado ou desligado.")
    custom = raw.get("custom")
    if custom is not None:
        try:
            custom = validate_custom(custom)
        except ValueError as exc:
            return False, _error(str(exc))
    return True, {"enabled": enabled, "custom": custom}


def _error(message: str) -> dict:
    return {"ok": False, "code": "INVALID_RICH_PRESENCE", "message": message}
