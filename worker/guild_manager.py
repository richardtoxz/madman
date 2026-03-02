from bulk_executor import safe_bulk_execute


class GuildManager:
    def __init__(self, client):
        self.client = client
        self.cancelled = False

    async def get_stats(self):
        return {"total": len(self.client.guilds)}

    async def leave_all_guilds(self, whitelist):
        guilds = [g for g in self.client.guilds if g.id not in whitelist]
        self.cancelled = False
        return await safe_bulk_execute(
            guilds,
            lambda g: g.leave(),
            lambda g: g.name,
            lambda: self.cancelled,
            "GUILD_LEAVE",
        )

    async def mute_all_guilds(self, whitelist):
        guilds = [g for g in self.client.guilds if g.id not in whitelist]
        self.cancelled = False

        async def mute_guild(guild):
            from discord.enums import NotificationLevel

            settings = guild.notification_settings
            is_muted = settings.muted.muted
            is_level_none = (
                settings.level == NotificationLevel.nothing
                or settings.level == NotificationLevel.none
            )
            is_suppress_all = settings.suppress_everyone and settings.suppress_roles

            if is_muted and is_level_none and is_suppress_all:
                return "SKIPPED"

            await guild.notification_settings.edit(
                muted_until=True,
                level=NotificationLevel.nothing,
                suppress_everyone=True,
                suppress_roles=True,
            )

        return await safe_bulk_execute(
            guilds, mute_guild, lambda g: g.name, lambda: self.cancelled, "GUILD_MUTE"
        )
