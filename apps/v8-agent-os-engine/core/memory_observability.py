from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.audit_logger import audit_logger


def log_memory_observation(action: str, status: str = "INFO", **details: Any) -> None:
    """Record compact Memory Runtime observability events without LLM overhead."""
    payload = {
        "observabilityKind": "memory_runtime",
        "action": str(action or "unknown"),
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        **details,
    }
    try:
        rendered = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        rendered = json.dumps(
            {
                "observabilityKind": "memory_runtime",
                "action": str(action or "unknown"),
                "recordedAt": datetime.now(timezone.utc).isoformat(),
                "serializationError": True,
            },
            ensure_ascii=False,
        )
    audit_logger.log(
        source_type="MEMORY",
        action=f"Memory Runtime: {str(action or 'unknown')}",
        status=str(status or "INFO").upper(),
        details=rendered[:6000],
    )
