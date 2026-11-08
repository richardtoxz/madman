"""Instala as dependencias do Madman em ambientes de importacao isolados."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def _pip_install(requirements: Path, target: Path | None = None) -> None:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "-r",
        str(requirements),
    ]
    if target is not None:
        command.extend(("--target", str(target)))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def _replace_isolated_environment(requirements_name: str, target_name: str) -> None:
    """Instala em staging e so substitui o ambiente antigo apos sucesso."""

    requirements = PROJECT_ROOT / requirements_name
    target = PROJECT_ROOT / target_name
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(
        tempfile.mkdtemp(prefix=f".{target.name}-staging-", dir=target.parent)
    )
    backup = target.with_name(f".{target.name}-backup")

    try:
        _pip_install(requirements, staged)
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            target.replace(backup)
        staged.replace(target)
    except Exception:
        if not target.exists() and backup.exists():
            backup.replace(target)
        shutil.rmtree(staged, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)


def install_libs() -> None:
    if sys.prefix == sys.base_prefix:
        raise SystemExit(
            "Ative o ambiente virtual antes de instalar: source .venv/bin/activate"
        )

    print("[1/3] Instalando dependencias compartilhadas...")
    _pip_install(PROJECT_ROOT / "requirements" / "base.txt")

    print("\n[2/3] Instalando discord.py do painel em panel/lib...")
    _replace_isolated_environment("requirements/panel.txt", "panel/lib")

    print("\n[3/3] Instalando discord.py-self do worker em worker/lib...")
    _replace_isolated_environment("requirements/worker.txt", "worker/lib")

    print("\nInstalacao concluida.")
    print(f"Valide com: {sys.executable} scripts/check_environment.py")
    print("Inicie com: python -m panel.main")


if __name__ == "__main__":
    install_libs()
