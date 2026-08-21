"""Worker local deterministico usado apenas pelos testes de IPC."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ipc_protocol import (
    encode_ready,
    encode_startup_error,
    format_line,
    format_response,
    split_request_id,
)


mode = sys.argv[1] if len(sys.argv) > 1 else "ready"
if mode == "error":
    print(
        "STARTUP_ERROR:"
        + encode_startup_error("fake_error", "Falha controlada de teste."),
        flush=True,
    )
    raise SystemExit(2)

print(f"READY:{encode_ready('fake:user', 123456789)}", flush=True)

for raw_line in sys.stdin:
    command, request_id = split_request_id(raw_line.strip())
    if command == "PING":
        print("PONG:EVENT", flush=True)
        print(format_response("PONG:", "OK", request_id), flush=True)
    elif command == "STREAM":
        print(format_line("JOB_PROGRESS:1:2", request_id), flush=True)
        print(format_line("JOB_RESULT:OK", request_id), flush=True)
    elif command == "DIE":
        raise SystemExit(7)
    elif command == "FAIL":
        print(format_response("IPC_ERROR:", "HANDLER_FAILED:Fake", request_id), flush=True)
    elif command == "CLOSE":
        break
