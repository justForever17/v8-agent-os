from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Protocol

from erc.capability_registry import RuntimeDescriptor, capability_registry, coerce_runtime_descriptor


class RuntimeAdapter(Protocol):
    kind: str


class RuntimeRegistry:
    """
    Phase 2 过渡层：
    先统一 Runtime 的注册和发现入口，
    后续再逐步补 can_handle / prepare / execute 等正式协议。
    """

    def __init__(self) -> None:
        self._runtimes: Dict[str, RuntimeAdapter] = {}
        self._descriptors: Dict[str, RuntimeDescriptor] = {}

    def register(self, runtime: RuntimeAdapter) -> RuntimeAdapter:
        self._runtimes[runtime.kind] = runtime
        descriptor = self._extract_descriptor(runtime)
        if descriptor is not None:
            self._descriptors[runtime.kind] = descriptor
            capability_registry.register(descriptor)
        return runtime

    def get(self, kind: str) -> Optional[RuntimeAdapter]:
        return self._runtimes.get(kind)

    def require(self, kind: str) -> RuntimeAdapter:
        runtime = self.get(kind)
        if runtime is None:
            raise KeyError(f"Runtime '{kind}' is not registered")
        return runtime

    def list_kinds(self) -> Iterable[str]:
        return tuple(self._runtimes.keys())

    def get_descriptor(self, kind: str) -> Optional[RuntimeDescriptor]:
        return self._descriptors.get(kind)

    def list_descriptors(self) -> Iterable[RuntimeDescriptor]:
        return tuple(self._descriptors.values())

    def _extract_descriptor(self, runtime: RuntimeAdapter) -> Optional[RuntimeDescriptor]:
        candidate = None
        for attr_name in ("runtime_descriptor", "describe_runtime", "descriptor"):
            attr = getattr(runtime, attr_name, None)
            if attr is None:
                continue
            candidate = attr() if callable(attr) else attr
            break
        if candidate is None:
            return None
        return coerce_runtime_descriptor(candidate)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "registered_runtimes": list(self.list_kinds()),
            "count": len(self._runtimes),
            "descriptors": [descriptor.as_dict() for descriptor in self.list_descriptors()],
        }


runtime_registry = RuntimeRegistry()
