from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass

from ipc_protocol import (
    MAX_IPC_COMMAND_LENGTH,
    REQUEST_ID_MARKER,
    IPCProtocolError,
    attach_request_id,
    decode_ready,
    decode_startup_error,
    split_request_id,
)


log = logging.getLogger(__name__)
_PROCESS_EXITED = object()


@dataclass(slots=True)
class _ResponseWaiter:
    queue: asyncio.Queue
    request_id: str


class SelfbotProcess:
    def __init__(self, process, status: str, *, redactions: tuple[str, ...] = ()):
        self.process = process
        self.status = status
        self._ready = False
        self._username: str | None = None
        self._user_id: int | None = None
        self._startup_error: str | None = None
        self._startup_error_code: str | None = None
        self._startup_event = asyncio.Event()
        self._response_waiters: dict[str, _ResponseWaiter] = {}
        self._event_callbacks: dict[str, list] = {}
        self._event_tasks: set[asyncio.Task] = set()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._waiter_condition = asyncio.Condition()
        self._closing = False
        self._closed = False
        self._redactions = tuple(value for value in redactions if value)

    @property
    def is_alive(self) -> bool:
        return not self._closed and self.process.returncode is None

    @property
    def ready(self) -> bool:
        return self._ready and self.is_alive

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def user_id(self) -> int | None:
        return self._user_id

    @property
    def startup_error(self) -> str | None:
        return self._startup_error

    @property
    def startup_error_code(self) -> str | None:
        return self._startup_error_code

    def start_reader(self) -> None:
        if self._reader_task or self._stderr_task:
            raise RuntimeError("Leitores do subprocesso ja foram iniciados")
        if self.process.stdout is None or self.process.stderr is None:
            raise RuntimeError("Subprocesso criado sem stdout/stderr PIPE")

        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"selfbot-stdout-{self.process.pid}"
        )
        self._stderr_task = asyncio.create_task(
            self._read_stderr_loop(), name=f"selfbot-stderr-{self.process.pid}"
        )

    async def wait_until_ready(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._startup_event.wait(), timeout=timeout)
        except TimeoutError:
            self._set_startup_error(
                "startup_timeout", "Tempo limite excedido ao iniciar o worker."
            )
            return False
        return self.ready

    def _sanitize(self, text: str) -> str:
        for secret in self._redactions:
            text = text.replace(secret, "[REDACTED]")
        return text

    def _set_startup_error(self, code: str, message: str) -> None:
        if self._startup_event.is_set():
            return
        self._startup_error_code = code
        self._startup_error = self._sanitize(message)
        self._startup_event.set()

    def _dispatch_event_line(self, text: str) -> None:
        for prefix, callbacks in list(self._event_callbacks.items()):
            if not text.startswith(prefix):
                continue
            for callback in tuple(callbacks):
                task = asyncio.create_task(callback(text))
                self._event_tasks.add(task)
                task.add_done_callback(self._event_task_done)
            break

    def _event_task_done(self, task: asyncio.Task) -> None:
        self._event_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            log.exception(
                "Falha em callback de evento do worker",
                exc_info=(type(error), error, error.__traceback__),
            )

    def emit_event(self, text: str) -> None:
        self._dispatch_event_line(text)

    @staticmethod
    def _prefixes_conflict(left: str, right: str) -> bool:
        return left == right or left.startswith(right) or right.startswith(left)

    async def _acquire_waiters(
        self,
        prefixes: list[str],
        queue: asyncio.Queue,
        request_id: str,
    ) -> None:
        async with self._waiter_condition:
            while any(
                self._prefixes_conflict(active, requested)
                for active in self._response_waiters
                for requested in prefixes
            ):
                await self._waiter_condition.wait()

            waiter = _ResponseWaiter(queue, request_id)
            for prefix in prefixes:
                self._response_waiters[prefix] = waiter

    async def _release_waiters(self, prefixes: list[str]) -> None:
        async with self._waiter_condition:
            for prefix in prefixes:
                self._response_waiters.pop(prefix, None)
            self._waiter_condition.notify_all()

    async def _notify_process_exited(self) -> None:
        queues = {id(waiter.queue): waiter.queue for waiter in self._response_waiters.values()}
        for queue in queues.values():
            await queue.put(_PROCESS_EXITED)

    def _handle_ready(self, payload: str) -> None:
        username, user_id = decode_ready(payload)
        self._username = username
        self._user_id = user_id
        self._ready = True
        self._startup_event.set()
        log.info("Selfbot ready: %s (%s)", username, user_id)

    def _handle_startup_error(self, payload: str) -> None:
        try:
            code, message = decode_startup_error(payload)
        except IPCProtocolError as error:
            self._set_startup_error("invalid_startup_error", str(error))
            return
        self._set_startup_error(code, message)

    async def _read_loop(self) -> None:
        failure: Exception | None = None
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                text = self._sanitize(line.decode("utf-8", errors="replace").strip())
                if not text:
                    continue

                if text.startswith("READY:"):
                    try:
                        self._handle_ready(text.removeprefix("READY:"))
                    except IPCProtocolError as error:
                        self._set_startup_error("protocol_error", str(error))
                        if self.process.returncode is None:
                            self.process.terminate()
                    continue

                if text.startswith("STARTUP_ERROR:") and not self._ready:
                    self._handle_startup_error(text.removeprefix("STARTUP_ERROR:"))
                    continue

                body, response_request_id = split_request_id(text)
                matched: _ResponseWaiter | None = None
                matched_prefix: str | None = None
                for prefix, waiter in list(self._response_waiters.items()):
                    if (
                        (body.startswith(prefix) or body.startswith("IPC_ERROR:"))
                        and response_request_id == waiter.request_id
                        and (matched_prefix is None or len(prefix) > len(matched_prefix))
                    ):
                        matched = waiter
                        matched_prefix = prefix

                if matched is not None:
                    await matched.queue.put(body)
                else:
                    self._dispatch_event_line(body)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure = error
            self._dispatch_event_line(
                f"PROCESS:STDOUT_READ_ERROR:{self._sanitize(str(error))}"
            )
        finally:
            self._ready = False
            if not self._startup_event.is_set():
                message = "Worker encerrou antes de concluir a inicializacao."
                if failure is not None:
                    message = f"Falha ao ler o worker: {failure}"
                self._set_startup_error("worker_exited", message)
            await self._notify_process_exited()
            self._dispatch_event_line("PROCESS:STDOUT_EOF")
            if failure is not None and self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()

    async def _read_stderr_loop(self) -> None:
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = self._sanitize(line.decode("utf-8", errors="replace").strip())
                if not text:
                    continue
                log.warning("SELFBOT_STDERR: %s", text)
                self._dispatch_event_line(f"PROCESS:STDERR:{text}")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._dispatch_event_line(
                f"PROCESS:STDERR_READ_ERROR:{self._sanitize(str(error))}"
            )

    def subscribe_event(self, prefix: str, callback) -> None:
        callbacks = self._event_callbacks.setdefault(prefix, [])
        if callback not in callbacks:
            callbacks.append(callback)

    def unsubscribe_event(self, prefix: str, callback) -> None:
        callbacks = self._event_callbacks.get(prefix)
        if not callbacks:
            return
        with suppress(ValueError):
            callbacks.remove(callback)
        if not callbacks:
            self._event_callbacks.pop(prefix, None)

    @staticmethod
    def _valid_command(command: str, *, allow_metadata: bool) -> bool:
        if not command or len(command) > MAX_IPC_COMMAND_LENGTH:
            return False
        if "\n" in command or "\r" in command or "\x00" in command:
            return False
        if not allow_metadata and REQUEST_ID_MARKER in command:
            return False
        return True

    async def _send_line(self, command: str, *, allow_metadata: bool = False) -> bool:
        if not self._valid_command(command, allow_metadata=allow_metadata):
            self._dispatch_event_line("PROCESS:IPC_SEND_FAILED:INVALID_COMMAND")
            return False
        if self.process.returncode is not None or self._closed:
            self._dispatch_event_line("PROCESS:IPC_SEND_FAILED:PROCESS_NOT_ALIVE")
            return False
        if self.process.stdin is None:
            self._dispatch_event_line("PROCESS:IPC_SEND_FAILED:STDIN_UNAVAILABLE")
            return False

        async with self._write_lock:
            try:
                self.process.stdin.write(f"{command}\n".encode("utf-8"))
                await self.process.stdin.drain()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._dispatch_event_line("PROCESS:IPC_SEND_FAILED:BROKEN_PIPE")
                return False

    async def send_startup_secret(self, secret: str) -> bool:
        if self._reader_task is None:
            raise RuntimeError("Leitores devem ser iniciados antes do segredo")
        return await self._send_line(secret)

    async def send(self, command: str) -> bool:
        if self._closing:
            return False
        return await self._send_line(command)

    async def send_and_wait(
        self, command: str, response_prefix: str, timeout: float = 10.0
    ):
        request_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        acquired = False
        try:
            await asyncio.wait_for(
                self._acquire_waiters([response_prefix], queue, request_id),
                timeout=min(timeout, 10.0),
            )
            acquired = True
            tagged_command = attach_request_id(command, request_id)
            if not await self._send_line(tagged_command, allow_metadata=True):
                return "ERRO:IPC_SEND_FAILED"

            item = await asyncio.wait_for(queue.get(), timeout=timeout)
            if item is _PROCESS_EXITED:
                return "ERRO:PROCESS_EXITED"
            if item.startswith("IPC_ERROR:"):
                return f"ERRO:{item}"
            return item[len(response_prefix) :]
        except TimeoutError:
            return None
        finally:
            if acquired:
                await self._release_waiters([response_prefix])

    async def send_and_wait_json(
        self, command: str, response_prefix: str, timeout: float = 10.0
    ):
        raw = await self.send_and_wait(command, response_prefix, timeout)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        return None

    async def send_and_stream(self, command: str, prefixes: list, timeout: float = 600):
        request_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        waiter_prefixes = list(dict.fromkeys(prefixes))
        acquired = False

        try:
            await asyncio.wait_for(
                self._acquire_waiters(waiter_prefixes, queue, request_id),
                timeout=min(timeout, 10.0),
            )
            acquired = True
            tagged_command = attach_request_id(command, request_id)
            if not await self._send_line(tagged_command, allow_metadata=True):
                yield "IPC_ERROR:IPC_SEND_FAILED"
                return

            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    yield "TIMEOUT:Operacao demorou demais"
                    return

                if item is _PROCESS_EXITED:
                    yield "IPC_ERROR:PROCESS_EXITED"
                    return

                text = item
                yield text
                if text.startswith("IPC_ERROR:"):
                    return
                if any(
                    text.startswith(prefix)
                    and ("_RESULT:" in text or "_CANCELLED" in text)
                    for prefix in waiter_prefixes
                ):
                    return
        except TimeoutError:
            yield "IPC_ERROR:WAITER_TIMEOUT"
        finally:
            if acquired:
                await self._release_waiters(waiter_prefixes)

    async def _wait_for_exit(self, timeout: float) -> bool:
        if self.process.returncode is not None:
            return True
        try:
            await asyncio.wait_for(self.process.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True

            if self.process.returncode is None:
                await self._send_line("CLOSE")
                await self._wait_for_exit(5.0)

            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
                await self._wait_for_exit(2.0)

            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.kill()
                await self._wait_for_exit(2.0)

            self._ready = False
            await self._notify_process_exited()

            current = asyncio.current_task()
            tasks = [
                task
                for task in (self._reader_task, self._stderr_task, *self._event_tasks)
                if task is not None and task is not current and not task.done()
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            if self.process.stdin is not None:
                with suppress(Exception):
                    self.process.stdin.close()
                    await self.process.stdin.wait_closed()

            self._redactions = ()
            self._closed = True
            self._closing = False
            log.info("Processo encerrado: %s (%s)", self._username, self._user_id)
