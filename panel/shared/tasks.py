from __future__ import annotations

import asyncio
from collections.abc import Coroutine


def create_panel_task(
    client,
    coroutine: Coroutine,
    *,
    name: str,
) -> asyncio.Task:
    """Create a panel task whose lifetime is owned by the control bot."""
    tasks = getattr(client, "_panel_background_tasks", None)
    if tasks is None:
        tasks = set()
        client._panel_background_tasks = tasks
    task = asyncio.create_task(coroutine, name=name)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


async def cancel_panel_tasks(client) -> None:
    tasks = getattr(client, "_panel_background_tasks", None)
    if not tasks:
        return
    current = asyncio.current_task()
    pending = [task for task in tuple(tasks) if task is not current and not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    tasks.clear()
