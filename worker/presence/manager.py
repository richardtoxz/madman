import asyncio
import datetime

import discord

from presence_protocol import validate_custom

from ..core.config import PresenceConfig
from ..core.ipc import emit_line

_ACTIVITY_TYPES = {
    "playing": discord.ActivityType.playing,
    "streaming": discord.ActivityType.streaming,
    "listening": discord.ActivityType.listening,
    "watching": discord.ActivityType.watching,
    "competing": discord.ActivityType.competing,
}
_STATUS_DISPLAYS = {
    "name": discord.StatusDisplayType.name,
    "state": discord.StatusDisplayType.state,
    "details": discord.StatusDisplayType.details,
}
_IMAGE_LABELS = {"large_image": "Imagem grande", "small_image": "Imagem pequena"}


class PresenceManager:
    # O proxy roda no laço de comandos: uma imagem lenta não pode segurar o worker.
    PROXY_TIMEOUT = 5.0

    def __init__(self, client: discord.Client):
        self.client = client
        self.config = PresenceConfig()
        self._login_time = discord.utils.utcnow()
        self._task_name: str | None = None
        self._task_detail: str | None = None
        self._task_start: datetime.datetime | None = None
        self._task_token: int | None = None
        self._task_counter = 0
        self._assets_map: dict[str, str] = {}
        self._resolving = False
        self._protection_enabled = False
        self._last_activity: discord.Activity | None = None
        self._published = False
        self._enabled = True
        self._custom: dict | None = None
        self._custom_saved_at: datetime.datetime | None = None
        self._proxied: dict[str, str] = {}
        self._proxy_failed: set[str] = set()
        self._warnings: list[str] = []

    async def _resolve_assets(self):
        if self._assets_map or self._resolving:
            return

        self._resolving = True
        try:
            assets_data = await self.client.http.get_app_assets(self.config.APP_ID)
            for asset in assets_data:
                self._assets_map[asset["name"]] = str(asset["id"])
        except Exception as e:
            emit_line(f"PRESENCE:ERRO ao resolver assets: {e}")
        finally:
            self._resolving = False

    def _custom_status(self):
        settings = getattr(self.client, "settings", None)
        try:
            custom = settings.custom_activity if settings is not None else None
        except Exception:
            return None
        if custom is None or not (custom.name or custom.emoji):
            return None
        expires_at = getattr(custom, "expires_at", None)
        if expires_at is not None and expires_at <= discord.utils.utcnow():
            return None
        return custom

    async def _push(self, activity: discord.Activity | None) -> None:
        # O status personalizado da conta vai junto com a atividade do Madman e as
        # configuracoes nunca sao editadas daqui: com edit_settings=True a biblioteca
        # apagava o status personalizado por ele nao estar na lista de atividades.
        # afk=True faz o Discord continuar enviando notificacoes ao celular mesmo com
        # esta sessao conectada (sem isso ela conta como um computador em uso).
        # Com o Rich Presence desligado (activity=None) so o status personalizado vai.
        activities: list = [] if activity is None else [activity]
        custom = self._custom_status()
        if custom is not None:
            activities.insert(0, custom)
        self._last_activity = activity
        self._published = True
        await self.client.change_presence(activities=activities, afk=True, edit_settings=False)

    async def refresh(self) -> None:
        """Reenvia a presenca atual (apos reconectar ou mudar o status personalizado)."""
        if not self._published:
            return
        try:
            await self._push(self._last_activity if self._enabled else None)
        except Exception as e:
            emit_line(f"PRESENCE:CRITICAL_ERROR:refresh:{type(e).__name__}")

    def _get_asset_id(self, name: str) -> str:
        return self._assets_map.get(name, name)

    def _logo_url(self) -> str | None:
        asset_id = self._assets_map.get(self.config.ASSET_LOGO)
        if asset_id is None:
            return None
        return f"https://cdn.discordapp.com/app-assets/{self.config.APP_ID}/{asset_id}.png"

    def _main_state_text(self) -> str:
        state_text = "aylo.me/to"
        if self._protection_enabled:
            state_text += " | Protecao Ativa"
        return state_text

    def _build_main_activity(self) -> discord.Activity:
        asset_id = self._get_asset_id(self.config.ASSET_LOGO)
        return discord.Activity(
            type=discord.ActivityType.playing,
            application_id=self.config.APP_ID,
            name="Madman",
            details="Madman Services",
            state=self._main_state_text(),
            assets={
                "large_image": asset_id,
                "large_text": "Madman Services",
                "small_image": asset_id,
                "small_text": "Standard",
            },
            timestamps=discord.ActivityTimestamps(start=self._login_time),
        )

    def _build_task_activity(self) -> discord.Activity:
        asset_id = self._get_asset_id(self.config.ASSET_LOGO)
        return discord.Activity(
            type=discord.ActivityType.playing,
            application_id=self.config.APP_ID,
            name="Madman",
            details=self._task_name or "Madman Services",
            state=self._task_detail or "👻",
            assets={
                "large_image": asset_id,
                "large_text": "Madman Services",
                "small_image": asset_id,
                "small_text": self._task_name or "Tarefa",
            },
            timestamps=discord.ActivityTimestamps(start=self._task_start),
        )

    def _custom_start(self) -> datetime.datetime | None:
        timer = (self._custom or {}).get("timer")
        if timer == "since_start":
            return self._login_time
        if timer in ("since_save", "duration"):
            return self._custom_saved_at
        return None

    def _custom_end(self) -> datetime.datetime | None:
        custom = self._custom or {}
        if custom.get("timer") != "duration" or self._custom_saved_at is None:
            return None
        return self._custom_saved_at + datetime.timedelta(minutes=custom["duration_minutes"])

    async def _proxy_image(self, key: str, url: str) -> str | None:
        if url in self._proxied:
            return self._proxied[url]
        if url in self._proxy_failed:
            # Só um novo salvamento tenta de novo; tarefas e proteção não refazem a chamada.
            return None
        try:
            proxied = await asyncio.wait_for(
                self.client.proxy_external_application_assets(self.config.APP_ID, url),
                timeout=self.PROXY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proxied, reason = None, "o Discord demorou para responder"
        except Exception as e:
            proxied, reason = None, type(e).__name__
        else:
            reason = "sem resposta do Discord"
        if not proxied:
            self._proxy_failed.add(url)
            self._warnings.append(f"{_IMAGE_LABELS[key]} não carregou: {reason}")
            return None
        self._proxied[url] = proxied[0]
        return proxied[0]

    async def _build_custom_activity(self) -> discord.Activity:
        custom = self._custom or {}
        assets: dict = {}
        for image_key, text_key in (("large_image", "large_text"), ("small_image", "small_text")):
            url = custom.get(image_key)
            proxied = await self._proxy_image(image_key, url) if url else None
            if proxied:
                assets[image_key] = proxied
                if custom.get(text_key):
                    assets[text_key] = custom[text_key]
                link_key = image_key.replace("_image", "_url")
                if custom.get(link_key):
                    assets[link_key] = custom[link_key]
        start, end = self._custom_start(), self._custom_end()
        party = custom.get("party_current")
        platform = custom.get("platform")
        display = custom.get("status_display") or "name"
        return discord.Activity(
            type=_ACTIVITY_TYPES[custom["type"]],
            application_id=self.config.APP_ID,
            name=custom["name"],
            url=custom.get("stream_url"),
            details=custom.get("details"),
            details_url=custom.get("details_url"),
            state=custom.get("state"),
            state_url=custom.get("state_url"),
            # "name" é o padrão do Discord: só manda o campo quando muda o texto da lista.
            status_display_type=_STATUS_DISPLAYS[display] if display != "name" else None,
            platform=discord.ActivityPlatform(platform) if platform else None,
            # O construtor converte o link do proxy para "mp:"; um dict cru iria sem converter.
            assets=discord.ActivityAssets(**assets),
            party=discord.ActivityParty(current_size=party, max_size=custom["party_max"]) if party else None,
            buttons=[discord.ActivityButton(b["label"], b["url"]) for b in custom.get("buttons") or []],
            timestamps=discord.ActivityTimestamps(start=start, end=end) if start else None,
        )

    async def _current_activity(self) -> discord.Activity:
        if self._task_name:
            return self._build_task_activity()
        if self._custom is not None:
            return await self._build_custom_activity()
        return self._build_main_activity()

    async def _publish(self) -> None:
        self._warnings = []
        if not self._enabled:
            await self._push(None)
            return
        await self._push(await self._current_activity())

    async def set_main(self):
        try:
            await self._resolve_assets()
            self._task_name = None
            self._task_detail = None
            self._task_start = None
            self._task_token = None
            await self._publish()
        except Exception as e:
            emit_line(f"PRESENCE:CRITICAL_ERROR:set_main:{e}")

    async def set_task(self, name: str, detail: str | None = None) -> int | None:
        try:
            await self._resolve_assets()
            self._task_counter += 1
            self._task_token = self._task_counter
            self._task_name = name
            self._task_detail = detail
            self._task_start = discord.utils.utcnow()
            if self._enabled:
                await self._push(self._build_task_activity())
            return self._task_token
        except Exception as e:
            emit_line(f"PRESENCE:CRITICAL_ERROR:set_task:{e}")
            return None

    async def complete_task(self, token: int | None):
        if token is None:
            await self.set_main()
            return

        if token != self._task_token:
            return

        self._task_name = None
        self._task_detail = None
        self._task_start = None
        self._task_token = None

        if not self._enabled:
            return
        try:
            await self._publish()
        except Exception as e:
            emit_line(f"PRESENCE:CRITICAL_ERROR:complete_task:{e}")

    async def set_protection(self, enabled: bool):
        self._protection_enabled = enabled
        if not self._task_name and self._enabled:
            await self.set_main()

    async def update_detail(self, detail: str):
        if not self._task_name:
            return
        self._task_detail = detail
        if not self._enabled:
            return
        activity = self._build_task_activity()
        try:
            await self._push(activity)
        except Exception:
            pass

    async def configure(self, enabled: bool, custom: dict | None) -> dict:
        """Liga/desliga e troca o Rich Presence base; lança ValueError se `custom` for inválido."""
        normalized = validate_custom(custom) if custom is not None else None
        await self._resolve_assets()
        if normalized != self._custom:
            self._custom_saved_at = discord.utils.utcnow()
        self._enabled = bool(enabled)
        self._custom = normalized
        self._proxy_failed.clear()
        await self._publish()
        result = self.status()
        if self._warnings:
            result["warnings"] = list(self._warnings)
        return result

    def status(self) -> dict:
        return {
            "ok": True,
            "enabled": self._enabled,
            "custom": self._custom,
            "default": self._default_preview(),
            "seed": self._seed_custom(),
            "live": self._live_preview(),
            "task": self._task_name,
        }

    @staticmethod
    def _ms(moment: datetime.datetime | None) -> int | None:
        return int(moment.timestamp() * 1000) if moment else None

    @staticmethod
    def _seed_custom() -> dict:
        """Ponto de partida para editar a partir do padrão (sem o aviso de proteção, que é dinâmico)."""
        return validate_custom(
            {
                "type": "playing",
                "name": "Madman",
                "details": "Madman Services",
                "state": "aylo.me/to",
                "timer": "since_start",
            }
        )

    def _default_preview(self) -> dict:
        logo = self._logo_url()
        return {
            "source": "default",
            "type": "playing",
            "name": "Madman",
            "details": "Madman Services",
            "state": self._main_state_text(),
            "large_image": logo,
            "large_text": "Madman Services",
            "small_image": logo,
            "small_text": "Standard",
            "buttons": [],
            "started_at_ms": self._ms(self._login_time),
        }

    def _live_preview(self) -> dict | None:
        if not self._enabled:
            return None
        if self._task_name:
            logo = self._logo_url()
            return {
                "source": "task",
                "type": "playing",
                "name": "Madman",
                "details": self._task_name,
                "state": self._task_detail or "👻",
                "large_image": logo,
                "large_text": "Madman Services",
                "small_image": logo,
                "small_text": self._task_name,
                "buttons": [],
                "started_at_ms": self._ms(self._task_start),
            }
        if self._custom is not None:
            custom = self._custom
            preview = {
                "source": "custom",
                **{key: custom.get(key) for key in ("type", "name", "details", "state")},
                "buttons": list(custom.get("buttons") or []),
                "started_at_ms": self._ms(self._custom_start()),
                "ends_at_ms": self._ms(self._custom_end()),
                **{
                    key: custom.get(key)
                    for key in (
                        "status_display",
                        "details_url",
                        "state_url",
                        "stream_url",
                        "party_current",
                        "party_max",
                        "platform",
                    )
                },
            }
            # A prévia mostra o que o Discord mostra: o link do proxy (o painel só carrega
            # imagens do Discord) e nada quando o proxy falhou.
            for image_key, text_key in (("large_image", "large_text"), ("small_image", "small_text")):
                proxied = self._proxied.get(custom.get(image_key) or "")
                link_key = image_key.replace("_image", "_url")
                preview[image_key] = proxied
                preview[text_key] = custom.get(text_key) if proxied else None
                preview[link_key] = custom.get(link_key) if proxied else None
            return preview
        return self._default_preview()
