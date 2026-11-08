"""Goldens de caracterização: gravam o comportamento atual antes de uma refatoração.

Cada módulo `tests/test_*_characterization.py` define `async def collect() -> dict` com um
cenário por chave e compara o resultado com `fixtures/characterization/<nome>.json`. O
arquivo é gerado uma vez, antes da refatoração, e não é regenerado depois:

    python -m tests.test_<nome>_characterization --update

Este módulo não importa `discord`, `worker` nem `panel`, então serve aos dois lados.
"""

from __future__ import annotations

import asyncio
import enum
import json
import sys
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "characterization"

Collect = Callable[[], Awaitable[dict[str, Any]]]


def plain(value: Any) -> Any:
    """Converte um valor para algo que o JSON grava e compara de forma estável."""
    if isinstance(value, BaseException):
        return {"error": type(value).__name__}
    if isinstance(value, enum.Enum):
        return str(value)
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((plain(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


class Recorder:
    """Registro único, em ordem, do que o código sob teste emitiu, esperou e chamou."""

    def __init__(self):
        self.log: list[list[Any]] = []

    def emit_line(self, line, request_id=None):
        self.log.append(["emit", line, request_id])

    def sleeper(self, result: bool = True):
        async def sleep(seconds, *args, **kwargs):
            self.log.append(["sleep", round(float(seconds), 3)])
            return result

        return sleep

    def note(self, *entry):
        self.log.append([plain(item) for item in entry])


def _fixture(name: str) -> Path:
    return FIXTURES / f"{name}.json"


async def check(testcase: unittest.TestCase, name: str, collect: Collect) -> None:
    expected = json.loads(_fixture(name).read_text(encoding="utf-8"))
    actual = plain(await collect())
    testcase.assertEqual(sorted(actual), sorted(expected))
    for key in expected:
        with testcase.subTest(scenario=key):
            testcase.assertEqual(actual[key], expected[key])


def main(name: str, collect: Collect) -> None:
    if "--update" not in sys.argv:
        unittest.main()
        return
    FIXTURES.mkdir(parents=True, exist_ok=True)
    data = plain(asyncio.run(collect()))
    _fixture(name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(data)} casos gravados em {_fixture(name)}")
