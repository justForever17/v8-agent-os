from __future__ import annotations

import os


STRICT_MSGPACK_ENV = "LANGGRAPH_STRICT_MSGPACK"


def enforce_strict_langgraph_msgpack() -> None:
    """Enable LangGraph's schema-derived msgpack allowlist before it is imported."""
    os.environ[STRICT_MSGPACK_ENV] = "true"


enforce_strict_langgraph_msgpack()
