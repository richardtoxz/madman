"""
Rich Presence Manager — gerencia o status do selfbot no perfil do Discord.
Dois modos: MAIN (idle/standby) e TASK (executando operações).
"""
import discord
import datetime


from config import PresenceConfig


class PresenceManager:
    def __init__(self, client: discord.Client):
        self.client = client
        self.config = PresenceConfig()
        self._login_time = discord.utils.utcnow()
        self._task_name: str | None = None
        self._task_detail: str | None = None
        self._task_start: datetime.datetime | None = None
        self._assets_map: dict[str, str] = {}
        self._resolving = False
        self._protection_enabled = False

    # ── Builders ──────────────────────────────────────────────

    async def _resolve_assets(self):
        """Busca os IDs dos assets da aplicação no Discord."""
        if self._assets_map or self._resolving:
            return
        
        self._resolving = True
        try:
            print(f"PRESENCE:Resolvendo assets para APP_ID {self.config.APP_ID}...", flush=True)
            # Acessa o endpoint via o cliente
            assets_data = await self.client.http.get_app_assets(self.config.APP_ID)
            for asset in assets_data:
                self._assets_map[asset['name']] = str(asset['id'])
            print(f"PRESENCE:Assets resolvidos: {list(self._assets_map.keys())}", flush=True)
        except Exception as e:
            print(f"PRESENCE:ERRO ao resolver assets: {e}", flush=True)
        finally:
            self._resolving = False

    def _get_asset_id(self, name: str) -> str:
        """Retorna o ID do asset se resolvido, senão o próprio nome."""
        return self._assets_map.get(name, name)

    def _build_main_activity(self) -> discord.Activity:
        """Atividade padrão: logado e aguardando."""
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
        """Atividade de tarefa em execução."""
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

    # ── Public API ────────────────────────────────────────────

    async def set_main(self):
        """Define o status principal (idle/standby)."""
        try:
            await self._resolve_assets()
            self._task_name = None
            self._task_detail = None
            self._task_start = None
            activity = self._build_main_activity()
            
            # Log do payload para debugar
            payload = activity.to_dict()
            print(f"PRESENCE:DEBUG_PAYLOAD:{payload}", flush=True)
            
            await self.client.change_presence(activity=activity)
            print("PRESENCE:MAIN_SET_SUCCESS", flush=True)
        except Exception as e:
            print(f"PRESENCE:CRITICAL_ERROR:set_main:{e}", flush=True)
            import traceback
            traceback.print_exc()

    async def set_task(self, name: str, detail: str | None = None):
        """Define o status de tarefa ativa."""
        try:
            await self._resolve_assets()
            self._task_name = name
            self._task_detail = detail
            self._task_start = discord.utils.utcnow()
            activity = self._build_task_activity()
            
            await self.client.change_presence(activity=activity)
            print(f"PRESENCE:TASK_SET_SUCCESS:{name}", flush=True)
        except Exception as e:
            print(f"PRESENCE:CRITICAL_ERROR:set_task:{e}", flush=True)

    async def set_protection(self, enabled: bool):
        """Atualiza o estado de proteção e faz o refresh do status."""
        self._protection_enabled = enabled
        # Se não houver tarefa rodando, atualiza o Main imediatamente
        if not self._task_name:
            await self.set_main()
        else:
            print(f"PRESENCE:PROTECTION_UPDATED:{enabled} (refresh pendente: tarefa ativa)", flush=True)

    async def update_detail(self, detail: str):
        """Atualiza apenas o detalhe da tarefa atual (sem resetar timer)."""
        if not self._task_name:
            return
        self._task_detail = detail
        activity = self._build_task_activity()
        try:
            await self.client.change_presence(activity=activity)
        except Exception:
            pass
