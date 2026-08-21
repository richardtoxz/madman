from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit

import discord

from .config import ProtectionConfig
from .ipc import emit_line, emit_response, sanitize_ipc_field


URL_PATTERN = re.compile(r"(?i)\bhttps?://[^\s<>\[\]{}\"']+")
BARE_INVITE_PATTERN = re.compile(
    r"(?i)(?<![\w.-])(?:www\.)?"
    r"(?:discord\.gg|discord(?:app)?\.com/invite)/[a-z0-9-]+"
)


@dataclass(frozen=True, slots=True)
class ThreatVerdict:
    kind: str
    reason: str


@dataclass(slots=True)
class MessagePayload:
    texts: list[str]
    urls: list[str]
    filenames: list[str]

    def fingerprint(self) -> str:
        digest = hashlib.blake2s(digest_size=16)
        for collection in (self.texts, self.urls, self.filenames):
            for item in collection:
                digest.update(item.encode("utf-8", errors="replace"))
                digest.update(b"\x00")
            digest.update(b"\xff")
        return digest.hexdigest()


class ProtectionManager:
    """Protecoes de DM e pedidos com estado isolado por worker/conta."""

    def __init__(self, client):
        self.client = client
        self.enabled = False
        self.config = ProtectionConfig()
        self.whitelist: set[int] = set()
        self.stats = {
            "threats_detected": 0,
            "actions_taken": 0,
            "scams_blocked": 0,
            "spam_closed": 0,
            "dm_floods": 0,
            "fr_floods": 0,
            "fr_rejected": 0,
            "trusted_ignored": 0,
            "errors": 0,
        }
        self.last_error: str | None = None

        self._lock = asyncio.Lock()
        self._dm_authors: deque[tuple[float, int]] = deque(
            maxlen=self.config.MAX_TRACKED_AUTHORS
        )
        self._dm_messages: OrderedDict[int, deque[float]] = OrderedDict()
        self._invite_messages: OrderedDict[int, deque[float]] = OrderedDict()
        self._fr_requests: deque[tuple[float, int]] = deque(
            maxlen=self.config.MAX_TRACKED_AUTHORS
        )
        self._message_fingerprints: OrderedDict[int, str] = OrderedDict()
        self._action_cooldowns: dict[tuple[int, str], float] = {}
        self._fr_flood_active = False

    @staticmethod
    def _result(ok: bool, code: str, message: str, **extra) -> dict:
        return {"ok": ok, "code": code, "message": message, **extra}

    def _emit_json(self, prefix: str, payload: dict, request_id: str | None) -> None:
        emit_response(
            prefix,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            request_id,
        )

    def configure(
        self,
        whitelist: Iterable[int] = (),
        request_id: str | None = None,
    ) -> dict:
        try:
            parsed = {int(user_id) for user_id in whitelist}
        except (TypeError, ValueError):
            result = self._result(False, "INVALID_WHITELIST", "Whitelist invalida.")
        else:
            if (
                len(parsed) > self.config.MAX_WHITELIST_USERS
                or any(user_id <= 0 or user_id >= 2**64 for user_id in parsed)
            ):
                result = self._result(
                    False,
                    "INVALID_WHITELIST",
                    "Whitelist excede o limite ou contem IDs invalidos.",
                )
            else:
                self.whitelist = parsed
                result = self._result(
                    True,
                    "CONFIGURED",
                    "Whitelist de protecao atualizada.",
                    whitelist_count=len(parsed),
                )

        if request_id is not None:
            self._emit_json("PROTECTION_RESULT:", result, request_id)
        return result

    def enable(
        self,
        whitelist: Iterable[int] | None = None,
        request_id: str | None = None,
    ) -> dict:
        if whitelist is not None:
            configured = self.configure(whitelist)
            if not configured["ok"]:
                if request_id is not None:
                    self._emit_json("PROTECTION_RESULT:", configured, request_id)
                return configured

        self._reset_windows()
        self.enabled = True
        result = self._result(
            True,
            "ENABLED",
            "Protecoes ativadas.",
            whitelist_count=len(self.whitelist),
        )
        if request_id is not None:
            self._emit_json("PROTECTION_RESULT:", result, request_id)
        return result

    def disable(self, request_id: str | None = None) -> dict:
        self.enabled = False
        self._reset_windows()
        result = self._result(True, "DISABLED", "Protecoes desativadas.")
        if request_id is not None:
            self._emit_json("PROTECTION_RESULT:", result, request_id)
        return result

    def status_data(self) -> dict:
        return {
            "ok": True,
            "code": "STATUS",
            "message": "Estado das protecoes carregado.",
            "enabled": self.enabled,
            "whitelist_count": len(self.whitelist),
            "whitelist": sorted(self.whitelist),
            "last_error": self.last_error,
            "limits": {
                "dm_burst_messages": self.config.DM_BURST_MAX_MESSAGES,
                "dm_burst_seconds": self.config.DM_BURST_WINDOW,
                "dm_raid_users": self.config.DM_RAID_MAX_UNIQUE,
                "dm_raid_seconds": self.config.DM_RAID_WINDOW,
                "invite_messages": self.config.INVITE_SPAM_MAX_MESSAGES,
                "invite_seconds": self.config.INVITE_SPAM_WINDOW,
                "friend_requests": self.config.FR_FLOOD_MAX_COUNT,
                "friend_request_seconds": self.config.FR_FLOOD_WINDOW,
            },
            **self.stats,
        }

    def get_status(self, request_id: str | None = None) -> dict:
        data = self.status_data()
        if request_id is not None:
            self._emit_json("PROTECTION_STATUS_RESULT:", data, request_id)
        return data

    def _reset_windows(self) -> None:
        self._dm_authors.clear()
        self._dm_messages.clear()
        self._invite_messages.clear()
        self._fr_requests.clear()
        self._message_fingerprints.clear()
        self._action_cooldowns.clear()
        self._fr_flood_active = False

    @staticmethod
    def _domain_matches(host: str, domain: str) -> bool:
        return host == domain or host.endswith(f".{domain}")

    def _is_safe_domain(self, host: str) -> bool:
        return any(
            self._domain_matches(host, safe) for safe in self.config.SAFE_DOMAINS
        )

    @staticmethod
    def _clean_url(url: str) -> str:
        return str(url or "").strip().strip("<>").rstrip(".,;:!?)]}\"")

    @classmethod
    def _extract_urls(cls, text: str) -> list[str]:
        urls = [cls._clean_url(match.group(0)) for match in URL_PATTERN.finditer(text)]
        for match in BARE_INVITE_PATTERN.finditer(text):
            candidate = cls._clean_url(match.group(0))
            if not candidate.lower().startswith(("http://", "https://")):
                candidate = f"https://{candidate}"
            urls.append(candidate)
        return list(dict.fromkeys(url for url in urls if url))[:100]

    @staticmethod
    def _parse_url(url: str):
        try:
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {"http", "https"}:
                return None
            host = (parsed.hostname or "").rstrip(".").lower()
            if not host:
                return None
            # Acessar port tambem valida portas fora da faixa e formatos ruins.
            parsed.port
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError:
                return None
            if len(host) > 253:
                return None
            try:
                ipaddress.ip_address(host)
            except ValueError:
                labels = host.split(".")
                if any(
                    not label
                    or len(label) > 63
                    or label.startswith("-")
                    or label.endswith("-")
                    or re.fullmatch(r"[a-z0-9-]+", label) is None
                    for label in labels
                ):
                    return None
            return parsed, host
        except (TypeError, ValueError, UnicodeError):
            return None

    @classmethod
    def _is_invite(cls, parsed, host: str) -> bool:
        path_parts = [part for part in parsed.path.lower().split("/") if part]
        if cls._domain_matches(host, "discord.gg"):
            return bool(path_parts)
        if cls._domain_matches(host, "discord.com") or cls._domain_matches(
            host, "discordapp.com"
        ):
            return len(path_parts) >= 2 and path_parts[0] == "invite"
        return False

    def _is_brand_impersonation(self, host: str) -> bool:
        if any(
            self._domain_matches(host, official)
            for official in (
                "discord.com",
                "discord.gg",
                "discordapp.com",
                "steamcommunity.com",
                "steampowered.com",
            )
        ):
            return False
        return any(
            pattern.search(host)
            for pattern in self.config.BRAND_IMPERSONATION_PATTERNS
        )

    def classify_payload(self, payload: MessagePayload) -> ThreatVerdict | None:
        for filename in payload.filenames:
            clean_name = filename.lower().removeprefix("spoiler_")
            if clean_name.endswith(self.config.DANGEROUS_ATTACHMENT_EXTENSIONS):
                return ThreatVerdict(
                    "SCAM", "anexo executavel recebido de remetente desconhecido"
                )

        text = "\n".join(payload.texts)[:50_000]
        parsed_urls = []
        for url in payload.urls:
            parsed = self._parse_url(url)
            if parsed is not None:
                parsed_urls.append((url, *parsed))

        for _, parsed, host in parsed_urls:
            if self._is_brand_impersonation(host):
                return ThreatVerdict("SCAM", "dominio imitando uma marca conhecida")
            if (
                parsed.username is not None or parsed.password is not None
            ) and not self._is_safe_domain(host):
                return ThreatVerdict("SCAM", "link externo usa credenciais na URL")

        external_hosts = [
            host for _, _, host in parsed_urls if not self._is_safe_domain(host)
        ]
        has_lure = any(pattern.search(text) for pattern in self.config.SCAM_LURE_PATTERNS)
        if has_lure and external_hosts:
            reason = "link externo combinado com linguagem de golpe"
            if any(host.endswith(self.config.SUSPICIOUS_TLDS) for host in external_hosts):
                reason = "linguagem de golpe em dominio de alto risco"
            return ThreatVerdict("SCAM", reason)

        if any(self._is_invite(parsed, host) for _, parsed, host in parsed_urls):
            return ThreatVerdict("INVITE", "convites repetidos em mensagem direta")
        return None

    def classify_threat(
        self, content: str, button_urls: list[str] | None = None
    ) -> str | None:
        """Interface mantida para chamadas antigas e testes manuais."""

        text = str(content or "")
        urls = self._extract_urls(text)
        urls.extend(str(url) for url in button_urls or () if url)
        verdict = self.classify_payload(
            MessagePayload([text] if text else [], list(dict.fromkeys(urls)), [])
        )
        return verdict.kind if verdict else None

    @staticmethod
    def _walk_component_urls(components) -> list[str]:
        urls: list[str] = []
        queue = list(components or ())
        visited: set[int] = set()
        while queue and len(visited) < 100:
            component = queue.pop(0)
            marker = id(component)
            if marker in visited:
                continue
            visited.add(marker)

            if isinstance(component, dict):
                url = component.get("url")
                nested = component.get("children") or component.get("components") or ()
                accessory = component.get("accessory")
            else:
                url = getattr(component, "url", None)
                nested = getattr(component, "children", None) or getattr(
                    component, "components", None
                ) or ()
                accessory = getattr(component, "accessory", None)
            if url:
                urls.append(str(url))
            try:
                queue.extend(nested)
            except TypeError:
                pass
            if accessory is not None:
                queue.append(accessory)
        return urls

    @classmethod
    def _extract_embed_data(cls, embed) -> tuple[list[str], list[str]]:
        try:
            data = embed.to_dict()
        except (AttributeError, TypeError, ValueError):
            data = {
                "title": getattr(embed, "title", None),
                "description": getattr(embed, "description", None),
                "url": getattr(embed, "url", None),
                "fields": getattr(embed, "fields", None),
            }

        texts: list[str] = []
        urls: list[str] = []
        queue: list[tuple[str, object]] = [("", data)]
        visited = 0
        while queue and visited < 300:
            key, value = queue.pop(0)
            visited += 1
            if isinstance(value, dict):
                queue.extend((str(child_key), child) for child_key, child in value.items())
            elif isinstance(value, (list, tuple)):
                queue.extend((key, child) for child in value)
            elif value is not None:
                string = str(value)
                if "url" in key.lower():
                    urls.append(string)
                elif key.lower() in {"title", "description", "name", "value", "text"}:
                    texts.append(string)
        return texts, urls

    @classmethod
    def _extract_message_payload(cls, message) -> MessagePayload:
        texts: list[str] = []
        urls: list[str] = []
        filenames: list[str] = []
        sources = [message, *list(getattr(message, "message_snapshots", ()) or ())[:10]]

        for source in sources:
            content = str(getattr(source, "content", "") or "")
            if content:
                texts.append(content)
                urls.extend(cls._extract_urls(content))

            urls.extend(cls._walk_component_urls(getattr(source, "components", ())))

            for embed in list(getattr(source, "embeds", ()) or ())[:10]:
                embed_texts, embed_urls = cls._extract_embed_data(embed)
                texts.extend(embed_texts)
                urls.extend(embed_urls)
                for embed_text in embed_texts:
                    urls.extend(cls._extract_urls(embed_text))

            for attachment in list(getattr(source, "attachments", ()) or ())[:20]:
                filename = str(getattr(attachment, "filename", "") or "")
                if filename:
                    filenames.append(filename)
                attachment_url = getattr(attachment, "url", None)
                if attachment_url:
                    urls.append(str(attachment_url))
                for field in ("description", "title"):
                    value = getattr(attachment, field, None)
                    if value:
                        texts.append(str(value))

        cleaned_urls = [cls._clean_url(url) for url in urls]
        return MessagePayload(
            texts=texts[:200],
            urls=list(dict.fromkeys(url for url in cleaned_urls if url))[:200],
            filenames=filenames[:200],
        )

    def _remember_message(self, message_id: int, fingerprint: str) -> bool:
        if message_id <= 0:
            return True
        previous = self._message_fingerprints.get(message_id)
        self._message_fingerprints[message_id] = fingerprint
        self._message_fingerprints.move_to_end(message_id)
        while len(self._message_fingerprints) > self.config.MAX_TRACKED_MESSAGES:
            self._message_fingerprints.popitem(last=False)
        return previous != fingerprint

    @staticmethod
    def _prune(queue: deque, cutoff: float) -> None:
        while queue and queue[0][0] <= cutoff:
            queue.popleft()

    def _author_queue(
        self, mapping: OrderedDict[int, deque[float]], author_id: int
    ) -> deque[float]:
        queue = mapping.get(author_id)
        if queue is None:
            if len(mapping) >= self.config.MAX_TRACKED_AUTHORS:
                mapping.popitem(last=False)
            queue = deque()
            mapping[author_id] = queue
        mapping.move_to_end(author_id)
        return queue

    def _record_dm(self, author_id: int, now: float) -> ThreatVerdict | None:
        messages = self._author_queue(self._dm_messages, author_id)
        burst_cutoff = now - self.config.DM_BURST_WINDOW
        while messages and messages[0] <= burst_cutoff:
            messages.popleft()
        messages.append(now)

        raid_cutoff = now - self.config.DM_RAID_WINDOW
        self._prune(self._dm_authors, raid_cutoff)
        recent_authors = {user_id for _, user_id in self._dm_authors}
        if author_id not in recent_authors:
            self._dm_authors.append((now, author_id))
            recent_authors.add(author_id)

        if len(messages) >= self.config.DM_BURST_MAX_MESSAGES:
            return ThreatVerdict("DM_BURST", "rajada de mensagens diretas")
        if len(recent_authors) >= self.config.DM_RAID_MAX_UNIQUE:
            return ThreatVerdict("DM_RAID", "rajada de remetentes diferentes")
        return None

    def _record_invite(self, author_id: int, now: float) -> bool:
        messages = self._author_queue(self._invite_messages, author_id)
        cutoff = now - self.config.INVITE_SPAM_WINDOW
        while messages and messages[0] <= cutoff:
            messages.popleft()
        messages.append(now)
        return len(messages) >= self.config.INVITE_SPAM_MAX_MESSAGES

    def _is_trusted(self, author) -> bool:
        author_id = int(getattr(author, "id", 0) or 0)
        if author_id in self.whitelist:
            return True
        if bool(getattr(author, "system", False)) or bool(getattr(author, "bot", False)):
            return True
        try:
            if author.is_friend():
                return True
        except (AttributeError, TypeError):
            pass
        relationship = getattr(author, "relationship", None)
        return getattr(relationship, "type", None) is discord.RelationshipType.friend

    def _reserve_action(self, author_id: int, kind: str, now: float) -> bool:
        cutoff = now - self.config.ACTION_COOLDOWN
        self._action_cooldowns = {
            key: timestamp
            for key, timestamp in self._action_cooldowns.items()
            if timestamp > cutoff
        }
        key = (author_id, kind)
        if key in self._action_cooldowns:
            return False
        self._action_cooldowns[key] = now
        return True

    def _emit_event(
        self,
        event: str,
        *,
        author,
        threat: ThreatVerdict,
        actions: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        payload = {
            "event": event,
            "threat": threat.kind,
            "author_id": int(getattr(author, "id", 0) or 0),
            "author_name": sanitize_ipc_field(author, limit=100),
            "reason": threat.reason,
            "actions": actions or [],
            "errors": errors or [],
        }
        emit_line(
            "EVENT:PROTECTION:"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    async def _apply_dm_action(self, message, threat: ThreatVerdict) -> None:
        author = message.author
        author_id = int(author.id)
        actions: list[str] = []
        errors: list[str] = []

        if threat.kind == "SCAM":
            try:
                try:
                    already_blocked = bool(author.is_blocked())
                except (AttributeError, TypeError):
                    already_blocked = getattr(
                        getattr(author, "relationship", None), "type", None
                    ) is discord.RelationshipType.blocked
                if already_blocked:
                    actions.append("already_blocked")
                else:
                    await author.block()
                    actions.append("blocked")
            except discord.NotFound:
                actions.append("already_blocked")
            except discord.HTTPException as error:
                errors.append(f"block:{type(error).__name__}")
            except Exception as error:
                errors.append(f"block:{type(error).__name__}")

        try:
            await message.channel.close()
            actions.append("dm_closed")
        except discord.NotFound:
            actions.append("dm_already_closed")
        except discord.HTTPException as error:
            errors.append(f"close:{type(error).__name__}")
        except Exception as error:
            errors.append(f"close:{type(error).__name__}")

        async with self._lock:
            if actions:
                self.stats["actions_taken"] += 1
                if threat.kind == "SCAM" and any(
                    action in {"blocked", "already_blocked"} for action in actions
                ):
                    self.stats["scams_blocked"] += 1
                if threat.kind == "INVITE_SPAM" and any(
                    action.startswith("dm_") for action in actions
                ):
                    self.stats["spam_closed"] += 1
            else:
                self._action_cooldowns.pop((author_id, threat.kind), None)
            if errors:
                self.stats["errors"] += len(errors)
                self.last_error = errors[-1]

        self._emit_event(
            "ACTION" if actions else "ACTION_FAILED",
            author=author,
            threat=threat,
            actions=actions,
            errors=errors,
        )

    async def on_message(self, message: discord.Message, *, count_flood: bool = True):
        if not self.enabled:
            return
        channel_type = getattr(getattr(message, "channel", None), "type", None)
        if channel_type is not discord.ChannelType.private:
            return

        author = getattr(message, "author", None)
        author_id = int(getattr(author, "id", 0) or 0)
        own_id = int(getattr(getattr(self.client, "user", None), "id", 0) or 0)
        if not author_id or author_id == own_id:
            return

        try:
            payload = self._extract_message_payload(message)
            fingerprint = payload.fingerprint()
            now = time.monotonic()

            async with self._lock:
                if not self.enabled or not self._remember_message(
                    int(getattr(message, "id", 0) or 0), fingerprint
                ):
                    return

                trusted = self._is_trusted(author)
                flood = None
                if count_flood and not trusted:
                    flood = self._record_dm(author_id, now)

                verdict = self.classify_payload(payload)
                if verdict and verdict.kind == "INVITE":
                    if trusted or not self._record_invite(author_id, now):
                        verdict = None
                    else:
                        verdict = ThreatVerdict("INVITE_SPAM", "convites repetidos em DM")

                threat = verdict or flood
                if threat is None:
                    return

                self.stats["threats_detected"] += 1
                if trusted:
                    self.stats["trusted_ignored"] += 1
                    should_alert_trusted = self._reserve_action(
                        author_id, f"TRUSTED_{threat.kind}", now
                    )
                    should_act = False
                else:
                    should_alert_trusted = False
                    should_act = self._reserve_action(author_id, threat.kind, now)
                    if should_act and threat.kind in {"DM_BURST", "DM_RAID"}:
                        self.stats["dm_floods"] += 1

            if should_alert_trusted:
                self._emit_event("TRUSTED_DETECTED", author=author, threat=threat)
            elif should_act:
                await self._apply_dm_action(message, threat)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            async with self._lock:
                self.stats["errors"] += 1
                self.last_error = f"message:{type(error).__name__}"

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # Edicoes e atualizacoes de embed devem ser verificadas, mas nunca contam
        # como uma nova mensagem para o anti-flood.
        await self.on_message(after, count_flood=False)

    async def on_relationship_add(self, relationship):
        if not self.enabled:
            return
        if getattr(relationship, "type", None) is not discord.RelationshipType.incoming_request:
            return

        author = getattr(relationship, "user", None)
        author_id = int(getattr(author, "id", 0) or 0)
        if not author_id or author_id in self.whitelist:
            return

        now = time.monotonic()
        async with self._lock:
            cutoff = now - self.config.FR_FLOOD_WINDOW
            self._prune(self._fr_requests, cutoff)
            recent_ids = {user_id for _, user_id in self._fr_requests}
            if author_id in recent_ids:
                return
            self._fr_requests.append((now, author_id))
            if len(self._fr_requests) < self.config.FR_FLOOD_MAX_COUNT:
                self._fr_flood_active = False
                return
            if not self._fr_flood_active:
                self.stats["fr_floods"] += 1
                self._fr_flood_active = True

        threat = ThreatVerdict("FR_FLOOD", "rajada de pedidos de amizade")
        actions: list[str] = []
        errors: list[str] = []
        try:
            await relationship.delete()
            actions.append("request_rejected")
        except discord.NotFound:
            actions.append("request_already_gone")
        except discord.HTTPException as error:
            errors.append(f"reject:{type(error).__name__}")
        except Exception as error:
            errors.append(f"reject:{type(error).__name__}")

        async with self._lock:
            self.stats["threats_detected"] += 1
            if actions:
                self.stats["actions_taken"] += 1
                self.stats["fr_rejected"] += 1
            if errors:
                self.stats["errors"] += len(errors)
                self.last_error = errors[-1]

        self._emit_event(
            "ACTION" if actions else "ACTION_FAILED",
            author=author,
            threat=threat,
            actions=actions,
            errors=errors,
        )
