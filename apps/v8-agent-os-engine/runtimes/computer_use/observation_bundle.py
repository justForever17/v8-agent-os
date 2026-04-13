from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List


def _normalize_text_token(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _frame_text_digest(observation: Dict[str, Any] | None) -> str:
    payload = dict(observation or {})
    metadata = dict(payload.get("metadata") or {})
    tokens: List[str] = []
    for candidate in (
        payload.get("windowTitle"),
        payload.get("app"),
        metadata.get("pageIdentity"),
        metadata.get("pageTitle"),
        metadata.get("pageUrl"),
    ):
        token = _normalize_text_token(candidate)
        if token and token not in tokens:
            tokens.append(token)
    for element in list(payload.get("elements") or [])[:18]:
        if not isinstance(element, dict):
            continue
        candidates: Iterable[Any] = (
            element.get("name"),
            element.get("automationId"),
            element.get("className"),
            (element.get("metadata") or {}).get("value") if isinstance(element.get("metadata"), dict) else None,
            (element.get("metadata") or {}).get("richText") if isinstance(element.get("metadata"), dict) else None,
            (element.get("metadata") or {}).get("title") if isinstance(element.get("metadata"), dict) else None,
        )
        merged = " ".join(_normalize_text_token(item) for item in candidates if _normalize_text_token(item))
        merged = _normalize_text_token(merged)
        if merged and merged not in tokens:
            tokens.append(merged)
    return " | ".join(tokens[:12])


def _frame_summary(observation: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(observation or {})
    metadata = dict(payload.get("metadata") or {})
    scene = dict(payload.get("sceneAssessment") or metadata.get("sceneAssessment") or {})
    return {
        "windowTitle": payload.get("windowTitle"),
        "windowHandle": metadata.get("windowHandle"),
        "app": payload.get("app"),
        "treeHash": payload.get("treeHash"),
        "screenHash": payload.get("screenHash"),
        "elementCount": len(list(payload.get("elements") or [])),
        "focusedElementId": payload.get("focusedElementId"),
        "pageIdentity": metadata.get("pageIdentity"),
        "pageTitle": metadata.get("pageTitle"),
        "pageUrl": metadata.get("pageUrl"),
        "bindingStatus": str((metadata.get("bindingAssessment") or {}).get("status") or "").strip() or None,
        "transitionState": scene.get("transitionState"),
        "blockerState": scene.get("blockerState"),
        "textDigest": _frame_text_digest(payload),
    }


def _change_flags(
    before_summary: Dict[str, Any],
    after_summary: Dict[str, Any],
) -> Dict[str, bool]:
    return {
        "windowChanged": before_summary.get("windowHandle") != after_summary.get("windowHandle"),
        "pageIdentityChanged": before_summary.get("pageIdentity") != after_summary.get("pageIdentity"),
        "treeChanged": before_summary.get("treeHash") != after_summary.get("treeHash"),
        "screenChanged": before_summary.get("screenHash") != after_summary.get("screenHash"),
        "focusChanged": before_summary.get("focusedElementId") != after_summary.get("focusedElementId"),
        "transitionStateChanged": before_summary.get("transitionState") != after_summary.get("transitionState"),
        "blockerStateChanged": before_summary.get("blockerState") != after_summary.get("blockerState"),
    }


def build_observation_bundle(
    *,
    action_type: str,
    action_payload: Dict[str, Any],
    route: str | None,
    before_observation: Dict[str, Any] | None,
    mid_observation: Dict[str, Any] | None,
    after_observation: Dict[str, Any] | None,
    desktop_live_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    before_summary = _frame_summary(before_observation)
    mid_summary = _frame_summary(mid_observation)
    after_summary = _frame_summary(after_observation)
    before_to_after = _change_flags(before_summary, after_summary)
    mid_to_after = _change_flags(mid_summary, after_summary)
    state_advanced = any(before_to_after.values()) or any(mid_to_after.values())
    desktop_live_payload = dict(desktop_live_context or {})
    if not desktop_live_payload:
        desktop_live_payload = {
            "source": "computer_use_local_capture",
            "sessionId": None,
            "frameTimestamp": None,
            "frameArtifactId": None,
            "frameRef": None,
        }
    sampling_source = str(desktop_live_payload.get("source") or "").strip() or "computer_use_local_capture"
    file_paths = [
        str(item)
        for item in list(
            action_payload.get("file_paths")
            or action_payload.get("attachment_paths")
            or ([action_payload.get("file_path")] if action_payload.get("file_path") else [])
        )
        if str(item or "").strip()
    ]
    text_value = str(action_payload.get("text") or "")
    return {
        "enabled": True,
        "actionType": action_type,
        "route": str(route or "").strip().lower() or None,
        "samplingSource": sampling_source,
        "targetSelector": {
            "selectorKey": action_payload.get("selector_key"),
            "controlType": action_payload.get("control_type"),
            "className": action_payload.get("class_name"),
            "automationId": action_payload.get("automation_id"),
            "targetText": action_payload.get("target_text"),
            "point": list(action_payload.get("point") or []) if isinstance(action_payload.get("point"), list) else None,
        },
        "actionContext": {
            "appId": action_payload.get("app_id") or action_payload.get("resolved_app_id"),
            "windowTitle": action_payload.get("window_title"),
            "windowHandle": action_payload.get("window_handle"),
            "targetInputKind": action_payload.get("target_input_kind"),
            "textPreview": text_value[:180] if text_value else None,
            "textLength": len(text_value) if text_value else 0,
            "fileCount": len(file_paths),
            "fileNames": [Path(path).name for path in file_paths[:6]],
            "scrollAmount": action_payload.get("amount"),
        },
        "frames": {
            "preAction": before_summary,
            "midAction": mid_summary,
            "postAction": after_summary,
        },
        "diff": {
            "preToPost": before_to_after,
            "midToPost": mid_to_after,
            "stateAdvanced": state_advanced,
        },
        "desktopLive": desktop_live_payload,
    }
