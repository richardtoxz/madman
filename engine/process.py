import asyncio
import json
import logging

log = logging.getLogger(__name__)


class SelfbotProcess:
    def __init__(self, process, status):
        self.process = process
        self.status = status
        self._ready = False
        self._username: str | None = None
        self._user_id: int | None = None
        self._response_waiters: dict[str, asyncio.Queue] = {}
        self._event_callbacks: dict[str, list] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def is_alive(self) -> bool:
        return self.process.returncode is None

    @property
    def ready(self) -> bool:
        return self._ready and self.is_alive

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def user_id(self) -> int | None:
        return self._user_id

    def start_reader(self):
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

    def _dispatch_event_line(self, text: str):
        for prefix, callbacks in list(self._event_callbacks.items()):
            if text.startswith(prefix):
                for callback in callbacks:
                    try:
                        asyncio.create_task(callback(text))
                    except Exception:
                        pass
                break

    def emit_event(self, text: str):
        self._dispatch_event_line(text)

    async def _read_loop(self):
        while True:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    self._dispatch_event_line("PROCESS:STDOUT_EOF")
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                if text.startswith("READY:"):
                    parts = text.split(":", 2)
                    if len(parts) >= 3:
                        self._username = parts[1]
                        try:
                            self._user_id = int(parts[2])
                        except ValueError:
                            pass
                    self._ready = True
                    log.info("Selfbot ready: %s (%s)", self._username, self._user_id)

                dispatched = False
                for prefix, queue in list(self._response_waiters.items()):
                    if text.startswith(prefix):
                        await queue.put(text)
                        dispatched = True
                        break

                if not dispatched:
                    self._dispatch_event_line(text)

            except Exception as e:
                self._dispatch_event_line(f"PROCESS:STDOUT_READ_ERROR:{e}")
                break

    async def _read_stderr_loop(self):
        while True:
            try:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                log.warning("SELFBOT_STDERR: %s", text)
                self._dispatch_event_line(f"PROCESS:STDERR:{text}")
            except Exception as e:
                self._dispatch_event_line(f"PROCESS:STDERR_READ_ERROR:{e}")
                break

    def subscribe_event(self, prefix: str, callback):
        if prefix not in self._event_callbacks:
            self._event_callbacks[prefix] = []
        if callback not in self._event_callbacks[prefix]:
            self._event_callbacks[prefix].append(callback)

    def unsubscribe_event(self, prefix: str, callback):
        if prefix in self._event_callbacks:
            try:
                self._event_callbacks[prefix].remove(callback)
            except ValueError:
                pass
            if not self._event_callbacks[prefix]:
                del self._event_callbacks[prefix]

    async def send(self, command: str) -> bool:
        if not self.is_alive:
            self._dispatch_event_line("PROCESS:IPC_SEND_FAILED:PROCESS_NOT_ALIVE")
            return False
        async with self._lock:
            try:
                self.process.stdin.write(f"{command}\n".encode())
                await self.process.stdin.drain()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._dispatch_event_line("PROCESS:IPC_SEND_FAILED:BROKEN_PIPE")
                return False

    async def send_and_wait(
        self, command: str, response_prefix: str, timeout: float = 10.0
    ):
        queue = asyncio.Queue()
        self._response_waiters[response_prefix] = queue
        try:
            sent = await self.send(command)
            if not sent:
                return "ERRO:IPC_SEND_FAILED"
            try:
                text = await asyncio.wait_for(queue.get(), timeout=timeout)
                return text[len(response_prefix) :]
            except asyncio.TimeoutError:
                return None
        finally:
            self._response_waiters.pop(response_prefix, None)

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
        queue = asyncio.Queue()

        for p in prefixes:
            self._response_waiters[p] = queue

        try:
            sent = await self.send(command)
            if not sent:
                yield "IPC_ERROR:IPC_SEND_FAILED"
                return

            while True:
                try:
                    text = await asyncio.wait_for(queue.get(), timeout=timeout)
                    yield text

                    for p in prefixes:
                        if text.startswith(p) and (
                            "_RESULT:" in text or "_CANCELLED" in text
                        ):
                            return
                except asyncio.TimeoutError:
                    yield "TIMEOUT:Operacao demorou demais"
                    return
        finally:
            for p in prefixes:
                self._response_waiters.pop(p, None)

    async def close(self):
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()

        if self.process.returncode is None:
            try:
                await self.send("CLOSE")
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

        if self.process.returncode is None:
            try:
                self.process.kill()
                await self.process.wait()
            except Exception:
                pass

        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe:
                try:
                    pipe.close()
                except Exception:
                    pass

        self._ready = False
        log.info("Processo encerrado: %s (%s)", self._username, self._user_id)
