"""LSP JSON-RPC 2.0 framing, encoding, decoding, and data structures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Tuple, Union


class LSPStatus(str, Enum):
    """Lifecycle status of a language server."""

    UNINSTALLED = "uninstalled"
    MISSING = "missing"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    IDLE = "idle"
    ERROR = "error"


class DiagnosticSeverity(IntEnum):
    """LSP DiagnosticSeverity values."""

    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4

    @classmethod
    def from_value(cls, val: Any) -> "DiagnosticSeverity":
        try:
            return cls(int(val))
        except (ValueError, TypeError):
            return cls.ERROR

    def to_label(self) -> str:
        return self.name.lower()


@dataclass
class Position:
    """Zero-based line and character position."""

    line: int
    character: int

    def to_dict(self) -> dict[str, int]:
        return {"line": self.line, "character": self.character}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Position":
        return cls(
            line=int(data.get("line", 0)),
            character=int(data.get("character", 0)),
        )


@dataclass
class Range:
    """LSP Range representing a text span."""

    start: Position
    end: Position

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.to_dict(), "end": self.end.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Range":
        return cls(
            start=Position.from_dict(data.get("start", {})),
            end=Position.from_dict(data.get("end", {})),
        )


@dataclass
class Location:
    """LSP Location representing a file URI and range."""

    uri: str
    range: Range

    def to_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "range": self.range.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Location":
        return cls(
            uri=str(data.get("uri", "")),
            range=Range.from_dict(data.get("range", {})),
        )


@dataclass
class Diagnostic:
    """LSP Diagnostic representation."""

    range: Range
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    code: Optional[Union[str, int]] = None
    source: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "range": self.range.to_dict(),
            "message": self.message,
            "severity": int(self.severity),
            "severityLabel": self.severity.to_label(),
        }
        if self.code is not None:
            result["code"] = str(self.code)
        if self.source:
            result["source"] = self.source
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Diagnostic":
        return cls(
            range=Range.from_dict(data.get("range", {})),
            message=str(data.get("message", "")),
            severity=DiagnosticSeverity.from_value(data.get("severity", 1)),
            code=data.get("code"),
            source=data.get("source"),
        )


@dataclass
class LSPMessage:
    jsonrpc: str = "2.0"


@dataclass
class LSPRequest(LSPMessage):
    id: int = 1
    method: str = ""
    params: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
        }
        if self.params is not None:
            payload["params"] = self.params
        return payload


@dataclass
class LSPNotification(LSPMessage):
    method: str = ""
    params: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
        }
        if self.params is not None:
            payload["params"] = self.params
        return payload


@dataclass
class LSPResponse(LSPMessage):
    id: Optional[int] = None
    result: Optional[Any] = None
    error: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        if self.error is not None:
            payload["error"] = self.error
        else:
            payload["result"] = self.result
        return payload


def encode_lsp_message(payload: dict[str, Any]) -> bytes:
    """Encode a dictionary into LSP Stdio JSON-RPC framed bytes."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


class LSPStreamDecoder:
    """Stream decoder that handles LSP framing (Content-Length and body extraction)."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        """Feed arbitrary bytes chunk and return list of fully decoded JSON-RPC dicts."""
        self._buffer.extend(chunk)
        messages: list[dict[str, Any]] = []

        while True:
            # Look for double separator (\r\n\r\n or \n\n)
            header_end = -1
            sep_len = 0

            crlf_pos = self._buffer.find(b"\r\n\r\n")
            lf_pos = self._buffer.find(b"\n\n")

            if crlf_pos != -1 and (lf_pos == -1 or crlf_pos <= lf_pos):
                header_end = crlf_pos
                sep_len = 4
            elif lf_pos != -1:
                header_end = lf_pos
                sep_len = 2

            if header_end == -1:
                break

            header_bytes = bytes(self._buffer[:header_end])
            header_str = header_bytes.decode("ascii", errors="replace")

            # Parse Content-Length
            content_length: Optional[int] = None
            for line in header_str.splitlines():
                if line.lower().startswith("content-length:"):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        try:
                            content_length = int(parts[1].strip())
                        except ValueError:
                            pass
                    break

            if content_length is None:
                # Malformed header, discard up to header_end + sep_len
                del self._buffer[: header_end + sep_len]
                continue

            body_start = header_end + sep_len
            body_end = body_start + content_length

            if len(self._buffer) < body_end:
                # Need more bytes for complete body
                break

            body_bytes = bytes(self._buffer[body_start:body_end])
            del self._buffer[:body_end]

            try:
                decoded = json.loads(body_bytes.decode("utf-8", errors="replace"))
                if isinstance(decoded, dict):
                    messages.append(decoded)
            except Exception:
                pass

        return messages
