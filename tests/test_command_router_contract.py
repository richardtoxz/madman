"""Contrato IPC do worker: o que cada comando emite, chama e dispara.

Executa cada comando em `dispatch` contra managers falsos que registram tudo e compara com
`fixtures/command_router_contract.json`, gerado do comportamento existente. Uma mudança
intencional no protocolo exige regenerar o arquivo:

    python -m tests.test_command_router_contract --update
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

from ipc_protocol import REQUEST_ID_MARKER
from worker.core.router import dispatch, register_handlers

FIXTURE = Path(__file__).parent / "fixtures" / "command_router_contract.json"
REQUEST_ID = "0123456789abcdef0123456789abcdef"
MANAGERS = (
    "msg_manager",
    "rel_manager",
    "guild_manager",
    "voice_manager",
    "presence_manager",
    "protection_manager",
    "tracker_manager",
    "quest_manager",
    "account_manager",
)
OPTIONAL_MANAGERS = ("msg_manager", "presence_manager", "quest_manager", "account_manager")
# Perfil "failing": estes métodos falham ao ser aguardados. ValueError em relacionamento e tracker
# precisa cair no INVALID_INPUT do handler; RuntimeError em voz e conta, na resposta de falha interna.
FAILURES = {
    "rel_manager.block_user": ValueError,
    "rel_manager.unblock_user": ValueError,
    "rel_manager.set_friend_nickname": ValueError,
    "rel_manager.send_friend_request_by_name": ValueError,
    "tracker_manager.start_tracking": ValueError,
    "voice_manager.join_voice": RuntimeError,
    "voice_manager.leave_voice": RuntimeError,
    "voice_manager.start_follow": RuntimeError,
    "voice_manager.stop_follow": RuntimeError,
    "account_manager.get_profile": RuntimeError,
    "account_manager.set_custom_status": RuntimeError,
    "account_manager.list_sessions": RuntimeError,
    "account_manager.list_mentions": RuntimeError,
    "account_manager.delete_mentions": RuntimeError,
    "account_manager.ack_all": RuntimeError,
    "presence_manager.configure": ValueError,
}
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 40

COMMANDS = [
    "START",
    "PAUSE",
    "RESUME",
    "CANCEL",
    "CLOSE",
    "REL_STATS",
    "REL_FRIENDS_LIST",
    'REL_FRIENDS_PAGE_JSON:{"page":1,"page_size":5,"sort":"name"}',
    'REL_FRIENDS_PAGE_JSON:{"page":-1}',
    'REL_CLOSEST_JSON:{"limit":5}',
    'REL_CLOSEST_JSON:{"limit":500}',
    "REL_PENDING_LIST",
    'REL_PENDING_PAGE_JSON:{"page":2,"page_size":10}',
    "REL_BLOCKED_LIST",
    'REL_BLOCKED_PAGE_JSON:{"page":0,"page_size":25}',
    "REL_REMOVE_ALL:1,2,x,3",
    "REL_UNBLOCK_ALL",
    "REL_ACCEPT_ALL",
    "REL_DENY_ALL",
    'REL_NICKNAME_JSON:{"target_id":7,"nickname":"apelido"}',
    'REL_NICKNAME_JSON:{"target_id":7,"nickname":""}',
    'REL_NICKNAME_JSON:{"target_id":0}',
    'REL_ADD_FRIEND_JSON:{"username":"  alice  "}',
    'REL_ADD_FRIEND_JSON:{"username":""}',
    'REL_BLOCK_JSON:{"target_id":9}',
    'REL_BLOCK_JSON:{"target_id":"x"}',
    'REL_UNBLOCK_JSON:{"target_id":9}',
    "REL_UNBLOCK_JSON:nao-e-json",
    "GUILD_STATS",
    'GUILD_LIST_PAGE_JSON:{"page":1,"page_size":20}',
    'GUILD_LEAVE_ALL_JSON:{"whitelist":[3,1,3]}',
    'GUILD_LEAVE_ALL_JSON:{"whitelist":"x"}',
    'GUILD_MUTE_ALL_JSON:{"whitelist":[]}',
    "TARGET:55",
    'MSG_PREVIEW_JSON:{"target_id":12,"guild":true}',
    'MSG_PREVIEW_JSON:{"target_id":-5}',
    "MSG_CLEAN_ALL_DMS",
    'MSG_CLEAN_BATCH_JSON:{"target_ids":[1,2]}',
    'MSG_CLEAN_BATCH_JSON:{"target_ids":[]}',
    "MSG_CLEAN_CHANNEL:77",
    "MSG_CLEAN_CHANNEL_NOW:77",
    "MSG_CLEAN_GUILD:88",
    "MSG_CLEAN_GUILD_NOW:88",
    "MSG_COUNT:66",
    "MSG_SCAN_GUILDS",
    "DM_CLOSE:44",
    "DM_CLOSE_ALL",
    "DM_LIST",
    "DM_LIST_REFRESH",
    "GROUP_DM_LEAVE:33",
    "GROUP_DM_LEAVE_ALL",
    'VOICE_JOIN_JSON:{"channel_id":123}',
    'VOICE_JOIN_JSON:{"channel_id":0}',
    "VOICE_LEAVE",
    'VOICE_FOLLOW_JSON:{"target_id":321}',
    "VOICE_FOLLOW_JSON:nao-e-json",
    "VOICE_UNFOLLOW",
    "VOICE_STATUS",
    "PROTECTION_ENABLE",
    'PROTECTION_ENABLE_JSON:{"whitelist":[5,4]}',
    "PROTECTION_DISABLE",
    "PROTECTION_STATUS",
    'PROTECTION_CONFIG_JSON:{"whitelist":[8]}',
    'PROTECTION_CONFIG_JSON:{"whitelist":[0]}',
    f'TRACKER_START_JSON:{{"target_id":42,"webhook_url":"{WEBHOOK}"}}',
    f'TRACKER_START_JSON:{{"target_ids":[1,2],"webhook_url":"{WEBHOOK}"}}',
    'TRACKER_START_JSON:{"target_id":42,"webhook_url":5}',
    "TRACKER_STOP",
    "TRACKER_STATUS",
    "PRESENCE_MAIN",
    "PRESENCE_TASK:Nome:Detalhe",
    "PRESENCE_TASK:",
    "PRESENCE_UPDATE:texto livre",
    "RICH_PRESENCE_STATUS",
    'RICH_PRESENCE_SET_JSON:{"enabled":true,"custom":null}',
    'RICH_PRESENCE_SET_JSON:{"enabled":"x"}',
    "RICH_PRESENCE_SET_JSON:nao-json",
    "QUEST_LIST",
    "QUEST_START:123",
    "QUEST_START:abc",
    "QUEST_CANCEL",
    "QUEST_AUTO",
    f'QUEST_CONFIG:{{"webhook_url":"{WEBHOOK}"}}',
    "QUEST_CONFIG:",
    "QUEST_CONFIG:[]",
    "QUEST_CONFIG:nao-e-json",
    "ACCOUNT_STATUS",
    "ACCOUNT_PROFILE",
    "ACCOUNT_PROFILE_REFRESH",
    'ACCOUNT_STATUS_SET_JSON:{"text":"oi"}',
    "ACCOUNT_STATUS_SET_JSON:nao-e-json",
    "ACCOUNT_SESSIONS",
    'ACCOUNT_MENTIONS_JSON:{"limit":10,"before":"99"}',
    'ACCOUNT_MENTIONS_JSON:{"limit":"x"}',
    'ACCOUNT_MENTION_DELETE_JSON:{"ids":[1,2]}',
    'ACCOUNT_MENTION_DELETE_JSON:{"ids":[]}',
    "ACCOUNT_UNREAD",
    'ACCOUNT_ACK_ALL_JSON:{"scope":"private"}',
    'ACCOUNT_ACK_ALL_JSON:{"scope":"bad"}',
    "COMANDO_DESCONHECIDO",
]


class _Result(dict):
    """Resposta de manager falso que funciona com e sem await."""

    def __await__(self):
        async def resolve():
            return self

        return resolve().__await__()


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(v) for v in value]
        return sorted(items, key=json.dumps) if isinstance(value, (set, frozenset)) else items
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return type(value).__name__


class _Failure:
    """Resposta de um método async que falha só quando aguardado, como uma coroutine real."""

    def __init__(self, error_type: type[Exception]):
        self.error_type = error_type

    def __await__(self):
        async def fail():
            raise self.error_type("falha simulada")

        return fail().__await__()


class _Recorder:
    def __init__(self, name: str, log: list, failures: dict):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_log", log)
        object.__setattr__(self, "_failures", failures)

    def __getattr__(self, attr):
        def method(*args, **kwargs):
            path = f"{self._name}.{attr}"
            self._log.append(["call", path, _plain(args), _plain(kwargs)])
            if path in self._failures:
                return _Failure(self._failures[path])
            return _Result({"ok": True, "via": path})

        return method

    def __setattr__(self, attr, value):
        self._log.append(["set", f"{self._name}.{attr}", _plain(value)])


class _Bot:
    def __init__(self, log: list, *, profile: str):
        self._current_request_id = None
        self._log = log
        self.spawned = []
        failures = FAILURES if profile == "failing" else {}
        self.control_state = _Recorder("control_state", log, failures)
        for name in MANAGERS:
            absent = profile == "minimal" and name in OPTIONAL_MANAGERS
            setattr(self, name, None if absent else _Recorder(name, log, failures))

    def spawn_command_task(self, coroutine):
        self.spawned.append(coroutine)

    def _safe_exec(self, coroutine, **kwargs):
        return ("safe_exec", coroutine, kwargs)

    async def close(self):
        self._log.append(["call", "bot.close", [], {}])


async def _run_command(command: str, *, profile: str) -> dict:
    log: list = []
    bot = _Bot(log, profile=profile)
    exact, prefix = register_handlers(bot)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        await dispatch(bot, exact, prefix, f"{command}{REQUEST_ID_MARKER}{REQUEST_ID}")
        for item in bot.spawned:
            if asyncio.iscoroutine(item):
                log.append(["spawn", "coroutine"])
                await item
            else:
                _, inner, kwargs = item
                log.append(["spawn", "safe_exec", _plain(inner), _plain(kwargs)])
    return {"log": log, "stdout": output.getvalue().splitlines()}


async def collect_contract() -> dict:
    contract = {}
    for profile in ("full", "minimal", "failing"):
        for command in COMMANDS:
            contract[f"{profile} {command}"] = await _run_command(command, profile=profile)
    return contract


class CommandRouterContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_command_keeps_its_ipc_contract(self):
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        actual = await collect_contract()
        self.assertEqual(sorted(actual), sorted(expected))
        for key in expected:
            with self.subTest(command=key):
                self.assertEqual(actual[key], expected[key])


if __name__ == "__main__":
    if "--update" in sys.argv:
        FIXTURE.parent.mkdir(exist_ok=True)
        data = asyncio.run(collect_contract())
        FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{len(data)} casos gravados em {FIXTURE}")
    else:
        unittest.main()
