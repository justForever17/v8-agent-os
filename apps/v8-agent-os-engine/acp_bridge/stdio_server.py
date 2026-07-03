from __future__ import annotations

import json
import sys
from typing import TextIO

from .bridge import AcpBridge
from .protocol import JsonRpcError, error_response

_FRAMING_NEWLINE = "newline"
_FRAMING_CONTENT_LENGTH = "content-length"


def _parse_content_length(line: str) -> int | None:
    name, sep, value = line.partition(":")
    if not sep or name.strip().lower() != "content-length":
        return None
    try:
        length = int(value.strip())
    except ValueError as exc:
        raise JsonRpcError(-32700, f"Invalid Content-Length header: {value.strip()}") from exc
    if length < 0:
        raise JsonRpcError(-32700, "Content-Length must be non-negative.")
    return length


def _read_stdio_frame(stdin: TextIO) -> tuple[str, str] | None:
    while True:
        first_line = stdin.readline()
        if first_line == "":
            return None
        stripped = first_line.strip()
        if not stripped:
            continue
        content_length = _parse_content_length(first_line)
        if content_length is None:
            return stripped, _FRAMING_NEWLINE

        while True:
            header_line = stdin.readline()
            if header_line == "":
                raise JsonRpcError(-32700, "Unexpected EOF while reading Content-Length headers.")
            if not header_line.strip():
                break
            maybe_length = _parse_content_length(header_line)
            if maybe_length is not None:
                content_length = maybe_length

        body_chars: list[str] = []
        body_bytes = 0
        while body_bytes < content_length:
            chunk = stdin.read(1)
            if chunk == "":
                raise JsonRpcError(-32700, "Unexpected EOF while reading Content-Length body.")
            body_chars.append(chunk)
            body_bytes += len(chunk.encode("utf-8"))
            if body_bytes > content_length:
                raise JsonRpcError(-32700, "Content-Length split a UTF-8 character.")
        body = "".join(body_chars)
        return body, _FRAMING_CONTENT_LENGTH


def _write_stdio_message(stdout: TextIO, payload: dict, framing: str) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if framing == _FRAMING_CONTENT_LENGTH:
        stdout.write(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}")
    else:
        stdout.write(body + "\n")
    stdout.flush()


def run_stdio_server(*, stdin: TextIO | None = None, stdout: TextIO | None = None, bridge: AcpBridge | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    bridge = bridge or AcpBridge()

    while True:
        framing = _FRAMING_NEWLINE
        try:
            frame = _read_stdio_frame(stdin)
            if frame is None:
                break
            line, framing = frame
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise JsonRpcError(-32600, "JSON-RPC message must be an object.")
            messages = bridge.handle_json_rpc(payload)
        except JsonRpcError as exc:
            messages = [error_response(None, exc)]
        except Exception as exc:
            messages = [error_response(None, JsonRpcError(-32700, f"Invalid JSON-RPC payload: {exc}"))]
        for message in messages:
            _write_stdio_message(stdout, message.as_dict(), framing)
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
