from __future__ import annotations

import json
import sys
from typing import TextIO

from .bridge import AcpBridge
from .protocol import JsonRpcError, error_response


def run_stdio_server(*, stdin: TextIO | None = None, stdout: TextIO | None = None, bridge: AcpBridge | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    bridge = bridge or AcpBridge()

    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise JsonRpcError(-32600, "JSON-RPC message must be an object.")
            messages = bridge.handle_json_rpc(payload)
        except JsonRpcError as exc:
            messages = [error_response(None, exc)]
        except Exception as exc:
            messages = [error_response(None, JsonRpcError(-32700, f"Invalid JSON-RPC payload: {exc}"))]
        for message in messages:
            stdout.write(json.dumps(message.as_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    return run_stdio_server()


if __name__ == "__main__":
    raise SystemExit(main())
