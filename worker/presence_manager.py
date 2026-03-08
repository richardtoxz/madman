import discord
import datetime

from .config import PresenceConfig
from .ipc import emit_line


class PresenceManager:
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

    def _get_asset_id(self, name: str) -> str:
        return self._assets_map.get(name, name)

    def _build_main_activity(self) -> discord.Activity:
        asset_id = self._get_asset_id(self.config.ASSET_LOGO)
        state_text = "ayo.so/toxz"
        if self._protection_enabled:
            state_text += " | Protecao Ativa"

        return discord.Activity(
            type=discord.ActivityType.playing,
            application_id=self.config.APP_ID,
            name="Madman",
            details="Madman Services",
            state=state_text,
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

    async def set_main(self):
        try:
            await self._resolve_assets()
            self._task_name = None
            self._task_detail = None
            self._task_start = None
            self._task_token = None
            activity = self._build_main_activity()
            await self.client.change_presence(activity=activity)
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
            activity = self._build_task_activity()

            await self.client.change_presence(activity=activity)
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

        try:
            activity = self._build_main_activity()
            await self.client.change_presence(activity=activity)
        except Exception as e:
            emit_line(f"PRESENCE:CRITICAL_ERROR:complete_task:{e}")

    async def set_protection(self, enabled: bool):
        self._protection_enabled = enabled
        if not self._task_name:
            await self.set_main()

    async def update_detail(self, detail: str):
        if not self._task_name:
            return
        self._task_detail = detail
        activity = self._build_task_activity()
        try:
            await self.client.change_presence(activity=activity)
        except Exception:
            pass
