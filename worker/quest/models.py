from __future__ import annotations

import math
from datetime import datetime, timezone

from .constants import AUTO_COMPLETABLE_TASKS, RECOGNIZED_TASKS, VIDEO_TASKS


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _first(mapping: dict, snake: str, camel: str, default=None):
    if snake in mapping:
        return mapping[snake]
    return mapping.get(camel, default)


class Quest:
    STATUS_KEYS = frozenset(
        (
            "enrolled_at",
            "completed_at",
            "claimed_at",
            "progress",
            "stream_progress_seconds",
            "enrolledAt",
            "completedAt",
            "claimedAt",
            "streamProgressSeconds",
        )
    )

    def __init__(self, raw: dict):
        if not isinstance(raw, dict):
            raise TypeError("Quest payload must be a mapping")
        self.raw = raw
        self.id = str(raw.get("id", "")).strip()
        self.config = _mapping(raw.get("config"))
        self.user_status = _mapping(
            _first(raw, "user_status", "userStatus", {})
        ).copy()

    @property
    def application_id(self) -> str:
        task_name, task = self.get_task_info()
        return self.application_id_for(task_name, task)

    def application_id_for(self, task_name: str | None, task: dict | None) -> str:
        configured = str(_mapping(self.config.get("application")).get("id", ""))
        if configured:
            return configured
        applications = _mapping(task).get("applications")
        if isinstance(applications, list):
            for application in applications:
                app_id = str(_mapping(application).get("id", "")).strip()
                if app_id:
                    return app_id
        return ""

    @property
    def application_name(self) -> str:
        name = _mapping(self.config.get("application")).get("name")
        return str(name or "Aplicação desconhecida")

    @property
    def quest_name(self) -> str:
        messages = _mapping(self.config.get("messages"))
        name = _first(messages, "quest_name", "questName")
        return str(name or "Quest desconhecida")

    @property
    def task_config(self) -> dict:
        return _mapping(
            _first(self.config, "task_config_v2", "taskConfigV2")
            or _first(self.config, "task_config", "taskConfig", {})
        )

    @property
    def tasks(self) -> dict:
        return _mapping(self.task_config.get("tasks"))

    def _ordered_task_names(self, *, auto_only: bool) -> tuple[str, ...]:
        order = AUTO_COMPLETABLE_TASKS if auto_only else RECOGNIZED_TASKS

        # Questify prefers the task which most recently received progress.
        progress = _mapping(self.user_status.get("progress"))
        latest: list[tuple[float, str]] = []
        for fallback_name, raw_progress in progress.items():
            entry = _mapping(raw_progress)
            name = str(_first(entry, "event_name", "eventName", fallback_name))
            heartbeat = _mapping(entry.get("heartbeat"))
            timestamp = _first(heartbeat, "last_beat_at", "lastBeatAt")
            timestamp = timestamp or _first(entry, "updated_at", "updatedAt")
            try:
                parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                score = parsed.timestamp()
            except (TypeError, ValueError):
                score = 0.0
            latest.append((score, name))

        result: list[str] = []
        allowed = set(order)
        for _, name in sorted(latest, reverse=True):
            # Discord shares video progress between the desktop and mobile
            # variants.  A status payload can therefore name one variant while
            # the quest configuration exposes only the other one.
            if name in VIDEO_TASKS and not isinstance(self.tasks.get(name), dict):
                name = next(
                    (
                        candidate
                        for candidate in VIDEO_TASKS
                        if isinstance(self.tasks.get(candidate), dict)
                    ),
                    name,
                )
            if name in allowed and name not in result:
                result.append(name)
        result.extend(name for name in order if name not in result)
        return tuple(result)

    def get_task_info(
        self, *, auto_only: bool = False
    ) -> tuple[str | None, dict | None]:
        for task_name in self._ordered_task_names(auto_only=auto_only):
            task = self.tasks.get(task_name)
            if isinstance(task, dict):
                return task_name, task
        return None, None

    @property
    def is_mobile_video_only(self) -> bool:
        return isinstance(self.tasks.get("WATCH_VIDEO_ON_MOBILE"), dict) and not isinstance(
            self.tasks.get("WATCH_VIDEO"), dict
        )

    @staticmethod
    def task_target(task: dict | None) -> int:
        try:
            value = float(_mapping(task).get("target", 0))
        except (TypeError, ValueError):
            return 0
        if not math.isfinite(value) or value <= 0:
            return 0
        return int(value)

    def get_progress(self, task_name: str) -> float:
        progress = _mapping(self.user_status.get("progress"))
        names = VIDEO_TASKS if task_name in VIDEO_TASKS else (task_name,)
        values: list[float] = []
        for name in names:
            raw_value = progress.get(name, 0)
            value = _mapping(raw_value).get("value", raw_value)
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed):
                values.append(max(0.0, parsed))

        if values:
            return max(values)

        config_version = _first(self.config, "config_version", "configVersion")
        if config_version == 1:
            value = _first(
                self.user_status,
                "stream_progress_seconds",
                "streamProgressSeconds",
                0,
            )
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                pass
        return 0.0

    @property
    def expires_at(self) -> datetime | None:
        raw = _first(self.config, "expires_at", "expiresAt", "")
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @property
    def is_expired(self) -> bool:
        expires_at = self.expires_at
        return expires_at is not None and datetime.now(timezone.utc) > expires_at

    def _status_value(self, snake: str, camel: str):
        return _first(self.user_status, snake, camel)

    @property
    def is_enrolled(self) -> bool:
        return bool(self._status_value("enrolled_at", "enrolledAt"))

    @property
    def is_completed(self) -> bool:
        return bool(self._status_value("completed_at", "completedAt"))

    @property
    def is_claimed(self) -> bool:
        return bool(self._status_value("claimed_at", "claimedAt"))

    @property
    def enrolled_at(self) -> datetime | None:
        raw = self._status_value("enrolled_at", "enrolledAt")
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def update_status(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        nested = _first(data, "user_status", "userStatus")
        status = nested if isinstance(nested, dict) else data
        if not any(key in status for key in self.STATUS_KEYS):
            return False

        merged = self.user_status.copy()
        for key, value in status.items():
            if key == "progress" and isinstance(value, dict):
                current_progress = _mapping(merged.get("progress")).copy()
                current_progress.update(value)
                merged[key] = current_progress
            else:
                merged[key] = value
        self.user_status = merged
        self.raw["user_status"] = merged
        return True
