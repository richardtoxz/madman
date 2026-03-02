import asyncio
import time
import random
import discord
from collections import namedtuple
from config import BulkConfig


Result = namedtuple(
    "Result",
    ["success", "failed", "rate_limited_count", "aborted", "abort_reason", "duration"],
)


async def safe_bulk_execute(
    items,
    operation,
    get_name,
    cancelled_check,
    progress_print_prefix="BULK",
    config=None,
):
    if config is None:
        config = BulkConfig()

    start_time = time.time()
    success = 0
    failed = 0
    rate_limited_count = 0
    aborted = False
    abort_reason = ""
    consecutive_429 = 0
    current_backoff = config.INITIAL_BACKOFF
    total = len(items)

    if total == 0:
        return Result(0, 0, 0, False, "", 0.0)

    print(f"{progress_print_prefix}_START:{total}", flush=True)

    actions_since_pause = 0
    for i, item in enumerate(items):
        if cancelled_check():
            aborted = True
            abort_reason = "Cancelado pelo usuario"
            print(f"{progress_print_prefix}_CANCELLED", flush=True)
            break

        if actions_since_pause > 0 and actions_since_pause >= config.BATCH_SIZE:
            pause = random.uniform(config.BATCH_PAUSE_MIN, config.BATCH_PAUSE_MAX)
            print(
                f"{progress_print_prefix}_BATCH_PAUSE:{pause:.0f}:{success}:{total}",
                flush=True,
            )
            await asyncio.sleep(pause)
            actions_since_pause = 0

        name = get_name(item)
        item_success = False

        for attempt in range(3):
            if cancelled_check():
                aborted = True
                abort_reason = "Cancelado pelo usuario"
                break
            try:
                res = await operation(item)
                if res == "SKIPPED":
                    item_success = "SKIPPED"
                    break
                item_success = True
                actions_since_pause += 1
                consecutive_429 = 0
                current_backoff = config.INITIAL_BACKOFF
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    consecutive_429 += 1
                    rate_limited_count += 1
                    retry_after = getattr(e, "retry_after", current_backoff)
                    wait_time = max(retry_after, current_backoff) + random.uniform(
                        2.0, 5.0
                    )
                    print(
                        f"{progress_print_prefix}_RATELIMIT:{consecutive_429}:{config.MAX_429_CONSECUTIVE}:{wait_time:.0f}",
                        flush=True,
                    )
                    if consecutive_429 >= config.MAX_429_CONSECUTIVE:
                        aborted = True
                        abort_reason = f"Muitos rate limits consecutivos ({consecutive_429}). Tente mais tarde."
                        print(
                            f"{progress_print_prefix}_ABORT_RATELIMIT:{abort_reason}",
                            flush=True,
                        )
                        break
                    await asyncio.sleep(wait_time)
                    current_backoff = min(
                        current_backoff * config.BACKOFF_MULTIPLIER, config.MAX_BACKOFF
                    )
                    continue
                elif e.status == 403:
                    break
                else:
                    await asyncio.sleep(5)
                    break
            except Exception as e:
                if "captcha" in str(e).lower():
                    aborted = True
                    abort_reason = "Discord exigiu CAPTCHA."
                    print(f"{progress_print_prefix}_ABORT_CAPTCHA", flush=True)
                    break
                await asyncio.sleep(5)
                break

        if aborted:
            break

        if item_success is True:
            success += 1
            print(
                f"{progress_print_prefix}_PROGRESS:{success}:{total}:{name}", flush=True
            )
        elif item_success == "SKIPPED":
            success += 1
            print(
                f"{progress_print_prefix}_SKIPPED:{success}:{total}:{name}", flush=True
            )
        else:
            failed += 1
            print(f"{progress_print_prefix}_FAILED:{failed}:{total}:{name}", flush=True)

        if i < total - 1 and not aborted and item_success != "SKIPPED":
            if item_success is True:
                delay = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
                if random.random() < config.LONG_PAUSE_CHANCE:
                    delay += random.uniform(
                        config.LONG_PAUSE_MIN, config.LONG_PAUSE_MAX
                    )
                await asyncio.sleep(delay)

    duration = time.time() - start_time
    status = "ABORTADO" if aborted else "COMPLETADO"
    print(
        f"{progress_print_prefix}_RESULT:{status}:{success}/{total} em {duration:.0f}s",
        flush=True,
    )
    return Result(success, failed, rate_limited_count, aborted, abort_reason, duration)
