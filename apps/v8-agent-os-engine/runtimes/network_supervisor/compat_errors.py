from __future__ import annotations


class CompatBridgeHardStop(RuntimeError):
    """Non-recoverable compat bridge failure; do not feed back to the model."""

    failure_class = "compat_bridge_hard_stop"

    def __init__(self, message: str, *, failure_class: str | None = None) -> None:
        super().__init__(message)
        if failure_class:
            self.failure_class = failure_class
