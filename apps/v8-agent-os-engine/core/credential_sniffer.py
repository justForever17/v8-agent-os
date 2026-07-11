from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class QwenCredentialSniffer:
    """Presence-only discovery for legacy Qwen CLI authorization.

    The Engine must not silently inherit another CLI's credential. Callers may
    show these sources to the user, but reading a value requires an explicit
    import action.
    """

    _SOURCES = (
        ("qwen-cli-oauth", Path.home() / ".qwen" / "oauth_creds.json"),
        ("qwen-code-config", Path.home() / ".qwen-code" / "config.json"),
    )

    @classmethod
    def discover(cls) -> list[dict[str, Any]]:
        return [
            {
                "sourceId": source_id,
                "kind": "file",
                "present": path.is_file(),
                "displayPath": str(path),
            }
            for source_id, path in cls._SOURCES
        ]

    @classmethod
    def get_qwen_token(cls) -> None:
        """Deprecated compatibility hook: silent credential reuse is disabled."""

        if any(item["present"] for item in cls.discover()):
            logger.info("Qwen CLI authorization detected; explicit import is required")
        return None

    @classmethod
    def import_explicit(cls, source_id: str) -> str:
        source = next((path for item_id, path in cls._SOURCES if item_id == source_id), None)
        if source is None or not source.is_file():
            raise ValueError("Qwen credential source is unavailable")
        payload = json.loads(source.read_text(encoding="utf-8"))
        for key in ("access_token", "api_key"):
            value = str(payload.get(key) or "") if isinstance(payload, dict) else ""
            if value:
                return value
        raise ValueError("Qwen credential source does not contain a supported credential")
