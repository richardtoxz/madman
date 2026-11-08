"""Golden do script de limpeza pelo pacote de dados: `clean_channel`, `run` e `main`.

python -m tests.test_data_package_characterization --update
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import delete_data_package_messages as script
from scripts.delete_data_package_messages import AbortCleanup, ChannelArchive, Checkpoint, DataPackageCleaner
from tests import characterization
from tests import test_data_package_cleanup as fakes

NAME = "data_package"
USER = 320000000000000001
CHANNEL = 220000000000000001
FIRST = 120000000000000001
SECOND = 120000000000000002


class _Client(fakes._Client):
    def __init__(self, *args, fetch_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fetch_error = fetch_error

    async def fetch_channel(self, channel_id):
        if self.fetch_error is not None:
            self.fetch_calls.append(channel_id)
            raise self.fetch_error
        return await super().fetch_channel(channel_id)


class _ClosingDM(fakes._DMChannel):
    def __init__(self, channel_id, messages=(), *, close_error=None):
        super().__init__(channel_id, messages)
        self.close_error = close_error

    async def close(self):
        if self.close_error is not None:
            raise self.close_error
        await super().close()


class _BrokenHistory(fakes._DMChannel):
    def history(self, **kwargs):
        async def iterator():
            raise RuntimeError("history")
            yield  # pragma: no cover

        return iterator()


async def _sleep(seconds):
    return None


def _messages(*specs):
    return [fakes._Message(message_id, author, **options) for message_id, author, options in specs]


def _own(message_id=FIRST, **options):
    return (message_id, USER, options)


async def _clean(channel=None, archive=None, *, channels=None, partial=(), fetch_error=None):
    cleaner = DataPackageCleaner(fakes.DISCORD, delay=0, verification_attempts=2)
    client = _Client(
        USER, channels if channels is not None else [channel], partial_channels=partial, fetch_error=fetch_error
    )
    output = io.StringIO()
    with patch("scripts.delete_data_package_messages.asyncio.sleep", _sleep), contextlib.redirect_stdout(output):
        try:
            result = (await cleaner.clean_channel(client, archive)).to_dict()
        except (AbortCleanup, script.ScanError) as error:
            result = {"raised": type(error).__name__, "detail": str(error)}
    return {
        "result": result,
        "stdout": output.getvalue().splitlines(),
        "closed": getattr(channel, "closed", None),
        "fetch_calls": client.fetch_calls,
        "partial_calls": client.partial_calls,
        "http_delete_calls": client.http_delete_calls,
    }


def _archive(ids=(FIRST,), kind="dm", channel_id=CHANNEL, name="conversa"):
    return ChannelArchive(channel_id, name, tuple(ids), kind=kind)


async def _run(channels, archives, *, owner=None, bind_error=False, record_error_on=None, clean_errors=None):
    cleaner = DataPackageCleaner(fakes.DISCORD, delay=0, verification_attempts=2)
    client = _Client(USER, channels)
    output = io.StringIO()
    with tempfile.TemporaryDirectory() as temp:
        checkpoint = Checkpoint(Path(temp) / "state.json", "fingerprint")
        if bind_error:
            checkpoint.data["user_id"] = 1
        if record_error_on is not None:
            original_record = checkpoint.record

            def record(result):
                if result.status == record_error_on:
                    raise OSError("disco cheio")
                original_record(result)

            checkpoint.record = record
        if clean_errors:
            original_clean = cleaner.clean_channel

            async def clean_channel(client, archive):
                error = clean_errors.get(archive.channel_id)
                if error is not None:
                    raise error
                return await original_clean(client, archive)

            cleaner.clean_channel = clean_channel
        with patch("scripts.delete_data_package_messages.asyncio.sleep", _sleep), contextlib.redirect_stdout(output):
            exit_code = await cleaner.run(client, archives, checkpoint, package_owner_id=owner)
        statuses = {key: value["status"] for key, value in checkpoint.data.get("channels", {}).items()}
    return {"exit_code": exit_code, "statuses": statuses, "stdout": output.getvalue().splitlines()}


def _package(root: Path) -> None:
    conversation = root / "messages" / f"c{CHANNEL}"
    conversation.mkdir(parents=True)
    (root / "messages" / "index.json").write_text(json.dumps({f"c{CHANNEL}": "Direct Message with Teste"}), "utf-8")
    (conversation / "channel.json").write_text(json.dumps({"id": str(CHANNEL), "type": "DM"}), "utf-8")
    (conversation / "messages.json").write_text(
        json.dumps([{"ID": str(FIRST), "Contents": "a"}, {"ID": str(SECOND), "Contents": "b"}]), "utf-8"
    )
    (root / "account").mkdir()
    (root / "account" / "user.json").write_text(json.dumps({"id": str(USER)}), "utf-8")


class _LoginFailure(Exception):
    pass


def _fake_discord(recorder, behavior):
    class Client:
        def __init__(self):
            recorder.note("client.init")

        def run(self, token):
            recorder.note("client.run", token)
            if behavior == "ready":
                asyncio.run(self.on_ready())
                asyncio.run(self.on_ready())
                return
            raise behavior

        async def close(self):
            recorder.note("client.close")

    return SimpleNamespace(
        Client=Client,
        LoginFailure=_LoginFailure,
        CaptchaRequired=fakes._CaptchaRequired,
        HTTPException=fakes._HTTPException,
    )


async def _main(
    *, args=("--execute",), answer="APAGAR 2", token="tok", prompt_token="", behavior="ready", state=None, source=None
):
    recorder = characterization.Recorder()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "pacote"
        root.mkdir()
        _package(root)
        if source is not None:
            source = Path(temp) / source
            source.mkdir()
        argv = [str(source or root), "--token-env", "MADMAN_TEST_TOKEN", *args]
        if state is not None:
            state_path = Path(temp) / "state.json"
            state_path.write_text(state, "utf-8")
            argv += ["--state", str(state_path)]

        def ask(prompt=""):
            recorder.note("input", prompt)
            if answer is EOFError:
                raise EOFError
            return answer

        def ask_token(prompt=""):
            recorder.note("getpass", prompt)
            return prompt_token

        async def run(self, client, archives, checkpoint, *, package_owner_id=None):
            recorder.note("cleaner.run", len(archives), package_owner_id, self.base_delay)
            return 0

        environment = {"MADMAN_TEST_TOKEN": token} if token else {}
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=False),
            patch("builtins.input", ask),
            patch("getpass.getpass", ask_token),
            patch.object(script, "_load_discord", lambda: _fake_discord(recorder, behavior)),
            patch.object(DataPackageCleaner, "run", run),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            if not token:
                os.environ.pop("MADMAN_TEST_TOKEN", None)
            exit_code = await asyncio.to_thread(script.main, argv)
        clean = lambda text: text.replace(str(Path(temp)), "<tmp>").replace("\\", "/").splitlines()  # noqa: E731
        return {
            "exit_code": exit_code,
            "log": recorder.log,
            "stdout": clean(stdout.getvalue()),
            "stderr": clean(stderr.getvalue()),
        }


async def collect() -> dict:
    not_found, forbidden = fakes._NotFound(), fakes._Forbidden()
    cases = {
        "clean dm_complete_closed": await _clean(fakes._DMChannel(CHANNEL, _messages(_own())), _archive()),
        "clean dm_not_found_partial_fallback": await _clean(
            None, _archive(), channels=[], partial=[fakes._DMChannel(CHANNEL, _messages(_own()))]
        ),
        "clean guild_not_found": await _clean(None, _archive(kind="guild"), channels=[]),
        "clean open_forbidden": await _clean(None, _archive(), channels=[], fetch_error=forbidden),
        "clean open_unexpected": await _clean(None, _archive(), channels=[], fetch_error=ValueError("x")),
        "clean open_captcha": await _clean(None, _archive(), channels=[], fetch_error=fakes._CaptchaRequired()),
        "clean unknown_kind_dm": await _clean(fakes._DMChannel(CHANNEL, _messages(_own())), _archive(kind="unknown")),
        "clean unknown_kind_group": await _clean(
            fakes._GroupChannel(CHANNEL, _messages(_own())), _archive(kind="unknown")
        ),
        "clean kind_mismatch": await _clean(fakes._DMChannel(CHANNEL, _messages(_own())), _archive(kind="guild")),
        "clean author_mismatch_partial": await _clean(
            fakes._DMChannel(CHANNEL, _messages((FIRST, 99, {}))), _archive()
        ),
        "clean non_deletable_with_skips": await _clean(
            fakes._DMChannel(CHANNEL, _messages(_own(deletable=False, message_type="call"), _own(SECOND))),
            _archive((FIRST, SECOND)),
        ),
        "clean retry_failed_message": await _clean(
            fakes._DMChannel(CHANNEL, _messages(_own(delete_errors=[fakes._HTTPException("x")]))), _archive()
        ),
        "clean already_absent": await _clean(fakes._DMChannel(CHANNEL, []), _archive()),
        "clean close_forbidden": await _clean(
            _ClosingDM(CHANNEL, _messages(_own()), close_error=forbidden), _archive()
        ),
        "clean close_unexpected": await _clean(
            _ClosingDM(CHANNEL, _messages(_own()), close_error=ValueError("x")), _archive()
        ),
        "clean close_not_found": await _clean(
            _ClosingDM(CHANNEL, _messages(_own()), close_error=not_found), _archive()
        ),
        "clean close_captcha": await _clean(
            _ClosingDM(CHANNEL, _messages(_own()), close_error=fakes._CaptchaRequired()), _archive()
        ),
        "clean group_kept_open": await _clean(
            fakes._GroupChannel(CHANNEL, _messages(_own())), _archive(kind="group_dm")
        ),
        "clean guild_kept_open": await _clean(fakes._GuildChannel(CHANNEL, _messages(_own())), _archive(kind="guild")),
        "clean scan_error": await _clean(_BrokenHistory(CHANNEL, []), _archive()),
    }

    second = CHANNEL + 1
    cases.update(
        {
            "run owner_mismatch": await _run([fakes._DMChannel(CHANNEL, _messages(_own()))], [_archive()], owner=99),
            "run bind_user_error": await _run(
                [fakes._DMChannel(CHANNEL, _messages(_own()))], [_archive()], bind_error=True
            ),
            "run abort_records_checkpoint": await _run(
                [_ClosingDM(CHANNEL, _messages(_own()), close_error=fakes._CaptchaRequired())], [_archive()]
            ),
            "run abort_checkpoint_write_error": await _run(
                [_ClosingDM(CHANNEL, _messages(_own()), close_error=fakes._CaptchaRequired())],
                [_archive()],
                record_error_on="aborted",
            ),
            "run scan_failed_dm_stops": await _run(
                [_BrokenHistory(CHANNEL, []), fakes._DMChannel(second, _messages(_own(SECOND)))],
                [_archive(), _archive((SECOND,), channel_id=second)],
            ),
            "run unexpected_error": await _run(
                [fakes._DMChannel(CHANNEL, [])], [_archive()], clean_errors={CHANNEL: ValueError("x")}
            ),
            "run checkpoint_write_error": await _run(
                [fakes._DMChannel(CHANNEL, _messages(_own()))], [_archive()], record_error_on="complete"
            ),
            "run guild_failure_continues": await _run(
                [fakes._GuildChannel(second, _messages(_own(SECOND)))],
                [_archive(kind="guild"), _archive((SECOND,), kind="guild", channel_id=second)],
            ),
            "run group_failure_continues": await _run(
                [fakes._GuildChannel(second, _messages(_own(SECOND)))],
                [_archive(kind="group_dm"), _archive((SECOND,), kind="guild", channel_id=second)],
            ),
            "run all_complete_open_dms_first": await _run(
                [fakes._DMChannel(second, _messages(_own(SECOND))), fakes._GuildChannel(CHANNEL, _messages(_own()))],
                [_archive(kind="guild"), _archive((SECOND,), channel_id=second)],
            ),
        }
    )

    cases.update(
        {
            "main dry_run": await _main(args=()),
            "main invalid_package": await _main(source="vazio"),
            "main wrong_confirmation": await _main(answer="nao"),
            "main confirmation_eof": await _main(answer=EOFError),
            "main slow_delay_warning": await _main(args=("--execute", "--delay", "0.01"), answer="nao"),
            "main checkpoint_error": await _main(state="{invalido"),
            "main token_from_env": await _main(),
            "main token_from_prompt": await _main(token="", prompt_token="digitado"),
            "main token_empty": await _main(token=""),
            "main login_failure": await _main(behavior=_LoginFailure()),
            "main captcha_on_login": await _main(behavior=fakes._CaptchaRequired()),
            "main http_on_login": await _main(behavior=fakes._HTTPException("x")),
            "main keyboard_interrupt": await _main(behavior=KeyboardInterrupt()),
        }
    )
    return cases


class DataPackageCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_data_package_matches_golden(self):
        await characterization.check(self, NAME, collect)


if __name__ == "__main__":
    characterization.main(NAME, collect)
