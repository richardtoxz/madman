"""Golden do laço de bulk de relationships e guilds (`_run_bulk`).

Grava, por cenário, a sequência de linhas IPC e esperas e o resultado. O `emit_line` é
substituído só no módulo de cada manager: o laço tem que continuar emitindo por ele.

    python -m tests.test_bulk_characterization --update
"""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from tests import characterization
from tests import test_guild_manager as guild_fakes
from tests import test_relationship_manager as rel_fakes
from worker.guilds.manager import GuildManager
from worker.relationships.manager import RelationshipManager

NAME = "bulk"
FRIEND = discord.RelationshipType.friend
NONE = discord.RelationshipType.none


class _Config:
    MIN_DELAY = 0.5
    MAX_DELAY = 1.0
    BATCH_SIZE = 2
    BATCH_PAUSE_MIN = 3.0
    BATCH_PAUSE_MAX = 4.0
    INITIAL_BACKOFF = 1.0
    MAX_BACKOFF = 5.0


def _http(status: int, retry_after: float | None = None):
    payload = {"message": "x"}
    if retry_after is not None:
        payload["retry_after"] = retry_after
    error = discord.HTTPException(SimpleNamespace(status=status, reason="x"), payload)
    if retry_after is not None:
        error.retry_after = retry_after
    return error


def _captcha():
    return discord.CaptchaRequired(
        SimpleNamespace(status=400, reason="x"),
        {"captcha_key": ["x"], "captcha_sitekey": "k", "captcha_service": "hcaptcha"},
    )


def _scripted(recorder, item_id, script, on_success=None):
    """Operação que consome, por item, uma lista de exceções ou valores de retorno."""
    pending = {key: list(value) for key, value in script.items()}

    async def operation(item):
        key = item_id(item)
        recorder.note("op", key)
        steps = pending.get(key) or []
        step = steps.pop(0) if steps else None
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(item)
        if on_success:
            on_success(item)
        return step

    return operation


def _result(result):
    return {key: value for key, value in result.items() if key != "duration"}


def _log(recorder):
    """O resumo traz `tempo Ns`, medido pelo relógio do loop; o golden fixa só o formato."""
    return [
        [entry[0], re.sub(r"tempo \d+s", "tempo <n>s", entry[1]), entry[2]] if entry[0] == "emit" else entry
        for entry in recorder.log
    ]


async def _run_rel(script=None, *, sleep_result=True, fetch_error=None, cancel_after=None, **kwargs):
    recorder = characterization.Recorder()
    relationships = [rel_fakes._FakeRelationship(n, FRIEND) for n in (1, 2, 3)]
    manager = RelationshipManager(rel_fakes._FakeClient(relationships))
    manager._sleep_interruptibly = recorder.sleeper(sleep_result)

    def on_success(relationship):
        relationship.type = NONE if kwargs.get("desired_result_type") is None else FRIEND
        if cancel_after == int(relationship.user.id):
            manager.cancelled = True

    operation = _scripted(recorder, lambda r: int(r.user.id), script or {}, on_success)
    options = {
        "desired_remaining_types": (FRIEND,),
        "desired_result_type": None,
        "not_found_is_success": True,
    }
    options.update(kwargs)
    with (
        patch("random.uniform", side_effect=lambda low, high: low),
        patch("worker.relationships.manager.emit_line", recorder.emit_line),
    ):
        manager.client.fetch_error = fetch_error
        result = await manager._run_bulk(
            relationships=relationships,
            operation=operation,
            prefix="REL_X",
            request_id="r1",
            config=_Config(),
            **options,
        )
    return {"log": _log(recorder), "result": _result(result)}


async def _run_guild(script=None, *, sleep_result=True, verify=None, protected_owners=0, whitelisted=0):
    recorder = characterization.Recorder()
    guilds = [SimpleNamespace(id=n, name=f"g{n}") for n in (1, 2, 3)]
    manager = GuildManager(guild_fakes._FakeClient())
    manager._sleep_interruptibly = recorder.sleeper(sleep_result)
    operation = _scripted(recorder, lambda g: int(g.id), script or {})
    with (
        patch("random.uniform", side_effect=lambda low, high: low),
        patch("worker.guilds.manager.emit_line", recorder.emit_line),
    ):
        result = await manager._run_bulk(
            guilds=guilds,
            operation=operation,
            prefix="GUILD_X",
            request_id="g1",
            verify=verify,
            protected_owners=protected_owners,
            whitelisted=whitelisted,
            config=_Config(),
        )
    return {"log": _log(recorder), "result": _result(result)}


async def _verify_unavailable(target_ids):
    return None, "ERRO_X"


async def collect() -> dict:
    not_found = rel_fakes._not_found
    forbidden = rel_fakes._forbidden
    return {
        "rel all_success": await _run_rel(),
        "rel not_found_success": await _run_rel({2: [not_found()]}),
        "rel not_found_failure": await _run_rel({2: [not_found()]}, not_found_is_success=False),
        "rel forbidden": await _run_rel({2: [forbidden()]}),
        "rel captcha": await _run_rel({1: [_captcha()]}),
        "rel rate_limited": await _run_rel({2: [discord.RateLimited(12.0)]}),
        "rel http_429_retry": await _run_rel({2: [_http(429, 2.0)]}),
        "rel http_429_too_long": await _run_rel({2: [_http(429, 9.0)]}),
        "rel http_500_retry": await _run_rel({2: [_http(500), _http(500)]}),
        "rel http_500_exhausted": await _run_rel({2: [_http(500), _http(500), _http(500)]}),
        "rel generic_error": await _run_rel({2: [ValueError("x")]}),
        "rel cancel_between_items": await _run_rel(cancel_after=1),
        "rel cancel_during_sleep": await _run_rel(sleep_result=False),
        "rel verification_unavailable": await _run_rel(fetch_error=RuntimeError("x")),
        "rel desired_result_type": await _run_rel(desired_result_type=FRIEND),
        "guild all_success": await _run_guild(),
        "guild operation_returns_already": await _run_guild({2: ["already"]}),
        "guild not_found": await _run_guild({2: [guild_fakes._not_found()]}),
        "guild forbidden": await _run_guild({2: [forbidden()]}),
        "guild captcha": await _run_guild({1: [_captcha()]}),
        "guild http_500_retry": await _run_guild({2: [_http(500), _http(500)]}),
        "guild verify_none": await _run_guild({2: [ValueError("x")]}),
        "guild verify_unavailable": await _run_guild(verify=_verify_unavailable),
        "guild extra_details": await _run_guild(protected_owners=2, whitelisted=1),
        "guild cancel_during_sleep": await _run_guild(sleep_result=False),
    }


class BulkCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_matches_golden(self):
        await characterization.check(self, NAME, collect)


if __name__ == "__main__":
    characterization.main(NAME, collect)
