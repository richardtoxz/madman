import asyncio
import json
import random

import discord

from .ipc import emit_line, emit_response


class MessageSessionMixin:
    async def _private_channels(self, request_id: str | None = None):
        try:
            return list(await self.client.fetch_private_channels())
        except (discord.HTTPException, discord.RateLimited) as error:
            emit_line(
                f"DM_FETCH_FALLBACK:{self._lookup_error_code(error)}", request_id
            )
            return list(self.client.private_channels)
        except Exception as error:
            emit_line(f"DM_FETCH_FALLBACK:{type(error).__name__}", request_id)
            return list(self.client.private_channels)

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
        except discord.NotFound:
            emit_response("DM_CLOSE_RESULT:", f"OK:{channel_id}:JA_FECHADA", request_id)
        except discord.Forbidden:
            emit_response("DM_CLOSE_RESULT:", "FAIL:SEM_ACESSO", request_id)
        except (discord.HTTPException, discord.RateLimited) as error:
            emit_response(
                "DM_CLOSE_RESULT:",
                f"FAIL:{self._lookup_error_code(error)}",
                request_id,
            )
        except Exception as error:
            emit_response(
                "DM_CLOSE_RESULT:", f"FAIL:{type(error).__name__}", request_id
            )

    async def close_all_dms(self, request_id: str | None = None):
        await self._reboot_state()
        dms = [
            ch
            for ch in await self._private_channels(request_id)
            if isinstance(ch, discord.DMChannel)
        ]
        total = len(dms)
        closed = 0
        failed = 0
        emit_line(f"DM_CLOSE_ALL_START:{total}", request_id)
        for index, dm in enumerate(dms, start=1):
            if self.cancelled:
                break
            try:
                await dm.close()
                closed += 1
            except discord.NotFound:
                closed += 1
            except (discord.Forbidden, discord.HTTPException, discord.RateLimited) as error:
                failed += 1
                emit_line(
                    f"DM_CLOSE_ALL_ERROR:{dm.id}:{self._lookup_error_code(error)}",
                    request_id,
                )
            except Exception as error:
                failed += 1
                emit_line(
                    f"DM_CLOSE_ALL_ERROR:{dm.id}:{type(error).__name__}", request_id
                )
            emit_line(f"DM_CLOSE_ALL_PROGRESS:{index}:{total}", request_id)
            if index < total and not await self._sleep_interruptibly(
                random.uniform(0.5, 1.5)
            ):
                break
        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        if failed and not self.cancelled:
            status = "PARCIAL"
        emit_line(
            f"DM_CLOSE_ALL_RESULT:{status}:{closed}/{total}:FALHAS:{failed}",
            request_id,
        )

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
        except discord.NotFound:
            emit_response(
                "GROUP_DM_LEAVE_RESULT:", f"OK:{channel_id}:JA_FECHADO", request_id
            )
        except discord.Forbidden:
            emit_response("GROUP_DM_LEAVE_RESULT:", "FAIL:SEM_ACESSO", request_id)
        except (discord.HTTPException, discord.RateLimited) as error:
            emit_response(
                "GROUP_DM_LEAVE_RESULT:",
                f"FAIL:{self._lookup_error_code(error)}",
                request_id,
            )
        except Exception as error:
            emit_response(
                "GROUP_DM_LEAVE_RESULT:",
                f"FAIL:{type(error).__name__}",
                request_id,
            )

    async def leave_all_group_dms(self, request_id: str | None = None):
        await self._reboot_state()
        groups = [
            ch
            for ch in await self._private_channels(request_id)
            if isinstance(ch, discord.GroupChannel)
        ]
        total = len(groups)
        left = 0
        failed = 0
        emit_line(f"GROUP_DM_LEAVE_ALL_START:{total}", request_id)
        for index, group in enumerate(groups, start=1):
            if self.cancelled:
                break
            try:
                await group.leave(silent=True)
                left += 1
            except discord.NotFound:
                left += 1
            except (discord.Forbidden, discord.HTTPException, discord.RateLimited) as error:
                failed += 1
                emit_line(
                    f"GROUP_DM_LEAVE_ALL_ERROR:{group.id}:"
                    f"{self._lookup_error_code(error)}",
                    request_id,
                )
            except Exception as error:
                failed += 1
                emit_line(
                    f"GROUP_DM_LEAVE_ALL_ERROR:{group.id}:"
                    f"{type(error).__name__}",
                    request_id,
                )
            emit_line(f"GROUP_DM_LEAVE_ALL_PROGRESS:{index}:{total}", request_id)
            if index < total and not await self._sleep_interruptibly(
                random.uniform(1.0, 2.5)
            ):
                break
        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        if failed and not self.cancelled:
            status = "PARCIAL"
        emit_line(
            f"GROUP_DM_LEAVE_ALL_RESULT:{status}:{left}/{total}:FALHAS:{failed}",
            request_id,
        )

    async def list_dms(self, request_id: str | None = None):
        dms = []
        try:
            channels = await self.client.fetch_private_channels()
        except (discord.HTTPException, discord.RateLimited) as error:
            emit_line(
                f"DM_LIST_FETCH_ERROR:{self._lookup_error_code(error)}", request_id
            )
            channels = list(self.client.private_channels)
        except Exception as error:
            emit_line(f"DM_LIST_FETCH_ERROR:{type(error).__name__}", request_id)
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
            except (AttributeError, TypeError, ValueError) as error:
                emit_line(
                    f"DM_LIST_CHANNEL_ERROR:{getattr(channel, 'id', 0)}:"
                    f"{type(error).__name__}",
                    request_id,
                )
            except Exception as error:
                emit_line(
                    f"DM_LIST_CHANNEL_ERROR:{getattr(channel, 'id', 0)}:"
                    f"{type(error).__name__}",
                    request_id,
                )

        emit_response("DM_LIST_RESULT:", json.dumps(dms), request_id)


class MessageUtilityMixin:
    async def scan_guilds_for_messages(self, request_id: str | None = None):
        await self._reboot_state()
        guilds = list(self.client.guilds)
        total = len(guilds)
        results = []
        emit_line(f"MSG_SCAN_GUILDS_START:{total}", request_id)
        for index, guild in enumerate(guilds):
            if self.cancelled:
                break
            guild_name = self._ipc_label(guild.name)
            emit_line(
                f"MSG_SCAN_GUILDS_CHECKING:{index + 1}:{total}:{guild_name}",
                request_id,
            )
            emit_line("INFO:TARGET_TYPE:channel", request_id)
            found = await self._has_destination_messages(
                guild, request_id, guild=True
            )
            if found:
                results.append(
                    {
                        "guild_id": guild.id,
                        "guild_name": guild.name,
                        "has_messages": True,
                    }
                )
                emit_line(
                    f"MSG_SCAN_GUILDS_FOUND:{guild_name}:{guild.id}", request_id
                )
            if index < total - 1 and not await self._sleep_interruptibly(
                random.uniform(1.0, 2.0)
            ):
                break
        emit_line(f"MSG_SCAN_GUILDS_RESULT:{json.dumps(results)}", request_id)

    async def count_messages(self, channel_id: int, request_id: str | None = None):
        await self._reboot_state()
        channel, target_type, error = await self._resolve_channel(channel_id)
        if error:
            emit_response("MSG_COUNT_RESULT:", f"ERRO:{error}", request_id)
            return

        channel_name = self._channel_display_name(channel)
        emit_line(f"INFO:CANAL_DETECTADO:{channel_name}", request_id)
        emit_line(f"INFO:TARGET_TYPE:{target_type}", request_id)

        count = await self._get_quick_count(channel, request_id)
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
