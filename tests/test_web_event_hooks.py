from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from engine.backend import SelfbotManager
from panel.main import create_web_app
from panel.web.routes.realtime import _ensure_worker_event_hooks

UID = 606
WEB_PREFIXES = ("EVENT:PROTECTION:", "EVENT:DM_", "EVENT:REL_", "EVENT:GUILD_", "TRACKER:", "PROCESS:")


class _FakeProcess:
    def __init__(self):
        self.is_alive = True
        self.username = "Alice#0001"
        self.callbacks: dict[str, list] = {}
        self.close = AsyncMock()

    def subscribe_event(self, prefix, callback):
        callbacks = self.callbacks.setdefault(prefix, [])
        if callback not in callbacks:
            callbacks.append(callback)

    def unsubscribe_event(self, prefix, callback):
        self.callbacks.get(prefix, []).remove(callback)


class WorkerEventHooksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = SelfbotManager()
        self.app = create_web_app(manager=self.manager, bot=None)
        self.addAsyncCleanup(self.manager.shutdown)

    def _connect(self) -> _FakeProcess:
        """O que `authenticate` faz com o processo novo, sem subir um worker."""
        proc = _FakeProcess()
        self.manager._session_tokens[UID] = "t" * 40
        self.manager.active_processes[UID] = proc
        self.manager._attach_event_subscriptions(UID, proc)
        return proc

    async def _disconnect_outside_web(self, how: str) -> None:
        if how == "disconnect":
            # Painel do Discord: a rota web /api/worker/disconnect não roda.
            await self.manager.disconnect(UID)
        else:
            # Supervisor descobre que o token foi revogado e esquece a sessão.
            await self.manager._cleanup_process(UID)
            self.manager._forget_user(UID)

    async def _assert_reconnect_rehooks(self, how: str) -> None:
        first = self._connect()
        _ensure_worker_event_hooks(self.app, self.manager, UID)
        self.assertEqual(sorted(first.callbacks), sorted(WEB_PREFIXES))

        await self._disconnect_outside_web(how)

        second = self._connect()
        _ensure_worker_event_hooks(self.app, self.manager, UID)
        self.assertEqual(sorted(second.callbacks), sorted(WEB_PREFIXES))

    async def test_reconnect_after_discord_panel_disconnect_reinstalls_live_event_hooks(self):
        await self._assert_reconnect_rehooks("disconnect")

    async def test_reconnect_after_revoked_token_reinstalls_live_event_hooks(self):
        await self._assert_reconnect_rehooks("forget_user")

    async def test_hooking_twice_does_not_duplicate_callbacks(self):
        proc = self._connect()
        _ensure_worker_event_hooks(self.app, self.manager, UID)
        _ensure_worker_event_hooks(self.app, self.manager, UID)
        self.assertEqual({prefix: len(cbs) for prefix, cbs in proc.callbacks.items()}, dict.fromkeys(WEB_PREFIXES, 1))


if __name__ == "__main__":
    unittest.main()
