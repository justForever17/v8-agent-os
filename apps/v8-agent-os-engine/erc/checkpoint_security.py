from __future__ import annotations

import contextvars
import dataclasses
import sqlite3
import sys
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

enforce_strict_langgraph_msgpack()

from langgraph.checkpoint.serde import _msgpack as langgraph_msgpack
from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


CHECKPOINT_SECURITY_POLICY_VERSION = 1
CHECKPOINT_SECURITY_AUDIT_TABLE = "v8_checkpoint_security_audit"

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


def build_checkpoint_serializer() -> StrictCheckpointSerializer:
    return StrictCheckpointSerializer(extra_allowlist=V8_STABLE_MSGPACK_ALLOWLIST)


def run_checkpoint_preflight(path: Path, serializer: StrictCheckpointSerializer) -> dict[str, Any]:
    """Audit the existing database once per policy version and persist a local marker."""
    started = time.perf_counter()
    path.parent.mkdir(parents=True, exist_ok=True)
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
        existing = conn.execute(
            f"SELECT checkpoint_rows, write_rows, database_bytes, completed_at "
            f"FROM {CHECKPOINT_SECURITY_AUDIT_TABLE} WHERE policy_version = ?",
            (CHECKPOINT_SECURITY_POLICY_VERSION,),
        ).fetchone()
        if existing is not None:
            return {
                "policyVersion": CHECKPOINT_SECURITY_POLICY_VERSION,
                "mode": "previously_completed",
                "checkpointRows": int(existing[0]),
                "writeRows": int(existing[1]),
                "databaseBytes": int(existing[2]),
                "completedAt": str(existing[3]),
                "durationMs": round((time.perf_counter() - started) * 1000, 2),
            }

        checkpoint_rows = 0
        write_rows = 0
        try:
            for serialization_type, blob in conn.execute(
                "SELECT type, checkpoint FROM checkpoints WHERE checkpoint IS NOT NULL ORDER BY rowid"
            ):
                restored = serializer.loads_typed((str(serialization_type or ""), bytes(blob)))
                if not isinstance(restored, dict):
                    raise CheckpointPreflightError("Historical checkpoint root is not a mapping.")
                checkpoint_rows += 1

            for serialization_type, blob in conn.execute(
                "SELECT type, value FROM writes WHERE value IS NOT NULL ORDER BY rowid"
            ):
                serializer.loads_typed((str(serialization_type or ""), bytes(blob)))
                write_rows += 1
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
        except CheckpointSecurityError as exc:
            raise CheckpointPreflightError(str(exc)) from exc

        completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        database_bytes = path.stat().st_size if path.exists() else 0
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
            "mode": "full_scan",
            "checkpointRows": checkpoint_rows,
            "writeRows": write_rows,
            "databaseBytes": database_bytes,
            "completedAt": completed_at,
            "durationMs": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
