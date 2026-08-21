import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass


def _optional_snowflake(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{name} deve conter um ID numerico positivo")
    return int(raw)


TOKEN_BOT_CONTROLE = os.environ.get("TOKEN_BOT_CONTROLE", "").strip()
ID_CANAL_SOBRE = _optional_snowflake("ID_CANAL_SOBRE")
ID_CANAL_PAINEL = _optional_snowflake("ID_CANAL_PAINEL")
