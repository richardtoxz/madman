"""Perfil da conta a partir do /users/@me (login com Discord ou por token).

Monta o mesmo formato que o worker devolve em ``profile_visuals``, para o painel mostrar
avatar, banner, decoração, tag de servidor e insígnias antes de a conta ser conectada.
"""

from __future__ import annotations

import re
from typing import Any

CDN = "https://cdn.discordapp.com"

_HASH = re.compile(r"^(a_)?[0-9a-f]{32}$")

# Não é um bit de public_flags: marca a insígnia que vem de premium_type.
_NITRO = -1

# Bit de public_flags -> (descrição, ícone em /badge-icons). Ordem igual à do perfil no Discord.
_FLAG_BADGES: tuple[tuple[int, str, str], ...] = (
    (1 << 0, "Equipe do Discord", "5e74e9b61934fc1f67c65515d1f7e60d"),
    (1 << 1, "Dono de servidor parceiro", "3f9748e53446a137a052f3454e2de41e"),
    (1 << 18, "Programas de moderação (ex-membro)", "fee1624003e2fee35cb398e125dc479b"),
    (1 << 2, "Eventos HypeSquad", "bf01d1073931f921909045f3a39fd264"),
    (1 << 6, "HypeSquad Bravery", "8a88d63823d8a71cd5e390baa45efa02"),
    (1 << 7, "HypeSquad Brilliance", "011940fd013da3f7fb926e4a1cd2e618"),
    (1 << 8, "HypeSquad Balance", "3aa41de486fa12454c3761e8e223442e"),
    (1 << 3, "Caçador de bugs do Discord", "2717692c7dca7289b35297368a940dd0"),
    (1 << 14, "Caçador de bugs do Discord", "848f79194d4be5ff5f81505cbd0ce1e6"),
    (1 << 22, "Desenvolvedor ativo", "6bdc42827a38498929a4920da12695d9"),
    (1 << 17, "Desenvolvedor verificado de bot pioneiro", "6df5892e0f35b051f8b61eace34f4967"),
    (_NITRO, "Assinante Nitro", "2ba85e8026a8614b640c2837bcdfe21b"),
    (1 << 9, "Apoiador inicial", "7060786766c9c840eb3019e725d2b358"),
)


def _clip(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def _asset(path: str, image_hash: Any, size: int) -> str | None:
    if not isinstance(image_hash, str) or not _HASH.match(image_hash):
        return None
    ext = "gif" if image_hash.startswith("a_") else "png"
    return f"{CDN}/{path}/{image_hash}.{ext}?size={size}"


def _avatar(user_id: int, image_hash: Any) -> str:
    # Sem foto, o Discord usa um dos seis avatares padrão.
    return _asset(f"avatars/{user_id}", image_hash, 128) or f"{CDN}/embed/avatars/{(user_id >> 22) % 6}.png"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _badges(data: dict[str, Any]) -> list[dict[str, str]]:
    flags = _int(data.get("public_flags"))
    has_nitro = _int(data.get("premium_type")) > 0
    return [
        {"description": description, "url": f"{CDN}/badge-icons/{icon}.png"}
        for bit, description, icon in _FLAG_BADGES
        if (has_nitro if bit == _NITRO else flags & bit)
    ][:8]


def _guild_tag(data: dict[str, Any]) -> str | None:
    primary = data.get("primary_guild")
    if not isinstance(primary, dict) or not primary.get("identity_enabled"):
        return None
    return _clip(primary.get("tag"), 8)


def _accent(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFFFFFF:
        return f"#{value:06x}"
    return None


def identity_profile(data: dict[str, Any]) -> dict[str, Any] | None:
    """Perfil visual da conta, ou None quando o /users/@me não trouxe um ID válido."""
    raw_id = str(data.get("id") or "")
    if not raw_id.isdigit():
        return None
    user_id = int(raw_id)
    decoration = data.get("avatar_decoration_data")
    decoration_asset = decoration.get("asset") if isinstance(decoration, dict) else None
    return {
        "id": raw_id,
        "username": _clip(data.get("username"), 40),
        "display_name": _clip(data.get("global_name"), 40),
        "avatar_url": _avatar(user_id, data.get("avatar")),
        "decoration_url": _asset("avatar-decoration-presets", decoration_asset, 160),
        "name_style": None,
        "guild_tag": _guild_tag(data),
        "banner_url": _asset(f"banners/{user_id}", data.get("banner"), 480),
        "accent": _accent(data.get("accent_color")),
        "theme": None,
        "bio": None,
        "pronouns": None,
        "badges": _badges(data),
        # Não é o perfil completo (bio, tema e insígnias como boost ficam com o worker).
        "complete": False,
        "source": "discord",
    }
