from __future__ import annotations

import base64
import binascii
import contextvars
import dataclasses
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from pathlib import Path, PurePath
from re import Pattern
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from core.langgraph_checkpoint_bootstrap import enforce_strict_langgraph_msgpack
from core.security.credentials import CredentialRefStore, CredentialStoreError, credential_ref_store

enforce_strict_langgraph_msgpack()

from langgraph.checkpoint.serde import _msgpack as langgraph_msgpack
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


CHECKPOINT_SECURITY_POLICY_VERSION = 2
CHECKPOINT_SECURITY_AUDIT_TABLE = "v8_checkpoint_security_audit"
CHECKPOINT_SECURITY_STATE_TABLE = "v8_checkpoint_security_state"
CHECKPOINT_ENCRYPTION_REFERENCE = "cred:v8-system:checkpoint-aes-v1"
CHECKPOINT_ENCRYPTION_ENV = "V8_CHECKPOINT_AES_KEY"
CHECKPOINT_ENCRYPTION_SCHEME = "v8aesgcm1"
CHECKPOINT_ENCRYPTION_AAD = b"v8-agent-os/checkpoint/v1"
CHECKPOINT_MESSAGE_RETENTION_METADATA_KEY = "v8_messages_checkpoint"

# No V8OS-owned Python class is currently necessary in durable graph state.
# Add only exact, stable (module, class) symbols after a migration/resume test proves
# that a primitive DTO or artifact/detail reference cannot represent the state.
V8_STABLE_MSGPACK_ALLOWLIST: tuple[tuple[str, str], ...] = ()

_ALLOWED_SERIALIZATION_TYPES = frozenset({"null", "bytes", "bytearray", "msgpack"})
_ACTIVE_SERDE_EVENTS: contextvars.ContextVar[list[dict[str, str]] | None] = contextvars.ContextVar(
    "v8_checkpoint_serde_events",
    default=None,
)


class CheckpointSecurityError(RuntimeError):
    """Base error for checkpoint integrity and compatibility failures."""


class CheckpointDeserializationBlocked(CheckpointSecurityError):
    """Raised when strict deserialization refuses an object reconstruction."""


class CheckpointWriteContractError(CheckpointSecurityError):
    """Raised before an unsupported Python object reaches durable state."""


class CheckpointPreflightError(CheckpointSecurityError):
    """Raised when historical checkpoint compatibility cannot be proven."""


class CheckpointEncryptionError(CheckpointSecurityError):
    """Raised when checkpoint confidentiality cannot be established."""


class CheckpointEncryptionKeyError(CheckpointEncryptionError):
    """Raised when the durable checkpoint key is missing or invalid."""


class CheckpointDecryptionBlocked(CheckpointEncryptionError):
    """Raised when authenticated checkpoint decryption fails."""


@dataclasses.dataclass(frozen=True, slots=True)
class CheckpointEncryptionKey:
    key: bytes
    source: str
    fingerprint: str


def _decode_checkpoint_key(raw_value: str) -> bytes:
    raw = str(raw_value or "").strip()
    if not raw:
        raise CheckpointEncryptionKeyError("Checkpoint encryption key is empty.")
    try:
        if raw.startswith("base64:"):
            key = base64.urlsafe_b64decode(raw[7:].encode("ascii"))
        elif len(raw) == 64:
            key = bytes.fromhex(raw)
        else:
            key = raw.encode("utf-8")
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise CheckpointEncryptionKeyError("Checkpoint encryption key encoding is invalid.") from exc
    if len(key) != 32:
        raise CheckpointEncryptionKeyError("V8OS checkpoint encryption requires an exact 32-byte AES-256 key.")
    return key


class CheckpointKeyManager:
    """Resolve one stable AES-256 key without ever persisting it in config or SQLite."""

    def __init__(self, store: CredentialRefStore | None = None) -> None:
        self._store = store or credential_ref_store
        self._lock = threading.RLock()
        self._cached: CheckpointEncryptionKey | None = None

    def resolve(self) -> CheckpointEncryptionKey:
        with self._lock:
            if self._cached is not None:
                return self._cached

            configured = str(
                os.environ.get(CHECKPOINT_ENCRYPTION_ENV)
                or os.environ.get("LANGGRAPH_AES_KEY")
                or ""
            ).strip()
            source = "environment"
            if not configured:
                source = "os_credential_store"
                try:
                    configured = self._store.resolve(CHECKPOINT_ENCRYPTION_REFERENCE)
                except CredentialStoreError as exc:
                    if "missing" not in str(exc).lower():
                        raise CheckpointEncryptionKeyError(
                            "Secure OS credential storage is unavailable; provide V8_CHECKPOINT_AES_KEY."
                        ) from exc
                    generated = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
                    configured = f"base64:{generated}"
                    try:
                        self._store.put(
                            configured,
                            reference=CHECKPOINT_ENCRYPTION_REFERENCE,
                            namespace="system",
                        )
                    except CredentialStoreError as store_exc:
                        raise CheckpointEncryptionKeyError(
                            "V8OS could not persist its checkpoint key in secure OS credential storage."
                        ) from store_exc

            key = _decode_checkpoint_key(configured)
            resolved = CheckpointEncryptionKey(
                key=key,
                source=source,
                fingerprint=hashlib.sha256(key).hexdigest()[:16],
            )
            self._cached = resolved
            return resolved


class V8AesGcmCipher:
    """Authenticated AES-256-GCM cipher used by LangGraph's EncryptedSerializer."""

    def __init__(self, key_info: CheckpointEncryptionKey) -> None:
        self.key_info = key_info
        self._cipher = AESGCM(key_info.key)

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, CHECKPOINT_ENCRYPTION_AAD)
        return CHECKPOINT_ENCRYPTION_SCHEME, nonce + ciphertext

    def decrypt(self, ciphername: str, ciphertext: bytes) -> bytes:
        if ciphername != CHECKPOINT_ENCRYPTION_SCHEME:
            raise CheckpointDecryptionBlocked(f"Unsupported checkpoint cipher '{ciphername}'.")
        if len(ciphertext) < 28:
            raise CheckpointDecryptionBlocked("Encrypted checkpoint payload is truncated.")
        try:
            return self._cipher.decrypt(
                ciphertext[:12],
                ciphertext[12:],
                CHECKPOINT_ENCRYPTION_AAD,
            )
        except InvalidTag as exc:
            raise CheckpointDecryptionBlocked(
                "Checkpoint authentication failed; recovery was stopped before state materialization."
            ) from exc


checkpoint_key_manager = CheckpointKeyManager()


def _record_serde_event(event: dict[str, str]) -> None:
    bucket = _ACTIVE_SERDE_EVENTS.get()
    if bucket is not None:
        bucket.append(dict(event))


# Keep the unregister callback alive for the process lifetime alongside the listener.
_SERDE_LISTENER_UNREGISTER = register_serde_event_listener(_record_serde_event)


def _type_key(value: object) -> tuple[str, str]:
    value_type = type(value)
    return value_type.__module__, value_type.__name__


def _format_type_key(key: tuple[str, str]) -> str:
    return f"{key[0]}.{key[1]}"


class StrictCheckpointSerializer(JsonPlusSerializer):
    """JsonPlus serializer with strict reconstruction and a write-side type contract."""

    def __init__(
        self,
        *,
        extra_allowlist: Iterable[tuple[str, str]] = V8_STABLE_MSGPACK_ALLOWLIST,
    ) -> None:
        super().__init__(
            pickle_fallback=False,
            allowed_json_modules=None,
            allowed_msgpack_modules=tuple(extra_allowlist) or None,
        )

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        serialization_type = str(data[0] or "")
        if serialization_type not in _ALLOWED_SERIALIZATION_TYPES:
            raise CheckpointDeserializationBlocked(
                f"Checkpoint serialization type '{serialization_type or 'unknown'}' is not allowed."
            )

        events: list[dict[str, str]] = []
        token = _ACTIVE_SERDE_EVENTS.set(events)
        try:
            value = super().loads_typed(data)
        except CheckpointSecurityError:
            raise
        except Exception as exc:
            raise CheckpointDeserializationBlocked(
                f"Checkpoint payload could not be restored under strict policy: {type(exc).__name__}."
            ) from exc
        finally:
            _ACTIVE_SERDE_EVENTS.reset(token)

        if events:
            event = events[0]
            symbol = _format_type_key((str(event.get("module") or "unknown"), str(event.get("name") or "unknown")))
            method = str(event.get("method") or "").strip()
            suffix = f".{method}" if method else ""
            raise CheckpointDeserializationBlocked(
                f"Checkpoint object reconstruction was blocked: {symbol}{suffix}."
            )
        return value

    def assert_write_safe(self, value: Any, *, root: str) -> None:
        allowed_modules = getattr(self, "_allowed_msgpack_modules", None)
        if allowed_modules is True:
            raise CheckpointWriteContractError("Checkpoint serializer is not running in strict mode.")
        explicit_allowed = set(allowed_modules or ())
        safe_types = set(langgraph_msgpack.SAFE_MSGPACK_TYPES)
        scalar_types = (bool, int, float, str, bytes, bytearray)
        stack: list[Any] = [value]
        seen: set[int] = set()

        while stack:
            current = stack.pop()
            if current is None or isinstance(current, scalar_types):
                continue

            if isinstance(current, dict):
                identity = id(current)
                if identity in seen:
                    continue
                seen.add(identity)
                for key, item in current.items():
                    if item is not None and not isinstance(item, scalar_types):
                        stack.append(item)
                    if key is not None and not isinstance(key, scalar_types):
                        stack.append(key)
                continue

            if isinstance(current, (list, tuple, set, frozenset, deque)):
                if isinstance(current, (set, frozenset, deque)):
                    collection_key = _type_key(current)
                    if collection_key not in safe_types and collection_key not in explicit_allowed:
                        raise CheckpointWriteContractError(
                            f"Unsupported checkpoint object under {root}: {_format_type_key(collection_key)}."
                        )
                identity = id(current)
                if identity in seen:
                    continue
                seen.add(identity)
                for item in current:
                    if item is not None and not isinstance(item, scalar_types):
                        stack.append(item)
                continue

            if isinstance(
                current,
                (
                    PurePath,
                    Pattern,
                    UUID,
                    Decimal,
                    IPv4Address,
                    IPv4Interface,
                    IPv4Network,
                    IPv6Address,
                    IPv6Interface,
                    IPv6Network,
                    datetime,
                    date,
                    datetime_time,
                    timedelta,
                    timezone,
                    ZoneInfo,
                ),
            ):
                leaf_key = _type_key(current)
                if leaf_key not in safe_types and leaf_key not in explicit_allowed:
                    raise CheckpointWriteContractError(
                        f"Unsupported checkpoint object under {root}: {_format_type_key(leaf_key)}."
                    )
                continue

            if isinstance(current, BaseException):
                # JsonPlusSerializer persists exceptions as repr strings, not constructors.
                continue

            numpy_module = sys.modules.get("numpy")
            if numpy_module is not None and isinstance(current, numpy_module.ndarray):
                continue

            key = _type_key(current)
            if key not in safe_types and key not in explicit_allowed:
                raise CheckpointWriteContractError(
                    f"Unsupported checkpoint object under {root}: {_format_type_key(key)}."
                )

            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)

            if isinstance(current, Enum):
                if current.value is not None and not isinstance(current.value, scalar_types):
                    stack.append(current.value)
                continue

            if hasattr(current, "get_secret_value") and callable(current.get_secret_value):
                raise CheckpointWriteContractError(
                    f"Secret-like object is not allowed under {root}: {_format_type_key(key)}."
                )

            if hasattr(current, "model_dump") and callable(current.model_dump):
                fields = dict(getattr(current, "__dict__", {}) or {})
                extras = getattr(current, "__pydantic_extra__", None)
                if isinstance(extras, dict):
                    fields.update(extras)
                for item in fields.values():
                    if item is not None and not isinstance(item, scalar_types):
                        stack.append(item)
                continue

            if hasattr(current, "dict") and callable(current.dict):
                for item in dict(getattr(current, "__dict__", {}) or {}).values():
                    if item is not None and not isinstance(item, scalar_types):
                        stack.append(item)
                continue

            if dataclasses.is_dataclass(current):
                for field in dataclasses.fields(current):
                    item = getattr(current, field.name)
                    if item is not None and not isinstance(item, scalar_types):
                        stack.append(item)
                continue

            if hasattr(current, "_asdict") and callable(current._asdict):
                for item in current._asdict().values():
                    if item is not None and not isinstance(item, scalar_types):
                        stack.append(item)
                continue

            if hasattr(current, "node") and hasattr(current, "arg"):
                if current.node is not None and not isinstance(current.node, scalar_types):
                    stack.append(current.node)
                if current.arg is not None and not isinstance(current.arg, scalar_types):
                    stack.append(current.arg)
                timeout_value = getattr(current, "timeout", None)
                if timeout_value is not None and not isinstance(timeout_value, scalar_types):
                    stack.append(timeout_value)
                continue

            slots = getattr(type(current), "__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            if slots:
                for name in slots:
                    if hasattr(current, name):
                        item = getattr(current, name)
                        if item is not None and not isinstance(item, scalar_types):
                            stack.append(item)
                continue

            raise CheckpointWriteContractError(
                f"Checkpoint object has no stable state contract under {root}: {_format_type_key(key)}."
            )


def strict_checkpoint_serializer(serializer: SerializerProtocol) -> StrictCheckpointSerializer:
    if isinstance(serializer, StrictCheckpointSerializer):
        return serializer
    if isinstance(serializer, EncryptedSerializer) and isinstance(serializer.serde, StrictCheckpointSerializer):
        return serializer.serde
    raise CheckpointSecurityError("Checkpoint serializer is not strict-msgpack governed.")


def checkpoint_encryption_key_info(serializer: SerializerProtocol) -> CheckpointEncryptionKey:
    if isinstance(serializer, EncryptedSerializer) and isinstance(serializer.cipher, V8AesGcmCipher):
        return serializer.cipher.key_info
    raise CheckpointEncryptionError("Checkpoint serializer is not protected by V8OS AES-256-GCM.")


def build_checkpoint_serializer(
    *,
    key_manager: CheckpointKeyManager | None = None,
) -> EncryptedSerializer:
    strict = StrictCheckpointSerializer(extra_allowlist=V8_STABLE_MSGPACK_ALLOWLIST)
    key_info = (key_manager or checkpoint_key_manager).resolve()
    return EncryptedSerializer(V8AesGcmCipher(key_info), strict)


def _is_current_encryption_type(serialization_type: str) -> bool:
    return str(serialization_type or "").endswith(f"+{CHECKPOINT_ENCRYPTION_SCHEME}")


def checkpoint_message_retention_mode(checkpoint: Any) -> str:
    if not isinstance(checkpoint, dict):
        return "none"
    values = checkpoint.get("channel_values")
    versions = checkpoint.get("channel_versions")
    if isinstance(values, dict) and "messages" in values:
        return "seed"
    if isinstance(versions, dict) and "messages" in versions:
        return "delta"
    return "none"


def checkpoint_retention_metadata(metadata: Any, checkpoint: Any) -> dict[str, Any]:
    result = dict(metadata or {})
    result[CHECKPOINT_MESSAGE_RETENTION_METADATA_KEY] = checkpoint_message_retention_mode(checkpoint)
    return result


def _require_encryption_migration_space(path: Path, *, database_bytes: int) -> None:
    if database_bytes <= 0:
        return
    free_bytes = int(shutil.disk_usage(path.parent).free)
    required_bytes = database_bytes + 256 * 1024 * 1024
    if free_bytes < required_bytes:
        raise CheckpointPreflightError(
            "Checkpoint encryption migration requires free space for SQLite WAL and physical compaction."
        )


def run_checkpoint_preflight(path: Path, serializer: SerializerProtocol) -> dict[str, Any]:
    """Strictly validate and atomically encrypt historical state once per policy/key."""
    started = time.perf_counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    strict = strict_checkpoint_serializer(serializer)
    key_info = checkpoint_encryption_key_info(serializer)
    conn = sqlite3.connect(path, timeout=120)
    try:
        conn.execute("PRAGMA busy_timeout=120000")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {CHECKPOINT_SECURITY_AUDIT_TABLE} (
                policy_version INTEGER PRIMARY KEY,
                checkpoint_rows INTEGER NOT NULL,
                write_rows INTEGER NOT NULL,
                database_bytes INTEGER NOT NULL,
                completed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {CHECKPOINT_SECURITY_STATE_TABLE} (
                policy_version INTEGER NOT NULL,
                key_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                checkpoint_rows INTEGER NOT NULL DEFAULT 0,
                write_rows INTEGER NOT NULL DEFAULT 0,
                encrypted_checkpoint_rows INTEGER NOT NULL DEFAULT 0,
                encrypted_write_rows INTEGER NOT NULL DEFAULT 0,
                database_bytes INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (policy_version, key_fingerprint)
            )
            """
        )
        conn.commit()

        completed_with_other_key = conn.execute(
            f"""
            SELECT key_fingerprint FROM {CHECKPOINT_SECURITY_STATE_TABLE}
            WHERE policy_version = ? AND state = 'completed' AND key_fingerprint <> ?
            LIMIT 1
            """,
            (CHECKPOINT_SECURITY_POLICY_VERSION, key_info.fingerprint),
        ).fetchone()
        if completed_with_other_key is not None:
            raise CheckpointEncryptionKeyError(
                "Checkpoint database was encrypted with a different key; automatic key substitution is blocked."
            )

        existing = conn.execute(
            f"""
            SELECT checkpoint_rows, write_rows, encrypted_checkpoint_rows,
                   encrypted_write_rows, database_bytes, completed_at
            FROM {CHECKPOINT_SECURITY_STATE_TABLE}
            WHERE policy_version = ? AND key_fingerprint = ? AND state = 'completed'
            """,
            (CHECKPOINT_SECURITY_POLICY_VERSION, key_info.fingerprint),
        ).fetchone()
        if existing is not None:
            unencrypted_checkpoint = conn.execute(
                """
                SELECT 1 FROM checkpoints
                WHERE checkpoint IS NOT NULL
                  AND COALESCE(type, '') NOT LIKE ?
                LIMIT 1
                """,
                (f"%+{CHECKPOINT_ENCRYPTION_SCHEME}",),
            ).fetchone()
            unencrypted_write = conn.execute(
                """
                SELECT 1 FROM writes
                WHERE value IS NOT NULL
                  AND COALESCE(type, '') NOT LIKE ?
                LIMIT 1
                """,
                (f"%+{CHECKPOINT_ENCRYPTION_SCHEME}",),
            ).fetchone()
            if unencrypted_checkpoint is not None or unencrypted_write is not None:
                raise CheckpointPreflightError(
                    "Checkpoint security marker is stale: an unencrypted or unsupported payload was detected."
                )
            return {
                "policyVersion": CHECKPOINT_SECURITY_POLICY_VERSION,
                "mode": "previously_completed",
                "checkpointRows": int(existing[0]),
                "writeRows": int(existing[1]),
                "encryptedCheckpointRows": int(existing[2]),
                "encryptedWriteRows": int(existing[3]),
                "databaseBytes": int(existing[4]),
                "completedAt": str(existing[5]),
                "encryption": "aes-256-gcm",
                "keySource": key_info.source,
                "keyFingerprint": key_info.fingerprint,
                "durationMs": round((time.perf_counter() - started) * 1000, 2),
            }

        checkpoint_rows = 0
        write_rows = 0
        encrypted_checkpoint_rows = 0
        encrypted_write_rows = 0
        database_bytes_before = path.stat().st_size if path.exists() else 0
        try:
            plaintext_checkpoints = int(
                conn.execute(
                    "SELECT COUNT(*) FROM checkpoints WHERE checkpoint IS NOT NULL AND instr(type, '+') = 0"
                ).fetchone()[0]
            )
            plaintext_writes = int(
                conn.execute(
                    "SELECT COUNT(*) FROM writes WHERE value IS NOT NULL AND instr(type, '+') = 0"
                ).fetchone()[0]
            )
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            plaintext_checkpoints = 0
            plaintext_writes = 0

        if plaintext_checkpoints or plaintext_writes:
            _require_encryption_migration_space(path, database_bytes=database_bytes_before)

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn.execute(
            f"""
            INSERT INTO {CHECKPOINT_SECURITY_STATE_TABLE}
                (policy_version, key_fingerprint, state, database_bytes, updated_at)
            VALUES (?, ?, 'applying', ?, ?)
            ON CONFLICT(policy_version, key_fingerprint) DO UPDATE SET
                state='applying', database_bytes=excluded.database_bytes,
                completed_at=NULL, updated_at=excluded.updated_at
            """,
            (CHECKPOINT_SECURITY_POLICY_VERSION, key_info.fingerprint, database_bytes_before, now),
        )
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        try:
            try:
                checkpoint_cursor = conn.execute(
                    "SELECT rowid, type, checkpoint, metadata FROM checkpoints "
                    "WHERE checkpoint IS NOT NULL ORDER BY rowid"
                )
                for rowid, serialization_type, blob, metadata_blob in checkpoint_cursor:
                    restored = serializer.loads_typed((str(serialization_type or ""), bytes(blob)))
                    if not isinstance(restored, dict):
                        raise CheckpointPreflightError("Historical checkpoint root is not a mapping.")
                    strict.assert_write_safe(restored, root=f"historical.checkpoints[{rowid}]")
                    try:
                        existing_metadata = (
                            json.loads(bytes(metadata_blob).decode("utf-8")) if metadata_blob else {}
                        )
                    except (TypeError, ValueError, UnicodeError) as exc:
                        raise CheckpointPreflightError(
                            f"Historical checkpoint metadata is invalid at row {rowid}."
                        ) from exc
                    if not isinstance(existing_metadata, dict):
                        raise CheckpointPreflightError(
                            f"Historical checkpoint metadata root is invalid at row {rowid}."
                        )
                    governed_metadata = checkpoint_retention_metadata(existing_metadata, restored)
                    encoded_metadata = json.dumps(
                        governed_metadata,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    if bytes(metadata_blob or b"") != encoded_metadata:
                        conn.execute(
                            "UPDATE checkpoints SET metadata = ? WHERE rowid = ?",
                            (encoded_metadata, rowid),
                        )
                    checkpoint_rows += 1
                    if _is_current_encryption_type(str(serialization_type or "")):
                        encrypted_checkpoint_rows += 1
                        continue
                    encrypted_type, encrypted_blob = serializer.dumps_typed(restored)
                    if not _is_current_encryption_type(encrypted_type):
                        raise CheckpointEncryptionError("Checkpoint serializer produced an unencrypted payload.")
                    conn.execute(
                        "UPDATE checkpoints SET type = ?, checkpoint = ? WHERE rowid = ?",
                        (encrypted_type, encrypted_blob, rowid),
                    )
                    encrypted_checkpoint_rows += 1

                write_cursor = conn.execute(
                    "SELECT rowid, type, value FROM writes WHERE value IS NOT NULL ORDER BY rowid"
                )
                for rowid, serialization_type, blob in write_cursor:
                    restored = serializer.loads_typed((str(serialization_type or ""), bytes(blob)))
                    strict.assert_write_safe(restored, root=f"historical.writes[{rowid}]")
                    write_rows += 1
                    if _is_current_encryption_type(str(serialization_type or "")):
                        encrypted_write_rows += 1
                        continue
                    encrypted_type, encrypted_blob = serializer.dumps_typed(restored)
                    if not _is_current_encryption_type(encrypted_type):
                        raise CheckpointEncryptionError("Checkpoint serializer produced an unencrypted write.")
                    conn.execute(
                        "UPDATE writes SET type = ?, value = ? WHERE rowid = ?",
                        (encrypted_type, encrypted_blob, rowid),
                    )
                    encrypted_write_rows += 1
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        migrated_rows = plaintext_checkpoints + plaintext_writes
        if migrated_rows:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        if not quick_check or str(quick_check[0]).strip().lower() != "ok":
            raise CheckpointPreflightError("Checkpoint database failed SQLite quick_check after encryption.")

        completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        database_bytes = path.stat().st_size if path.exists() else 0
        conn.execute(
            f"""
            UPDATE {CHECKPOINT_SECURITY_STATE_TABLE}
            SET state='completed', checkpoint_rows=?, write_rows=?,
                encrypted_checkpoint_rows=?, encrypted_write_rows=?,
                database_bytes=?, completed_at=?, updated_at=?
            WHERE policy_version=? AND key_fingerprint=?
            """,
            (
                checkpoint_rows,
                write_rows,
                encrypted_checkpoint_rows,
                encrypted_write_rows,
                database_bytes,
                completed_at,
                completed_at,
                CHECKPOINT_SECURITY_POLICY_VERSION,
                key_info.fingerprint,
            ),
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {CHECKPOINT_SECURITY_AUDIT_TABLE}
                (policy_version, checkpoint_rows, write_rows, database_bytes, completed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                CHECKPOINT_SECURITY_POLICY_VERSION,
                checkpoint_rows,
                write_rows,
                database_bytes,
                completed_at,
            ),
        )
        conn.commit()
        return {
            "policyVersion": CHECKPOINT_SECURITY_POLICY_VERSION,
            "mode": "full_scan_and_encrypt",
            "checkpointRows": checkpoint_rows,
            "writeRows": write_rows,
            "encryptedCheckpointRows": encrypted_checkpoint_rows,
            "encryptedWriteRows": encrypted_write_rows,
            "migratedRows": migrated_rows,
            "databaseBytes": database_bytes,
            "completedAt": completed_at,
            "encryption": "aes-256-gcm",
            "keySource": key_info.source,
            "keyFingerprint": key_info.fingerprint,
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
        }
    except (CheckpointEncryptionKeyError, CheckpointPreflightError):
        conn.rollback()
        raise
    except CheckpointSecurityError as exc:
        conn.rollback()
        raise CheckpointPreflightError(str(exc)) from exc
    except Exception as exc:
        conn.rollback()
        raise CheckpointPreflightError(
            f"Checkpoint security preflight failed: {type(exc).__name__}."
        ) from exc
    finally:
        conn.close()
