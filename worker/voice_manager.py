import discord

from .ipc import emit_line, emit_response


class VoiceManager:
    def __init__(self, client):
        self.client = client
        self.current_channel_id = None
        self.current_guild_id = None
        self.follow_target_id = None
        self._follow_active = False

    @property
    def is_in_voice(self):
        return self.current_channel_id is not None

    @property
    def is_following(self):
        return self._follow_active and self.follow_target_id is not None

    async def join_voice(self, channel_id: int, request_id: str | None = None):
        channel = self.client.get_channel(channel_id)
        if not channel:
            emit_response(
                "VOICE_STATUS:",
                f"ERRO:Canal {channel_id} não encontrado.",
                request_id,
            )
            return

        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            emit_response(
                "VOICE_STATUS:",
                f"ERRO:Canal {channel_id} não é um canal de voz.",
                request_id,
            )
            return

        guild = channel.guild
        if not guild:
            emit_response(
                "VOICE_STATUS:", "ERRO:Guild do canal não encontrada.", request_id
            )
            return

        try:
            await guild.change_voice_state(
                channel=channel, self_mute=True, self_deaf=True
            )
            self.current_channel_id = channel_id
            self.current_guild_id = guild.id

            if self.client.presence_manager:
                if self.is_following:
                    await self.client.presence_manager.set_task(
                        "Seguindo usuario", f"Canal: {channel.name}"
                    )
                else:
                    await self.client.presence_manager.set_task(
                        "Em canal de voz", f"Canal: {channel.name}"
                    )

            emit_response(
                "VOICE_STATUS:", f"JOINED:{channel.name}:{guild.name}", request_id
            )
        except Exception as e:
            emit_response("VOICE_STATUS:", f"ERRO:{e}", request_id)

    async def leave_voice(self, request_id: str | None = None):
        if not self.current_guild_id:
            emit_response(
                "VOICE_STATUS:", "LEFT:Não estava em nenhum canal.", request_id
            )
            if self.client.presence_manager and not self.is_following:
                await self.client.presence_manager.set_main()
            return

        guild = self.client.get_guild(self.current_guild_id)
        if guild:
            try:
                await guild.change_voice_state(channel=None)
            except Exception:
                pass

        self.current_channel_id = None
        self.current_guild_id = None

        if self.client.presence_manager:
            if self.is_following:
                await self.client.presence_manager.set_task(
                    "Seguindo usuario", "Aguardando alvo"
                )
            else:
                await self.client.presence_manager.set_main()

        emit_response("VOICE_STATUS:", "LEFT", request_id)

    async def start_follow(self, target_user_id: int, request_id: str | None = None):
        self.follow_target_id = target_user_id
        self._follow_active = True

        if self.client.presence_manager:
            await self.client.presence_manager.set_task(
                "Seguindo usuario", "Aguardando alvo"
            )

        for guild in self.client.guilds:
            member = guild.get_member(target_user_id)
            if member and member.voice and member.voice.channel:
                await self.join_voice(member.voice.channel.id, request_id=request_id)
                user_name = member.display_name
                emit_response(
                    "VOICE_STATUS:",
                    f"FOLLOWING:{user_name}:{member.voice.channel.name}",
                    request_id,
                )
                return

        user = self.client.get_user(target_user_id)
        name = user.display_name if user else str(target_user_id)
        if self.client.presence_manager:
            await self.client.presence_manager.update_detail(f"Aguardando: {name}")
        emit_response("VOICE_STATUS:", f"FOLLOWING:{name}:aguardando", request_id)

    async def stop_follow(self, request_id: str | None = None):
        was_following = self._follow_active
        self.follow_target_id = None
        self._follow_active = False

        if self.is_in_voice:
            await self.leave_voice(request_id=request_id)

        if was_following:
            emit_response("VOICE_STATUS:", "UNFOLLOWED", request_id)
        else:
            emit_response(
                "VOICE_STATUS:",
                "UNFOLLOWED:Não estava seguindo ninguém.",
                request_id,
            )

        if self.client.presence_manager and not self.is_in_voice:
            await self.client.presence_manager.set_main()

    async def on_voice_state_update(self, member, before, after):
        if not self._follow_active or member.id != self.follow_target_id:
            return

        if after.channel and (
            not before.channel or before.channel.id != after.channel.id
        ):
            await self.join_voice(after.channel.id)
            if self.client.presence_manager:
                await self.client.presence_manager.update_detail(
                    f"Canal: {after.channel.name}"
                )
            emit_line(
                f"VOICE_STATUS:FOLLOW_MOVED:{member.display_name}:{after.channel.name}"
            )
        elif not after.channel and before.channel:
            await self.leave_voice()
            if self.client.presence_manager:
                await self.client.presence_manager.update_detail("Aguardando alvo")
            emit_line(f"VOICE_STATUS:FOLLOW_TARGET_LEFT:{member.display_name}")
