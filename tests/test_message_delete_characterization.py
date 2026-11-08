"""Golden da exclusão de uma mensagem (`MessageDeleteMixin._delete_one_message`).

python -m tests.test_message_delete_characterization --update
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker  # noqa: F401 - ativa o ambiente isolado do discord.py-self
import discord

from tests import characterization
from worker.messages.helpers import MessageDeleteMixin

NAME = "message_delete"


def _http(status: int, *, retry_after=None, json_payload=None):
    error = discord.HTTPException(SimpleNamespace(status=status, reason="x"), {"message": "x"})
    if retry_after is not None:
        error.retry_after = retry_after
    if json_payload is not None:
        error.json = json_payload
    return error


def _captcha():
    return discord.CaptchaRequired(
        SimpleNamespace(status=400, reason="x"),
        {"captcha_key": ["x"], "captcha_sitekey": "k", "captcha_service": "hcaptcha"},
    )


class _Message:
    def __init__(self, recorder, errors):
        self.id = 77
        self.recorder = recorder
        self.errors = list(errors)

    async def delete(self):
        self.recorder.note("delete")
        if self.errors:
            raise self.errors.pop(0)


class _Owner(MessageDeleteMixin):
    def __init__(self, recorder, *, deletable=True, paused_ok=True, sleep_ok=True, cancelled=False):
        self.recorder = recorder
        self.cancelled = cancelled
        self.config = SimpleNamespace(MAX_429_CONSECUTIVE=3)
        self._deletable = deletable
        self._paused_ok = paused_ok
        self._sleep_interruptibly = recorder.sleeper(sleep_ok)

    def _is_deletable_message(self, message):
        return self._deletable

    async def _wait_while_paused(self):
        self.recorder.note("wait_paused")
        return self._paused_ok


async def _delete(errors=(), **options):
    recorder = characterization.Recorder()
    owner = _Owner(recorder, **options)
    with patch("worker.messages.helpers.emit_line", recorder.emit_line):
        outcome = await owner._delete_one_message(_Message(recorder, errors), "MSG_X", "m1")
    return {"log": recorder.log, "outcome": outcome}


def _not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="x"), {"message": "x"})


def _forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="x"), {"message": "x"})


async def collect() -> dict:
    return {
        "not_deletable": await _delete(deletable=False),
        "deleted": await _delete(),
        "not_found": await _delete([_not_found()]),
        "forbidden": await _delete([_forbidden()]),
        "captcha": await _delete([_captcha()]),
        "rate_limited_then_ok": await _delete([discord.RateLimited(0.4)]),
        "rate_limited_cloudflare": await _delete([discord.RateLimited(2.0, cloudflare=True)]),
        "rate_limited_cancel": await _delete([discord.RateLimited(2.0)], sleep_ok=False),
        "http_429_from_attribute": await _delete([_http(429, retry_after=2.5)]),
        "http_429_from_json": await _delete([_http(429, json_payload={"retry_after": 3.0})]),
        "http_429_default": await _delete([_http(429)]),
        "http_429_limit": await _delete([_http(429, retry_after=1.0)] * 3),
        "http_429_cancel": await _delete([_http(429, retry_after=1.0)], sleep_ok=False),
        "http_500_retries_then_fail": await _delete([_http(500)] * 4),
        "http_500_then_ok": await _delete([_http(503)]),
        "http_500_cancel": await _delete([_http(500)], sleep_ok=False),
        "http_400": await _delete([_http(400)]),
        "http_no_status": await _delete([_http(0)]),
        "generic_error": await _delete([ValueError("x")]),
        "paused_cancelled": await _delete(paused_ok=False),
        "cancelled_before_start": await _delete(cancelled=True),
    }


class MessageDeleteCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_delete_matches_golden(self):
        await characterization.check(self, NAME, collect)


if __name__ == "__main__":
    characterization.main(NAME, collect)
