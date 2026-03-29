from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator


_RUNTIME_CONTEXT: ContextVar[Dict[str, Any]] = ContextVar("v8_agent_os_runtime_context", default={})


def get_runtime_context() -> Dict[str, Any]:
    return dict(_RUNTIME_CONTEXT.get() or {})


@contextmanager
def bind_runtime_context(**context: Any) -> Iterator[Dict[str, Any]]:
    current = get_runtime_context()
    current.update({key: value for key, value in context.items() if value is not None})
    token = _RUNTIME_CONTEXT.set(current)
    try:
        yield current
    finally:
        _RUNTIME_CONTEXT.reset(token)
