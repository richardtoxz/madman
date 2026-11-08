"""Laço compartilhado das operações em lote de relações e servidores.

Cada manager chama `run_bulk` pelo seu `_run_bulk`, passando o próprio `emit_line` no momento
da chamada (os testes substituem o nome no módulo do manager) e o que muda entre os recursos:
o que conta como sucesso, como tratar `NotFound`, quando esperar entre itens e como verificar
o resultado final.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, Protocol

import discord

Emit = Callable[[str, "str | None"], None]
Verify = Callable[[set[int]], Awaitable[tuple["int | None", "str | None"]]]


class BulkOwner(Protocol):
    """O que `run_bulk` usa do manager que o chama."""

    cancelled: bool
    MAX_TRANSIENT_ATTEMPTS: int

    async def _sleep_interruptibly(self, seconds: float) -> bool: ...

    def _error_code(self, error: Exception) -> str: ...


class _Attempt(NamedTuple):
    outcome: str
    rate_limits: int
    cancelled: bool = False
    aborted: bool = False
    abort_reason: str = ""


async def _attempt_item(
    owner: BulkOwner,
    item: Any,
    operation: Callable[[Any], Awaitable[str | None]],
    *,
    not_found_outcome: str,
    rate_limits: int,
    config,
    emit: Emit,
    prefix: str,
    request_id: str | None,
) -> _Attempt:
    """Executa a operação de um item, com novas tentativas em 429 curto e em 5xx."""
    for attempt in range(owner.MAX_TRANSIENT_ATTEMPTS):
        if owner.cancelled:
            return _Attempt("failed", rate_limits, cancelled=True)
        try:
            return _Attempt(await operation(item) or "success", rate_limits)
        except asyncio.CancelledError:
            raise
        except discord.NotFound:
            return _Attempt(not_found_outcome, rate_limits)
        except discord.CaptchaRequired:
            return _Attempt("failed", rate_limits, aborted=True, abort_reason="CAPTCHA exigido pelo Discord")
        except discord.RateLimited as error:
            rate_limits += 1
            retry_after = max(0.0, float(error.retry_after))
            emit(f"{prefix}_RATELIMIT:{rate_limits}:{retry_after:.0f}", request_id)
            reason = f"espera longa solicitada ({retry_after:.0f}s)"
            return _Attempt("failed", rate_limits, aborted=True, abort_reason=reason)
        except discord.Forbidden:
            return _Attempt("failed", rate_limits)
        except discord.HTTPException as error:
            status = int(getattr(error, "status", 0) or 0)
            can_retry = attempt + 1 < owner.MAX_TRANSIENT_ATTEMPTS
            if status == 429:
                rate_limits += 1
                retry_after = max(1.0, float(getattr(error, "retry_after", 1.0) or 1.0))
                emit(f"{prefix}_RATELIMIT:{rate_limits}:{retry_after:.0f}", request_id)
                if can_retry and retry_after <= config.MAX_BACKOFF:
                    if not await owner._sleep_interruptibly(retry_after):
                        return _Attempt("failed", rate_limits, cancelled=True)
                    continue
                return _Attempt("failed", rate_limits, aborted=True, abort_reason="rate limit persistente")
            if can_retry and status >= 500:
                wait = min(config.INITIAL_BACKOFF * (2**attempt), config.MAX_BACKOFF)
                emit(f"{prefix}_ERROR:{owner._error_code(error)}:RETRY:{wait:.0f}", request_id)
                if not await owner._sleep_interruptibly(wait):
                    return _Attempt("failed", rate_limits, cancelled=True)
                continue
            return _Attempt("failed", rate_limits)
        except Exception:
            return _Attempt("failed", rate_limits)
    return _Attempt("failed", rate_limits)


@dataclass
class _Tally:
    """Contagem do lote e o resumo que vai na linha `_RESULT`."""

    total: int
    processed: int = 0
    success: int = 0
    already: int = 0
    failed: int = 0
    rate_limits: int = 0
    cancelled: bool = False
    aborted: bool = False
    abort_reason: str = ""

    def record(self, outcome: str) -> str:
        """Conta o item e devolve o desfecho normalizado: success, already ou failed."""
        self.processed += 1
        outcome = outcome if outcome in ("success", "already") else "failed"
        setattr(self, outcome, getattr(self, outcome) + 1)
        return outcome

    def status(self, remaining: int | None) -> str:
        if self.cancelled:
            return "CANCELADO"
        if self.aborted:
            return "ABORTADO"
        if self.failed or remaining is None or remaining:
            return "PARCIAL"
        return "COMPLETADO"

    def summary(self, remaining: int | None, verification_error: str | None, extra_details, duration: float) -> str:
        details = [
            f"processados {self.processed}/{self.total}",
            f"sucesso {self.success}",
            f"ja ajustados {self.already}",
            f"falhas {self.failed}",
            f"restantes {remaining}" if remaining is not None else "verificacao indisponivel",
            *extra_details,
        ]
        if self.abort_reason:
            details.append(self.abort_reason)
        elif verification_error:
            details.append(f"verificacao {verification_error.lower()}")
        details.append(f"tempo {duration:.0f}s")
        return "; ".join(details)

    def result(self, status: str, remaining: int | None, duration: float) -> dict[str, Any]:
        return {
            "status": status,
            "processed": self.processed,
            "total": self.total,
            "success": self.success,
            "already_done": self.already,
            "failed": self.failed,
            "remaining": remaining,
            "rate_limits": self.rate_limits,
            "duration": duration,
        }


async def run_bulk(
    owner: BulkOwner,
    items: list[Any],
    *,
    prefix: str,
    request_id: str | None,
    config,
    emit: Emit,
    operation: Callable[[Any], Awaitable[str | None]],
    item_id: Callable[[Any], int],
    label: Callable[[Any], str],
    not_found_outcome: Literal["already", "failed"],
    delay_after_success_only: bool,
    verify: Verify | None,
    extra_details: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Executa `operation` em cada item com pausas, novas tentativas e um resumo verificado.

    `verify(target_ids)` só roda quando o lote não foi cancelado nem abortado e devolve
    `(restantes, erro_de_verificacao)`; sem `verify`, os restantes são as falhas.
    """
    tally = _Tally(total=len(items))
    # Lidos antes do _START: um ID invalido falha antes de qualquer operacao, como sempre falhou.
    target_ids = {item_id(item) for item in items}
    started_at = asyncio.get_running_loop().time()
    emit(f"{prefix}_START:{tally.total}", request_id)

    for item in items:
        if owner.cancelled:
            tally.cancelled = True
            break

        if tally.processed and tally.processed % config.BATCH_SIZE == 0:
            pause = random.uniform(config.BATCH_PAUSE_MIN, config.BATCH_PAUSE_MAX)
            emit(f"{prefix}_BATCH_PAUSE:{pause:.0f}:{tally.processed}:{tally.total}", request_id)
            if not await owner._sleep_interruptibly(pause):
                tally.cancelled = True
                break

        attempt = await _attempt_item(
            owner,
            item,
            operation,
            not_found_outcome=not_found_outcome,
            rate_limits=tally.rate_limits,
            config=config,
            emit=emit,
            prefix=prefix,
            request_id=request_id,
        )
        tally.rate_limits = attempt.rate_limits
        if attempt.cancelled:
            tally.cancelled = True
            break
        tally.aborted, tally.abort_reason = attempt.aborted, attempt.abort_reason

        outcome = tally.record(attempt.outcome)
        kind = "FAILED" if outcome == "failed" else "PROGRESS"
        emit(f"{prefix}_{kind}:{tally.processed}:{tally.total}:{label(item)}", request_id)
        if tally.aborted:
            break

        if tally.processed < tally.total and (outcome == "success" or not delay_after_success_only):
            if not await owner._sleep_interruptibly(random.uniform(config.MIN_DELAY, config.MAX_DELAY)):
                tally.cancelled = True
                break

    remaining: int | None = None
    verification_error: str | None = None
    if not tally.cancelled and not tally.aborted:
        if verify is None:
            remaining = tally.failed
        else:
            remaining, verification_error = await verify(target_ids)

    status = tally.status(remaining)
    duration = asyncio.get_running_loop().time() - started_at
    emit(
        f"{prefix}_RESULT:{status}:{tally.summary(remaining, verification_error, extra_details, duration)}", request_id
    )
    return tally.result(status, remaining, duration)
