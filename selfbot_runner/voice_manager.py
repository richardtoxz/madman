import asyncio
import discord

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

    async def join_voice(self, channel_id: int):
        channel = self.client.get_channel(channel_id)
        if not channel:
            print(f"VOICE_RESULT:ERRO:Canal {channel_id} não encontrado.", flush=True)
            return

        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            print(f"VOICE_RESULT:ERRO:Canal {channel_id} não é um canal de voz.", flush=True)
            return

        guild = channel.guild
        if not guild:
            print(f"VOICE_RESULT:ERRO:Guild do canal não encontrada.", flush=True)
            return

        try:
            await guild.change_voice_state(channel=channel, self_mute=True, self_deaf=True)
            self.current_channel_id = channel_id
            self.current_guild_id = guild.id
            print(f"VOICE_STATUS:JOINED:{channel.name}:{guild.name}", flush=True)
        except Exception as e:
            print(f"VOICE_RESULT:ERRO:{e}", flush=True)

    async def leave_voice(self):
        if not self.current_guild_id:
            print("VOICE_STATUS:LEFT:Não estava em nenhum canal.", flush=True)
            return

        guild = self.client.get_guild(self.current_guild_id)
        if guild:
            try:
                await guild.change_voice_state(channel=None)
            except Exception:
                pass

        self.current_channel_id = None
        self.current_guild_id = None
        print("VOICE_STATUS:LEFT", flush=True)

    async def start_follow(self, target_user_id: int):
        self.follow_target_id = target_user_id
        self._follow_active = True

        # Se o alvo já está em um canal, join imediatamente
        for guild in self.client.guilds:
            member = guild.get_member(target_user_id)
            if member and member.voice and member.voice.channel:
                await self.join_voice(member.voice.channel.id)
                user_name = member.display_name
                print(f"VOICE_STATUS:FOLLOWING:{user_name}:{member.voice.channel.name}", flush=True)
                return

        user = self.client.get_user(target_user_id)
        name = user.display_name if user else str(target_user_id)
        print(f"VOICE_STATUS:FOLLOWING:{name}:aguardando", flush=True)

    async def stop_follow(self):
        was_following = self._follow_active
        self.follow_target_id = None
        self._follow_active = False

        if self.is_in_voice:
            await self.leave_voice()

        if was_following:
            print("VOICE_STATUS:UNFOLLOWED", flush=True)
        else:
            print("VOICE_STATUS:UNFOLLOWED:Não estava seguindo ninguém.", flush=True)

    async def on_voice_state_update(self, member, before, after):
        if not self._follow_active or member.id != self.follow_target_id:
            return

        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            # Alvo entrou ou mudou de canal
            await self.join_voice(after.channel.id)
            print(
                f"VOICE_STATUS:FOLLOW_MOVED:{member.display_name}:{after.channel.name}",
                flush=True
            )
        elif not after.channel and before.channel:
            # Alvo saiu do voice
            await self.leave_voice()
            print(
                f"VOICE_STATUS:FOLLOW_TARGET_LEFT:{member.display_name}",
                flush=True
            )
