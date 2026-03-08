import asyncio
import json
import random

import discord

from .ipc import emit_line, emit_response


class MessageSessionMixin:
    async def close_dm(self, channel_id: int, request_id: str | None = None):
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                channel = await self.client.fetch_channel(channel_id)
            if isinstance(channel, discord.DMChannel):
                await channel.close()
                emit_response("DM_CLOSE_RESULT:", f"OK:{channel_id}", request_id)
            elif isinstance(channel, discord.GroupChannel):
                await channel.leave(silent=True)
                emit_response("DM_CLOSE_RESULT:", f"OK:{channel_id}", request_id)
            else:
                emit_response("DM_CLOSE_RESULT:", "FAIL:Nao e DM", request_id)
        except Exception as e:
            emit_response("DM_CLOSE_RESULT:", f"FAIL:{e}", request_id)

    async def close_all_dms(self, request_id: str | None = None):
        self.cancelled = False
        self.control_state.cancelled = False
        dms = [
            ch
            for ch in self.client.private_channels
            if isinstance(ch, discord.DMChannel)
        ]
        total = len(dms)
        closed = 0
        emit_line(f"DM_CLOSE_ALL_START:{total}", request_id)
        for dm in dms:
            if self.cancelled:
                break
            try:
                await dm.close()
                closed += 1
                emit_line(f"DM_CLOSE_ALL_PROGRESS:{closed}:{total}", request_id)
            except Exception as e:
                emit_line(f"DM_CLOSE_ALL_ERROR:{dm.id}:{e}", request_id)
            await asyncio.sleep(random.uniform(0.5, 1.5))
        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        emit_line(f"DM_CLOSE_ALL_RESULT:{status}:{closed}/{total}", request_id)

    async def leave_group_dm(self, channel_id: int, request_id: str | None = None):
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                channel = await self.client.fetch_channel(channel_id)
            if isinstance(channel, discord.GroupChannel):
                await channel.leave(silent=True)
                emit_response(
                    "GROUP_DM_LEAVE_RESULT:", f"OK:{channel_id}", request_id
                )
            else:
                emit_response(
                    "GROUP_DM_LEAVE_RESULT:", "FAIL:Nao e grupo", request_id
                )
        except Exception as e:
            emit_response("GROUP_DM_LEAVE_RESULT:", f"FAIL:{e}", request_id)

    async def leave_all_group_dms(self, request_id: str | None = None):
        self.cancelled = False
        self.control_state.cancelled = False
        groups = [
            ch
            for ch in self.client.private_channels
            if isinstance(ch, discord.GroupChannel)
        ]
        total = len(groups)
        left = 0
        emit_line(f"GROUP_DM_LEAVE_ALL_START:{total}", request_id)
        for group in groups:
            if self.cancelled:
                break
            try:
                await group.leave(silent=True)
                left += 1
                emit_line(f"GROUP_DM_LEAVE_ALL_PROGRESS:{left}:{total}", request_id)
            except Exception as e:
                emit_line(f"GROUP_DM_LEAVE_ALL_ERROR:{group.id}:{e}", request_id)
            await asyncio.sleep(random.uniform(1.0, 2.5))
        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        emit_line(f"GROUP_DM_LEAVE_ALL_RESULT:{status}:{left}/{total}", request_id)

    async def list_dms(self, request_id: str | None = None):
        dms = []
        try:
            channels = await self.client.fetch_private_channels()
        except Exception as e:
            emit_line(f"DM_LIST_FETCH_ERROR: {e} - usando cache", request_id)
            channels = list(self.client.private_channels)

        for channel in channels:
            try:
                if isinstance(channel, discord.DMChannel):
                    recipient = channel.recipient
                    if not recipient:
                        continue
                    dms.append(
                        {
                            "id": channel.id,
                            "type": "dm",
                            "name": recipient.name,
                            "global_name": getattr(
                                recipient, "global_name", recipient.name
                            )
                            or recipient.name,
                            "avatar_url": str(recipient.display_avatar.url)
                            if recipient.display_avatar
                            else "",
                            "user_id": recipient.id,
                            "last_message_id": channel.last_message_id,
                        }
                    )
                elif isinstance(channel, discord.GroupChannel):
                    icon_url = str(channel.icon.url) if channel.icon else ""
                    dms.append(
                        {
                            "id": channel.id,
                            "type": "group",
                            "name": str(channel),
                            "global_name": str(channel),
                            "avatar_url": icon_url,
                            "owner_id": channel.owner_id,
                            "members": len(channel.recipients) + 1,
                            "last_message_id": channel.last_message_id,
                        }
                    )
            except Exception as e:
                emit_line(f"DM_LIST_CHANNEL_ERROR: {channel.id}: {e}", request_id)

        emit_response("DM_LIST_RESULT:", json.dumps(dms), request_id)


class MessageUtilityMixin:
    async def scan_guilds_for_messages(self, request_id: str | None = None):
        self.cancelled = False
        self.control_state.cancelled = False
        guilds = list(self.client.guilds)
        total = len(guilds)
        results = []
        emit_line(f"MSG_SCAN_GUILDS_START:{total}", request_id)
        for index, guild in enumerate(guilds):
            if self.cancelled:
                break
            emit_line(
                f"MSG_SCAN_GUILDS_CHECKING:{index + 1}:{total}:{guild.name}",
                request_id,
            )
            emit_line("INFO:TARGET_TYPE:channel", request_id)
            try:
                found = False
                async for _ in guild.search(authors=[self.client.user], limit=5):
                    found = True
                    break
                if found:
                    results.append(
                        {
                            "guild_id": guild.id,
                            "guild_name": guild.name,
                            "has_messages": True,
                        }
                    )
                    emit_line(
                        f"MSG_SCAN_GUILDS_FOUND:{guild.name}:{guild.id}", request_id
                    )
            except Exception:
                pass
            await asyncio.sleep(random.uniform(1.5, 3.0))
        emit_line(f"MSG_SCAN_GUILDS_RESULT:{json.dumps(results)}", request_id)

    async def count_messages(self, channel_id: int, request_id: str | None = None):
        self.cancelled = False
        self.control_state.cancelled = False
        channel, target_type, error = await self._resolve_channel(channel_id)
        if error:
            emit_response("MSG_COUNT_RESULT:", f"ERRO:{error}", request_id)
            return

        channel_name = self._channel_display_name(channel)
        emit_line(f"INFO:CANAL_DETECTADO:{channel_name}", request_id)
        emit_line(f"INFO:TARGET_TYPE:{target_type}", request_id)

        count = await self._get_quick_count(channel)
        name = self._channel_display_name(channel)
        emit_response("MSG_COUNT_RESULT:", f"OK:{count}:{name}", request_id)

    async def _wait_for_start(self, timeout=300):
        self.control_state.started = False
        waited = 0
        while (
            not self.control_state.started
            and not self.control_state.cancelled
            and waited < timeout
        ):
            await asyncio.sleep(0.5)
            waited += 0.5

        if self.control_state.cancelled or waited >= timeout:
            return False
        return True