import asyncio
import json
import random

import discord

from ..core.ipc import emit_line, emit_response

DISCORD_EPOCH_MS = 1420070400000


class MessageSessionMixin:
    @staticmethod
    def _snowflake_to_ms(snowflake) -> int:
        try:
            value = int(snowflake)
            if value > 0:
                return (value >> 22) + DISCORD_EPOCH_MS
        except (TypeError, ValueError):
            pass
        return 0

    def _serialize_private_channel(self, channel) -> dict | None:
        last_message_id = getattr(channel, "last_message_id", None)
        last_activity_ms = self._snowflake_to_ms(last_message_id)
        read_state = getattr(channel, "read_state", None)
        badge_count = 0
        acked_id = None
        if read_state is not None:
            try:
                badge_count = int(getattr(read_state, "badge_count", 0) or getattr(read_state, "mention_count", 0) or 0)
            except (TypeError, ValueError):
                badge_count = 0
            acked_id = getattr(read_state, "acked_message_id", None) or getattr(read_state, "last_message_id", None)

        unread = bool(badge_count > 0)
        if not unread and last_message_id and acked_id:
            try:
                unread = int(last_message_id) > int(acked_id)
            except (TypeError, ValueError):
                unread = False

        if isinstance(channel, discord.DMChannel):
            recipient = getattr(channel, "recipient", None)
            if not recipient:
                return None
            is_req_attr = getattr(channel, "is_message_request", False)
            is_request = bool(is_req_attr() if callable(is_req_attr) else is_req_attr)
            # is_message_request() tambem e True para pedidos JA aceitos; so e
            # pendente quando ainda nao foi aceito.
            is_accepted_attr = getattr(channel, "is_accepted", None)
            if is_request and callable(is_accepted_attr):
                is_request = not bool(is_accepted_attr())
            is_spam_attr = getattr(channel, "is_spam", False)
            is_spam = bool(is_spam_attr() if callable(is_spam_attr) else is_spam_attr)
            if not last_activity_ms:
                req_at = getattr(channel, "requested_at", None)
                if req_at is not None and hasattr(req_at, "timestamp"):
                    try:
                        last_activity_ms = int(req_at.timestamp() * 1000)
                    except Exception:
                        last_activity_ms = 0
                if not last_activity_ms:
                    last_activity_ms = self._snowflake_to_ms(getattr(channel, "id", 0))

            avatar = getattr(recipient, "display_avatar", None)
            return {
                "id": str(channel.id),
                "type": "dm",
                "name": str(recipient.name),
                "global_name": str(getattr(recipient, "global_name", None) or recipient.name),
                "avatar_url": str(avatar.url) if avatar else "",
                "user_id": str(recipient.id),
                "last_message_id": str(last_message_id) if last_message_id is not None else None,
                "last_activity_ms": int(last_activity_ms),
                "unread": bool(unread),
                "mention_count": int(badge_count),
                "is_request": bool(is_request),
                "is_spam": bool(is_spam),
            }

        if isinstance(channel, discord.GroupChannel):
            icon = getattr(channel, "icon", None)
            icon_url = str(icon.url) if icon else ""
            owner_id = getattr(channel, "owner_id", None)
            self_id = getattr(getattr(self.client, "user", None), "id", None)
            is_owner = False
            if owner_id is not None and self_id is not None:
                try:
                    is_owner = int(owner_id) == int(self_id)
                except (TypeError, ValueError):
                    is_owner = False

            recipients = list(getattr(channel, "recipients", ()) or ())
            raw_name = getattr(channel, "name", None)
            fallback_name = ", ".join(
                str(getattr(r, "global_name", None) or getattr(r, "name", ""))
                for r in recipients
                if getattr(r, "name", None)
            )
            display_name = str(raw_name or fallback_name or str(channel) or f"Grupo {channel.id}")
            if not last_activity_ms:
                last_activity_ms = self._snowflake_to_ms(getattr(channel, "id", 0))

            return {
                "id": str(channel.id),
                "type": "group",
                "name": display_name,
                "global_name": display_name,
                "avatar_url": icon_url,
                "owner_id": str(owner_id) if owner_id is not None else None,
                "is_owner": bool(is_owner),
                "members": len(recipients) + 1,
                "last_message_id": str(last_message_id) if last_message_id is not None else None,
                "last_activity_ms": int(last_activity_ms),
                "unread": bool(unread),
                "mention_count": int(badge_count),
                "is_request": False,
                "is_spam": False,
            }

        return None

    async def _private_channels(self, request_id: str | None = None):
        try:
            return list(await self.client.fetch_private_channels())
        except (discord.HTTPException, discord.RateLimited) as error:
            emit_line(f"DM_FETCH_FALLBACK:{self._lookup_error_code(error)}", request_id)
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
            emit_response("DM_CLOSE_RESULT:", f"FAIL:{type(error).__name__}", request_id)

    async def close_all_dms(self, request_id: str | None = None):
        await self._reboot_state()
        dms = [ch for ch in await self._private_channels(request_id) if isinstance(ch, discord.DMChannel)]
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
                emit_line(f"DM_CLOSE_ALL_ERROR:{dm.id}:{type(error).__name__}", request_id)
            emit_line(f"DM_CLOSE_ALL_PROGRESS:{index}:{total}", request_id)
            if index < total and not await self._sleep_interruptibly(random.uniform(0.5, 1.5)):
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
                emit_response("GROUP_DM_LEAVE_RESULT:", f"OK:{channel_id}", request_id)
            else:
                emit_response("GROUP_DM_LEAVE_RESULT:", "FAIL:Nao e grupo", request_id)
        except discord.NotFound:
            emit_response("GROUP_DM_LEAVE_RESULT:", f"OK:{channel_id}:JA_FECHADO", request_id)
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
        groups = [ch for ch in await self._private_channels(request_id) if isinstance(ch, discord.GroupChannel)]
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
                    f"GROUP_DM_LEAVE_ALL_ERROR:{group.id}:{self._lookup_error_code(error)}",
                    request_id,
                )
            except Exception as error:
                failed += 1
                emit_line(
                    f"GROUP_DM_LEAVE_ALL_ERROR:{group.id}:{type(error).__name__}",
                    request_id,
                )
            emit_line(f"GROUP_DM_LEAVE_ALL_PROGRESS:{index}:{total}", request_id)
            if index < total and not await self._sleep_interruptibly(random.uniform(1.0, 2.5)):
                break
        status = "CANCELADO" if self.cancelled else "COMPLETADO"
        if failed and not self.cancelled:
            status = "PARCIAL"
        emit_line(
            f"GROUP_DM_LEAVE_ALL_RESULT:{status}:{left}/{total}:FALHAS:{failed}",
            request_id,
        )

    async def list_dms(self, request_id: str | None = None, *, refresh: bool = False):
        dms = []
        cached_channels = list(getattr(self.client, "private_channels", ()) or ())
        if not refresh and cached_channels:
            channels = cached_channels
        else:
            try:
                channels = list(await self.client.fetch_private_channels())
            except (discord.HTTPException, discord.RateLimited) as error:
                emit_line(
                    f"DM_LIST_FETCH_ERROR:{self._lookup_error_code(error)}",
                    request_id,
                )
                channels = cached_channels
            except Exception as error:
                emit_line(f"DM_LIST_FETCH_ERROR:{type(error).__name__}", request_id)
                channels = cached_channels

        for channel in channels:
            try:
                serialized = self._serialize_private_channel(channel)
                if serialized is not None:
                    dms.append(serialized)
            except (AttributeError, TypeError, ValueError) as error:
                emit_line(
                    f"DM_LIST_CHANNEL_ERROR:{getattr(channel, 'id', 0)}:{type(error).__name__}",
                    request_id,
                )
            except Exception as error:
                emit_line(
                    f"DM_LIST_CHANNEL_ERROR:{getattr(channel, 'id', 0)}:{type(error).__name__}",
                    request_id,
                )

        dms.sort(
            key=lambda item: (
                -int(item.get("last_activity_ms") or 0),
                str(item.get("global_name") or item.get("name") or "").casefold(),
                str(item.get("id") or ""),
            )
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
            found = await self._has_destination_messages(guild, request_id, guild=True)
            if found:
                results.append(
                    {
                        "guild_id": str(guild.id),
                        "guild_name": guild.name,
                        "has_messages": True,
                    }
                )
                emit_line(f"MSG_SCAN_GUILDS_FOUND:{guild_name}:{guild.id}", request_id)
            if index < total - 1 and not await self._sleep_interruptibly(random.uniform(1.0, 2.0)):
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
        while not self.control_state.started and not self.control_state.cancelled and waited < timeout:
            await asyncio.sleep(0.5)
            waited += 0.5

        if self.control_state.cancelled or waited >= timeout:
            return False
        return True
