from __future__ import annotations

import asyncio
from collections import OrderedDict

import aiohttp

from tracker_protocol import normalize_target_ids
from ..core.ipc import emit_line
from .history import collect_group_history
from .manager import TrackerManager


class TrackerCollection:
    """One isolated tracker per ID, owned by one authenticated account."""

    def __init__(self, client):
        self.client = client
        self.trackers: dict[int, TrackerManager] = {}
        self._lock = asyncio.Lock()
        self._history_lock = asyncio.Lock()
        self._subscription_lock = asyncio.Lock()
        self._webhook_send_lock = asyncio.Lock()
        self._shared_msg_cache: OrderedDict[int, dict] = OrderedDict()
        self._shared_webhook_session: aiohttp.ClientSession | None = None
        self._epoch = 0
        self._history_tasks: set[asyncio.Task] = set()

    async def _get_shared_webhook_session(self) -> aiohttp.ClientSession:
        if self._shared_webhook_session is None or self._shared_webhook_session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._shared_webhook_session = aiohttp.ClientSession(timeout=timeout)
        return self._shared_webhook_session

    async def _close_shared_webhook_session(self) -> None:
        if self._shared_webhook_session is not None and not self._shared_webhook_session.closed:
            await self._shared_webhook_session.close()
        self._shared_webhook_session = None

    def _build_tracker(self) -> TrackerManager:
        tracker = TrackerManager(self.client)
        tracker._history_lock = self._history_lock
        tracker._subscription_lock = self._subscription_lock
        tracker._webhook_send_lock = self._webhook_send_lock
        tracker._msg_cache = self._shared_msg_cache
        tracker._shared_resources = True
        tracker._shared_session_getter = self._get_shared_webhook_session
        return tracker

    async def _shutdown_trackers(self, trackers) -> None:
        items = tuple(trackers)
        if not items:
            return
        await asyncio.gather(*(item.shutdown() for item in items), return_exceptions=True)

    async def start_tracking(self, target_ids, webhook_url: str) -> dict:
        try:
            target_ids = normalize_target_ids(target_ids)
        except (TypeError, ValueError) as error:
            return {"ok": False, "code": "INVALID_TARGET_ID", "message": str(error)}
        webhook_url = TrackerManager.normalize_webhook_url(webhook_url)
        if webhook_url is None:
            return {"ok": False, "code": "INVALID_WEBHOOK", "message": "URL de webhook inválida."}

        async with self._lock:
            self._epoch += 1
            epoch = self._epoch
            retained = {}
            to_start = []
            for target_id in target_ids:
                existing = self.trackers.get(target_id)
                if existing and existing.enabled and existing.webhook_url == webhook_url:
                    retained[target_id] = existing
                else:
                    to_start.append(target_id)

        staged: dict[int, TrackerManager] = {}
        try:
            for target_id in to_start:
                async with self._lock:
                    if epoch != self._epoch:
                        break
                tracker = self._build_tracker()
                staged[target_id] = tracker
                result = await tracker.start_tracking(target_id, webhook_url, own_history=False)
                if not result.get("ok"):
                    await self._shutdown_trackers(staged.values())
                    return result
        except BaseException:
            await self._shutdown_trackers(staged.values())
            raise

        obsolete: list[TrackerManager] = []
        discarded: list[TrackerManager] = []
        async with self._lock:
            if epoch != self._epoch:
                discarded.extend(staged.values())
            else:
                next_trackers: dict[int, TrackerManager] = {}
                for target_id in target_ids:
                    if target_id in retained:
                        next_trackers[target_id] = retained[target_id]
                    elif target_id in staged:
                        next_trackers[target_id] = staged[target_id]
                for tracker in self.trackers.values():
                    if tracker not in next_trackers.values():
                        obsolete.append(tracker)
                self.trackers = next_trackers

        await self._shutdown_trackers(obsolete)
        await self._shutdown_trackers(discarded)
        self._start_group_history([t for t in staged.values() if t in self.trackers.values() and t.enabled])
        status = self.get_status()
        if not status.get("enabled"):
            await self._close_shared_webhook_session()
            self._shared_msg_cache.clear()
            return {
                **status,
                "code": "STOPPED",
                "message": "Todos os trackers foram desativados.",
            }
        return {
            **status,
            "code": "STARTED",
            "message": f"Tracker ativo para {len(status.get('target_ids') or [])} usuário(s).",
        }

    def _start_group_history(self, trackers) -> None:
        """Uma unica varredura de historico para os alvos iniciados juntos."""
        if not trackers:
            return
        members = [(tracker, tracker._run_id) for tracker in trackers]
        task = asyncio.create_task(
            collect_group_history(self.client, members, self._history_lock, trackers[0].config),
            name="tracker-history-group",
        )
        self._history_tasks.add(task)
        task.add_done_callback(self._history_tasks.discard)

    async def _cancel_group_history(self) -> None:
        pending = [task for task in tuple(self._history_tasks) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def stop_tracking(self) -> dict:
        async with self._lock:
            self._epoch += 1
            trackers = list(self.trackers.values())
            self.trackers = {}
        await self._cancel_group_history()
        await self._shutdown_trackers(trackers)
        await self._close_shared_webhook_session()
        self._shared_msg_cache.clear()
        return {
            "ok": True,
            "code": "STOPPED",
            "enabled": False,
            "message": "Todos os trackers foram desativados.",
        }

    async def shutdown(self):
        await self.stop_tracking()

    def get_status(self) -> dict:
        statuses = [tracker.get_status() for tracker in self.trackers.values()]
        totals = {
            key: sum(int(status.get(key, 0)) for status in statuses)
            for key, value in TrackerManager._new_stats().items()
            if isinstance(value, int)
        }
        return {
            "ok": True,
            "code": "STATUS",
            "enabled": bool(statuses),
            "target_id": statuses[0]["target_id"] if len(statuses) == 1 else None,
            "target_ids": list(self.trackers),
            "targets": statuses,
            "shared_guilds": len(set().union(*(tracker.tracked_guild_ids for tracker in self.trackers.values()))),
            "queue_size": sum(status["queue_size"] for status in statuses),
            "last_error": next((status["last_error"] for status in statuses if status["last_error"]), None),
            **totals,
        }

    def __getattr__(self, name):
        # The worker's existing event hooks can fan out without sharing target state.
        if not name.startswith("on_") or not callable(getattr(TrackerManager, name, None)):
            raise AttributeError(name)

        async def dispatch(*args):
            for tracker in tuple(self.trackers.values()):
                try:
                    await getattr(tracker, name)(*args)
                except Exception as error:
                    tracker._stats["event_errors"] += 1
                    tracker._stats["last_error"] = "EVENT_ERROR"
                    emit_line(f"TRACKER:EVENT_ERROR:{name}:{type(error).__name__}")
            if name == "on_raw_message_delete" and args:
                message_id = getattr(args[0], "message_id", None)
                if message_id is not None:
                    self._shared_msg_cache.pop(message_id, None)

        return dispatch
