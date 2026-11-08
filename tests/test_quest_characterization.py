"""Golden das Quests: fila automática, Quest única e tarefa de achievement.

python -m tests.test_quest_characterization --update
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self

from tests import characterization
from tests import test_quest as quest_fakes
from worker.quest.manager import QuestManager
from worker.quest.models import Quest

NAME = "quest"


def _quest(task="WATCH_VIDEO", *, quest_id="1", name=None, expires_in=None, no_expiry=False, **kwargs):
    payload = quest_fakes._quest_payload(task, quest_id=quest_id, name=name or f"Quest {quest_id}", **kwargs)
    if no_expiry:
        payload["config"].pop("expires_at")
    elif expires_in is not None:
        moment = datetime.now(timezone.utc) + timedelta(days=expires_in)
        payload["config"]["expires_at"] = quest_fakes._iso(moment)
    return Quest(payload)


class _Presence:
    def __init__(self, recorder, token=True):
        self.recorder = recorder
        self.token = token

    async def set_task(self, kind, label):
        self.recorder.note("presence.set_task", kind, label)
        return f"tok:{label}" if self.token else None

    async def complete_task(self, token):
        self.recorder.note("presence.complete_task", token)

    async def set_main(self):
        self.recorder.note("presence.set_main")


def _manager(recorder, *, quests=(), fetch_error=None, runs=None, verified=None, presence_token=True):
    client = quest_fakes._Client()
    client.presence_manager = _Presence(recorder, presence_token)
    manager = QuestManager(client)
    runs = runs or {}
    verified = verified or {}

    async def fetch():
        recorder.note("fetch")
        if fetch_error:
            raise fetch_error
        return list(quests)

    async def run_task_logic(quest, task_name, task, target, request_id):
        recorder.note("run", quest.id, task_name, target)
        outcome = runs.get(quest.id, True)
        if callable(outcome):
            outcome = outcome(manager)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def verify(quest):
        recorder.note("verify", quest.id)
        return verified.get(quest.id, True)

    def dispatch(coroutine, *, name):
        coroutine.close()
        recorder.note("background", name)

    manager.fetch_quests = fetch
    manager._run_task_logic = run_task_logic
    manager._verify_completion = verify
    manager._dispatch_background = dispatch
    return manager


def _state(manager):
    return {
        "task_cleared": manager._task is None,
        "active_quest_id": manager._active_quest_id,
        "buckets": dict(manager._progress_webhook_bucket),
        "last_presence_detail": manager._last_presence_detail,
    }


async def _auto(**kwargs):
    recorder = characterization.Recorder()
    manager = _manager(recorder, **kwargs)
    with patch("worker.quest.manager.emit_line", recorder.emit_line):
        await manager.auto_complete_all("rq")
    return {"log": recorder.log, "state": _state(manager)}


async def _single(quest_id="1", *, busy=False, **kwargs):
    recorder = characterization.Recorder()
    manager = _manager(recorder, **kwargs)
    blocker = None
    if busy:
        blocker = asyncio.create_task(asyncio.sleep(30))
        manager._task = blocker
    try:
        with patch("worker.quest.manager.emit_line", recorder.emit_line):
            await manager.complete_quest(quest_id, "rq")
    finally:
        if blocker:
            blocker.cancel()
    state = _state(manager)
    state["task_cleared"] = manager._task is None or manager._task is blocker
    return {"log": recorder.log, "state": state}


def _cancel_flag(manager):
    manager._cancelled = True
    return False


class _Api:
    def __init__(self, recorder, *, tokens_error=False, authorize=None, ticket=None, delete_error=False):
        self.recorder = recorder
        self.tokens_error = tokens_error
        self.authorize = {"location": "https://callback.invalid/?code=oauth-code"} if authorize is None else authorize
        self.ticket = {"ticket": "proxy-ticket"} if ticket is None else ticket
        self.delete_error = delete_error
        self.token_reads = 0

    async def get(self, path):
        self.token_reads += 1
        self.recorder.note("GET", path)
        if self.tokens_error and self.token_reads == 1:
            raise RuntimeError("tokens")
        old = {"id": "old-grant", "application": {"id": "987654321098765432"}}
        new = {"id": "new-grant", "application": {"id": "987654321098765432"}}
        return [old] if self.token_reads == 1 else [old, new]

    async def post(self, path, payload, **kwargs):
        self.recorder.note("POST", path, payload, kwargs)
        return self.authorize if path == "/oauth2/authorize" else self.ticket

    async def delete(self, path, **kwargs):
        self.recorder.note("DELETE", path, kwargs)
        if self.delete_error:
            raise RuntimeError("delete")


async def _achievement(*, activity=None, invalid_app=False, **api_options):
    recorder = characterization.Recorder()
    quest = _quest("ACHIEVEMENT_IN_ACTIVITY", target=1, application_in_config=False)
    if invalid_app:
        quest.tasks["ACHIEVEMENT_IN_ACTIVITY"]["applications"] = [{"id": "abc"}]
    manager = _manager(recorder)
    manager.api = _Api(recorder, **api_options)
    replies = list(activity or [(True, {"token": "progress-token"}), (True, {})])

    async def activity_post(application_id, path, payload, headers):
        recorder.note("activity", application_id, path, payload, headers)
        return replies.pop(0)

    async def emit_progress(quest, task_name, progress, target, request_id):
        recorder.note("progress", task_name, progress, target)

    manager._activity_post = activity_post
    manager._emit_progress = emit_progress
    task = quest.tasks["ACHIEVEMENT_IN_ACTIVITY"]
    with patch("worker.quest.manager.emit_line", recorder.emit_line):
        result = await manager.doing_achievement_in_activity_quest(quest, task, 1, "rq")
    return {"log": recorder.log, "result": result}


async def collect() -> dict:
    expired = _quest(quest_id="11", expires_in=-1)
    claimed = _quest(quest_id="12", completed=True, claimed=True)
    unsupported = _quest("STREAM_ON_DESKTOP", quest_id="13")
    invalid_target = _quest(quest_id="14", target=0)
    cases = {
        "auto fetch_failed": await _auto(fetch_error=RuntimeError("x")),
        "auto empty": await _auto(),
        "auto all_skipped_manual_enroll": await _auto(quests=[_quest(enrolled=False)]),
        "auto all_skipped_manual_claim": await _auto(quests=[_quest(completed=True)]),
        "auto nothing_pending": await _auto(quests=[expired, claimed, unsupported, invalid_target]),
        "auto progress_complete_verified": await _auto(quests=[_quest(progress=60)]),
        "auto progress_complete_not_verified": await _auto(quests=[_quest(progress=60)], verified={"1": False}),
        "auto queue_success_and_failure": await _auto(
            quests=[
                _quest(quest_id="21", expires_in=2),
                _quest(quest_id="22", expires_in=1, progress=30),
                _quest(quest_id="23", no_expiry=True),
            ],
            runs={"22": False},
        ),
        "auto queue_exception": await _auto(quests=[_quest(quest_id="31")], runs={"31": RuntimeError("x")}),
        "auto queue_cancelled_flag": await _auto(
            quests=[_quest(quest_id="41", expires_in=1), _quest(quest_id="42", expires_in=2)],
            runs={"41": _cancel_flag},
        ),
        "auto queue_cancelled_error": await _auto(
            quests=[_quest(quest_id="51")], runs={"51": asyncio.CancelledError()}
        ),
        "auto presence_token_none": await _auto(quests=[_quest(quest_id="61")], presence_token=False),
    }
    single = [_quest(quest_id="1")]
    cases.update(
        {
            "single already_running": await _single(busy=True, quests=single),
            "single not_found": await _single("999", quests=single),
            "single expired": await _single("11", quests=[expired]),
            "single already_completed": await _single(quests=[_quest(completed=True)]),
            "single manual_enroll": await _single(quests=[_quest(enrolled=False)]),
            "single unsupported": await _single("13", quests=[unsupported]),
            "single success": await _single(quests=single),
            "single not_confirmed": await _single(quests=single, runs={"1": False}),
            "single cancelled": await _single(quests=single, runs={"1": asyncio.CancelledError()}),
            "single failed": await _single(quests=single, runs={"1": RuntimeError("x")}),
            "single presence_token_none": await _single(quests=single, presence_token=False),
        }
    )
    cases.update(
        {
            "achievement invalid_application_id": await _achievement(invalid_app=True),
            "achievement success_revokes_new_grant": await _achievement(),
            "achievement existing_tokens_unavailable": await _achievement(tokens_error=True),
            "achievement no_auth_code": await _achievement(authorize={}),
            "achievement no_ticket": await _achievement(ticket={}),
            "achievement authorize_refused": await _achievement(activity=[(False, None)]),
            "achievement progress_refused": await _achievement(activity=[(True, {"token": "t"}), (False, None)]),
            "achievement revoke_error_ignored": await _achievement(delete_error=True),
        }
    )
    return cases


class QuestCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_quest_matches_golden(self):
        await characterization.check(self, NAME, collect)


if __name__ == "__main__":
    characterization.main(NAME, collect)
