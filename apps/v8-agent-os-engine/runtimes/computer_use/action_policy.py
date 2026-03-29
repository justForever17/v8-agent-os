from __future__ import annotations

from typing import Any, Dict


def binding_allows_profile(binding_decision: Any) -> bool:
    if binding_decision is None:
        return False
    try:
        return bool(binding_decision.profile_eligible)
    except Exception:
        return False


def promotion_allowed_for_invocation(
    *,
    primitive_payload: Dict[str, Any],
    invocation: Any,
) -> bool:
    supports = bool((primitive_payload or {}).get("supportsRpaPromotion", False))
    if not supports:
        return False
    if invocation is None:
        return supports
    try:
        return supports and bool(invocation.promotion_allowed)
    except Exception:
        return supports


def build_action_policy_metadata(
    *,
    binding_decision: Any,
    invocation: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if binding_decision is not None:
        payload.update(
            {
                "bindingMode": getattr(binding_decision, "binding_mode", "none"),
                "bindingConfidence": round(float(getattr(binding_decision, "binding_confidence", 0.0) or 0.0), 3),
                "requestedAppId": getattr(binding_decision, "requested_app_id", None),
                "resolvedAppId": getattr(binding_decision, "resolved_app_id", None),
                "bindingEvidence": dict(getattr(binding_decision, "binding_evidence", {}) or {}),
                "profileEligible": bool(getattr(binding_decision, "profile_eligible", False)),
            }
        )
    if invocation is not None:
        payload.update(invocation.as_dict())
    return payload
