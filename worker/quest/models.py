from __future__ import annotations

from datetime import datetime, timezone

from .constants import RECOGNIZED_TASKS


class Quest:
    def __init__(self, raw: dict):
        self.raw = raw
        self.id = str(raw.get("id", ""))
        self.config = raw.get("config", {})
        self.user_status = raw.get("user_status") or {}

    @property
    def application_id(self) -> str:
        return self.config.get("application", {}).get("id", "")

    @property
    def application_name(self) -> str:
        return self.config.get("application", {}).get("name", "Unknown")

    @property
    def quest_name(self) -> str:
        return self.config.get("messages", {}).get("quest_name", "Unknown")

    @property
    def task_config(self) -> dict:
        return self.config.get("task_config_v2") or self.config.get("task_config", {})

    @property
    def tasks(self) -> dict:
        return self.task_config.get("tasks", {})

    def get_task_info(self) -> tuple[str | None, dict | None]:
        for task_name in RECOGNIZED_TASKS:
            if task_name in self.tasks:
                return task_name, self.tasks[task_name]
        return None, None

    @property
    def is_mobile_video_only(self) -> bool:
        return bool(self.tasks.get("WATCH_VIDEO_ON_MOBILE")) and not bool(
            self.tasks.get("WATCH_VIDEO")
        )

    def get_progress(self, task_name: str) -> float:
        progress = self.user_status.get("progress") or {}
        if task_name in progress:
            try:
                return float(progress[task_name].get("value", 0))
            except Exception:
                pass

        if self.config.get("config_version") == 1:
            try:
                return float(self.user_status.get("stream_progress_seconds", 0))
            except Exception:
                pass

        return 0.0

    @property
    def is_expired(self) -> bool:
        expires_at = self.config.get("expires_at", "")
        if not expires_at:
            return False
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) > exp
        except Exception:
            return False

    @property
    def is_enrolled(self) -> bool:
        return bool(self.user_status.get("enrolled_at"))

    @property
    def is_completed(self) -> bool:
        return bool(self.user_status.get("completed_at"))

    @property
    def is_claimed(self) -> bool:
        return bool(self.user_status.get("claimed_at"))

    def update_status(self, data: dict):
        if isinstance(data, dict):
            self.user_status = data
            self.raw["user_status"] = data
