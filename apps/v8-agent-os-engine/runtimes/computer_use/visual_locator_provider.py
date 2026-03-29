from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable

from runtimes.computer_use.visual_locator_runtime import RPADesktopVisualLocatorRuntime


@runtime_checkable
class VisualLocatorProvider(Protocol):
    provider_id: str

    def availability_summary(self) -> Dict[str, Any]:
        ...

    def is_available(self) -> bool:
        ...

    def locate(self, **kwargs: Any) -> Dict[str, Any]:
        ...


class RPADesktopVisualLocatorProvider:
    provider_id = "rpa_desktop_visual_locator"

    def __init__(self) -> None:
        self._runtime = RPADesktopVisualLocatorRuntime()
        self.provider_id = getattr(self._runtime, "provider_id", self.provider_id)

    def availability_summary(self) -> Dict[str, Any]:
        return dict(self._runtime.availability_summary() or {})

    def is_available(self) -> bool:
        return bool(self._runtime.is_available())

    def locate(self, **kwargs: Any) -> Dict[str, Any]:
        return dict(self._runtime.locate(**kwargs) or {})


def create_visual_locator_provider() -> VisualLocatorProvider:
    return RPADesktopVisualLocatorProvider()
