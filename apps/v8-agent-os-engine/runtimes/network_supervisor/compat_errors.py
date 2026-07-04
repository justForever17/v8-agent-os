from __future__ import annotations

from typing import Any


class CompatBridgeHardStop(RuntimeError):
    """Non-recoverable compat bridge failure; do not feed back to the model."""

    failure_class = "compat_bridge_hard_stop"

    def __init__(self, message: str, *, failure_class: str | None = None) -> None:
        super().__init__(message)
        if failure_class:
            self.failure_class = failure_class


class CompatExternalToolRequest(RuntimeError):
    """Recoverable compat pause: return an external tool call to the client."""

    failure_class = "external_tool_requested"

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__("External client tool requested; waiting for client tool_result.")
        self.payload = dict(payload or {})
