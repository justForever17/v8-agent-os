"""Encrypted file credentials for headless Linux Engine deployments.

The key is supplied by an explicit path (environment or constructor), while the
encrypted store remains under the instance state root.  This module never creates
or replaces a key implicitly during read/write.
"""
from __future__ import annotations

import json
import base64
import os
import secrets
import stat
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = 1
_KEY_BYTES = 32
_NONCE_BYTES = 12
_KEY_ENV = "V8_AGENT_OS_CREDENTIAL_KEY_FILE"
_CREDENTIALS_DIR = "CREDENTIALS_DIRECTORY"
_PROCESS_LOCK = threading.RLock()


def _lock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LinuxServerCredentialBackend:
    """AES-256-GCM credential map for a headless Linux server profile."""

    def __init__(self, state_root: str | os.PathLike[str], *, key_file: str | os.PathLike[str] | None = None, instance_id: str = "default") -> None:
        self.state_root = Path(state_root)
        self.credential_dir = self.state_root / "credentials"
        self.ciphertext_path = self.credential_dir / "server.enc"
        self.lock_path = self.credential_dir / "server.lock"
        self.instance_id = str(instance_id or "default")
        self.key_path = self.resolve_key_path(key_file)

    @staticmethod
    def resolve_key_path(key_file: str | os.PathLike[str] | None = None) -> Path:
        from .credentials import CredentialStoreError
        explicit = str(key_file or os.environ.get(_KEY_ENV) or "").strip()
        if explicit:
            return Path(explicit).expanduser()
        directory = str(os.environ.get(_CREDENTIALS_DIR) or "").strip()
        if directory:
            return Path(directory) / "v8-agent-os-credential-key"
        raise CredentialStoreError(f"server credential key path is required ({_KEY_ENV} or {_CREDENTIALS_DIR})")

    @classmethod
    def initialize_key(cls, key_file: str | os.PathLike[str] | None = None, *, state_root: str | os.PathLike[str] | None = None) -> Path:
        from .credentials import CredentialStoreError
        path = cls.resolve_key_path(key_file)
        if not path.is_absolute():
            raise CredentialStoreError("server credential key path must be absolute")
        if state_root is not None and (Path(state_root) / "credentials" / "server.enc").exists():
            raise CredentialStoreError("encrypted credentials already exist; restore their key instead of creating a new one")
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        cls._check_permissions(path.parent, 0o700, "key directory")
        if path.exists() or path.is_symlink():
            raise CredentialStoreError("server credential key already exists; refusing to replace it")
        key = secrets.token_bytes(_KEY_BYTES)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(key)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            key = b""
        cls._check_permissions(path, 0o600, "key file")
        return path

    @staticmethod
    def _check_permissions(path: Path, expected: int, label: str) -> None:
        from .credentials import CredentialStoreError
        if path.is_symlink():
            raise CredentialStoreError(f"{label} must not be a symbolic link")
        if os.name != "posix":
            return
        details = path.stat()
        mode = stat.S_IMODE(details.st_mode)
        if mode != expected or details.st_uid != os.geteuid():
            raise CredentialStoreError(f"{label} permissions must be {expected:04o}")

    def _key(self) -> bytes:
        from .credentials import CredentialStoreError
        try:
            # systemd credentials directories can be read-only (0500/0700);
            # explicitly configured key files require a private parent too.
            if not os.environ.get(_CREDENTIALS_DIR) or self.key_path.parent != Path(os.environ[_CREDENTIALS_DIR]):
                self._check_permissions(self.key_path.parent, 0o700, "key directory")
                self._check_permissions(self.key_path, 0o600, "key file")
            else:
                for path, allowed, label in ((self.key_path.parent, {0o500, 0o700}, "systemd credential directory"), (self.key_path, {0o400, 0o600}, "systemd credential file")):
                    mode = stat.S_IMODE(path.stat().st_mode)
                    if os.name == "posix" and mode not in allowed:
                        raise CredentialStoreError(f"{label} must be private and readable by the service")
                    self._check_permissions(path, mode, label)
            key = self.key_path.read_bytes()
        except FileNotFoundError as exc:
            raise CredentialStoreError("server credential key is missing; initialize it explicitly") from exc
        except OSError as exc:
            raise CredentialStoreError("server credential key cannot be read") from exc
        if len(key) != _KEY_BYTES:
            raise CredentialStoreError("server credential key must contain exactly 32 bytes")
        return key

    def _aad(self) -> bytes:
        return f"v8-agent-os/server-credentials/{self.instance_id}/v{_VERSION}".encode()

    def _load(self) -> dict[str, str]:
        from .credentials import CredentialStoreError
        # Validate the key even for an empty store; a missing key must never
        # look like a valid freshly initialized server.
        key = self._key()
        if self.ciphertext_path.is_symlink():
            raise CredentialStoreError("credential ciphertext must not be a symbolic link")
        if not self.ciphertext_path.exists():
            return {}
        try:
            self._check_permissions(self.ciphertext_path, 0o600, "credential ciphertext")
            envelope = json.loads(self.ciphertext_path.read_text("utf-8"))
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            if envelope.get("version") != _VERSION or envelope.get("instanceId") != self.instance_id:
                raise ValueError("version or instance mismatch")
            data = AESGCM(key).decrypt(nonce, ciphertext, self._aad())
            value = json.loads(data.decode("utf-8"))
            if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
                raise ValueError("invalid credential map")
            return value
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("server credential ciphertext authentication failed") from exc

    def _save(self, values: dict[str, str]) -> None:
        self._check_permissions(self.credential_dir, 0o700, "credential directory")
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        nonce = secrets.token_bytes(_NONCE_BYTES)
        envelope = {"version": _VERSION, "instanceId": self.instance_id, "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(AESGCM(self._key()).encrypt(nonce, payload, self._aad())).decode()}
        fd, temp = tempfile.mkstemp(prefix="server.enc.", dir=self.credential_dir)
        try:
            if os.name == "posix": os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(envelope, stream, separators=(",", ":"))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, self.ciphertext_path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        self._check_permissions(self.ciphertext_path, 0o600, "credential ciphertext")

    @contextmanager
    def _locked(self):
        with _PROCESS_LOCK:
            self.credential_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._check_permissions(self.credential_dir, 0o700, "credential directory")
            if self.lock_path.exists() or self.lock_path.is_symlink():
                self._check_permissions(self.lock_path, 0o600, "credential lock")
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "r+b") as handle:
                _lock_file(handle)
                try:
                    yield handle
                finally:
                    _unlock_file(handle)

    def write(self, target: str, value: str) -> None:
        with self._locked():
            values = self._load(); values[str(target)] = str(value); self._save(values)

    def read(self, target: str) -> str | None:
        with self._locked(): return self._load().get(str(target))

    def delete(self, target: str) -> bool:
        with self._locked():
            values = self._load(); existed = str(target) in values
            if existed: del values[str(target)]; self._save(values)
            return existed


__all__ = ["LinuxServerCredentialBackend"]
