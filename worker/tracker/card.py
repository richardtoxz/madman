"""Snapshot de um usuário acompanhado, no formato consumido pelo painel web."""

from __future__ import annotations

import discord

_ACTIVITY_KEYS = {"playing", "streaming", "listening", "watching", "competing"}
_DEVICES = ("desktop", "mobile", "web", "embedded")
_STATUSES = {"online", "idle", "dnd", "offline"}
_MAX_ACTIVITIES = 5


def _clip(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _image_url(assets, which: str) -> str | None:
    try:
        image = getattr(assets, which, None)
        return str(image.url) if image else None
    except Exception:
        return None


def _millis(moment) -> int | None:
    try:
        return int(moment.timestamp() * 1000) if moment else None
    except Exception:
        return None


def _party(activity) -> str | None:
    party = getattr(activity, "party", None)
    current = getattr(party, "current_size", None)
    maximum = getattr(party, "max_size", None)
    return f"{current} de {maximum}" if current and maximum else None


def activity_items(subject) -> tuple[dict, ...]:
    """Atividades que não são status personalizado, já estruturadas."""
    items = []
    for activity in getattr(subject, "activities", None) or ():
        if isinstance(activity, discord.CustomActivity):
            continue
        if isinstance(activity, discord.Spotify):
            items.append(
                {
                    "type": "spotify",
                    "name": _clip(activity.title, 100),
                    "detail": _clip(activity.artist, 100),
                    "state": _clip(getattr(activity, "album", None), 100),
                    "image_url": None,
                    "started_at": _millis(getattr(activity, "start", None)),
                    "ends_at": _millis(getattr(activity, "end", None)),
                    "party": None,
                }
            )
            continue
        type_name = getattr(getattr(activity, "type", None), "name", "")
        assets = getattr(activity, "assets", None)
        timestamps = getattr(activity, "timestamps", None)
        items.append(
            {
                "type": type_name if type_name in _ACTIVITY_KEYS else "other",
                "name": _clip(getattr(activity, "name", None), 100) or "Desconhecida",
                "detail": _clip(getattr(activity, "details", None), 100),
                "state": _clip(getattr(activity, "state", None), 100),
                "image_url": _image_url(assets, "large_image"),
                "started_at": _millis(getattr(timestamps, "start", None)),
                "ends_at": _millis(getattr(timestamps, "end", None)),
                "party": _party(activity),
            }
        )
    return tuple(_merge_duplicates(items)[:_MAX_ACTIVITIES])


def _richness(item: dict) -> int:
    return (
        4 * bool(item.get("image_url"))
        + 2 * bool(item.get("detail"))
        + 2 * bool(item.get("state"))
        + bool(item.get("party"))
        + bool(item.get("ends_at"))
    )


def _merge_duplicates(items: list[dict]) -> list[dict]:
    """O Discord manda o jogo detectado e a presença rica do mesmo jogo como duas
    atividades; o cliente mostra só a mais completa, e aqui também."""
    order: list[tuple[str, str]] = []
    best: dict[tuple[str, str], dict] = {}
    for item in items:
        key = (item["type"], item["name"].casefold())
        if key not in best:
            order.append(key)
            best[key] = item
            continue
        current = best[key]
        keep, other = (item, current) if _richness(item) > _richness(current) else (current, item)
        if not keep.get("started_at") and other.get("started_at"):
            keep = {**keep, "started_at": other["started_at"]}
        best[key] = keep
    return [best[key] for key in order]


def device_items(subject) -> tuple[dict, ...]:
    items = []
    for device in _DEVICES:
        status = getattr(subject, f"{device}_status", None)
        text = str(status) if status is not None else ""
        if text and text != "offline":
            items.append({"device": device, "status": text if text in _STATUSES else "online"})
    return tuple(items)


def _hex(colour) -> str | None:
    try:
        return f"#{int(colour.value):06x}"
    except Exception:
        return None


def _asset_url(asset, size: int) -> str | None:
    if not asset:
        return None
    try:
        return str(asset.with_size(size).url)
    except Exception:
        try:
            return str(asset.url)
        except Exception:
            return None


def name_style(user) -> dict | None:
    """Fonte, efeito e cores do nome de exibição, quando o usuário definiu um estilo."""
    style = getattr(user, "display_name_style", None) if user is not None else None
    if style is None:
        return None
    colors = [value for value in (_hex(c) for c in getattr(style, "colors", ()) or ()) if value]
    if not colors:
        return None
    return {"effect": getattr(getattr(style, "effect", None), "name", "solid"), "colors": colors[:3]}


def guild_tag(user) -> str | None:
    primary = getattr(user, "primary_guild", None) if user is not None else None
    if primary is None or not getattr(primary, "identity_enabled", False):
        return None
    return _clip(getattr(primary, "tag", None), 8) or None


def profile_visuals(profile) -> dict:
    """Parte visual do perfil público: banner, cores do tema, bio, pronomes e insígnias."""
    metadata = getattr(profile, "metadata", None)
    theme = getattr(metadata, "theme_colors", None)
    badges = []
    for badge in (getattr(profile, "badges", None) or [])[:8]:
        try:
            badges.append({"description": _clip(getattr(badge, "description", None), 80), "url": str(badge.url)})
        except Exception:
            continue
    return {
        "username": getattr(profile, "name", None),
        "display_name": getattr(profile, "global_name", None),
        "avatar_url": _asset_url(getattr(profile, "display_avatar", None), 128),
        "decoration_url": _asset_url(getattr(profile, "avatar_decoration", None), 160),
        "name_style": name_style(profile),
        "guild_tag": guild_tag(profile),
        "banner_url": _asset_url(getattr(profile, "banner", None), 480),
        "accent": _hex(getattr(profile, "accent_colour", None)) if getattr(profile, "accent_colour", None) else None,
        "theme": [value for value in (_hex(c) for c in (theme or ())) if value] or None,
        "bio": _clip(getattr(profile, "bio", None), 220) or None,
        "pronouns": _clip(getattr(metadata, "pronouns", None), 40) or None,
        "badges": badges,
    }


def _latest_snapshot(state) -> dict | None:
    snapshots = tuple(state.guild_presence.values())
    return max(snapshots, key=lambda item: item["updated_at"]) if snapshots else None


def _avatar_url(user, fallback: str | None) -> str | None:
    try:
        return str(user.display_avatar.with_size(128).url)
    except Exception:
        return fallback


def _voice(client, channel_id: int | None) -> dict | None:
    if not channel_id:
        return None
    lookup = getattr(client, "get_channel", None)
    channel = lookup(channel_id) if lookup else None
    if channel is None:
        return {"channel": None, "guild": None}
    guild = getattr(channel, "guild", None)
    return {
        "channel": _clip(getattr(channel, "name", None), 100) or None,
        "guild": _clip(getattr(guild, "name", None), 100) or None,
    }


def build_card(tracker) -> dict | None:
    """Identidade e presença atual do alvo; None quando o tracker está parado."""
    if not tracker.enabled or tracker.target_id is None:
        return None
    state = tracker.state
    lookup = getattr(tracker.client, "get_user", None)
    user = lookup(tracker.target_id) if lookup else None
    snapshot = _latest_snapshot(state)
    profile = dict(getattr(state, "profile_card", None) or {})

    status = (snapshot or {}).get("status") or state.status
    status = status if status in _STATUSES else ("offline" if status == "invisible" else None)

    custom = state.custom_status
    if snapshot and snapshot.get("custom_known"):
        custom = snapshot.get("custom_status")
    if custom is not None and not (custom.text or custom.emoji_key):
        custom = None

    # O usuário em cache vale mais; o perfil consultado cobre quem não está no cache da conta.
    cached_avatar = _avatar_url(user, None) if user is not None else None

    return {
        "id": tracker.target_id,
        "username": getattr(user, "name", None) or state.username or profile.get("username"),
        "display_name": getattr(user, "global_name", None) or state.global_name or profile.get("display_name"),
        "avatar_url": cached_avatar or state.avatar_url or profile.get("avatar_url"),
        "status": status or "unknown",
        "custom_status": (
            {
                "text": _clip(custom.text, 128),
                "emoji": "" if custom.emoji_key.startswith("custom:") else custom.emoji_key,
            }
            if custom
            else None
        ),
        "decoration_url": (_asset_url(getattr(user, "avatar_decoration", None), 160) if user is not None else None)
        or profile.get("decoration_url"),
        "name_style": name_style(user) or profile.get("name_style"),
        "guild_tag": guild_tag(user) or profile.get("guild_tag"),
        "mutual_servers": len(tracker.tracked_guild_ids),
        "profile": profile,
        "activities": list((snapshot or {}).get("activity_items", ())),
        "devices": list((snapshot or {}).get("device_items", ())),
        "voice": _voice(tracker.client, state.in_voice_channel_id),
    }
