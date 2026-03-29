from __future__ import annotations

from typing import Any, Dict


def build_preflight_context(
    *,
    action_type: str,
    goal: str | None,
    action_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = dict(action_payload or {})
    return {
        "actionType": str(action_type or "").strip(),
        "goal": str(goal or action_type or "").strip() or None,
        "requestedAppId": str(payload.get("app_id") or payload.get("requested_app_id") or "").strip() or None,
        "windowTitle": str(payload.get("window_title") or "").strip() or None,
        "windowHandle": payload.get("window_handle"),
        "profileAction": str(payload.get("profile_action") or action_type or "").strip() or None,
        "selectorKey": str(payload.get("selector_key") or "").strip() or None,
    }


def requires_scene_evidence(action_type: str) -> bool:
    normalized = str(action_type or "").strip().lower()
    return normalized not in {"observe", "capture_screenshot", "screenshot"}
