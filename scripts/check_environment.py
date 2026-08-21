"""Diagnostico local sem conectar ao Discord nem ler os tokens do .env."""

from __future__ import annotations

import compileall
import importlib.metadata
import os
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMUM_PYTHON = (3, 10)
EXPECTED_SHARED = {
    "Flask": "3.1.3",
    "python-dotenv": "1.2.3",
}


def _ok(message: str) -> None:
    print(f"[OK] {message}")


def _fail(message: str) -> None:
    print(f"[ERRO] {message}")


def _check_shared_dependencies() -> bool:
    valid = True
    for distribution, expected in EXPECTED_SHARED.items():
        try:
            installed = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            _fail(f"{distribution} nao esta instalado no venv")
            valid = False
            continue

        if installed != expected:
            _fail(f"{distribution}: esperado {expected}, encontrado {installed}")
            valid = False
        else:
            _ok(f"{distribution} {installed}")
    return valid


def _check_env_permissions() -> bool:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists() or os.name != "posix":
        return True
    permissions = env_file.stat().st_mode & 0o777
    if permissions & 0o077:
        _fail(f".env esta com permissao {permissions:o}; use 600")
        return False
    _ok(".env acessivel apenas pelo usuario atual (600)")
    return True


def _check_isolated_import(
    package: str,
    lib_directory: str,
    expected_versions: dict[str, str],
) -> bool:
    imports = "; ".join(f"import {module}" for module in expected_versions)
    assertions = "; ".join(
        (
            f"assert {module}.__version__ == {expected!r}, "
            f"f'versao inesperada de {module}: {{{module}.__version__}}'"
        )
        for module, expected in expected_versions.items()
    )
    versions = ", ".join(
        f"f'{module}={{{module}.__version__}}'" for module in expected_versions
    )
    code = (
        f"import {package}; {imports}; "
        "from pathlib import Path; "
        f"root = Path({str(PROJECT_ROOT)!r}); "
        "origin = Path(discord.__file__).resolve(); "
        f"expected_root = (root / {lib_directory!r}).resolve(); "
        "assert origin.is_relative_to(expected_root), "
        "f'discord carregado do local incorreto: {origin}'; "
        f"{assertions}; "
        f"print(','.join(({versions},)) + '|' + str(origin))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr else "erro"
        _fail(f"{package}: {detail}")
        return False

    versions_found, origin = result.stdout.strip().split("|", 1)
    _ok(f"{package}: {versions_found}; discord isolado em {origin}")
    return True


def main() -> int:
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < MINIMUM_PYTHON:
        _fail("Python 3.10 ou superior e necessario")
        return 1
    _ok("versao do Python compativel")

    checks = [_check_shared_dependencies(), _check_env_permissions()]
    checks.append(
        _check_isolated_import(
            "panel",
            "panel/lib",
            {"discord": "2.7.1", "aiohttp": "3.14.3"},
        )
    )
    checks.append(
        _check_isolated_import(
            "worker",
            "worker/lib",
            {
                "discord": "2.1.0",
                "aiohttp": "3.14.3",
                "curl_cffi": "0.16.0",
            },
        )
    )

    compiled = compileall.compile_dir(
        PROJECT_ROOT,
        quiet=1,
        rx=re.compile(r"[\\/](?:\.git|\.venv|lib|__pycache__)[\\/]"),
    )
    if compiled:
        _ok("arquivos Python compilam sem erro de sintaxe")
    else:
        _fail("ha arquivos Python com erro de sintaxe")
    checks.append(compiled)

    if all(checks):
        print("\nAmbiente da Parte 1 validado com sucesso.")
        return 0

    print("\nAmbiente incompleto. Execute: python setup_envs.py")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
