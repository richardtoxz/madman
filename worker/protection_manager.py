import re
import time
import json
import discord
from collections import deque
from config import ProtectionConfig

URL_PATTERN = re.compile(
    r"(https?://([a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,})[^\s\)]*)", re.IGNORECASE
)


class ProtectionManager:
    def __init__(self, client):
        self.client = client
        self.enabled = False
        self.config = ProtectionConfig()
        self.stats = {
            "scams_blocked": 0,
            "spam_closed": 0,
            "dm_floods": 0,
            "fr_floods": 0,
            "fr_rejected": 0,
        }

        self._dm_authors = deque(maxlen=self.config.DM_FLOOD_MAXLEN)
        self._fr_timestamps = deque(maxlen=self.config.FR_FLOOD_MAXLEN)

    def enable(self):
        self.enabled = True
        print("PROTECTION:ENABLED", flush=True)

    def disable(self):
        self.enabled = False
        print("PROTECTION:DISABLED", flush=True)

    def get_status(self):
        data = {"enabled": self.enabled, **self.stats}
        print(f"PROTECTION:STATUS:{json.dumps(data)}", flush=True)

    def _is_safe_domain(self, domain: str) -> bool:
        domain_lower = domain.lower()
        for safe in self.config.SAFE_DOMAINS:
            if domain_lower == safe or domain_lower.endswith(f".{safe}"):
                return True
        return False

    def _is_media_url(self, url: str) -> bool:
        path = url.split("?")[0].split("#")[0].lower()
        return any(path.endswith(ext) for ext in self.config.MEDIA_EXTENSIONS)

    @staticmethod
    def _extract_component_urls(message: discord.Message) -> list[str]:
        urls = []
        for row in message.components:
            for child in row.children:
                if hasattr(child, "url") and child.url:
                    urls.append(child.url)
        return urls

    def _has_invite_link(self, urls: list[str]) -> bool:
        for url in urls:
            for pattern in self.config.INVITE_PATTERNS:
                if pattern.search(url):
                    return True
        return False

    def classify_threat(
        self, content: str, button_urls: list[str] | None = None
    ) -> str | None:
        if not content and not button_urls:
            return None

        text = content or ""
        all_matches = URL_PATTERN.findall(text)
        masked_text = text

        for full_url, domain in all_matches:
            if self._is_safe_domain(domain):
                if self._is_media_url(full_url):
                    masked_text = masked_text.replace(full_url, "[SAFE_MEDIA]")
                else:
                    masked_text = masked_text.replace(full_url, "[SAFE_URL]")

        for pattern in self.config.SCAM_PATTERNS:
            if pattern.search(masked_text):
                return "SCAM"

        for full_url, domain in all_matches:
            if self._is_safe_domain(domain):
                continue
            for tld in self.config.SUSPICIOUS_TLDS:
                if domain.lower().endswith(tld):
                    return "SCAM"

        for pattern in self.config.INVITE_PATTERNS:
            if pattern.search(text):
                return "SPAM"

        if button_urls and self._has_invite_link(button_urls):
            return "SPAM"

        return None

    async def on_message(self, message: discord.Message):
        if not self.enabled:
            return

        if message.channel.type != discord.ChannelType.private:
            return

        if message.author.id == self.client.user.id:
            return

        author = message.author
        author_name = str(author)

        now = time.time()
        self._dm_authors.append((now, author.id))
        cutoff = now - self.config.DM_FLOOD_WINDOW

        recent = [(t, aid) for t, aid in self._dm_authors if t > cutoff]
        unique_authors = {aid for _, aid in recent}

        if len(unique_authors) >= self.config.DM_FLOOD_MAX_UNIQUE:
            self.stats["dm_floods"] += 1
            print(f"PROTECTION:DM_FLOOD:{len(unique_authors)}", flush=True)
            await message.channel.close()
            return

        button_urls = self._extract_component_urls(message)
        threat = self.classify_threat(message.content, button_urls=button_urls)
        if not threat:
            return

        try:
            if threat == "SCAM":
                await message.channel.close()
                try:
                    rel = author.relationship
                    if not rel or rel.type != discord.RelationshipType.friend:
                        await author.block()
                except Exception:
                    pass
                self.stats["scams_blocked"] += 1
                print(
                    f"PROTECTION:BLOCKED:{author_name}:{author.id}:scam link detectado",
                    flush=True,
                )

            elif threat == "SPAM":
                await message.channel.close()
                self.stats["spam_closed"] += 1
                print(
                    f"PROTECTION:CLOSED:{author_name}:{author.id}:invite link detectado",
                    flush=True,
                )

        except discord.HTTPException as e:
            print(f"PROTECTION:ERROR:{e}", flush=True)

    async def on_relationship_add(self, relationship):
        if not self.enabled:
            return

        if relationship.type != discord.RelationshipType.incoming_request:
            return

        now = time.time()
        self._fr_timestamps.append(now)

        cutoff = now - self.config.FR_FLOOD_WINDOW
        recent = [t for t in self._fr_timestamps if t > cutoff]
        if len(recent) >= self.config.FR_FLOOD_MAX_COUNT:
            self.stats["fr_floods"] += 1
            self.stats["fr_rejected"] += 1
            print(
                f"PROTECTION:FR_FLOOD:{len(recent)}:rejeitando {relationship.user}",
                flush=True,
            )
            try:
                await relationship.delete()
            except discord.HTTPException:
                pass
