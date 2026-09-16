from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any
import json
import os
import uuid


def atomic_write_json(path: Path, value: Any) -> None:
    """Publish a complete JSON revision; readers see either the old or new file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(to_jsonable(value), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def to_jsonable(value: Any) -> Any:
    return _to_jsonable(value, seen=set())


def _to_jsonable(value: Any, *, seen: set[int]) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, Enum):
        return _to_jsonable(value.value, seen=seen)

    object_id = id(value)
    if object_id in seen:
        return str(value)

    seen.add(object_id)
    try:
        if isinstance(value, dict):
            return {str(key): _to_jsonable(item, seen=seen) for key, item in value.items()}

        if isinstance(value, (list, tuple, set, frozenset)):
            return [_to_jsonable(item, seen=seen) for item in value]

        if hasattr(value, "model_dump"):
            return _to_jsonable(value.model_dump(), seen=seen)

        if hasattr(value, "as_dict"):
            return _to_jsonable(value.as_dict(), seen=seen)

        if is_dataclass(value):
            return _to_jsonable(asdict(value), seen=seen)
    except Exception:
        return str(value)
    finally:
        seen.discard(object_id)

    return str(value)
