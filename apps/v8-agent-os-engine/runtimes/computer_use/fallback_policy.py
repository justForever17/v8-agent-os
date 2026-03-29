from __future__ import annotations

from typing import Any, Dict, List


DEFAULT_FALLBACK_ORDER = ["selector_memory", "window_rebind", "win32", "coordinate_anchor", "visual"]
HIGH_RISK_FALLBACK_ORDER = ["selector_memory", "window_rebind", "win32", "visual"]


def recovery_fallback_order(*, high_risk: bool = False) -> List[str]:
    return list(HIGH_RISK_FALLBACK_ORDER if high_risk else DEFAULT_FALLBACK_ORDER)


def normalize_visual_fallback_payload(
    *,
    attempted: bool,
    status: str,
    reason: str | None = None,
    model_id: str | None = None,
    provider_id: str | None = None,
    artifact: Dict[str, Any] | None = None,
    analysis: str | None = None,
    target_exists: bool | None = None,
    suggested_selector: Dict[str, Any] | None = None,
    suggested_point: List[float] | None = None,
    coordinate_anchor: Dict[str, Any] | None = None,
    error: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "attempted": attempted,
        "status": str(status or "unknown"),
        "reason": str(reason or "").strip() or None,
        "modelId": str(model_id or "").strip() or None,
        "providerId": str(provider_id or "").strip() or None,
        "artifact": dict(artifact or {}) if artifact else None,
        "analysis": analysis,
        "targetExists": target_exists,
        "suggestedSelector": dict(suggested_selector or {}) if suggested_selector else None,
        "suggestedPoint": list(suggested_point or []) if suggested_point else None,
        "coordinateAnchor": dict(coordinate_anchor or {}) if coordinate_anchor else None,
        "error": str(error or "").strip() or None,
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}
