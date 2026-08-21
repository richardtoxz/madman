from __future__ import annotations

import asyncio
from typing import Any

import discord

from .ipc import emit_line, sanitize_ipc_field


class VoiceManager:
    """Controla um estado de voz sem abrir uma conexao de audio/Opus."""

    STATE_TIMEOUT = 12.0

    def __init__(self, client):
        self.client = client
        self.current_channel_id: int | None = None
        self.current_guild_id: int | None = None
        self.follow_target_id: int | None = None
        self.desired_channel_id: int | None = None
        self._follow_active = False
        self._follow_preferred_channel = None
        self._current_channel = None
        self._operation_lock = asyncio.Lock()
        self._state_event = asyncio.Event()
        self._background_tasks: set[asyncio.Task] = set()
        self._follow_reconcile_task: asyncio.Task | None = None
        self._follow_reconcile_pending = False

    @property
    def is_in_voice(self) -> bool:
        return self.current_channel_id is not None

    @property
    def is_following(self) -> bool:
        return self._follow_active and self.follow_target_id is not None

    @staticmethod
    def _result(ok: bool, code: str, message: str, **extra) -> dict[str, Any]:
        return {"ok": ok, "code": code, "message": message, **extra}

    @staticmethod
    def _label(value: Any, limit: int = 100) -> str:
        text = " ".join(str(value or "Desconhecido").split())
        return text[:limit] or "Desconhecido"

    def _channel_result(self, channel) -> dict[str, Any]:
        guild = getattr(channel, "guild", None)
        return {
            "channel_id": int(channel.id),
            "channel_name": self._label(getattr(channel, "name", None)),
            "guild_id": int(guild.id) if guild else None,
            "guild_name": self._label(getattr(guild, "name", None)) if guild else None,
        }

    @staticmethod
    def _vocal_channels(guild) -> tuple[Any, ...]:
        channels = []
        for attribute in ("voice_channels", "stage_channels"):
            try:
                channels.extend(getattr(guild, attribute, ()) or ())
            except Exception:
                pass
        return tuple(dict.fromkeys(channels))

    def _find_user_voice(self, user_id: int) -> tuple[Any | None, Any | None]:
        user_id = int(user_id)
        for guild in tuple(getattr(self.client, "guilds", ())):
            if bool(getattr(guild, "unavailable", False)):
                continue

            try:
                member = guild.get_member(user_id)
                state = getattr(member, "voice", None) if member else None
                channel = getattr(state, "channel", None) if state else None
                if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                    return channel, state
            except Exception:
                pass

            for channel in self._vocal_channels(guild):
                try:
                    state = channel.voice_states.get(user_id)
                except Exception:
                    continue
                if state is not None and getattr(state, "channel", None) is not None:
                    return channel, state
        return None, None

    def _target_voice(self) -> tuple[Any | None, Any | None]:
        if not self.is_following:
            return None, None
        target_id = int(self.follow_target_id)
        preferred = self._follow_preferred_channel
        if preferred is not None:
            try:
                state = preferred.voice_states.get(target_id)
                if state is not None and getattr(state, "channel", None) is not None:
                    return preferred, state
            except Exception:
                pass
        channel, state = self._find_user_voice(target_id)
        self._follow_preferred_channel = channel
        return channel, state

    def _set_current(self, channel) -> None:
        self._current_channel = channel
        self.current_channel_id = int(channel.id)
        guild = getattr(channel, "guild", None)
        self.current_guild_id = int(guild.id) if guild else None

    def _clear_current(self) -> None:
        self._current_channel = None
        self.current_channel_id = None
        self.current_guild_id = None

    def _sync_current(self) -> tuple[Any | None, Any | None]:
        self_id = int(getattr(getattr(self.client, "user", None), "id", 0) or 0)
        if not self_id:
            return self._current_channel, None
        channel, state = self._find_user_voice(self_id)
        if channel is not None:
            self._set_current(channel)
        elif self.current_guild_id is not None:
            guild = self.client.get_guild(int(self.current_guild_id))
            if guild is not None and not bool(getattr(guild, "unavailable", False)):
                self._clear_current()
        return channel, state

    async def _resolve_channel(self, channel_id: int):
        channel = self.client.get_channel(int(channel_id))
        if channel is None:
            last_error = None
            for attempt in range(2):
                try:
                    channel = await self.client.fetch_channel(int(channel_id))
                    break
                except asyncio.CancelledError:
                    raise
                except discord.HTTPException as error:
                    last_error = error
                    status = int(getattr(error, "status", 0) or 0)
                    if attempt == 0 and status >= 500:
                        await asyncio.sleep(1.0)
                        continue
                    break
                except Exception as error:
                    last_error = error
                    break
            if channel is None:
                if isinstance(last_error, discord.NotFound):
                    return None, self._result(
                        False, "CHANNEL_NOT_FOUND", "Canal de voz nao encontrado."
                    )
                if isinstance(last_error, discord.Forbidden):
                    return None, self._result(
                        False, "CHANNEL_FORBIDDEN", "Sem acesso ao canal informado."
                    )
                return None, self._result(
                    False,
                    "CHANNEL_FETCH_FAILED",
                    "Nao foi possivel confirmar o canal pela API.",
                )

        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return None, self._result(
                False, "NOT_VOICE_CHANNEL", "O ID informado nao e de um canal de voz."
            )

        guild = getattr(channel, "guild", None)
        if guild is None:
            return None, self._result(
                False, "GUILD_NOT_FOUND", "O servidor do canal nao foi encontrado."
            )
        if bool(getattr(guild, "unavailable", False)):
            return None, self._result(
                False,
                "GUILD_UNAVAILABLE",
                "O servidor esta temporariamente indisponivel.",
            )

        me = getattr(guild, "me", None)
        permissions = None
        if me is not None:
            try:
                permissions = channel.permissions_for(me)
            except Exception:
                permissions = None
        if permissions is not None and (
            not bool(getattr(permissions, "view_channel", True))
            or not bool(getattr(permissions, "connect", True))
        ):
            return None, self._result(
                False,
                "CONNECT_FORBIDDEN",
                "A conta nao tem permissao para entrar nesse canal.",
            )

        try:
            user_limit = int(getattr(channel, "user_limit", 0) or 0)
            voice_states = channel.voice_states
            self_id = int(self.client.user.id)
            full = (
                user_limit > 0
                and len(voice_states) >= user_limit
                and self_id not in voice_states
            )
            can_bypass = bool(getattr(permissions, "move_members", False))
            if full and not can_bypass:
                return None, self._result(
                    False, "CHANNEL_FULL", "O canal de voz esta lotado."
                )
        except Exception:
            pass

        return channel, None

    async def _wait_for_channel(self, expected_channel_id: int | None) -> bool:
        deadline = asyncio.get_running_loop().time() + self.STATE_TIMEOUT
        while True:
            channel, _ = self._sync_current()
            actual_id = int(channel.id) if channel else self.current_channel_id
            if expected_channel_id is None:
                if actual_id is None:
                    return True
            elif actual_id == int(expected_channel_id):
                return True

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False

            self._state_event.clear()
            channel, _ = self._sync_current()
            actual_id = int(channel.id) if channel else self.current_channel_id
            if expected_channel_id is None:
                if actual_id is None:
                    return True
            elif actual_id == int(expected_channel_id):
                return True

            try:
                await asyncio.wait_for(self._state_event.wait(), timeout=remaining)
            except TimeoutError:
                return False

    def _exception_result(self, error: Exception, action: str) -> dict[str, Any]:
        if isinstance(error, discord.Forbidden):
            return self._result(False, "FORBIDDEN", f"Discord recusou {action}.")
        if isinstance(error, discord.NotFound):
            return self._result(False, "NOT_FOUND", "Canal ou servidor nao encontrado.")
        if isinstance(error, discord.HTTPException):
            status = int(getattr(error, "status", 0) or 0)
            return self._result(
                False,
                f"HTTP_{status}" if status else "HTTP_ERROR",
                f"Falha do Discord ao {action}.",
            )
        if isinstance(error, (discord.ConnectionClosed, ConnectionError, OSError)):
            return self._result(
                False, "CONNECTION_LOST", "A conexao com o Discord foi interrompida."
            )
        return self._result(False, "VOICE_ERROR", f"Nao foi possivel {action}.")

    async def _update_presence(self) -> None:
        presence = getattr(self.client, "presence_manager", None)
        if presence is None:
            return
        if self.is_following:
            if self.is_in_voice and self._current_channel is not None:
                detail = f"Canal: {self._label(self._current_channel.name, 70)}"
            else:
                detail = "Aguardando alvo"
            await presence.set_task("Seguindo usuario", detail)
        elif self.is_in_voice and self._current_channel is not None:
            await presence.set_task(
                "Em canal de voz",
                f"Canal: {self._label(self._current_channel.name, 70)}",
            )
        else:
            await presence.set_main()

    async def _leave_locked(self, *, update_presence: bool = True) -> dict[str, Any]:
        located, _ = self._sync_current()
        channel = located or self._current_channel
        if channel is None and self.current_guild_id is None:
            self._clear_current()
            if update_presence:
                await self._update_presence()
            return self._result(True, "ALREADY_LEFT", "A conta ja estava fora da voz.")

        guild = getattr(channel, "guild", None) if channel is not None else None
        if guild is None and self.current_guild_id is not None:
            guild = self.client.get_guild(int(self.current_guild_id))
        if guild is None:
            return self._result(
                False,
                "GUILD_NOT_FOUND",
                "Nao foi possivel localizar o servidor para desconectar.",
            )

        try:
            await guild.change_voice_state(channel=None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return self._exception_result(error, "sair do canal de voz")

        if not await self._wait_for_channel(None):
            return self._result(
                False,
                "LEAVE_UNCONFIRMED",
                "O Discord nao confirmou a saida do canal no tempo esperado.",
            )

        self._clear_current()
        if update_presence:
            await self._update_presence()
        return self._result(True, "LEFT", "A conta saiu do canal de voz.")

    async def _join_channel_locked(self, channel) -> dict[str, Any]:
        current, state = self._sync_current()
        if current is not None and int(current.id) == int(channel.id):
            if bool(getattr(state, "self_mute", False)) and bool(
                getattr(state, "self_deaf", False)
            ):
                self._set_current(channel)
                await self._update_presence()
                return self._result(
                    True,
                    "ALREADY_JOINED",
                    "A conta ja estava nesse canal com mute e deaf ativados.",
                    **self._channel_result(channel),
                )

        current_guild_id = (
            int(current.guild.id)
            if current is not None and getattr(current, "guild", None)
            else self.current_guild_id
        )
        target_guild_id = int(channel.guild.id)
        if current_guild_id is not None and current_guild_id != target_guild_id:
            left = await self._leave_locked(update_presence=False)
            if not left["ok"]:
                return self._result(
                    False,
                    "SWITCH_LEAVE_FAILED",
                    "Nao foi possivel sair do canal anterior para trocar de servidor.",
                )

        try:
            await channel.guild.change_voice_state(
                channel=channel,
                self_mute=True,
                self_deaf=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return self._exception_result(error, "entrar no canal de voz")

        if not await self._wait_for_channel(int(channel.id)):
            return self._result(
                False,
                "JOIN_UNCONFIRMED",
                "O Discord nao confirmou a entrada no canal no tempo esperado.",
                **self._channel_result(channel),
            )

        self._set_current(channel)
        await self._update_presence()
        return self._result(
            True,
            "JOINED",
            "Conta conectada com microfone e audio desativados.",
            **self._channel_result(channel),
        )

    async def join_voice(self, channel_id: int) -> dict[str, Any]:
        if self._operation_lock.locked():
            return self._result(
                False, "BUSY", "Outra alteracao de voz esta em andamento nesta conta."
            )
        async with self._operation_lock:
            channel, error = await self._resolve_channel(int(channel_id))
            if error:
                return error

            self._follow_active = False
            self.follow_target_id = None
            self._follow_preferred_channel = None
            result = await self._join_channel_locked(channel)
            if result["ok"]:
                self.desired_channel_id = int(channel.id)
            return result

    async def leave_voice(self) -> dict[str, Any]:
        if not self.is_following:
            self.desired_channel_id = None
        async with self._operation_lock:
            return await self._leave_locked()

    async def start_follow(self, target_user_id: int) -> dict[str, Any]:
        target_user_id = int(target_user_id)
        self_id = int(getattr(getattr(self.client, "user", None), "id", 0) or 0)
        if target_user_id <= 0 or target_user_id >= 2**64:
            return self._result(False, "INVALID_TARGET", "ID de usuario invalido.")
        if target_user_id == self_id:
            return self._result(False, "SELF_TARGET", "A conta nao pode seguir a si mesma.")
        if self._operation_lock.locked():
            return self._result(
                False, "BUSY", "Outra alteracao de voz esta em andamento nesta conta."
            )

        async with self._operation_lock:
            self.follow_target_id = target_user_id
            self._follow_active = True
            self.desired_channel_id = None
            channel, _ = self._find_user_voice(target_user_id)
            self._follow_preferred_channel = channel
            if channel is None:
                await self._update_presence()
                return self._result(
                    True,
                    "FOLLOW_WAITING",
                    "Follow ativado; aguardando o usuario entrar em um canal compartilhado.",
                    target_id=target_user_id,
                    following=True,
                )

            resolved, error = await self._resolve_channel(int(channel.id))
            if error:
                await self._leave_locked(update_presence=False)
                await self._update_presence()
                return self._result(
                    True,
                    "FOLLOW_WAITING",
                    f"Follow ativado, mas o canal atual nao esta acessivel: {error['message']}",
                    target_id=target_user_id,
                    following=True,
                    reason_code=error["code"],
                )

            joined = await self._join_channel_locked(resolved)
            if not joined["ok"]:
                await self._leave_locked(update_presence=False)
                await self._update_presence()
                return self._result(
                    True,
                    "FOLLOW_WAITING",
                    f"Follow ativado; aguardando uma proxima mudanca: {joined['message']}",
                    target_id=target_user_id,
                    following=True,
                    reason_code=joined["code"],
                )
            return self._result(
                True,
                "FOLLOWING",
                "Follow ativado e conectado ao canal atual do usuario.",
                target_id=target_user_id,
                following=True,
                **self._channel_result(resolved),
            )

    async def stop_follow(self) -> dict[str, Any]:
        was_following = self.is_following
        self._follow_active = False
        self.follow_target_id = None
        self._follow_preferred_channel = None
        self.desired_channel_id = None
        async with self._operation_lock:
            left = await self._leave_locked()
        if not left["ok"]:
            return self._result(
                False,
                "UNFOLLOWED_PARTIAL",
                f"Follow desativado, mas a saida nao foi confirmada: {left['message']}",
                following=False,
            )
        return self._result(
            True,
            "UNFOLLOWED" if was_following else "ALREADY_UNFOLLOWED",
            (
                "Follow desativado e conta desconectada da voz."
                if was_following
                else "A conta nao estava seguindo nenhum usuario."
            ),
            following=False,
        )

    async def get_status(self) -> dict[str, Any]:
        channel, state = self._sync_current()
        mode = (
            "FOLLOW"
            if self.is_following
            else "AFK"
            if self.desired_channel_id
            else "IDLE"
        )
        result = self._result(
            True,
            "STATUS",
            "Estado de voz atualizado.",
            mode=mode,
            in_voice=channel is not None or self.is_in_voice,
            following=self.is_following,
            target_id=self.follow_target_id,
            desired_channel_id=self.desired_channel_id,
            self_mute=bool(getattr(state, "self_mute", False)) if state else None,
            self_deaf=bool(getattr(state, "self_deaf", False)) if state else None,
        )
        if channel is not None:
            result.update(self._channel_result(channel))
        elif self._current_channel is not None:
            result.update(self._channel_result(self._current_channel))
        return result

    async def _reconcile_follow(self) -> None:
        async with self._operation_lock:
            if not self.is_following:
                return
            target_id = int(self.follow_target_id)
            channel, _ = self._target_voice()
            if channel is None:
                await self._leave_locked(update_presence=False)
                await self._update_presence()
                emit_line(f"EVENT:VOICE:FOLLOW_TARGET_LEFT:{target_id}")
                return

            resolved, error = await self._resolve_channel(int(channel.id))
            if error:
                await self._leave_locked(update_presence=False)
                await self._update_presence()
                emit_line(
                    f"EVENT:VOICE:FOLLOW_WAITING:{target_id}:"
                    f"{sanitize_ipc_field(error['code'])}"
                )
                return

            joined = await self._join_channel_locked(resolved)
            event = "FOLLOW_MOVED" if joined["ok"] else "FOLLOW_FAILED"
            emit_line(
                f"EVENT:VOICE:{event}:{target_id}:{resolved.id}:"
                f"{sanitize_ipc_field(resolved.name)}"
            )

    def _reconcile_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if task is self._follow_reconcile_task:
            self._follow_reconcile_task = None
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            emit_line(f"EVENT:VOICE:FOLLOW_ERROR:{type(error).__name__}")

    async def _follow_reconcile_loop(self) -> None:
        while self._follow_reconcile_pending:
            self._follow_reconcile_pending = False
            await self._reconcile_follow()

    def _schedule_follow_reconcile(self) -> None:
        self._follow_reconcile_pending = True
        if (
            self._follow_reconcile_task is not None
            and not self._follow_reconcile_task.done()
        ):
            return
        task = asyncio.create_task(
            self._follow_reconcile_loop(), name="voice-follow-reconcile"
        )
        self._follow_reconcile_task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._reconcile_done)

    async def on_reconnect(self) -> None:
        if self._operation_lock.locked():
            return
        if self.is_following:
            await self._reconcile_follow()
            return
        if self.desired_channel_id is None:
            return
        async with self._operation_lock:
            channel, error = await self._resolve_channel(self.desired_channel_id)
            if error:
                emit_line(
                    f"EVENT:VOICE:RECONNECT_FAILED:{sanitize_ipc_field(error['code'])}"
                )
                return
            result = await self._join_channel_locked(channel)
            event = "RECONNECTED" if result["ok"] else "RECONNECT_FAILED"
            emit_line(f"EVENT:VOICE:{event}:{sanitize_ipc_field(result['code'])}")

    async def on_voice_state_update(self, member, before, after) -> None:
        member_id = int(getattr(member, "id", 0) or 0)
        self_id = int(getattr(getattr(self.client, "user", None), "id", 0) or 0)

        if member_id == self_id:
            channel = getattr(after, "channel", None)
            if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                self._set_current(channel)
                emit_line(
                    f"EVENT:VOICE:SELF_JOINED:{channel.id}:"
                    f"{sanitize_ipc_field(channel.name)}"
                )
            else:
                previous_id = self.current_channel_id
                self._clear_current()
                emit_line(f"EVENT:VOICE:SELF_LEFT:{previous_id or 0}")
            self._state_event.set()
            return

        if self.is_following and member_id == self.follow_target_id:
            channel = getattr(after, "channel", None)
            if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                self._follow_preferred_channel = channel
            elif (
                self._follow_preferred_channel is not None
                and getattr(getattr(before, "channel", None), "id", None)
                == getattr(self._follow_preferred_channel, "id", None)
            ):
                self._follow_preferred_channel = None
            self._schedule_follow_reconcile()

    async def shutdown(self) -> None:
        for task in tuple(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._follow_reconcile_task = None
        self._follow_reconcile_pending = False
