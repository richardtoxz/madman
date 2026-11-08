"""Read inherited stdin without requiring overlapped Windows pipe handles."""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading


async def _threaded_lines(stream, *, limit: int):
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=32)
    stopped = threading.Event()

    def read():
        while not stopped.is_set():
            try:
                item = stream.readline(limit + 1)
                if len(item) > limit:
                    raise ValueError("IPC line exceeds the size limit")
            except Exception as error:
                item = error
            if stopped.is_set():
                return
            put = queue.put(item)
            try:
                future = asyncio.run_coroutine_threadsafe(put, loop)
            except RuntimeError:
                put.close()
                return
            try:
                while not stopped.is_set():
                    try:
                        future.result(timeout=0.1)
                        break
                    except concurrent.futures.TimeoutError:
                        continue
            except concurrent.futures.CancelledError:
                return
            finally:
                if stopped.is_set():
                    future.cancel()
            if not item or isinstance(item, Exception):
                return

    # A blocked stdin read must not keep the worker alive during shutdown.
    thread = threading.Thread(target=read, name="worker-stdin", daemon=True)
    thread.start()
    try:
        while True:
            item = await queue.get()
            if isinstance(item, Exception):
                raise item
            if not item:
                return
            yield item
    finally:
        stopped.set()


async def stdin_lines(stream, *, limit: int):
    if sys.platform == "win32":
        from contextlib import aclosing

        async with aclosing(_threaded_lines(stream, limit=limit)) as lines:
            async for line in lines:
                yield line
        return

    reader = asyncio.StreamReader(limit=limit)
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, stream)
    try:
        while line := await reader.readline():
            if len(line) > limit:
                raise ValueError("IPC line exceeds the size limit")
            yield line
    finally:
        transport.close()
