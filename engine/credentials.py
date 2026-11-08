"""Limpeza do código da conta colado pelo usuário."""

from __future__ import annotations

import re

# Espaços, quebras de linha e caracteres invisíveis que vêm junto ao copiar de apps e páginas.
_INVISIBLE = re.compile(r"[\s\u00a0\u200b-\u200d\u2060\ufeff]+")
_QUOTES = "\"'`\u201c\u201d\u2018\u2019"
_AUTH_PREFIX = re.compile(r"^authorization:", re.IGNORECASE)


def clean_token(raw: str | None) -> str:
    """Tira espaços, invisíveis, aspas nas pontas e um `Authorization:` colado por engano."""
    token = _INVISIBLE.sub("", raw or "").strip(_QUOTES)
    # O prefixo pode vir dentro das aspas ("Authorization: ...") ou antes delas (Authorization:"...").
    token = _AUTH_PREFIX.sub("", token)
    return token.strip(_QUOTES)
