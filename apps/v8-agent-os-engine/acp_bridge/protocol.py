from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JSONRPC_VERSION = "2.0"


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


@dataclass(frozen=True)
class JsonRpcMessage:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload


def result_response(request_id: Any, result: dict[str, Any] | list[Any] | str | bool | None) -> JsonRpcMessage:
    return JsonRpcMessage({"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result})


def error_response(request_id: Any, error: JsonRpcError) -> JsonRpcMessage:
    payload: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {
            "code": error.code,
            "message": error.message,
        },
    }
    if error.data:
        payload["error"]["data"] = error.data
    return JsonRpcMessage(payload)


def notification(method: str, params: dict[str, Any] | None = None) -> JsonRpcMessage:
    payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        payload["params"] = params
    return JsonRpcMessage(payload)


def require_object(value: Any, *, field_name: str = "params") -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise JsonRpcError(-32602, f"{field_name} must be an object.")
    return value
