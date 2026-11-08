"""Quem pode usar o painel, no site e no bot.

ALLOWED_USER_IDS vazio ou ausente: qualquer conta do Discord entra (o comportamento de sempre).
Preenchido (IDs separados por vírgula, espaço ou ponto e vírgula): só esses IDs entram. Se nenhum
item da lista for um ID válido, ninguém entra, para que um erro de digitação não abra o painel a todos.
"""

from __future__ import annotations

import os
import re

ACCESS_DENIED_MESSAGE = "Esta conta do Discord não tem acesso a este painel."


def allowed_user_ids() -> frozenset[int] | None:
    """IDs liberados, ou None quando não há lista (todos liberados)."""
    raw = os.environ.get("ALLOWED_USER_IDS", "").strip()
    if not raw:
        return None
    return frozenset(int(item) for item in re.split(r"[\s,;]+", raw) if item.isdigit() and int(item) > 0)


def is_user_allowed(user_id: int) -> bool:
    allowed = allowed_user_ids()
    return allowed is None or int(user_id) in allowed
