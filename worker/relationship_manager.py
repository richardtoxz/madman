import discord
from bulk_executor import safe_bulk_execute


class RelationshipManager:
    def __init__(self, client):
        self.client = client
        self.cancelled = False

    async def get_stats(self):
        friends = self.client.friends
        blocked = self.client.blocked
        all_rels = list(self.client.relationships)
        incoming = [
            r for r in all_rels if r.type == discord.RelationshipType.incoming_request
        ]
        outgoing = [
            r for r in all_rels if r.type == discord.RelationshipType.outgoing_request
        ]
        online_friends = [f for f in friends if f.status != discord.Status.offline]
        return {
            "friends": len(friends),
            "blocked": len(blocked),
            "incoming_requests": len(incoming),
            "outgoing_requests": len(outgoing),
            "online_friends": len(online_friends),
            "total_relationships": len(all_rels),
        }

    async def get_friends_list(self):
        friends = []
        for rel in self.client.friends:
            user = rel.user
            name_str = (
                f"{user.global_name} (@{user.name})"
                if hasattr(user, "global_name") and user.global_name
                else str(user)
            )
            avatar_url = (
                str(user.display_avatar.with_size(64).url)
                if user.display_avatar
                else None
            )
            activity_str = None
            if rel.activity and hasattr(rel.activity, "name") and rel.activity.name:
                prefixes = {
                    0: "Jogando",
                    1: "Streaming",
                    2: "Ouvindo",
                    3: "Assistindo",
                    4: "",
                    5: "Competindo",
                }
                prefix = prefixes.get(rel.activity.type.value, "")
                activity_str = f"{prefix} {rel.activity.name}".strip()
            since_ts = int(rel.since.timestamp()) if rel.since else None
            friends.append(
                {
                    "name": name_str,
                    "id": user.id,
                    "avatar_url": avatar_url,
                    "nick": rel.nick,
                    "since": since_ts,
                    "status": str(rel.status),
                    "on_mobile": rel.is_on_mobile(),
                    "desktop_status": str(rel.desktop_status),
                    "mobile_status": str(rel.mobile_status),
                    "web_status": str(rel.web_status),
                    "activity": activity_str,
                }
            )
        status_order = {"online": 0, "idle": 1, "dnd": 2, "offline": 3}
        friends.sort(
            key=lambda f: (status_order.get(f["status"], 9), f["name"].lower())
        )
        return friends

    async def get_pending_requests(self):
        all_rels = list(self.client.relationships)
        incoming, outgoing = [], []
        for rel in all_rels:
            user = rel.user
            info = {
                "name": str(user),
                "id": user.id,
                "avatar_url": (
                    str(user.display_avatar.with_size(64).url)
                    if user.display_avatar
                    else None
                ),
            }
            if rel.type == discord.RelationshipType.incoming_request:
                incoming.append(info)
            elif rel.type == discord.RelationshipType.outgoing_request:
                outgoing.append(info)
        return {"incoming": incoming, "outgoing": outgoing}

    async def get_blocked_list(self):
        return [
            {
                "name": str(r.user),
                "id": r.user.id,
                "avatar_url": (
                    str(r.user.display_avatar.with_size(64).url)
                    if r.user.display_avatar
                    else None
                ),
            }
            for r in self.client.blocked
        ]

    async def remove_all_friends(self, whitelist):
        friends = [f for f in self.client.friends if f.user.id not in whitelist]
        self.cancelled = False
        return await safe_bulk_execute(
            friends,
            lambda r: r.delete(),
            lambda r: f"{r.user.name} ({r.user.id})",
            lambda: self.cancelled,
            "REL_CLEANUP",
        )

    async def unblock_all(self):
        blocked = list(self.client.blocked)
        self.cancelled = False
        return await safe_bulk_execute(
            blocked,
            lambda r: r.delete(),
            lambda r: f"{r.user.name} ({r.user.id})",
            lambda: self.cancelled,
            "REL_UNBLOCK",
        )

    async def accept_all_requests(self):
        incoming = [
            r
            for r in self.client.relationships
            if r.type == discord.RelationshipType.incoming_request
        ]
        self.cancelled = False
        return await safe_bulk_execute(
            incoming,
            lambda r: r.accept(),
            lambda r: f"{r.user.name} ({r.user.id})",
            lambda: self.cancelled,
            "REL_ACCEPT",
        )

    async def deny_all_requests(self):
        pending = [
            r
            for r in self.client.relationships
            if r.type
            in (
                discord.RelationshipType.incoming_request,
                discord.RelationshipType.outgoing_request,
            )
        ]
        self.cancelled = False
        return await safe_bulk_execute(
            pending,
            lambda r: r.delete(),
            lambda r: f"{r.user.name} ({r.user.id})",
            lambda: self.cancelled,
            "REL_DENY",
        )

    async def set_friend_nickname(self, user_id, nickname):
        rel = self.client.get_relationship(user_id)
        if rel and rel.type == discord.RelationshipType.friend:
            await rel.edit(nick=nickname)
            return True
        return False

    async def send_friend_request_by_name(self, username):
        await self.client.send_friend_request(username)

    async def block_user(self, user_id):
        user = self.client.get_user(user_id) or await self.client.fetch_user(user_id)
        await user.block()

    async def unblock_user(self, user_id):
        user = self.client.get_user(user_id) or await self.client.fetch_user(user_id)
        await user.unblock()
