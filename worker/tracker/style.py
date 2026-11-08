"""Aparencia das mensagens do tracker no Discord.

Regras: cor por categoria (o mesmo tipo de evento sempre tem a mesma cor),
horarios nativos do Discord (<t:...>) que aparecem no fuso de quem le, mencoes
de canal/usuario resolvidas pelo proprio cliente e texto secundario pequeno
(`-#`) para metadados. Sem textos promocionais.
"""

from __future__ import annotations

from datetime import datetime

CATEGORY_COLORS = {
    "message_batch": 0x5865F2,
    "history_message": 0x4E5058,
    "message_edit": 0xFEE75C,
    "message_delete": 0xED4245,
    "interaction": 0x9B84EE,
    "reaction": 0x9B84EE,
    "poll_vote": 0x9B84EE,
    "voice": 0x1ABC9C,
    "profile": 0xEB459E,
    "member": 0x3498DB,
    "server": 0x3498DB,
    "private_call": 0xF0B232,
    "typing": 0x80848E,
    "general": 0x80848E,
}

STATUS_COLORS = {
    "online": 0x23A55A,
    "idle": 0xF0B232,
    "dnd": 0xF23F43,
    "offline": 0x80848E,
}

INLINE_VALUE_MAX = 45
INLINE_NAME_MAX = 28


def color_for(event_type: str, status: str | None = None) -> int:
    if event_type == "presence":
        return STATUS_COLORS.get(str(status or "").lower(), 0x80848E)
    return CATEGORY_COLORS.get(event_type, CATEGORY_COLORS["general"])


def discord_time(value, style: str = "R") -> str:
    """<t:unix:estilo>; aceita datetime ou segundos Unix."""
    if isinstance(value, datetime):
        value = int(value.timestamp())
    try:
        return f"<t:{int(value)}:{style}>"
    except (TypeError, ValueError):
        return ""


def channel_ref(channel_id) -> str:
    return f"<#{int(channel_id)}>" if channel_id else "DM"


def user_ref(user_id) -> str:
    return f"<@{int(user_id)}>"


def small(text: str) -> str:
    """Texto secundario pequeno (subtexto do Discord)."""
    return f"-# {text}"


def is_short_field(name: str, value: str) -> bool:
    return len(name) <= INLINE_NAME_MAX and len(value) <= INLINE_VALUE_MAX and "\n" not in value


def footer_text(target_id, extra: str | None = None) -> str:
    parts = [extra] if extra else []
    parts.append(f"Alvo {target_id}")
    parts.append("Madman Tracker")
    return " • ".join(parts)
