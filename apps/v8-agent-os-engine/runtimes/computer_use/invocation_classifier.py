from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(slots=True)
class ComputerUseInvocation:
    trigger_source: str
    invocation_source: str
    execution_intent: str
    compat_debug: bool
    promotion_allowed: bool
    route_kind: str
    endpoint: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "triggerSource": self.trigger_source,
            "invocationSource": self.invocation_source,
            "executionIntent": self.execution_intent,
            "compatDebug": bool(self.compat_debug),
            "promotionAllowed": bool(self.promotion_allowed),
            "routeKind": self.route_kind,
            "endpoint": self.endpoint,
        }


def classify_computer_use_invocation(
    invocation_metadata: Dict[str, Any] | None = None,
    *,
    default_trigger_source: str = "computer_use_api",
) -> ComputerUseInvocation:
    payload = dict(invocation_metadata or {})
    invocation_source = str(payload.get("invocationSource") or payload.get("invocation_source") or "runtime_native").strip().lower()
    execution_intent = str(payload.get("executionIntent") or payload.get("execution_intent") or "formal_task").strip().lower()
    route_kind = str(payload.get("routeKind") or payload.get("route_kind") or "runtime").strip().lower() or "runtime"
    endpoint = str(payload.get("endpoint") or "").strip() or None
    compat_debug = invocation_source in {"compat_http", "debug_http"} or execution_intent in {"debug_primitive", "compat_primitive"}
    trigger_source = str(payload.get("triggerSource") or payload.get("trigger_source") or "").strip()
    if not trigger_source:
        trigger_source = "computer_use_compat_http" if compat_debug else default_trigger_source
    promotion_allowed = bool(payload.get("promotionAllowed", not compat_debug))
    return ComputerUseInvocation(
        trigger_source=trigger_source,
        invocation_source=invocation_source or "runtime_native",
        execution_intent=execution_intent or "formal_task",
        compat_debug=compat_debug,
        promotion_allowed=promotion_allowed,
        route_kind=route_kind,
        endpoint=endpoint,
    )
