from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_REF_PREFIXES = {
    "plugin": "cred:v8-plugin:",
    "model": "cred:v8-model:",
    "system": "cred:v8-system:",
}
_TARGET_PREFIXES = {
    "plugin": "V8AgentOS/plugin/",
    "model": "V8AgentOS/model/",
    "system": "V8AgentOS/system/",
}
_OS_CREDENTIAL_ACCOUNT = "V8 Agent OS"
_NATIVE_HELPER_PROTOCOL_VERSION = 1
_NATIVE_HELPER_TIMEOUT_SECONDS = 6.0
_NATIVE_KEYRING_HELPER = Path(__file__).with_name("native_keyring_helper.py")
_NATIVE_HELPER_ENV_KEYS = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "LOGNAME",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
        "WAYLAND_DISPLAY",
        "WINDIR",
        "XDG_RUNTIME_DIR",
    }
)


class CredentialStoreError(RuntimeError):
    pass


class CredentialOperationIndeterminate(CredentialStoreError):
    """A timed-out mutation may already have reached the native credential store."""

    def __init__(self, operation: str, *, reference: str = "") -> None:
        self.operation = str(operation or "").strip().lower()
        self.reference = str(reference or "").strip()
        detail = f" for {self.reference}" if self.reference else ""
        super().__init__(f"credential {self.operation} outcome is indeterminate{detail}")

    def with_reference(self, reference: str) -> CredentialOperationIndeterminate:
        return type(self)(self.operation, reference=reference)


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
    def __init__(self, win32cred_module: Any | None = None) -> None:
        if win32cred_module is None:
            try:
                import win32cred  # type: ignore
            except Exception as exc:  # pragma: no cover - platform dependent
                raise CredentialStoreError("Windows Credential Manager is unavailable") from exc
            win32cred_module = win32cred
        self._win32cred = win32cred_module

    def write(self, target: str, value: str) -> None:
        try:
            self._win32cred.CredWrite(
                {
                    "Type": self._win32cred.CRED_TYPE_GENERIC,
                    "TargetName": target,
                    "CredentialBlob": value,
                    "Persist": self._win32cred.CRED_PERSIST_LOCAL_MACHINE,
                    "UserName": _OS_CREDENTIAL_ACCOUNT,
                    "Comment": "Managed V8 Agent OS credential. Do not edit manually.",
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


class _KeyringCredentialBackend(CredentialBackend):
    """Run native keyring operations outside the long-lived Engine process."""

    def __init__(
        self,
        backend: Any | None,
        *,
        platform_name: str,
        label: str,
    ) -> None:
        self._backend = backend
        self._platform_name = platform_name
        self._label = label
        self._helper_path = _NATIVE_KEYRING_HELPER
        self._timeout_seconds = _NATIVE_HELPER_TIMEOUT_SECONDS

    @staticmethod
    def _helper_environment() -> dict[str, str]:
        return {
            key: value
            for key in _NATIVE_HELPER_ENV_KEYS
            if (value := os.environ.get(key)) is not None
        }

    def _invoke_helper(self, action: str, target: str, *, value: str | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {
            "protocolVersion": _NATIVE_HELPER_PROTOCOL_VERSION,
            "platform": self._platform_name,
            "action": action,
            "target": target,
        }
        if value is not None:
            request["secret"] = value
        payload = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        helper_path = self._helper_path.resolve()
        if not helper_path.is_file():
            raise CredentialStoreError(f"{self._label} credential helper is unavailable")
        command = [
            sys.executable,
            "-I",
            "-B",
            "-X",
            "utf8",
            "-u",
            str(helper_path),
        ]
        process_options: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": self._helper_environment(),
            "shell": False,
        }
        if os.name == "nt":  # Tests exercise the helper boundary on Windows too.
            process_options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            process_options["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **process_options)
        except OSError as exc:
            raise CredentialStoreError(f"{self._label} credential helper is unavailable") from exc
        try:
            stdout, _stderr = process.communicate(payload, timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            if action in {"write", "delete"}:
                raise CredentialOperationIndeterminate(action) from None
            raise CredentialStoreError(f"{self._label} credential read timed out") from None

        try:
            response = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialStoreError(f"{self._label} credential helper returned an invalid response") from exc
        if not isinstance(response, dict) or response.get("protocolVersion") != _NATIVE_HELPER_PROTOCOL_VERSION:
            raise CredentialStoreError(f"{self._label} credential helper returned an invalid response")
        if response.get("ok") is not True:
            error_code = str(response.get("errorCode") or "native_error")
            if error_code == "locked":
                raise CredentialStoreError(f"{self._label} is locked or requires user interaction")
            if error_code in {"platform_mismatch", "unavailable"}:
                raise CredentialStoreError(f"{self._label} is unavailable")
            if error_code == "invalid_request":
                raise CredentialStoreError(f"{self._label} credential helper rejected the request")
            raise CredentialStoreError(f"{self._label} credential operation failed")
        return response

    def write(self, target: str, value: str) -> None:
        if self._backend is None:
            self._invoke_helper("write", target, value=value)
            return
        try:
            self._backend.set_password(target, _OS_CREDENTIAL_ACCOUNT, value)
        except CredentialStoreError:
            raise
        except Exception as exc:  # pragma: no cover - native backend dependent
            raise CredentialStoreError(f"failed to write {self._label} credential") from exc

    def read(self, target: str) -> str | None:
        if self._backend is None:
            response = self._invoke_helper("read", target)
            if response.get("found") is not True:
                return None
            value = response.get("secret")
            if not isinstance(value, str):
                raise CredentialStoreError(f"{self._label} returned an invalid credential value")
            return value
        try:
            value = self._backend.get_password(target, _OS_CREDENTIAL_ACCOUNT)
        except CredentialStoreError:
            raise
        except Exception as exc:  # pragma: no cover - native backend dependent
            raise CredentialStoreError(f"failed to read {self._label} credential") from exc
        if value is None:
            return None
        if not isinstance(value, str):
            raise CredentialStoreError(f"{self._label} returned an invalid credential value")
        return value

    def delete(self, target: str) -> bool:
        if self._backend is None:
            response = self._invoke_helper("delete", target)
            return response.get("deleted") is True
        if self.read(target) is None:
            return False
        try:
            self._backend.delete_password(target, _OS_CREDENTIAL_ACCOUNT)
            return True
        except CredentialStoreError:
            raise
        except Exception as exc:  # pragma: no cover - native backend dependent
            raise CredentialStoreError(f"failed to delete {self._label} credential") from exc


class LinuxSecretServiceCredentialBackend(_KeyringCredentialBackend):
    def __init__(self, backend: Any | None = None) -> None:
        super().__init__(
            backend,
            platform_name="linux",
            label="Linux Secret Service",
        )


class MacOSKeychainCredentialBackend(_KeyringCredentialBackend):
    def __init__(self, backend: Any | None = None) -> None:
        super().__init__(backend, platform_name="darwin", label="macOS Keychain")


class UnavailableCredentialBackend(CredentialBackend):
    def __init__(self, reason: str = "secure OS credential storage is unavailable") -> None:
        self._reason = reason

    def write(self, target: str, value: str) -> None:
        raise CredentialStoreError(self._reason)

    def read(self, target: str) -> str | None:
        raise CredentialStoreError(self._reason)

    def delete(self, target: str) -> bool:
        raise CredentialStoreError(self._reason)


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
        namespace = next(
            (key for key, prefix in _REF_PREFIXES.items() if normalized.startswith(prefix)),
            "",
        )
        if not namespace:
            raise CredentialStoreError("invalid credential reference")
        prefix = _REF_PREFIXES[namespace]
        suffix = normalized[len(prefix):]
        if not suffix or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in suffix):
            raise CredentialStoreError("invalid credential reference")
        return f"{_TARGET_PREFIXES[namespace]}{suffix}"

    def put(self, value: str, *, reference: str | None = None, namespace: str = "plugin") -> str:
        secret_value = str(value or "")
        if not secret_value:
            raise CredentialStoreError("credential value is empty")
        normalized_namespace = str(namespace or "plugin").strip().lower()
        if normalized_namespace not in _REF_PREFIXES:
            raise CredentialStoreError("unsupported credential namespace")
        resolved_ref = str(reference or "").strip() or f"{_REF_PREFIXES[normalized_namespace]}{secrets.token_urlsafe(24)}"
        try:
            self._backend.write(self._target(resolved_ref), secret_value)
        except CredentialOperationIndeterminate as exc:
            raise exc.with_reference(resolved_ref) from exc
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
        normalized_reference = str(reference or "").strip()
        try:
            return self._backend.delete(self._target(normalized_reference))
        except CredentialOperationIndeterminate as exc:
            raise exc.with_reference(normalized_reference) from exc


def _default_backend(platform_name: str | None = None) -> CredentialBackend:
    resolved_platform = platform_name or sys.platform
    try:
        if resolved_platform == "win32":
            return WindowsCredentialBackend()
        if resolved_platform == "linux":
            return LinuxSecretServiceCredentialBackend()
        if resolved_platform == "darwin":
            return MacOSKeychainCredentialBackend()
        raise CredentialStoreError(
            f"secure OS credential storage is unavailable on platform {resolved_platform}"
        )
    except CredentialStoreError as exc:
        return UnavailableCredentialBackend(str(exc))


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
    "CredentialOperationIndeterminate",
    "CredentialStatus",
    "CredentialStoreError",
    "LinuxSecretServiceCredentialBackend",
    "MacOSKeychainCredentialBackend",
    "MemoryCredentialBackend",
    "UnavailableCredentialBackend",
    "WindowsCredentialBackend",
    "credential_ref_store",
    "resolve_config_credential_refs",
]
