from __future__ import annotations

import secrets
import threading
from copy import deepcopy
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


_REF_PREFIX = "cred:v8-plugin:"
_TARGET_PREFIX = "V8AgentOS/plugin/"


class CredentialStoreError(RuntimeError):
    pass


class CredentialBackend(ABC):
    @abstractmethod
    def write(self, target: str, value: str) -> None: ...

    @abstractmethod
    def read(self, target: str) -> str | None: ...

    @abstractmethod
    def delete(self, target: str) -> bool: ...


class MemoryCredentialBackend(CredentialBackend):
    """Deterministic test backend. Never selected by production automatically."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.RLock()

    def write(self, target: str, value: str) -> None:
        with self._lock:
            self._values[target] = value

    def read(self, target: str) -> str | None:
        with self._lock:
            return self._values.get(target)

    def delete(self, target: str) -> bool:
        with self._lock:
            return self._values.pop(target, None) is not None


class WindowsCredentialBackend(CredentialBackend):
    def __init__(self) -> None:
        try:
            import win32cred  # type: ignore
        except Exception as exc:  # pragma: no cover - platform dependent
            raise CredentialStoreError("Windows Credential Manager is unavailable") from exc
        self._win32cred = win32cred

    def write(self, target: str, value: str) -> None:
        try:
            self._win32cred.CredWrite(
                {
                    "Type": self._win32cred.CRED_TYPE_GENERIC,
                    "TargetName": target,
                    "CredentialBlob": value,
                    "Persist": self._win32cred.CRED_PERSIST_LOCAL_MACHINE,
                    "UserName": "V8 Agent OS",
                    "Comment": "Managed plugin credential. Do not edit manually.",
                },
                0,
            )
        except Exception as exc:  # pragma: no cover - platform dependent
            raise CredentialStoreError("failed to write Windows credential") from exc

    def read(self, target: str) -> str | None:
        try:
            item = self._win32cred.CredRead(target, self._win32cred.CRED_TYPE_GENERIC, 0)
        except Exception as exc:  # pragma: no cover - platform dependent
            if getattr(exc, "winerror", None) == 1168:
                return None
            raise CredentialStoreError("failed to read Windows credential") from exc
        value = item.get("CredentialBlob")
        if isinstance(value, bytes):
            return value.decode("utf-16-le") if b"\x00" in value else value.decode("utf-8")
        return str(value) if value is not None else None

    def delete(self, target: str) -> bool:
        try:
            self._win32cred.CredDelete(target, self._win32cred.CRED_TYPE_GENERIC, 0)
            return True
        except Exception as exc:  # pragma: no cover - platform dependent
            if getattr(exc, "winerror", None) == 1168:
                return False
            raise CredentialStoreError("failed to delete Windows credential") from exc


class UnavailableCredentialBackend(CredentialBackend):
    def write(self, target: str, value: str) -> None:
        raise CredentialStoreError("secure OS credential storage is unavailable")

    def read(self, target: str) -> str | None:
        raise CredentialStoreError("secure OS credential storage is unavailable")

    def delete(self, target: str) -> bool:
        raise CredentialStoreError("secure OS credential storage is unavailable")


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    reference: str
    configured: bool

    def public_payload(self) -> dict[str, Any]:
        return {"secretRef": self.reference, "configured": self.configured}


class CredentialRefStore:
    def __init__(self, backend: CredentialBackend) -> None:
        self._backend = backend

    @staticmethod
    def _target(reference: str) -> str:
        normalized = str(reference or "").strip()
        if not normalized.startswith(_REF_PREFIX):
            raise CredentialStoreError("invalid credential reference")
        suffix = normalized[len(_REF_PREFIX):]
        if not suffix or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in suffix):
            raise CredentialStoreError("invalid credential reference")
        return f"{_TARGET_PREFIX}{suffix}"

    def put(self, value: str, *, reference: str | None = None) -> str:
        secret_value = str(value or "")
        if not secret_value:
            raise CredentialStoreError("credential value is empty")
        resolved_ref = str(reference or "").strip() or f"{_REF_PREFIX}{secrets.token_urlsafe(24)}"
        self._backend.write(self._target(resolved_ref), secret_value)
        return resolved_ref

    def resolve(self, reference: str) -> str:
        value = self._backend.read(self._target(reference))
        if value is None:
            raise CredentialStoreError("credential reference is missing")
        return value

    def status(self, reference: str) -> CredentialStatus:
        try:
            configured = self._backend.read(self._target(reference)) is not None
        except CredentialStoreError:
            configured = False
        return CredentialStatus(str(reference or ""), configured)

    def delete(self, reference: str) -> bool:
        return self._backend.delete(self._target(reference))


def _default_backend() -> CredentialBackend:
    try:
        return WindowsCredentialBackend()
    except CredentialStoreError:
        return UnavailableCredentialBackend()


credential_ref_store = CredentialRefStore(_default_backend())


def resolve_config_credential_refs(
    config: dict[str, Any],
    *,
    store: CredentialRefStore | None = None,
) -> dict[str, Any]:
    """Materialize plugin credential refs into an ephemeral runtime config."""

    result = deepcopy(config or {})
    refs = result.pop("x-v8-credential-refs", {})
    if not isinstance(refs, dict):
        return result
    active_store = store or credential_ref_store
    for binding in refs.values():
        if not isinstance(binding, dict):
            continue
        reference = str(binding.get("secretRef") or "").strip()
        if not reference:
            continue
        value = active_store.resolve(reference)
        target = str(binding.get("target") or "")
        target_name = str(binding.get("targetName") or "").strip()
        if target == "env" and target_name:
            result.setdefault("env", {})[target_name] = value
        elif target == "header" and target_name:
            result.setdefault("headers", {})[target_name] = value
    return result


__all__ = [
    "CredentialBackend",
    "CredentialRefStore",
    "CredentialStatus",
    "CredentialStoreError",
    "MemoryCredentialBackend",
    "UnavailableCredentialBackend",
    "WindowsCredentialBackend",
    "credential_ref_store",
    "resolve_config_credential_refs",
]
