from __future__ import annotations

import json
import sys
from typing import Any


_PROTOCOL_VERSION = 1
_ACCOUNT = "V8 Agent OS"
_MAX_REQUEST_BYTES = 1024 * 1024
_TARGET_PREFIXES = (
    "V8AgentOS/plugin/",
    "V8AgentOS/model/",
    "V8AgentOS/system/",
)
_TARGET_SUFFIX_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.flush()


def _failure(error_code: str) -> int:
    _emit({"protocolVersion": _PROTOCOL_VERSION, "ok": False, "errorCode": error_code})
    return 1


def _valid_target(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    prefix = next((item for item in _TARGET_PREFIXES if value.startswith(item)), "")
    suffix = value[len(prefix):] if prefix else ""
    return bool(suffix) and all(char in _TARGET_SUFFIX_CHARS for char in suffix)


def _load_backend(platform_name: str) -> Any:
    if platform_name == "linux" and sys.platform == "linux":
        from keyring.backends.SecretService import Keyring
    elif platform_name == "darwin" and sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
    else:
        raise RuntimeError("platform_mismatch")
    _ = Keyring.priority
    return Keyring()


def _classify_error(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    if any(token in name for token in ("locked", "denied", "interaction")):
        return "locked"
    if any(token in name for token in ("init", "unavailable", "secretservicenotavailable", "dbus")):
        return "unavailable"
    if str(exc) == "platform_mismatch":
        return "platform_mismatch"
    return "native_error"


def main() -> int:
    try:
        raw_request = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(raw_request) > _MAX_REQUEST_BYTES:
            return _failure("invalid_request")
        request = json.loads(raw_request.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _failure("invalid_request")
    if not isinstance(request, dict) or request.get("protocolVersion") != _PROTOCOL_VERSION:
        return _failure("invalid_request")
    action = request.get("action")
    target = request.get("target")
    platform_name = request.get("platform")
    if action not in {"read", "write", "delete"} or not _valid_target(target):
        return _failure("invalid_request")
    if platform_name not in {"linux", "darwin"}:
        return _failure("invalid_request")
    if action == "write":
        if not isinstance(request.get("secret"), str) or not request["secret"]:
            return _failure("invalid_request")
    elif "secret" in request:
        return _failure("invalid_request")

    try:
        backend = _load_backend(platform_name)
        if action == "read":
            secret = backend.get_password(target, _ACCOUNT)
            if secret is not None and not isinstance(secret, str):
                return _failure("native_error")
            _emit(
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "ok": True,
                    "found": secret is not None,
                    **({"secret": secret} if secret is not None else {}),
                }
            )
            return 0
        if action == "write":
            backend.set_password(target, _ACCOUNT, request["secret"])
            _emit({"protocolVersion": _PROTOCOL_VERSION, "ok": True, "written": True})
            return 0
        existing = backend.get_password(target, _ACCOUNT)
        if existing is None:
            _emit({"protocolVersion": _PROTOCOL_VERSION, "ok": True, "deleted": False})
            return 0
        backend.delete_password(target, _ACCOUNT)
        _emit({"protocolVersion": _PROTOCOL_VERSION, "ok": True, "deleted": True})
        return 0
    except Exception as exc:
        return _failure(_classify_error(exc))


if __name__ == "__main__":
    raise SystemExit(main())
