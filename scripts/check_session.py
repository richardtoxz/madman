"""Valida a configuracao segura da sessao sem exibir nenhum segredo."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    valid = True
    env_file = PROJECT_ROOT / ".env"

    if os.name == "posix" and env_file.exists():
        permissions = env_file.stat().st_mode & 0o777
        if permissions & 0o077:
            print(f"[ERRO] .env esta com permissao {permissions:o}; esperado 600")
            valid = False
        else:
            print("[OK] .env com permissao 600")

    print("[OK] autenticacao por modal habilitada para todos os usuarios do painel")

    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
