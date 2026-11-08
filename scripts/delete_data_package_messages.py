"""Remove mensagens listadas em um pacote de dados do Discord.

O programa e separado do painel de controle de proposito. Por padrao ele apenas
le o pacote e mostra o plano. A exclusao exige ``--execute`` e uma confirmacao
interativa. DMs individuais sao fechadas; grupos e servidores permanecem
abertos. Tokens nunca sao gravados no disco.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import logging
import math
import os
import re
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKER_LIB = PROJECT_ROOT / "worker" / "lib"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from worker.core.pacing import MessageConfig


STATE_VERSION = 1
SNOWFLAKE = re.compile(r"^(?:c)?(?P<id>\d{15,20})$", re.IGNORECASE)
RETRY_AFTER_PATTERN = re.compile(r"Retrying in ([0-9]+(?:\.[0-9]+)?) seconds")
MAX_SNOWFLAKE = (1 << 64) - 1
MESSAGE_FILE_NAMES = {"messages.json", "message.json"}
MESSAGE_ID_KEYS = ("id", "message_id", "messageid", "message id")
CHANNEL_ID_KEYS = ("channel_id", "channelid", "channel id", "id")


class PackageError(ValueError):
    """O pacote de dados nao pode ser interpretado com seguranca."""


class ScanError(RuntimeError):
    """O historico de uma DM nao pode ser verificado por completo."""


class AbortCleanup(RuntimeError):
    """A execucao inteira deve parar, por exemplo ao receber um captcha."""


class DiscordRateLimitMonitor(logging.Handler):
    """Observa os 429 que discord.py-self aguarda internamente."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.count = 0
        self.last_retry_after = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("discord.http"):
            return
        try:
            message = record.getMessage()
        except Exception:
            return
        if "being rate limited" not in message.casefold():
            return
        match = RETRY_AFTER_PATTERN.search(message)
        self.count += 1
        self.last_retry_after = float(match.group(1)) if match else 1.0

    def snapshot(self) -> int:
        return self.count

    def since(self, snapshot: int) -> tuple[int, float]:
        return max(0, self.count - snapshot), self.last_retry_after


@dataclass(frozen=True)
class _SnowflakeBoundary:
    id: int


@dataclass(frozen=True)
class ChannelArchive:
    channel_id: int
    name: str
    message_ids: tuple[int, ...]
    kind: str = "unknown"
    sources: tuple[str, ...] = ()


@dataclass
class ChannelResult:
    channel_id: int
    status: str
    target_count: int
    deleted: int = 0
    already_absent: int = 0
    non_deletable: dict[str, int] = field(default_factory=dict)
    author_mismatches: int = 0
    remaining: int = 0
    failures: int = 0
    rate_limits: int = 0
    final_delay: float = 0.0
    closed: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target_count": self.target_count,
            "deleted": self.deleted,
            "already_absent": self.already_absent,
            "non_deletable": self.non_deletable,
            "author_mismatches": self.author_mismatches,
            "remaining": self.remaining,
            "failures": self.failures,
            "rate_limits": self.rate_limits,
            "final_delay": self.final_delay,
            "closed": self.closed,
            "detail": self.detail,
        }


def _normal_key(value: object) -> str:
    return str(value).strip().casefold().replace("-", "_")


def _mapping_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    normalized = {_normal_key(key): value for key, value in mapping.items()}
    for key in keys:
        candidate = normalized.get(_normal_key(key))
        if candidate is not None:
            return candidate
    return None


def _snowflake(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    if not text.isdigit() or not 15 <= len(text) <= 20:
        return None
    snowflake = int(text)
    return snowflake if snowflake <= MAX_SNOWFLAKE else None


def _channel_snowflake(value: object) -> int | None:
    match = SNOWFLAKE.fullmatch(str(value or "").strip())
    if not match:
        return None
    channel_id = int(match.group("id"))
    return channel_id if channel_id <= MAX_SNOWFLAKE else None


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PackageError(f"JSON invalido em {path}: {error}") from error


def _message_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        if any(not isinstance(item, dict) for item in data):
            raise PackageError("O transcript contem um registro que nao e objeto JSON.")
        return list(data)
    if not isinstance(data, dict):
        raise PackageError("O transcript deve ser uma lista ou objeto JSON.")

    nested = _mapping_value(data, ("messages", "items", "records"))
    if isinstance(nested, list):
        if any(not isinstance(item, dict) for item in nested):
            raise PackageError("O transcript contem um registro que nao e objeto JSON.")
        return list(nested)

    # Alguns exportadores representam as mensagens como {message_id: dados}.
    records: list[dict[str, Any]] = []
    for key, value in data.items():
        message_id = _snowflake(key)
        if message_id is None:
            continue
        if not isinstance(value, dict):
            raise PackageError("Uma mensagem indexada nao e um objeto JSON.")
        record = dict(value)
        record.setdefault("id", str(message_id))
        records.append(record)
    if data and not records:
        raise PackageError("Formato de transcript JSON nao reconhecido.")
    return records


def _message_ids(data: Any) -> set[int]:
    result: set[int] = set()
    for position, record in enumerate(_message_records(data), start=1):
        message_id = _snowflake(_mapping_value(record, MESSAGE_ID_KEYS))
        if message_id is None:
            raise PackageError(f"Mensagem na posicao {position} nao possui um ID valido.")
        result.add(message_id)
    return result


def _channel_id_from_parent(path: Path) -> int | None:
    match = SNOWFLAKE.fullmatch(path.parent.name)
    return int(match.group("id")) if match else None


def _channel_metadata(path: Path) -> dict[str, Any]:
    for name in ("channel.json", "metadata.json"):
        candidate = path.parent / name
        if candidate == path or not candidate.is_file():
            continue
        data = _load_json(candidate)
        if isinstance(data, dict):
            return data
    return {}


def _channel_kind(metadata: dict[str, Any]) -> str:
    raw_type = _mapping_value(
        metadata,
        ("type", "channel_type", "channeltype", "channel type"),
    )
    channel_type = str(raw_type if raw_type is not None else "").strip().upper()
    if channel_type in {"DM", "1"}:
        return "dm"
    if channel_type in {"GROUP_DM", "GROUP DM", "3"}:
        return "group_dm"

    guild = _mapping_value(metadata, ("guild", "guild_id", "guildid", "guild id"))
    if guild not in (None, "", {}):
        return "guild"
    if channel_type:
        return "guild"
    return "unknown"


def load_package_owner(root: Path) -> int | None:
    """Retorna o ID da conta em account/user.json, quando ele existe."""

    if not root.is_dir():
        return None
    matches = [path for path in root.rglob("user.json") if path.parent.name.casefold() == "account"]
    if not matches:
        return None
    owner_file = min(matches, key=lambda path: len(path.parts))
    data = _load_json(owner_file)
    if not isinstance(data, dict):
        raise PackageError(f"Identidade do pacote invalida em {owner_file}")
    owner_id = _snowflake(_mapping_value(data, ("id", "user_id", "user id")))
    if owner_id is None:
        raise PackageError(f"Identidade do pacote invalida em {owner_file}")
    return owner_id


def _index_names(root: Path) -> dict[int, str]:
    names: dict[int, str] = {}
    for path in root.rglob("index.json"):
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        for raw_id, value in data.items():
            channel_id = _channel_snowflake(raw_id)
            if channel_id is None:
                continue
            if isinstance(value, str):
                names[channel_id] = value
            elif isinstance(value, dict):
                name = _mapping_value(value, ("name", "channel_name", "channelname"))
                if name:
                    names[channel_id] = str(name)
    return names


def _transcript_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*.json"):
        lowered = path.name.casefold()
        if lowered in MESSAGE_FILE_NAMES or lowered.startswith("messages_") or lowered.startswith("messages-"):
            candidates.append(path)
    return sorted(candidates)


def load_archives(root: Path, *, channel_id: int | None = None) -> list[ChannelArchive]:
    """Le um JSON individual ou uma arvore extraida do pacote do Discord."""

    root = root.resolve()
    names: dict[int, str] = {}
    if root.is_file():
        if root.suffix.casefold() != ".json":
            raise PackageError("O arquivo informado deve ser JSON ou ZIP.")
        transcripts = [root]
    elif root.is_dir():
        names = _index_names(root)
        transcripts = _transcript_files(root)
    else:
        raise PackageError(f"Entrada nao encontrada: {root}")

    if not transcripts:
        raise PackageError("Nenhum messages.json foi encontrado no pacote.")

    grouped: dict[int, set[int]] = {}
    sources: dict[int, list[str]] = {}
    kinds: dict[int, str] = {}
    discovered_names = dict(names)

    for transcript in transcripts:
        data = _load_json(transcript)
        ids = _message_ids(data)
        if not ids:
            continue

        metadata = _channel_metadata(transcript)
        discovered_id = channel_id if len(transcripts) == 1 else None
        if discovered_id is None:
            discovered_id = _channel_snowflake(_mapping_value(metadata, CHANNEL_ID_KEYS))
        if discovered_id is None:
            discovered_id = _channel_id_from_parent(transcript)
        if discovered_id is None:
            raise PackageError(
                f"Nao foi possivel descobrir o channel_id de {transcript}. "
                "Use --channel-id ao fornecer um JSON individual."
            )

        metadata_name = _mapping_value(metadata, ("name", "channel_name", "channelname"))
        if metadata_name:
            discovered_names.setdefault(discovered_id, str(metadata_name))
        discovered_kind = _channel_kind(metadata)
        previous_kind = kinds.get(discovered_id, "unknown")
        if previous_kind == "unknown":
            kinds[discovered_id] = discovered_kind
        elif discovered_kind not in {"unknown", previous_kind}:
            kinds[discovered_id] = "unknown"
        grouped.setdefault(discovered_id, set()).update(ids)
        sources.setdefault(discovered_id, []).append(str(transcript))

    if not grouped:
        raise PackageError("Os JSONs encontrados nao contem IDs de mensagens validos.")

    return [
        ChannelArchive(
            channel_id=current_id,
            name=discovered_names.get(current_id, f"DM {current_id}"),
            message_ids=tuple(sorted(message_ids, reverse=True)),
            kind=kinds.get(current_id, "unknown"),
            sources=tuple(sources[current_id]),
        )
        for current_id, message_ids in sorted(grouped.items())
    ]


def source_fingerprint(path: Path) -> str:
    resolved = path.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8"))

    if resolved.is_file():
        stat = resolved.stat()
        digest.update(f"|{stat.st_size}|{stat.st_mtime_ns}".encode("ascii"))
        return digest.hexdigest()

    if not resolved.is_dir():
        resolved.stat()  # Levanta FileNotFoundError com a entrada original.

    relevant_names = {*MESSAGE_FILE_NAMES, "channel.json", "metadata.json", "index.json"}
    relevant = []
    for candidate in resolved.rglob("*.json"):
        lowered = candidate.name.casefold()
        if (
            lowered in relevant_names
            or lowered.startswith("messages_")
            or lowered.startswith("messages-")
            or (lowered == "user.json" and candidate.parent.name.casefold() == "account")
        ):
            relevant.append(candidate)

    for candidate in sorted(relevant):
        stat = candidate.stat()
        relative = candidate.relative_to(resolved)
        digest.update(f"|{relative}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8"))
    return digest.hexdigest()


class Checkpoint:
    def __init__(self, path: Path, fingerprint: str):
        self.path = path
        self.fingerprint = fingerprint
        self.data: dict[str, Any] = {
            "version": STATE_VERSION,
            "source_fingerprint": fingerprint,
            "channels": {},
        }
        if path.exists():
            loaded = _load_json(path)
            if not isinstance(loaded, dict):
                raise PackageError(f"Checkpoint invalido: {path}")
            if loaded.get("version") != STATE_VERSION:
                raise PackageError("Versao do checkpoint nao suportada.")
            if loaded.get("source_fingerprint") != fingerprint:
                raise PackageError("O checkpoint pertence a outro pacote. Use outro --state.")
            if not isinstance(loaded.get("channels"), dict):
                raise PackageError(f"Checkpoint invalido: {path}")
            saved_user_id = loaded.get("user_id")
            if saved_user_id is not None and _snowflake(saved_user_id) is None:
                raise PackageError(f"Checkpoint invalido: {path}")
            self.data = loaded

    def is_finished(self, channel_id: int) -> bool:
        entry = self.data.get("channels", {}).get(str(channel_id), {})
        return entry.get("status") in {
            "complete",
            "complete_with_skips",
        }

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def bind_user(self, user_id: int) -> None:
        current_user_id = _snowflake(user_id)
        if current_user_id is None:
            raise PackageError("ID da conta conectada invalido.")
        saved_user_id = self.data.get("user_id")
        if saved_user_id is not None and _snowflake(saved_user_id) != current_user_id:
            raise PackageError("O checkpoint pertence a outra conta Discord. Use outro --state.")
        self.data["user_id"] = current_user_id
        self._write()

    def record(self, result: ChannelResult) -> None:
        self.data.setdefault("channels", {})[str(result.channel_id)] = result.to_dict()
        self._write()


def _is_relevant_zip_member(member: zipfile.ZipInfo) -> bool:
    if member.is_dir():
        return False
    parts = [part.casefold() for part in re.split(r"[\\/]", member.filename) if part]
    lowered = parts[-1] if parts else ""
    return (
        lowered in {*MESSAGE_FILE_NAMES, "channel.json", "metadata.json", "index.json"}
        or lowered.startswith("messages_")
        or lowered.startswith("messages-")
        or (lowered == "user.json" and len(parts) >= 2 and parts[-2] == "account")
    )


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        selected: list[zipfile.ZipInfo] = []
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(destination)
            except ValueError as error:
                raise PackageError(f"Caminho inseguro dentro do ZIP: {member.filename}") from error
            if _is_relevant_zip_member(member):
                selected.append(member)

        for member in selected:
            package.extract(member, destination)


@contextmanager
def prepared_input(path: Path) -> Iterator[Path]:
    path = path.resolve()
    if path.suffix.casefold() != ".zip":
        yield path
        return

    with tempfile.TemporaryDirectory(prefix="madman-data-package-") as temp:
        destination = Path(temp)
        try:
            _safe_extract(path, destination)
        except (OSError, zipfile.BadZipFile) as error:
            raise PackageError(f"ZIP invalido: {error}") from error
        yield destination


def _message_type_name(message: Any) -> str:
    message_type = getattr(message, "type", None)
    return str(getattr(message_type, "name", message_type or "unknown"))


def _is_deletable(message: Any) -> bool:
    check = getattr(getattr(message, "type", None), "is_deletable", None)
    return bool(check()) if callable(check) else True


async def _scan_targets(channel: Any, target_ids: set[int]) -> dict[int, Any]:
    found: dict[int, Any] = {}
    if not target_ids:
        return found

    oldest_target = min(target_ids)
    newest_boundary = _SnowflakeBoundary(max(target_ids) + 1)
    scanned = 0
    try:
        async for message in channel.history(
            limit=None,
            before=newest_boundary,
            oldest_first=False,
        ):
            scanned += 1
            message_id = int(getattr(message, "id", 0) or 0)
            if message_id < oldest_target:
                break
            if message_id in target_ids:
                found[message_id] = message
                if len(found) == len(target_ids):
                    break
            if scanned % 1000 == 0:
                print(f"Historico verificado: {scanned} mensagens; IDs encontrados: {len(found)}/{len(target_ids)}")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise ScanError(f"Falha ao percorrer o historico: {type(error).__name__}") from error
    return found


@dataclass
class _VerificationOutcome:
    """O que a última leitura do histórico encontrou e o que as rodadas apagaram."""

    last_found: dict[int, Any] = field(default_factory=dict)
    non_deletable: dict[int, str] = field(default_factory=dict)
    deleted_ids: set[int] = field(default_factory=set)
    absent_ids: set[int] = field(default_factory=set)
    mismatch_ids: set[int] = field(default_factory=set)
    failures: int = 0
    rate_limits: int = 0


def _complete_result(result: ChannelResult, target_kind: str, type_counts: dict[str, int]) -> None:
    destination = {
        "dm": "DM fechada",
        "group_dm": "grupo mantido aberto",
        "guild": "canal do servidor mantido aberto",
    }[target_kind]
    if type_counts:
        skipped = ", ".join(f"{message_type}={count}" for message_type, count in sorted(type_counts.items()))
        result.status = "complete_with_skips"
        result.detail = (
            "Todos os IDs apagaveis foram confirmados como ausentes; "
            f"eventos nao apagaveis ignorados ({skipped}); {destination}."
        )
    else:
        result.status = "complete"
        result.detail = f"Todos os IDs foram confirmados como ausentes; {destination}."


def _failed_result(archive: ChannelArchive, status: str, detail: str) -> ChannelResult:
    """Resultado de um canal que nem chegou a ser limpo: todos os IDs continuam pendentes."""
    return ChannelResult(
        channel_id=archive.channel_id,
        status=status,
        target_count=len(archive.message_ids),
        remaining=len(archive.message_ids),
        detail=detail,
    )


def _print_result(result: ChannelResult) -> None:
    print(
        f"{result.status.upper()}: apagadas={result.deleted}, "
        f"ja_ausentes={result.already_absent}, restantes={result.remaining}, "
        f"nao_apagaveis={sum(result.non_deletable.values())}, "
        f"autoria_divergente={result.author_mismatches}, fechada={result.closed}"
    )
    if result.rate_limits:
        print(f"Rate limits observados={result.rate_limits}; intervalo final={result.final_delay:.2f}s")
    if result.non_deletable:
        print(
            "Tipos nao apagaveis: "
            + ", ".join(f"{message_type}={count}" for message_type, count in sorted(result.non_deletable.items()))
        )
    if result.detail:
        print(result.detail)


class DataPackageCleaner:
    def __init__(
        self,
        discord_module: Any,
        *,
        delay: float = MessageConfig.BASE_DELAY,
        verification_attempts: int = 2,
        rate_limit_monitor: DiscordRateLimitMonitor | None = None,
    ):
        self.discord = discord_module
        self.base_delay = max(0.0, delay)
        self.current_delay = self.base_delay
        self.max_delay = max(self.base_delay, MessageConfig.MAX_DELAY)
        self.verification_attempts = max(1, verification_attempts)
        self.rate_limit_monitor = rate_limit_monitor
        self.consecutive_rate_limited_deletes = 0

    def _rate_limit_snapshot(self) -> int:
        if self.rate_limit_monitor is None:
            return 0
        return self.rate_limit_monitor.snapshot()

    def _observed_rate_limits(self, snapshot: int) -> tuple[int, float]:
        if self.rate_limit_monitor is None:
            return 0, 0.0
        return self.rate_limit_monitor.since(snapshot)

    def _apply_rate_limit_backoff(self, events: int, retry_after: float) -> None:
        if events <= 0:
            self.consecutive_rate_limited_deletes = 0
            return

        self.consecutive_rate_limited_deletes += 1
        previous_delay = self.current_delay
        self.current_delay = min(
            self.max_delay,
            max(
                self.base_delay,
                previous_delay * MessageConfig.BACKOFF_MULTIPLIER,
                retry_after + 0.25,
            ),
        )
        print(f"Ritmo ajustado apos rate limit: {previous_delay:.2f}s -> {self.current_delay:.2f}s.")
        if self.consecutive_rate_limited_deletes >= MessageConfig.MAX_429_CONSECUTIVE:
            raise AbortCleanup("Rate limit persistente; execucao interrompida para proteger a conta.")

    async def _delete_round(
        self,
        messages: list[Any],
        *,
        deleted_ids: set[int],
        absent_ids: set[int],
    ) -> tuple[int, int]:
        failures = 0
        rate_limits = 0
        for index, message in enumerate(messages, start=1):
            monitor_snapshot = self._rate_limit_snapshot()
            explicit_rate_limit = False
            explicit_retry_after = 0.0
            try:
                await message.delete()
                deleted_ids.add(int(message.id))
            except self.discord.NotFound:
                absent_ids.add(int(message.id))
            except self.discord.CaptchaRequired as error:
                raise AbortCleanup("Captcha solicitado; execucao interrompida.") from error
            except self.discord.RateLimited as error:
                if getattr(error, "cloudflare", False):
                    raise AbortCleanup("Rate limit global/Cloudflare; execucao interrompida.") from error
                explicit_rate_limit = True
                explicit_retry_after = max(float(getattr(error, "retry_after", 1.0)), 1.0)
                print(f"Rate limit exposto: aguardando {explicit_retry_after:.1f}s.")
                await asyncio.sleep(explicit_retry_after)
            except self.discord.Forbidden:
                failures += 1
            except self.discord.HTTPException as error:
                status = int(getattr(error, "status", 0) or 0)
                if status == 429:
                    explicit_rate_limit = True
                    retry_after = getattr(error, "retry_after", None)
                    if retry_after is None:
                        retry_after = getattr(error, "json", {}).get("retry_after", 5.0)
                    explicit_retry_after = max(float(retry_after or 5.0), 1.0)
                    print(f"Rate limit HTTP exposto: aguardando {explicit_retry_after:.1f}s.")
                    await asyncio.sleep(explicit_retry_after)
                else:
                    failures += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1

            observed_events, observed_retry_after = self._observed_rate_limits(monitor_snapshot)
            if explicit_rate_limit:
                observed_events = max(1, observed_events)
                observed_retry_after = max(observed_retry_after, explicit_retry_after)
                failures += 1
            rate_limits += observed_events
            self._apply_rate_limit_backoff(
                observed_events,
                observed_retry_after,
            )

            if self.current_delay:
                await asyncio.sleep(self.current_delay)
            if index == 1 or index % 25 == 0 or index == len(messages):
                print(f"Exclusao nesta rodada: {index}/{len(messages)} (intervalo {self.current_delay:.2f}s)")
        return failures, rate_limits

    async def clean_channel(self, client: Any, archive: ChannelArchive) -> ChannelResult:
        result = ChannelResult(
            channel_id=archive.channel_id,
            status="error",
            target_count=len(archive.message_ids),
        )
        opened = await self._open_channel(client, archive, result)
        if opened is None:
            return result
        channel, partial_dm = opened

        target_kind = self._effective_kind(archive, channel, partial_dm)
        if target_kind is None:
            result.status = "skipped_unsupported"
            result.detail = f"Tipo do pacote/API incompativel ou sem historico: {archive.kind}/{type(channel).__name__}"
            result.remaining = result.target_count
            return result

        outcome = await self._verify_and_delete(client, channel, archive)
        non_deletable_ids = set(outcome.non_deletable)
        unresolved_ids = set(outcome.last_found) - non_deletable_ids
        type_counts = dict(Counter(outcome.non_deletable.values()))
        result.failures = outcome.failures
        result.rate_limits = outcome.rate_limits

        result.deleted = len(outcome.deleted_ids)
        result.already_absent = len(outcome.absent_ids - outcome.deleted_ids)
        result.non_deletable = type_counts
        result.author_mismatches = len(outcome.mismatch_ids)
        # Eventos de sistema que a propria biblioteca declara nao apagaveis
        # permanecem visiveis na API, mas nao sao falhas recuperaveis. Somente
        # mensagens apagaveis ou de autoria divergente bloqueiam o canal.
        result.remaining = len(unresolved_ids)
        result.final_delay = self.current_delay

        if unresolved_ids:
            result.status = "partial"
            result.detail = "Ainda existem IDs apagaveis ou de autoria divergente no canal."
            return result

        if target_kind == "dm":
            if not await self._close_dm(client, channel, archive, partial_dm, result):
                return result
            result.closed = True

        _complete_result(result, target_kind, type_counts)
        return result

    async def _open_channel(
        self, client: Any, archive: ChannelArchive, result: ChannelResult
    ) -> tuple[Any, bool] | None:
        """Abre o canal do arquivo; devolve `(canal, dm_parcial)` ou None depois de preencher `result`."""
        try:
            channel = client.get_channel(archive.channel_id)
            if channel is None:
                channel = await client.fetch_channel(archive.channel_id)
            return channel, False
        except self.discord.CaptchaRequired as error:
            raise AbortCleanup("Captcha solicitado ao abrir o canal.") from error
        except self.discord.NotFound:
            if archive.kind == "dm":
                channel = client.get_partial_messageable(
                    archive.channel_id,
                    type=self.discord.ChannelType.private,
                )
                return channel, True
            result.status = "inaccessible"
            result.detail = "Canal nao encontrado ou conta sem acesso."
        except (
            self.discord.Forbidden,
            self.discord.RateLimited,
            self.discord.HTTPException,
        ) as error:
            result.status = "inaccessible"
            result.detail = f"Falha ao abrir o canal: {type(error).__name__}"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result.status = "inaccessible"
            result.detail = f"Falha inesperada ao abrir o canal: {type(error).__name__}"
        result.remaining = result.target_count
        return None

    def _effective_kind(self, archive: ChannelArchive, channel: Any, partial_dm: bool) -> str | None:
        """O tipo do canal confirmado pela API, ou None se não bate com o pacote ou não tem histórico."""
        group_channel_type = getattr(self.discord, "GroupChannel", None)
        is_dm = partial_dm or isinstance(channel, self.discord.DMChannel)
        is_group = bool(group_channel_type is not None and isinstance(channel, group_channel_type))
        has_history = callable(getattr(channel, "history", None))

        target_kind = archive.kind
        if target_kind == "unknown":
            if is_dm:
                target_kind = "dm"
            elif is_group:
                target_kind = "group_dm"
            elif has_history:
                target_kind = "guild"

        kind_matches = {
            "dm": is_dm,
            "group_dm": is_group,
            "guild": has_history and not is_dm and not is_group,
        }
        return target_kind if kind_matches.get(target_kind, False) else None

    async def _verify_and_delete(self, client: Any, channel: Any, archive: ChannelArchive) -> _VerificationOutcome:
        """Rodadas de leitura e exclusão até a verificação final."""
        target_ids = set(archive.message_ids)
        outcome = _VerificationOutcome()

        # Cada rodada comeca com uma leitura completa do historico. Assim, a
        # autoria e o tipo sao validados na resposta atual da API, nao no arquivo.
        for attempt in range(self.verification_attempts + 1):
            print(f"Verificacao {attempt + 1}/{self.verification_attempts + 1} dos IDs do pacote.")
            outcome.last_found = await _scan_targets(channel, target_ids)
            candidates: list[Any] = []
            outcome.mismatch_ids.clear()
            outcome.non_deletable.clear()

            for message_id, message in outcome.last_found.items():
                author_id = int(getattr(getattr(message, "author", None), "id", 0) or 0)
                if author_id != int(client.user.id):
                    outcome.mismatch_ids.add(message_id)
                    continue
                if not _is_deletable(message):
                    outcome.non_deletable[message_id] = _message_type_name(message)
                    continue
                candidates.append(message)

            outcome.absent_ids.update(target_ids - set(outcome.last_found))
            if not candidates or attempt >= self.verification_attempts:
                break

            round_failures, round_rate_limits = await self._delete_round(
                candidates,
                deleted_ids=outcome.deleted_ids,
                absent_ids=outcome.absent_ids,
            )
            outcome.failures += round_failures
            outcome.rate_limits += round_rate_limits
        return outcome

    async def _close_dm(
        self, client: Any, channel: Any, archive: ChannelArchive, partial_dm: bool, result: ChannelResult
    ) -> bool:
        """Fecha a DM limpa; devolve False depois de registrar a falha em `result`."""
        try:
            if partial_dm:
                await client.http.delete_channel(
                    archive.channel_id,
                    silent=False,
                )
            else:
                await channel.close()
        except self.discord.CaptchaRequired as error:
            raise AbortCleanup("Captcha solicitado ao fechar a DM.") from error
        except self.discord.NotFound:
            pass
        except (
            self.discord.Forbidden,
            self.discord.RateLimited,
            self.discord.HTTPException,
        ) as error:
            result.status = "messages_complete_close_failed"
            result.detail = f"Mensagens removidas, mas a DM nao fechou: {type(error).__name__}"
            return False
        except asyncio.CancelledError:
            raise
        except Exception as error:
            result.status = "messages_complete_close_failed"
            result.detail = f"Mensagens removidas, mas houve uma falha inesperada ao fechar: {type(error).__name__}"
            return False
        return True

    async def run(
        self,
        client: Any,
        archives: list[ChannelArchive],
        checkpoint: Checkpoint,
        *,
        package_owner_id: int | None = None,
    ) -> int:
        open_ids = {
            int(channel.id)
            for channel in getattr(client, "private_channels", [])
            if isinstance(channel, self.discord.DMChannel)
        }
        if package_owner_id is not None and int(client.user.id) != package_owner_id:
            print("ERRO: a conta conectada nao corresponde ao account/user.json do pacote.")
            return 2
        try:
            checkpoint.bind_user(int(client.user.id))
        except (OSError, PackageError) as error:
            print(f"ERRO: {error}")
            return 2

        pending = [item for item in archives if not checkpoint.is_finished(item.channel_id)]
        pending.sort(key=lambda item: (item.channel_id not in open_ids, item.channel_id))

        print(f"Conectado como {client.user} ({client.user.id}).")
        print(f"Conversas pendentes: {len(pending)}")

        non_dm_failures = 0
        for index, archive in enumerate(pending, start=1):
            print(f"\n[{index}/{len(pending)}] {archive.name} ({archive.channel_id}) - {len(archive.message_ids)} IDs")
            try:
                result = await self.clean_channel(client, archive)
            except AbortCleanup as error:
                try:
                    checkpoint.record(_failed_result(archive, "aborted", str(error)))
                except OSError as checkpoint_error:
                    print(f"ERRO AO SALVAR CHECKPOINT: {checkpoint_error}")
                print(f"ABORTADO: {error}")
                return 2
            except ScanError as error:
                result = _failed_result(archive, "scan_failed", str(error))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                result = _failed_result(archive, "error", f"Falha inesperada: {type(error).__name__}")

            try:
                checkpoint.record(result)
            except OSError as error:
                print(f"ERRO AO SALVAR CHECKPOINT: {error}")
                print("Execucao parada antes da proxima conversa.")
                return 2
            _print_result(result)

            # DMs individuais so liberam a proxima conversa depois de fechadas.
            # Grupos e servidores permanecem abertos depois da verificacao.
            if result.status not in {
                "complete",
                "complete_with_skips",
            }:
                if archive.kind in {"guild", "group_dm"}:
                    non_dm_failures += 1
                    print("Canal mantido pendente; a execucao continuara nas outras conversas.")
                    continue
                print("Execucao parada neste canal. Corrija o problema e rode novamente.")
                return 1

        if non_dm_failures:
            print(f"\nExecucao finalizada com {non_dm_failures} canal(is) de grupo ou servidor ainda pendente(s).")
            return 1
        return 0


def _load_discord() -> Any:
    if WORKER_LIB.is_dir():
        sys.path.insert(0, str(WORKER_LIB))
    try:
        import discord
    except ImportError as error:
        raise SystemExit("discord.py-self nao encontrado. Ative o venv e execute python setup_envs.py.") from error

    if not hasattr(discord, "CaptchaRequired") or not hasattr(discord.Client, "fetch_private_channels"):
        raise SystemExit(
            "O pacote 'discord' carregado nao e discord.py-self. Execute o setup_envs.py antes deste programa."
        )
    return discord


def _default_state_path(source: Path) -> Path:
    if source.is_dir():
        return source / ".madman-data-cleanup-state.json"
    return source.with_name(f".{source.stem}-cleanup-state.json")


def _channel_id_argument(value: str) -> int:
    channel_id = _channel_snowflake(value)
    if channel_id is None:
        raise argparse.ArgumentTypeError("channel ID invalido")
    return channel_id


def _nonnegative_delay(value: str) -> float:
    try:
        delay = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("o delay deve ser numerico") from error
    if not math.isfinite(delay) or delay < 0:
        raise argparse.ArgumentTypeError("o delay deve ser finito e maior ou igual a zero")
    return delay


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("o valor deve ser inteiro") from error
    if number < 1:
        raise argparse.ArgumentTypeError("o valor deve ser maior ou igual a 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Apaga e verifica mensagens listadas no pacote de dados do Discord; fecha apenas DMs individuais.")
    )
    parser.add_argument("input", type=Path, help="ZIP, pasta extraida ou messages.json")
    parser.add_argument(
        "--channel-id",
        type=_channel_id_argument,
        help="Channel ID obrigatorio quando um JSON individual nao possui metadata.",
    )
    parser.add_argument(
        "--state",
        type=Path,
        help="Checkpoint para retomada (padrao: ao lado da entrada).",
    )
    parser.add_argument(
        "--delay",
        type=_nonnegative_delay,
        default=MessageConfig.BASE_DELAY,
        help=(
            "Pausa base por exclusao; aumenta automaticamente em 429 "
            f"(padrao compartilhado com a limpeza principal: {MessageConfig.BASE_DELAY}s)."
        ),
    )
    parser.add_argument(
        "--verification-attempts",
        type=_positive_int,
        default=2,
        help="Rodadas de exclusao antes da verificacao final (padrao: 2).",
    )
    parser.add_argument(
        "--token-env",
        default="DISCORD_USER_TOKEN",
        help="Variavel opcional que contem o token; se vazia, solicita sem eco.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Executa exclusoes. Sem esta opcao, apenas mostra o plano.",
    )
    return parser


def _print_plan(
    archives: list[ChannelArchive],
    *,
    package_owner_id: int | None = None,
) -> None:
    total = sum(len(item.message_ids) for item in archives)
    print(f"Conversas encontradas: {len(archives)}")
    print(f"IDs unicos de mensagens: {total}")
    if package_owner_id is not None:
        print(f"Conta identificada no pacote: {package_owner_id}")
    for archive in archives:
        print(f"- {archive.channel_id}: {len(archive.message_ids)} IDs [{archive.kind}] ({archive.name})")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.input.resolve()
    try:
        fingerprint = source_fingerprint(source)
        with prepared_input(source) as prepared:
            archives = load_archives(prepared, channel_id=args.channel_id)
            package_owner_id = load_package_owner(prepared)
    except (OSError, PackageError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2

    _print_plan(archives, package_owner_id=package_owner_id)
    if not args.execute:
        print("\nSimulacao concluida. Nenhuma conta foi acessada e nada foi apagado.")
        print("Use --execute somente depois de conferir os totais.")
        return 0

    if not _confirm_execution(sum(len(item.message_ids) for item in archives), args.delay):
        print("Confirmacao incorreta. Nada foi apagado.")
        return 1

    state_path = (args.state or _default_state_path(source)).resolve()
    try:
        checkpoint = Checkpoint(state_path, fingerprint)
    except (OSError, PackageError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 2

    token = _read_token(args.token_env)
    if not token:
        print("Token vazio. Nada foi apagado.", file=sys.stderr)
        return 2

    discord = _load_discord()
    rate_limit_monitor = DiscordRateLimitMonitor()
    cleaner = DataPackageCleaner(
        discord,
        delay=args.delay,
        verification_attempts=args.verification_attempts,
        rate_limit_monitor=rate_limit_monitor,
    )
    return _run_client(cleaner, archives, checkpoint, package_owner_id, token)


def _confirm_execution(total: int, delay: float) -> bool:
    """Mostra os avisos da execução real e pede a frase de confirmação com o total de IDs."""
    print("\nATENCAO: esta operacao e permanente.")
    print("DMs individuais serao fechadas apos a limpeza; grupos e servidores permanecerao abertos.")
    print(f"Ritmo: {delay:.2f}s por exclusao.")
    if delay < MessageConfig.BASE_DELAY:
        print(
            f"AVISO: {delay:.2f}s e menor que o perfil seguro de "
            f"{MessageConfig.BASE_DELAY:.2f}s e pode gerar mais rate limits."
        )
    expected = f"APAGAR {total}"
    try:
        confirmation = input(f"Digite '{expected}' para continuar: ").strip()
    except EOFError:
        confirmation = ""
    return confirmation == expected


def _read_token(token_env: str) -> str:
    """O token da variável de ambiente ou, se vazia, digitado sem eco; nunca é salvo."""
    token = os.environ.get(token_env, "").strip()
    if token:
        return token
    try:
        return getpass.getpass("Token da conta Discord (nao sera salvo): ").strip()
    except EOFError:
        return ""


def _run_client(
    cleaner: DataPackageCleaner,
    archives: list[ChannelArchive],
    checkpoint: Checkpoint,
    package_owner_id: int | None,
    token: str,
) -> int:
    """Conecta a conta, roda a limpeza uma única vez no `on_ready` e traduz falhas de login em código de saída."""
    discord = cleaner.discord
    rate_limit_monitor = cleaner.rate_limit_monitor

    class CleanupClient(discord.Client):
        def __init__(self):
            super().__init__()
            self.cleanup_started = False
            self.exit_code = 2

        async def on_ready(self):
            if self.cleanup_started:
                return
            self.cleanup_started = True
            try:
                self.exit_code = await cleaner.run(
                    self,
                    archives,
                    checkpoint,
                    package_owner_id=package_owner_id,
                )
            finally:
                await self.close()

    client = CleanupClient()
    discord_http_logger = logging.getLogger("discord.http")
    discord_http_logger.addHandler(rate_limit_monitor)
    try:
        client.run(token)
    except KeyboardInterrupt:
        print("\nInterrompido. O checkpoint permite retomar depois.")
        return 130
    except discord.LoginFailure:
        print("Falha no login: token rejeitado pelo Discord.", file=sys.stderr)
        return 2
    except discord.CaptchaRequired:
        print("Captcha solicitado durante o login; nada foi iniciado.", file=sys.stderr)
        return 2
    except discord.HTTPException as error:
        print(f"Falha HTTP durante a conexao: {type(error).__name__}", file=sys.stderr)
        return 2
    finally:
        discord_http_logger.removeHandler(rate_limit_monitor)
    return client.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
