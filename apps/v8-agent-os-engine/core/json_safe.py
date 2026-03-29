from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any


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
