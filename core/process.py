"""
Wrapper de um processo selfbot com leitor de stdout thread-safe.
Gerencia comunicação IPC via stdin/stdout com o processo filho.
"""
import asyncio
import json
import logging

log = logging.getLogger(__name__)


class SelfbotProcess:
    """Wrapper de um processo selfbot com leitor de stdout thread-safe."""

    def __init__(self, process, status):
        self.process = process
        self.status = status
        self._ready = False
        self._username: str | None = None
        self._user_id: int | None = None
        self._response_waiters: dict[str, asyncio.Queue] = {}
        self._event_callbacks: dict[str, list] = {}
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # Lock para escrita no stdin

    # ── Properties ────────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        """True se o processo ainda não terminou."""
        return self.process.returncode is None

    @property
    def ready(self) -> bool:
        """True se o selfbot emitiu READY."""
        return self._ready and self.is_alive

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def user_id(self) -> int | None:
        return self._user_id

    # ── Reader ────────────────────────────────────────────────

    def start_reader(self):
        """Inicia a task de leitura em background."""
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        """Loop único que lê stdout e despacha para os waiters."""
        while True:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break
                text = line.decode('utf-8', errors='replace').strip()
                if not text:
                    continue

                # Parseia READY:username:id
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

                # Tenta despachar para waiters registrados
                dispatched = False
                for prefix, queue in list(self._response_waiters.items()):
                    if text.startswith(prefix):
                        await queue.put(text)
                        dispatched = True
                        break

                # Se não despachou para waiter específico, tenta event callbacks
                if not dispatched:
                    for prefix, callbacks in list(self._event_callbacks.items()):
                        if text.startswith(prefix):
                            for callback in callbacks:
                                try:
                                    asyncio.create_task(callback(text))
                                except Exception:
                                    pass
                            break

            except Exception:
                break

    # ── Event Pub/Sub ─────────────────────────────────────────

    def subscribe_event(self, prefix: str, callback):
        """Inscreve um callback para um prefixo de evento."""
        if prefix not in self._event_callbacks:
            self._event_callbacks[prefix] = []
        if callback not in self._event_callbacks[prefix]:
            self._event_callbacks[prefix].append(callback)

    def unsubscribe_event(self, prefix: str, callback):
        """Remove a inscrição de um callback."""
        if prefix in self._event_callbacks:
            try:
                self._event_callbacks[prefix].remove(callback)
            except ValueError:
                pass
            if not self._event_callbacks[prefix]:
                del self._event_callbacks[prefix]

    # ── IPC Commands ──────────────────────────────────────────

    async def send(self, command: str):
        """Envia comando ao stdin do processo (thread-safe)."""
        if not self.is_alive:
            return
        async with self._lock:
            try:
                self.process.stdin.write(f"{command}\n".encode())
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    async def send_and_wait(self, command: str, response_prefix: str, timeout: float = 10.0):
        """Envia comando e espera UMA linha de resposta com o prefixo."""
        queue = asyncio.Queue()
        self._response_waiters[response_prefix] = queue
        try:
            await self.send(command)
            try:
                text = await asyncio.wait_for(queue.get(), timeout=timeout)
                return text[len(response_prefix):]
            except asyncio.TimeoutError:
                return None
        finally:
            self._response_waiters.pop(response_prefix, None)

    async def send_and_wait_json(self, command: str, response_prefix: str, timeout: float = 10.0):
        """Envia comando e parseia resposta como JSON."""
        raw = await self.send_and_wait(command, response_prefix, timeout)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        return None

    async def send_and_stream(self, command: str, prefixes: list, timeout: float = 600):
        """
        Envia comando e faz yield de todas as linhas que matcham qualquer prefixo,
        até encontrar um prefixo que termine com _RESULT: ou _CANCELLED ou timeout.
        """
        queue = asyncio.Queue()

        for p in prefixes:
            self._response_waiters[p] = queue

        try:
            await self.send(command)

            while True:
                try:
                    text = await asyncio.wait_for(queue.get(), timeout=timeout)
                    yield text

                    for p in prefixes:
                        if text.startswith(p) and ("_RESULT:" in text or "_CANCELLED" in text):
                            return
                except asyncio.TimeoutError:
                    yield "TIMEOUT:Operacao demorou demais"
                    return
        finally:
            for p in prefixes:
                self._response_waiters.pop(p, None)

    # ── Lifecycle ─────────────────────────────────────────────

    async def close(self):
        """Para o reader e mata o processo com shutdown seguro."""
        # 1. Cancela o reader
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()

        # 2. Tenta graceful shutdown
        if self.process.returncode is None:
            try:
                await self.send("CLOSE")
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

        # 3. Kill forçado se ainda vivo
        if self.process.returncode is None:
            try:
                self.process.kill()
                await self.process.wait()
            except Exception:
                pass

        # 4. Fecha pipes para evitar ResourceWarning
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe:
                try:
                    pipe.close()
                except Exception:
                    pass

        self._ready = False
        log.info("Processo encerrado: %s (%s)", self._username, self._user_id)
