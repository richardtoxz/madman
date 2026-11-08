"""Golden do backend: leitura do stream de bulk (`_run_bulk_stream`) e supervisão do worker.

python -m tests.test_backend_characterization --update
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from engine.backend import SelfbotManager
from tests import characterization

NAME = "backend"
USER = 1


class _StreamProc:
    is_alive = True

    def __init__(self, lines):
        self.lines = list(lines)

    async def send_and_stream(self, command, prefixes, timeout):
        for line in self.lines:
            yield line


async def _stream(lines, *, prefixes=("REL_",), raises=False, with_process=True):
    recorder = characterization.Recorder()
    manager = SelfbotManager()
    if with_process:
        manager.active_processes[USER] = _StreamProc(lines)

    async def callback(current, total, status, name):
        recorder.note("cb", current, total, status, name)
        if raises:
            raise RuntimeError("callback")

    try:
        result = await manager._run_bulk_stream(USER, "CMD", list(prefixes), callback)
    except Exception as error:
        return {"log": recorder.log, "raised": type(error).__name__}
    return {"log": recorder.log, "result": result}


def _single(line):
    """Uma linha isolada com o prefixo `REL_`, seguida de um resultado se ela não encerra."""
    text = line if line.startswith("REL_") else f"REL_{line}"
    return _stream([text, "REL_RESULT:fim"])


class _Process:
    def __init__(self, on_wait=None):
        self.on_wait = on_wait

    async def wait(self):
        if self.on_wait:
            self.on_wait()


class _WorkerProc:
    def __init__(self, recorder, name, on_wait=None):
        self.recorder = recorder
        self.name = name
        self.is_alive = True

        def died():
            self.is_alive = False
            recorder.note("exit", name)
            if on_wait:
                on_wait()

        self.process = _Process(died)

    def emit_event(self, event):
        self.recorder.note("event", self.name, event)

    async def close(self):
        self.recorder.note("close", self.name)


class _LogCapture(logging.Handler):
    def __init__(self, recorder):
        super().__init__(logging.DEBUG)
        self.recorder = recorder

    def emit(self, record):
        self.recorder.note("log", record.levelname, record.getMessage())


async def _supervise(*, spawn_results, restores=None, before=None, first_on_wait=None):
    """Roda `_supervise_process` com o worker atual morrendo e o reinício roteirizado."""
    recorder = characterization.Recorder()
    manager = SelfbotManager()
    manager._session_tokens[USER] = "token"
    manager._protection_enabled_users.add(USER)
    manager._tracker_settings[USER] = {"x": 1}
    manager._quest_settings[USER] = {"x": 1}
    manager._event_subscriptions[USER] = {}
    manager._protection_listeners[USER] = object()
    manager._settings_state[USER] = {}
    manager._quest_sessions[USER] = {}
    manager._user_bulk_locks[USER] = object()
    manager.user_whitelists[USER] = [1]
    manager.user_guild_whitelists[USER] = [1]
    manager.active_processes[USER] = _WorkerProc(recorder, "p1", first_on_wait)
    if before:
        before(manager)

    spawns = list(spawn_results)
    restores = restores or {}

    async def spawn(token):
        recorder.note("spawn", token)
        ok, value = spawns.pop(0)
        if not spawns and not ok and "Token inválido" not in value:
            manager._session_tokens.pop(USER, None)
        if ok:
            return True, _WorkerProc(recorder, value, lambda: manager._session_tokens.pop(USER, None))
        return False, value

    def restore(kind):
        async def run(user_id, sp):
            recorder.note("restore", kind, sp.name)
            return restores.get(kind, {"ok": True})

        return run

    manager._spawn_ready_process = spawn
    manager._restore_protection_state = restore("protection")
    manager._restore_tracker_state = restore("tracker")
    manager._restore_quest_state = restore("quest")
    manager._attach_event_subscriptions = lambda user_id, sp: recorder.note("attach", sp.name)

    logger = logging.getLogger("engine.backend")
    handler = _LogCapture(recorder)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        with patch("engine.backend.asyncio.sleep", recorder.sleeper()):
            await manager._supervise_process(USER)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    state = {
        name: USER in getattr(manager, name)
        for name in (
            "_session_tokens",
            "_event_subscriptions",
            "_protection_listeners",
            "_protection_enabled_users",
            "_tracker_settings",
            "_quest_settings",
            "_settings_state",
            "_quest_sessions",
            "_user_bulk_locks",
            "user_whitelists",
            "user_guild_whitelists",
        )
    }
    active = manager.active_processes.get(USER)
    return {"log": recorder.log, "state": state, "active": getattr(active, "name", None)}


def _manual(manager):
    manager._manual_disconnects.add(USER)


async def collect() -> dict:
    cases = {}
    for line in (
        'PROGRESS_JSON:{"current": 1, "total": 3, "status": "X", "name": "n"}',
        "PROGRESS_JSON:nao-json",
        'RESULT_JSON:{"text": "pronto"}',
        'RESULT_JSON:{"ok": true}',
        "RESULT_JSON:nao-json",
        "START:7",
        "START:x",
        "PROGRESS:1:3:nome",
        "PROGRESS:1:3",
        "PROGRESS:a:b",
        "FAILED:2:3:nome",
        "BATCH_PAUSE:30:2:3",
        "BATCH_PAUSE:30:2",
        "REL_RATELIMIT:1:5",
        "RESULT:ok",
        "CANCELLED",
        "REL_ABORT_CAPTCHA",
        "ERROR:x",
        "DESCONHECIDA:1",
        "REL_RESULT:ABORTADO:x",
        "REL_PROGRESS:1:2:RATELIMIT",
        "REL_ERROR:ABORT",
        "REL_RATELIMIT_ABORT",
    ):
        cases[f"stream {line}"] = await _single(line)
    cases["stream two_prefixes"] = await _stream(
        ["A_START:2", "B_PROGRESS:1:2:x", "B_RESULT:ok"], prefixes=("A_", "B_")
    )
    cases["stream timeout"] = await _stream(["REL_START:2", "TIMEOUT:x", "REL_RESULT:depois"])
    cases["stream ipc_error"] = await _stream(["IPC_ERROR:x", "REL_RESULT:depois"])
    cases["stream lines_after_result"] = await _stream(["REL_RESULT:primeiro", "REL_PROGRESS:1:2:x"])
    cases["stream no_result"] = await _stream(["REL_START:2"])
    cases["stream no_process"] = await _stream([], with_process=False)
    for line in ("START:2", "PROGRESS:1:2:x", "FAILED:1:2:x", "BATCH_PAUSE:3:1:2"):
        cases[f"stream callback_raises {line}"] = await _stream([f"REL_{line}", "REL_RESULT:fim"], raises=True)
    for line in ('PROGRESS_JSON:{"current": 1}', "RATELIMIT:1:2", "ERROR:x"):
        cases[f"stream callback_raises {line}"] = await _stream([f"REL_{line}", "REL_RESULT:fim"], raises=True)

    cases["supervise restart_ok_all_restored"] = await _supervise(spawn_results=[(True, "p2")])
    cases["supervise restart_ok_protection_failed"] = await _supervise(
        spawn_results=[(True, "p2")], restores={"protection": {"ok": False}}
    )
    cases["supervise restart_ok_tracker_failed"] = await _supervise(
        spawn_results=[(True, "p2")], restores={"tracker": None}
    )
    cases["supervise restart_ok_quest_failed"] = await _supervise(
        spawn_results=[(True, "p2")], restores={"quest": {"ok": False}}
    )
    cases["supervise restart_failed_backoff"] = await _supervise(spawn_results=[(False, "erro x")] * 6)
    cases["supervise restart_invalid_token"] = await _supervise(spawn_results=[(False, "Token inválido ou revogado.")])
    cases["supervise manual_disconnect_before"] = await _supervise(spawn_results=[], before=_manual)
    cases["supervise token_missing"] = await _supervise(
        spawn_results=[], before=lambda manager: manager._session_tokens.__setitem__(USER, "")
    )

    manual_after = {}

    def add_manual():
        manual_after["manager"]._manual_disconnects.add(USER)

    cases["supervise manual_disconnect_after_wait"] = await _supervise(
        spawn_results=[],
        before=lambda manager: manual_after.__setitem__("manager", manager),
        first_on_wait=add_manual,
    )
    return cases


class BackendCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_matches_golden(self):
        await characterization.check(self, NAME, collect)


if __name__ == "__main__":
    characterization.main(NAME, collect)
