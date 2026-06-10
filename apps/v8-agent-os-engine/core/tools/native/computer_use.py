from __future__ import annotations

import json
import mimetypes
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from core.computer_use_execution_route import build_compact_execution_route, determine_execution_ready_mode
from core.tools.native.desktop_governance import (
    _DESKTOP_ROUTE_SOURCE,
    _computer_use_action_guard,
    _desktop_route_compact_metadata,
    _desktop_route_executable_draft_id as _governance_desktop_route_executable_draft_id,
    _desktop_route_gate as _governance_desktop_route_gate,
    _desktop_route_gate_failure_response as _governance_desktop_route_gate_failure_response,
    _desktop_route_latest_bound_human_message,
    _desktop_route_merge_into_response as _governance_desktop_route_merge_into_response,
    _desktop_route_task_mismatch_reason as _governance_desktop_route_task_mismatch_reason,
    _guard_computer_use_steps as _governance_guard_computer_use_steps,
)
from core.tools.native.rpa import (
    _get_rpa_runtime,
    _rpa_compact_run_draft_response,
    _rpa_compact_run_existing_flow_response,
    _rpa_compact_script_list,
)
from erc.runtime_context import get_runtime_context
from runtimes.computer_use.primitives import list_computer_use_primitives, primitive_validation_matrix
from runtimes.computer_use.verification_contract import (
    build_environment_signal_summary_payload,
    build_evidence_summary_payload,
    build_timing_signal_summary_payload,
    build_visual_signal_summary_payload,
    recommended_next_action_payload,
)
from runtimes.rpa.promotion_gate import draft_environment_signal_summary, draft_timing_signal_summary

__all__ = [
    "_COMPUTER_USE_APP_RESOLUTION_CACHE_TTL_MS",
    "_COMPUTER_USE_POINT_TAG_PATTERN",
    "_get_computer_use_runtime",
    "_get_rpa_runtime",
    "_computer_use_runtime_kwargs",
    "_agent_compact_desktop_match",
    "_agent_compact_desktop_route_payload",
    "_agent_count_nodes",
    "_computer_use_build_desktop_route",
    "_computer_use_parse_point_tag",
    "_computer_use_cache_app_resolution",
    "_computer_use_resolve_app",
    "_computer_use_running_window_title",
    "_computer_use_effective_window_title",
    "_computer_use_refresh_resolved_app_window",
    "_computer_use_prebind_window",
    "_computer_use_update_resolved_app_from_raw_result",
    "_computer_use_launch_target_override",
    "_computer_use_apply_visual_locator_step",
    "_computer_use_apply_post_action_visual_check_step",
    "_computer_use_apply_environment_probe_step",
    "_computer_use_guard_failure_response",
    "_computer_use_artifacts_from_result",
    "_computer_use_primary_action",
    "_computer_use_recommended_next_action",
    "_computer_use_compact_response",
    "_computer_use_compact_observation",
    "_computer_use_parse_variables_json",
    "_computer_use_compact_memory_lookup",
    "_computer_use_plan_step_contract",
    "_computer_use_aggregate_plan_step_contracts",
    "_computer_use_attach_plan_contract_summary",
    "_computer_use_execute_task_step_samples",
    "_computer_use_execute_task_next_action",
    "_computer_use_execute_task_compact_computer_use_result",
    "_computer_use_execute_task_compact_rpa_result",
    "_computer_use_compact_memory_list",
    "_computer_use_compact_primitive_catalog",
    "_computer_use_compact_driver_capabilities",
    "_rpa_compact_script_list",
    "_rpa_compact_run_existing_flow_response",
    "_rpa_compact_run_draft_response",
    "_computer_use_execute_single_step",
    "computer_use_list_windows",
    "computer_use_observe",
    "computer_use_find_element",
    "computer_use_click",
    "computer_use_type_text",
    "computer_use_hotkey",
    "computer_use_scroll",
    "computer_use_wait_for_element",
    "computer_use_capture_screenshot",
    "computer_use_open_app",
    "computer_use_focus_window",
    "computer_use_find_and_type",
    "computer_use_scroll_list",
    "computer_use_click_toolbar_action",
    "computer_use_execute_plan",
    "computer_use_list_apps",
    "computer_use_list_primitives",
    "computer_use_desktop_capabilities",
    "computer_use_lookup_muscle_memory",
    "computer_use_list_muscle_memories",
    "computer_use_resolve_execution_route",
    "computer_use_execute_task",
    "computer_use_launch_app",
    "computer_use_ensure_window",
    "computer_use_observe_scene",
    "computer_use_click_target",
    "computer_use_input_text",
    "computer_use_paste_text",
    "computer_use_paste_files",
    "computer_use_right_click_target",
    "computer_use_hover_target",
    "computer_use_send_hotkey",
    "computer_use_scroll_view",
    "computer_use_drag_pointer",
]


def _agent_preview_text(value: Any, *, limit: int = 700) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _agent_compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        compact[key] = value
    return compact


def _agent_limited_list(values: Any, *, limit: int = 20) -> list[Any]:
    return list(values or [])[: max(0, int(limit))]


def _agent_signal_flags(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flags: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, bool):
            if value:
                flags[key] = True
        elif isinstance(value, (int, float)):
            if value:
                flags[key] = value
        elif isinstance(value, str):
            if value.strip():
                flags[key] = value.strip()
        elif isinstance(value, list):
            items = [item for item in value if item not in (None, "")]
            if items:
                flags[key] = items[:8]
        elif isinstance(value, dict):
            nested = _agent_signal_flags(value)
            if nested:
                flags[key] = nested
    return flags


def _agent_compact_signal_bundle(*payloads: Any) -> dict[str, Any]:
    bundle: dict[str, Any] = {}
    for payload in payloads:
        bundle.update(_agent_signal_flags(payload))
    return bundle


def _agent_count_nodes(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_agent_count_nodes(item) for item in value.values())
    if isinstance(value, list):
        return len(value) + sum(_agent_count_nodes(item) for item in value)
    return 1 if value not in (None, "") else 0


def _compat_native_attr(name: str, local_value: Any | None = None) -> Any:
    native = sys.modules.get("core.native_tools")
    if native is not None and hasattr(native, name):
        value = getattr(native, name)
        if local_value is None or value is not local_value:
            return value
    if local_value is not None:
        return local_value
    return globals().get(name)


def _desktop_route_gate(*args: Any, **kwargs: Any) -> Any:
    patched = _compat_native_attr("_desktop_route_gate", _desktop_route_gate)
    if patched is not _desktop_route_gate:
        return patched(*args, **kwargs)
    return _governance_desktop_route_gate(*args, **kwargs)


def _desktop_route_merge_into_response(*args: Any, **kwargs: Any) -> Any:
    patched = _compat_native_attr("_desktop_route_merge_into_response", _desktop_route_merge_into_response)
    if patched is not _desktop_route_merge_into_response:
        return patched(*args, **kwargs)
    return _governance_desktop_route_merge_into_response(*args, **kwargs)


def _desktop_route_gate_failure_response(*args: Any, **kwargs: Any) -> Any:
    patched = _compat_native_attr("_desktop_route_gate_failure_response", _desktop_route_gate_failure_response)
    if patched is not _desktop_route_gate_failure_response:
        return patched(*args, **kwargs)
    return _governance_desktop_route_gate_failure_response(*args, **kwargs)


def _desktop_route_executable_draft_id(*args: Any, **kwargs: Any) -> Any:
    patched = _compat_native_attr("_desktop_route_executable_draft_id", _desktop_route_executable_draft_id)
    if patched is not _desktop_route_executable_draft_id:
        return patched(*args, **kwargs)
    return _governance_desktop_route_executable_draft_id(*args, **kwargs)


def _desktop_route_task_mismatch_reason(*args: Any, **kwargs: Any) -> Any:
    patched = _compat_native_attr("_desktop_route_task_mismatch_reason", _desktop_route_task_mismatch_reason)
    if patched is not _desktop_route_task_mismatch_reason:
        return patched(*args, **kwargs)
    return _governance_desktop_route_task_mismatch_reason(*args, **kwargs)


def _guard_computer_use_steps(*args: Any, **kwargs: Any) -> Any:
    patched = _compat_native_attr("_guard_computer_use_steps", _guard_computer_use_steps)
    if patched is not _guard_computer_use_steps:
        return patched(*args, **kwargs)
    return _governance_guard_computer_use_steps(*args, **kwargs)


_COMPUTER_USE_APP_RESOLUTION_CACHE_TTL_MS = 3000
_COMPUTER_USE_APP_RESOLUTION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COMPUTER_USE_POINT_TAG_PATTERN = re.compile(
    r"<point>\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*</point>",
    re.IGNORECASE,
)


def _get_computer_use_runtime():
    patched = _compat_native_attr("_get_computer_use_runtime", _get_computer_use_runtime)
    if patched is not _get_computer_use_runtime:
        return patched()
    from runtimes.computer_use.runtime import computer_use_runtime

    return computer_use_runtime


def _computer_use_runtime_kwargs(goal: str) -> dict:
    runtime_context = get_runtime_context()
    root_goal = str(runtime_context.get("goal") or "").strip()
    normalized_goal = str(goal or "").strip()
    local_goal_prefixes = (
        "launch_app:",
        "launch_app_recover:",
        "observe_scene",
        "click_target",
        "input_text",
        "paste_text",
        "paste_files",
        "right_click_target",
        "hover_target",
        "send_hotkey",
        "scroll_view",
        "drag_pointer",
        "focus_window",
        "plan_step_",
    )
    effective_goal = normalized_goal
    if root_goal and (
        not normalized_goal
        or normalized_goal.startswith(local_goal_prefixes)
    ):
        effective_goal = root_goal
    return {
        "session_id": runtime_context.get("session_id"),
        "run_id": runtime_context.get("run_id"),
        "user_id": runtime_context.get("user_id") or "anonymous",
        "project_id": runtime_context.get("project_id"),
        "workspace_id": runtime_context.get("workspace_id"),
        "workspace_path": runtime_context.get("workspace_path"),
        "goal": effective_goal,
        "invocation_metadata": {
            "requestedGoal": normalized_goal or None,
            "rootGoal": root_goal or None,
        },
    }


def _agent_compact_desktop_match(match: Any) -> dict[str, Any]:
    if not isinstance(match, dict) or not match:
        return {}
    return _agent_compact_dict(
        {
            "kind": match.get("kind"),
            "id": match.get("id"),
            "name": match.get("name"),
            "goal": _agent_preview_text(match.get("goal"), limit=240),
            "status": match.get("status"),
            "stage": match.get("stage"),
            "rolloutMode": match.get("rolloutMode"),
            "routeMode": match.get("routeMode"),
            "routeAction": match.get("routeAction"),
            "score": match.get("score"),
            "confidence": match.get("confidence"),
            "executionPath": match.get("executionPath"),
            "promotionGateStatus": match.get("promotionGateStatus"),
            "promotionGateBlocked": bool(match.get("promotionGateBlocked")),
            "missingVariables": _agent_limited_list(match.get("missingVariables"), limit=8),
            "reasons": _agent_limited_list(match.get("reasons") or match.get("promotionGateReasons"), limit=4),
            "signals": _agent_compact_signal_bundle(
                match.get("visualSignalSummary"),
                match.get("timingSignalSummary"),
                match.get("environmentSignalSummary"),
                match.get("promotionGateSignals"),
            ),
        }
    )


def _agent_compact_desktop_route_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload.get("summary") or {})
    promotion_gate = dict(payload.get("promotionGate") or {})
    matches = [
        _agent_compact_desktop_match(item)
        for item in list(payload.get("matches") or [])[:5]
        if isinstance(item, dict)
    ]
    return _agent_compact_dict(
        {
            "ok": payload.get("ok"),
            "action": payload.get("action"),
            "goal": _agent_preview_text(payload.get("goal"), limit=360),
            "target": payload.get("target"),
            "app": payload.get("app"),
            "lookupMode": payload.get("lookupMode"),
            "recommendedMode": payload.get("recommendedMode"),
            "executionReadyMode": payload.get("executionReadyMode"),
            "recommendedAction": payload.get("recommendedAction"),
            "recommendedRuntime": payload.get("recommendedRuntime"),
            "recommendedTool": payload.get("recommendedTool"),
            "recommendedToolSummary": _agent_preview_text(payload.get("recommendedToolSummary"), limit=360),
            "recommendedToolInput": payload.get("recommendedToolInput"),
            "recommendedTemplateId": payload.get("recommendedTemplateId"),
            "recommendedDraftId": payload.get("recommendedDraftId"),
            "requiresVariableBinding": bool(payload.get("requiresVariableBinding")),
            "missingVariables": _agent_limited_list(payload.get("missingVariables"), limit=8),
            "providedVariables": _agent_limited_list(payload.get("providedVariables"), limit=8),
            "recommendedMatch": _agent_compact_desktop_match(payload.get("recommendedMatch")),
            "summary": _agent_compact_dict(
                {
                    "templateCount": summary.get("templateCount"),
                    "draftCount": summary.get("draftCount"),
                    "bestScore": summary.get("bestScore"),
                    "bestConfidence": summary.get("bestConfidence"),
                    "hasReusableMemory": summary.get("hasReusableMemory"),
                    "requiresLearning": summary.get("requiresLearning"),
                    "promotionGateStatus": summary.get("promotionGateStatus"),
                    "promotionGateBlocked": summary.get("promotionGateBlocked"),
                    "signals": _agent_compact_signal_bundle(
                        summary.get("visualSignalSummary"),
                        summary.get("timingSignalSummary"),
                        summary.get("environmentSignalSummary"),
                        summary.get("matchSignalSummary"),
                    ),
                }
            ),
            "promotionGate": _agent_compact_dict(
                {
                    "status": promotion_gate.get("status"),
                    "blocked": promotion_gate.get("blocked"),
                    "reasons": _agent_limited_list(promotion_gate.get("reasons"), limit=4),
                    "signals": _agent_compact_signal_bundle(
                        promotion_gate.get("signals"),
                        promotion_gate.get("visualSignalSummary"),
                        promotion_gate.get("environmentSignalSummary"),
                    ),
                }
            ),
            "manualControls": payload.get("manualControls"),
            "matches": [item for item in matches if item],
            "routeGateApplied": payload.get("routeGateApplied"),
            "desktopRouteMode": payload.get("desktopRouteMode"),
            "runtimeGoverned": payload.get("runtimeGoverned"),
            "gateErrorCode": payload.get("gateErrorCode"),
        }
    )


def _agent_count_nodes(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_agent_count_nodes(item) for item in value.values())
    if isinstance(value, list):
        return len(value) + sum(_agent_count_nodes(item) for item in value)
    return 1 if value not in (None, "") else 0



def _computer_use_build_desktop_route(
    *,
    goal: str,
    app_query: str | None,
    target_hint: str | None,
    resolved_app: dict[str, Any] | None,
    variables: dict[str, Any] | None,
    state: dict[str, Any] | None,
    limit: int = 5,
) -> tuple[dict[str, Any], dict[str, Any]]:
    patched = _compat_native_attr("_computer_use_build_desktop_route", _computer_use_build_desktop_route)
    if patched is not _computer_use_build_desktop_route:
        return patched(
            goal=goal,
            app_query=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            variables=variables,
            state=state,
            limit=limit,
        )
    runtime_context = get_runtime_context()
    route = _get_rpa_runtime().recommend_execution_route(
        goal=goal,
        app_id=(resolved_app or {}).get("appId") or app_query,
        variables=variables,
        session_id=runtime_context.get("session_id"),
        run_id=runtime_context.get("run_id"),
        limit=max(1, min(limit, 10)),
        allow_materialization=True,
    )
    payload = build_compact_execution_route(
        action="resolve_execution_route",
        goal=goal,
        app_hint=app_query,
        target_hint=target_hint,
        resolved_app=resolved_app,
        route=route,
    )
    latest_human_id, _ = _desktop_route_latest_bound_human_message(state if isinstance(state, dict) else {})
    desktop_route = {
        "goal": goal,
        "appId": payload.get("app", {}).get("appId") or route.get("appId"),
        "requestedApp": app_query,
        "target": target_hint,
        "recommendedMode": payload.get("recommendedMode"),
        "executionReadyMode": payload.get("executionReadyMode") or determine_execution_ready_mode(route),
        "recommendedRuntime": payload.get("recommendedRuntime"),
        "recommendedTool": payload.get("recommendedTool"),
        "recommendedDraftId": payload.get("recommendedDraftId"),
        "recommendedTemplateId": payload.get("recommendedTemplateId"),
        "recommendedMatch": payload.get("recommendedMatch"),
        "routeAction": payload.get("recommendedAction"),
        "boundHumanMessageId": latest_human_id,
        "source": "computer_use_execute_task" if not isinstance(state, dict) else _DESKTOP_ROUTE_SOURCE,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(
        _desktop_route_compact_metadata(
            desktop_route,
            route_gate_applied=False,
            runtime_governed=True,
        )
    )
    return _agent_compact_desktop_route_payload(payload), desktop_route


def _computer_use_parse_point_tag(value: str | None) -> list[float] | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    matched = _COMPUTER_USE_POINT_TAG_PATTERN.search(normalized)
    if not matched:
        return None
    return [float(matched.group(1)), float(matched.group(2))]


def _computer_use_cache_app_resolution(query: str | None, resolved_app: dict[str, Any] | None) -> None:
    normalized = str(query or "").strip().lower()
    if not normalized or not isinstance(resolved_app, dict) or not resolved_app:
        return
    _COMPUTER_USE_APP_RESOLUTION_CACHE[normalized] = (time.monotonic(), dict(resolved_app))


def _computer_use_resolve_app(
    app: str | None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    patched = _compat_native_attr("_computer_use_resolve_app", _computer_use_resolve_app)
    if patched is not _computer_use_resolve_app:
        return patched(app, force_refresh=force_refresh)
    query = str(app or "").strip()
    if not query:
        return None
    normalized = query.lower()
    cached_entry = _COMPUTER_USE_APP_RESOLUTION_CACHE.get(normalized)
    if (
        not force_refresh
        and isinstance(cached_entry, tuple)
        and len(cached_entry) == 2
        and (time.monotonic() - float(cached_entry[0])) * 1000 <= _COMPUTER_USE_APP_RESOLUTION_CACHE_TTL_MS
    ):
        return dict(cached_entry[1])
    payload = _get_computer_use_runtime().list_apps(
        query=query,
        limit=5,
        include_running=True,
        force_refresh=bool(force_refresh),
    )
    apps = list(payload.get("apps") or [])
    if not apps:
        return None

    def _score(entry: dict[str, Any]) -> tuple[int, int]:
        score = 0
        app_id = str(entry.get("appId") or "").strip().lower()
        display_name = str(entry.get("displayName") or "").strip().lower()
        aliases = [str(item).strip().lower() for item in list(entry.get("aliases") or []) if str(item).strip()]
        titles = [str(item).strip().lower() for item in list(entry.get("titlePatterns") or []) if str(item).strip()]
        process_names = [str(item).strip().lower() for item in list(entry.get("processNames") or []) if str(item).strip()]
        haystack = [app_id, display_name, *aliases, *titles, *process_names]
        if normalized == app_id:
            score += 180
        if normalized == display_name:
            score += 160
        if normalized in aliases:
            score += 120
        if normalized in titles:
            score += 90
        if any(normalized and normalized in item for item in haystack):
            score += 36
        if bool(entry.get("isRunning")):
            score += 12
        if bool(entry.get("profileBound")):
            score += 6
        return score, int(bool(entry.get("launchable")))

    ranked = sorted(
        (dict(entry) for entry in apps if isinstance(entry, dict)),
        key=lambda item: _score(item),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    _computer_use_cache_app_resolution(query, best)
    return best


def _computer_use_running_window_title(resolved_app: dict[str, Any] | None) -> str | None:
    if not isinstance(resolved_app, dict):
        return None
    windows = list(resolved_app.get("runningWindows") or [])
    for item in windows:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if title:
            return title
    return None


def _computer_use_effective_window_title(
    explicit_title: str | None,
    resolved_app: dict[str, Any] | None,
) -> str | None:
    normalized = str(explicit_title or "").strip()
    if normalized:
        return normalized
    return _computer_use_running_window_title(resolved_app)


def _computer_use_refresh_resolved_app_window(
    *,
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
    app_id: str | None = None,
    window_title: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(resolved_app, dict) or not resolved_app:
        return resolved_app
    refreshed = dict(resolved_app)
    normalized_title = str(window_title or "").strip()
    normalized_app_id = str(app_id or "").strip()
    if normalized_app_id and not str(refreshed.get("appId") or "").strip():
        refreshed["appId"] = normalized_app_id
    if normalized_title:
        refreshed["runningWindows"] = [{"title": normalized_title}]
    _computer_use_cache_app_resolution(app_query, refreshed)
    return refreshed


def _computer_use_prebind_window(
    *,
    action_name: str,
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
    window_title: str | None,
    window_handle: int | None = None,
    class_name: str | None = None,
    target_path: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, int | None, str | None]:
    normalized_title = str(window_title or "").strip() or None
    normalized_class = str(class_name or "").strip() or None
    normalized_target_path = str(target_path or "").strip() or None
    if not app_query and not normalized_title and not normalized_class and window_handle in (None, ""):
        return resolved_app, normalized_title, window_handle, None
    try:
        raw_result = _get_computer_use_runtime().focus_window(
            **_computer_use_runtime_kwargs(f"{action_name}:{app_query or normalized_title or normalized_class or 'desktop'}"),
            app_id=(resolved_app or {}).get("appId"),
            target_path=normalized_target_path,
            window_title=normalized_title,
            window_handle=int(window_handle) if window_handle not in (None, "") else None,
            class_name=normalized_class,
            require_visual_guard=False,
            prefer_fast_path=True,
            post_action_settle_timeout_ms=220,
            post_action_settle_poll_ms=120,
            post_action_stable_rounds=1,
        )
    except Exception as exc:
        return resolved_app, normalized_title, window_handle, f"Error ensuring desktop window: {exc}"
    updated_app = _computer_use_update_resolved_app_from_raw_result(
        app_query=app_query,
        resolved_app=resolved_app,
        raw_result=raw_result,
    )
    result = dict(raw_result.get("result") or {})
    observation = dict(result.get("observation") or {})
    target = dict(result.get("target") or {})
    metadata = dict(observation.get("metadata") or {})
    bound_title = (
        str(target.get("windowTitle") or "").strip()
        or str(observation.get("windowTitle") or "").strip()
        or normalized_title
    )
    bound_handle = (
        target.get("windowHandle")
        or target.get("handle")
        or metadata.get("windowHandle")
        or window_handle
    )
    if bound_handle not in (None, "") or bound_title:
        try:
            _get_computer_use_runtime().driver.focus_window(
                window_title=bound_title or None,
                window_handle=int(bound_handle) if bound_handle not in (None, "") else None,
            )
        except Exception:
            pass
    return updated_app, bound_title or None, int(bound_handle) if bound_handle not in (None, "") else None, None


def _computer_use_update_resolved_app_from_raw_result(
    *,
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
    raw_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = dict(raw_result or {})
    result = dict(payload.get("result") or {})
    target = dict(result.get("target") or {})
    observation = dict(result.get("observation") or payload.get("observation") or {})
    metadata = dict(observation.get("metadata") or {})
    window_title = (
        str(target.get("windowTitle") or "").strip()
        or str(observation.get("windowTitle") or "").strip()
    )
    app_id = (
        str(target.get("appId") or "").strip()
        or str(metadata.get("appId") or "").strip()
    )
    return _computer_use_refresh_resolved_app_window(
        app_query=app_query,
        resolved_app=resolved_app,
        app_id=app_id or None,
        window_title=window_title or None,
    )


def _computer_use_launch_target_override(
    *,
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
    target: str | None,
) -> dict[str, Any]:
    normalized_target = str(target or "").strip()
    if not normalized_target:
        return {}
    resolved_app_id = str((resolved_app or {}).get("appId") or "").strip().lower()
    normalized_app_query = str(app_query or "").strip().lower()
    if resolved_app_id != "explorer" and normalized_app_query not in {"explorer", "文件资源管理器", "file explorer"}:
        return {}
    target_path = Path(normalized_target).expanduser()
    try:
        target_path = target_path.resolve(strict=False)
    except Exception:
        target_path = target_path.absolute()
    if not target_path.exists() or not target_path.is_dir():
        return {}
    expected_window_title = str(target_path.name or target_path.drive or target_path).strip()
    quoted_target = str(target_path)
    return {
        "command": f'explorer.exe /e,/root,"{quoted_target}"',
        "expected_window_title": expected_window_title,
        "strict_expected_window_title": True,
        "resolved_target_path": str(target_path),
    }


def _computer_use_apply_visual_locator_step(
    step: dict[str, Any],
    *,
    visual_locator: str | None = None,
    visual_locator_scope: str | None = None,
    visual_locator_scope_padding: list[int] | None = None,
    visual_locator_scope_seed_strategy: str | None = None,
    visual_locator_confidence: float | None = None,
    visual_locator_timeout_ms: int | None = None,
    visual_locator_read_text: bool | None = None,
    visual_locator_multiple: bool | None = None,
    prefix: str = "",
) -> None:
    locator = str(visual_locator or "").strip()
    if not locator:
        return
    snake_prefix = str(prefix or "")
    step[f"{snake_prefix}visual_locator"] = locator
    scope_locator = str(visual_locator_scope or "").strip()
    if scope_locator:
        step[f"{snake_prefix}visual_locator_scope"] = scope_locator
    if isinstance(visual_locator_scope_padding, list) and len(visual_locator_scope_padding) == 4:
        step[f"{snake_prefix}visual_locator_scope_padding"] = [int(v) for v in visual_locator_scope_padding]
    scope_seed_strategy = str(visual_locator_scope_seed_strategy or "").strip()
    if scope_seed_strategy:
        step[f"{snake_prefix}visual_locator_scope_seed_strategy"] = scope_seed_strategy
    if visual_locator_confidence not in (None, ""):
        step[f"{snake_prefix}visual_locator_confidence"] = float(visual_locator_confidence)
    if visual_locator_timeout_ms not in (None, ""):
        step[f"{snake_prefix}visual_locator_timeout_ms"] = max(250, int(visual_locator_timeout_ms))
    if visual_locator_read_text is True:
        step[f"{snake_prefix}visual_locator_read_text"] = True
    if visual_locator_multiple is True:
        step[f"{snake_prefix}visual_locator_multiple"] = True


def _computer_use_apply_post_action_visual_check_step(
    step: dict[str, Any],
    *,
    post_action_visual_locator: str | None = None,
    post_action_visual_locator_confidence: float | None = None,
    post_action_visual_locator_timeout_ms: int | None = None,
    post_action_visual_locator_read_text: bool | None = None,
    post_action_visual_locator_multiple: bool | None = None,
    post_action_expect_text: str | list[str] | None = None,
) -> None:
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=post_action_visual_locator,
        visual_locator_confidence=post_action_visual_locator_confidence,
        visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        visual_locator_read_text=post_action_visual_locator_read_text,
        visual_locator_multiple=post_action_visual_locator_multiple,
        prefix="post_action_",
    )
    if isinstance(post_action_expect_text, list):
        normalized = [str(item).strip() for item in post_action_expect_text if str(item).strip()]
        if normalized:
            step["post_action_expect_texts"] = normalized
    else:
        token = str(post_action_expect_text or "").strip()
        if token:
            step["post_action_expect_text"] = token


def _computer_use_apply_environment_probe_step(
    step: dict[str, Any],
    *,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: str | None = None,
) -> None:
    if observe_notifications:
        step["observe_notifications"] = True
    if observe_sound:
        step["observe_sound"] = True
    mode = str(environment_probe_mode or "").strip().lower()
    if mode:
        step["environment_probe_mode"] = mode


def _computer_use_guard_failure_response(
    *,
    action: str,
    summary: str,
    app_hint: str | None = None,
    target_hint: str | None = None,
    resolved_app: dict[str, Any] | None = None,
    window_title: str | None = None,
) -> str:
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    return json.dumps(
        {
            "ok": False,
            "action": action,
            "status": "blocked",
            "blocked": True,
            "summary": summary,
            "blockedReason": summary,
            "app": {
                "requested": app_hint,
                "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
                "appId": (resolved_app or {}).get("appId"),
            },
            "target": {
                "requested": target_hint,
            },
            "window": {
                "title": effective_window_title,
                "handle": None,
                "appId": (resolved_app or {}).get("appId"),
                "profileId": (resolved_app or {}).get("profileId"),
                "focusedElementId": None,
            },
            "verification": {
                "passed": False,
                "status": "guardian_blocked",
                "reason": summary,
                "level": "review_required",
            },
            "scene": {
                "pageIdentity": None,
                "blockerState": "guardian_blocked",
                "transitionState": "blocked",
                "confidence": "high",
                "reasons": ["safety_guardian"],
            },
            "budget": {
                "withinBudget": True,
            },
            "executionMode": None,
            "learningLoop": None,
            "updateRequest": {
                "requested": True,
                "kind": "human_approval_required",
                "reason": summary,
            },
            "evidence": {
                "message": summary,
                "artifacts": [],
                "screenHash": None,
                "treeHash": None,
                "selectorStats": None,
                "stabilityWait": None,
                "focusedElementId": None,
            },
            "recommendedNextAction": "request_human_confirmation",
            "sessionId": None,
            "runId": None,
        },
        ensure_ascii=False,
        indent=2,
    )


def _computer_use_artifacts_from_result(action_result: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    direct_artifact = action_result.get("artifact")
    if isinstance(direct_artifact, dict) and direct_artifact:
        artifacts.append(dict(direct_artifact))
    observation = action_result.get("observation")
    screenshot_artifact = ((observation or {}).get("screenshotArtifact") or {}) if isinstance(observation, dict) else {}
    if isinstance(screenshot_artifact, dict) and screenshot_artifact:
        artifacts.append(dict(screenshot_artifact))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in artifacts:
        normalized = dict(item or {})
        normalized.setdefault("filePath", item.get("sourcePath") or item.get("source_path") or item.get("path") or item.get("file_path"))
        normalized.setdefault("workspacePath", item.get("workspacePath") or item.get("workspace_path"))
        normalized.setdefault("previewUrl", item.get("previewUrl") or item.get("preview_url"))
        key = (
            str(normalized.get("artifactId") or ""),
            str(normalized.get("filePath") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _computer_use_primary_action(raw_result: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(raw_result, dict):
        return None, None
    if isinstance(raw_result.get("result"), dict):
        return raw_result, None
    steps = list(raw_result.get("steps") or [])
    if not steps:
        return None, None
    primary_step = next((item for item in reversed(steps) if isinstance(item, dict)), None)
    if not isinstance(primary_step, dict):
        return None, None
    inner_result = primary_step.get("result")
    if isinstance(inner_result, dict) and isinstance(inner_result.get("result"), dict):
        return inner_result, primary_step
    return None, primary_step


def _computer_use_recommended_next_action(
    *,
    action_result: dict[str, Any],
    verification: dict[str, Any],
    update_request: dict[str, Any] | None,
) -> str:
    recommended = recommended_next_action_payload(
        action_type=str(action_result.get("actionType") or action_result.get("action_type") or "").strip(),
        status=str(action_result.get("status") or "").strip(),
        verification=verification,
        scene=dict((action_result.get("metadata") or {}).get("scene") or {}),
        update_request=update_request,
    )
    if recommended == "ensure_window_then_retry":
        return "rebind_window_then_retry"
    return recommended


def _computer_use_compact_response(
    *,
    action: str,
    raw_result: dict[str, Any],
    app_hint: str | None = None,
    target_hint: str | None = None,
    resolved_app: dict[str, Any] | None = None,
    expected_window_title: str | None = None,
    strict_expected_window_title: bool = False,
) -> str:
    primary_result, primary_step = _computer_use_primary_action(raw_result)
    if not isinstance(primary_result, dict):
        return json.dumps(
            {
                "ok": False,
                "action": action,
                "status": "error",
                "blocked": False,
                "summary": "未能从 computer use 返回结果中解析出主动作结果。",
                "raw": raw_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    action_result = dict(primary_result.get("result") or {})
    verification = dict(action_result.get("verification") or {})
    metadata = dict(action_result.get("metadata") or {})
    observation = dict(action_result.get("observation") or {})
    target = dict(action_result.get("target") or {})
    scene = dict(metadata.get("scene") or {})
    budget = dict(metadata.get("budget") or {})
    update_request = dict(metadata.get("updateRequest") or {}) if isinstance(metadata.get("updateRequest"), dict) else None
    status = str(action_result.get("status") or "unknown").strip()
    verification_passed = bool(verification.get("passed"))
    blocked = status == "blocked" or bool(update_request and update_request.get("requested")) or str(verification.get("level") or "").strip().lower() == "review_required"
    ok = status == "completed" and verification_passed and not blocked
    window = {
        "title": observation.get("windowTitle") or target.get("windowTitle") or target.get("title"),
        "handle": observation.get("metadata", {}).get("windowHandle") if isinstance(observation.get("metadata"), dict) else None,
        "appId": target.get("appId") or target.get("profileId") or (resolved_app or {}).get("appId"),
        "profileId": target.get("profileId") or (resolved_app or {}).get("profileId"),
        "focusedElementId": observation.get("focusedElementId"),
    }
    if window["handle"] is None:
        window["handle"] = target.get("windowHandle") or target.get("handle")
    evidence = {
        "message": action_result.get("message"),
        "artifacts": _computer_use_artifacts_from_result(action_result),
        "screenHash": observation.get("screenHash"),
        "treeHash": observation.get("treeHash"),
        "selectorStats": metadata.get("selectorStats"),
        "stabilityWait": metadata.get("stabilityWait"),
        "focusedElementId": observation.get("focusedElementId"),
        "visualLocator": dict(metadata.get("visualLocator") or {}),
        "postActionVisualLocator": dict(metadata.get("postActionVisualLocator") or {}),
        "startVisualLocator": dict(metadata.get("startVisualLocator") or {}),
        "endVisualLocator": dict(metadata.get("endVisualLocator") or {}),
    }
    primary_visual_locator = dict(evidence.get("visualLocator") or {})
    evidence["visualObservation"] = dict(primary_visual_locator.get("visualObservation") or {})
    evidence["visualJudge"] = dict(primary_visual_locator.get("visualJudge") or {})
    evidence["visualSemanticCandidates"] = [
        dict(item or {})
        for item in list(primary_visual_locator.get("visualSemanticCandidates") or [])[:6]
        if isinstance(item, dict)
    ]
    if evidence["artifacts"]:
        primary_artifact = dict(evidence["artifacts"][0] or {})
        evidence["primaryArtifact"] = primary_artifact
        evidence["location"] = {
            "kind": "runtime_artifact",
            "artifactPath": primary_artifact.get("filePath"),
            "workspacePath": primary_artifact.get("workspacePath"),
            "previewUrl": primary_artifact.get("previewUrl"),
            "artifactRootHint": ".v8-agent-os/artifacts",
        }
    runtime_recommended = str(metadata.get("recommendedNextAction") or "").strip() or None
    visual_decision = dict(metadata.get("visualDecision") or {})
    if not visual_decision:
        visual_decision = {
            "role": evidence["visualObservation"].get("role"),
            "candidateCount": evidence["visualObservation"].get("candidateCount"),
            "ambiguityLevel": evidence["visualObservation"].get("ambiguityLevel"),
            "judgeDecision": evidence["visualJudge"].get("decision"),
            "judgeConfidence": evidence["visualJudge"].get("confidence"),
        }
    visual_signal_summary = dict(metadata.get("visualSignalSummary") or evidence.get("visualSignalSummary") or {})
    if not visual_signal_summary:
        visual_signal_summary = build_visual_signal_summary_payload(
            metadata=metadata,
            visual_decision=visual_decision,
            verification=verification,
            evidence_summary=evidence,
        )
    timing_signal_summary = dict(metadata.get("timingSignalSummary") or evidence.get("timingSignalSummary") or {})
    if not timing_signal_summary:
        timing_signal_summary = build_timing_signal_summary_payload(
            metadata=metadata,
            scene=scene,
            evidence_summary=evidence,
        )
    environment_signal_summary = dict(metadata.get("environmentSignalSummary") or evidence.get("environmentSignalSummary") or {})
    if not environment_signal_summary:
        environment_signal_summary = build_environment_signal_summary_payload(
            metadata=metadata,
            observation=observation,
            evidence_summary=evidence,
        )
    browser_automation = dict(observation.get("metadata", {}).get("browserAutomation") or metadata.get("browserAutomation") or {})
    if metadata.get("browserLaneProvider") and not browser_automation.get("provider"):
        browser_automation["provider"] = metadata.get("browserLaneProvider")
    if metadata.get("browserTargetFamily") and not browser_automation.get("family"):
        browser_automation["family"] = metadata.get("browserTargetFamily")
    if metadata.get("browserTargetId") and not browser_automation.get("targetId"):
        browser_automation["targetId"] = metadata.get("browserTargetId")
    if metadata.get("route") and not browser_automation.get("route"):
        browser_automation["route"] = metadata.get("route")
    browser_session_mode = str(browser_automation.get("profilePersistenceMode") or "").strip() or None
    if browser_session_mode:
        browser_automation["preservesLoginState"] = browser_session_mode in {
            "reused_existing_window",
            "attached_existing_debug_browser",
            "default_user_profile_launch",
        }
    response = {
        "ok": ok,
        "action": action,
        "status": status,
        "blocked": blocked,
        "summary": action_result.get("message") or verification.get("reason") or "",
        "blockedReason": metadata.get("blockedReason"),
        "app": {
            "requested": app_hint,
            "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
            "appId": (resolved_app or {}).get("appId") or target.get("appId") or target.get("profileId"),
            "controlClass": metadata.get("controlClass") or (resolved_app or {}).get("controlClass"),
            "appAdapterId": metadata.get("appAdapterId") or (resolved_app or {}).get("appAdapterId"),
            "launchSelectionReason": metadata.get("launchSelectionReason"),
            "launchCandidateSource": metadata.get("launchCandidateSource"),
            "launchCandidateRole": metadata.get("launchCandidateRole"),
            "launchCandidateScore": metadata.get("launchCandidateScore"),
            "restoreStrategy": metadata.get("restoreStrategy"),
            "spawnSuppressedByRestore": bool(metadata.get("spawnSuppressedByRestore")),
        },
        "target": {
            "requested": target_hint,
            "name": target.get("name") or target.get("automationId") or target.get("elementId"),
            "selectorKey": metadata.get("selectorKey") or metadata.get("profileSelectorKey"),
            "clickedPoint": target.get("clickedPoint"),
            "bounds": target.get("bounds"),
            "visualSemanticRole": evidence["visualObservation"].get("role"),
        },
        "window": window,
        "verification": verification,
        "scene": scene,
        "budget": budget,
        "executionMode": metadata.get("executionMode"),
        "learningLoop": metadata.get("learningLoop"),
        "updateRequest": update_request,
        "evidence": evidence,
        "visualDecision": visual_decision,
        "visualSignalSummary": visual_signal_summary,
        "timingSignalSummary": timing_signal_summary,
        "environmentSignalSummary": environment_signal_summary,
        "browserAutomation": browser_automation,
        "appAdapter": dict(metadata.get("appAdapter") or {}),
        "browserSession": {
            "mode": browser_session_mode,
            "preservesLoginState": bool(browser_automation.get("preservesLoginState")),
            "attachedExistingBrowser": bool(browser_automation.get("attachedExistingBrowser")),
            "reusedExistingBrowserWindow": bool(browser_automation.get("reusedExistingBrowserWindow")),
        } if browser_automation else None,
        "recommendedNextAction": runtime_recommended
        or _computer_use_recommended_next_action(
            action_result=action_result,
            verification=verification,
            update_request=update_request,
        ),
        "sessionId": primary_result.get("sessionId") or raw_result.get("sessionId"),
        "runId": primary_result.get("runId") or raw_result.get("runId"),
    }
    if action == "paste_files" and status == "completed":
        response["summary"] = "文件粘贴动作已执行。"
    if isinstance(primary_step, dict):
        response["planStep"] = {
            "index": primary_step.get("index"),
            "status": primary_step.get("status"),
            "attemptCount": primary_step.get("attemptCount"),
            "elapsedSeconds": primary_step.get("elapsedSeconds"),
        }
    normalized_expected_title = re.sub(r"\s+", "", str(expected_window_title or "").replace("\u200b", "").strip()).lower()
    normalized_actual_title = re.sub(r"\s+", "", str(window.get("title") or "").replace("\u200b", "").strip()).lower()
    if (
        strict_expected_window_title
        and normalized_expected_title
        and normalized_actual_title
        and normalized_expected_title not in normalized_actual_title
        and normalized_actual_title not in normalized_expected_title
    ):
        mismatch_reason = f"动作执行后窗口上下文漂移，期望窗口“{expected_window_title}”，实际窗口“{window.get('title')}”。"
        response["ok"] = False
        response["blocked"] = True
        response["status"] = "blocked"
        response["summary"] = mismatch_reason
        response["blockedReason"] = mismatch_reason
        response["verification"] = {
            **dict(response.get("verification") or {}),
            "passed": False,
            "status": "post_action_window_binding_mismatch",
            "reason": mismatch_reason,
            "level": "review_required",
        }
        response["scene"] = {
            **dict(response.get("scene") or {}),
            "transitionState": "blocked",
            "blockerState": "window_context_drift",
        }
        response["updateRequest"] = {
            "requested": True,
            "kind": "ui_update_request",
            "reason": mismatch_reason,
        }
        response["recommendedNextAction"] = "ensure_window_then_retry"
    return json.dumps(response, ensure_ascii=False, indent=2)


def _computer_use_compact_observation(
    *,
    raw_result: dict[str, Any],
    app_hint: str | None = None,
    resolved_app: dict[str, Any] | None = None,
) -> str:
    observation = dict(raw_result.get("observation") or {})
    metadata = dict(observation.get("metadata") or {})
    scene_assessment = dict(observation.get("sceneAssessment") or metadata.get("sceneAssessment") or {})
    binding_assessment = dict(observation.get("bindingAssessment") or metadata.get("bindingAssessment") or {})
    environment_signal_summary = build_environment_signal_summary_payload(
        metadata={**metadata, "sceneAssessment": scene_assessment, "bindingAssessment": binding_assessment},
        observation=observation,
        evidence_summary={
            "sceneAssessment": scene_assessment,
            "bindingAssessment": binding_assessment,
        },
    )
    elements = []
    total_elements = len(list(observation.get("elements") or []))
    for item in list(observation.get("elements") or [])[:12]:
        if not isinstance(item, dict):
            continue
        elements.append(
            _agent_compact_dict(
                {
                    "elementId": item.get("elementId"),
                    "role": item.get("role"),
                    "name": _agent_preview_text(item.get("name"), limit=120),
                    "actions": _agent_limited_list(item.get("actions"), limit=4),
                    "confidence": item.get("confidence"),
                }
            )
        )
    payload = {
        "ok": True,
        "action": "observe_scene",
        "summary": "已完成当前窗口观察。",
        "app": {
            "requested": app_hint,
            "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
            "appId": (resolved_app or {}).get("appId") or observation.get("metadata", {}).get("appId"),
            "controlClass": observation.get("metadata", {}).get("controlClass") or (resolved_app or {}).get("controlClass"),
            "appAdapterId": observation.get("metadata", {}).get("appAdapterId") or (resolved_app or {}).get("appAdapterId"),
        },
        "window": {
            "title": observation.get("windowTitle"),
            "handle": metadata.get("windowHandle"),
            "focusedElementId": observation.get("focusedElementId"),
        },
        "scene": {
            "pageIdentity": scene_assessment.get("pageIdentity") or metadata.get("pageIdentity"),
            "blockerState": scene_assessment.get("blockerState") or "none",
            "transitionState": scene_assessment.get("transitionState") or "observed",
            "confidence": scene_assessment.get("confidence") or metadata.get("pageIdentityConfidence") or "low",
            "reasons": _agent_limited_list(scene_assessment.get("reasons"), limit=4),
            "elementCount": total_elements,
        },
        "bindingAssessment": {
            "status": binding_assessment.get("status"),
            "confidence": binding_assessment.get("confidence"),
            "score": binding_assessment.get("score"),
            "strictBindingRequired": bool(binding_assessment.get("strictBindingRequired")),
            "requiresUpdateRequest": bool(binding_assessment.get("requiresUpdateRequest")),
            "reasons": _agent_limited_list(binding_assessment.get("reasons"), limit=4),
        },
        "environmentSignalFlags": _agent_signal_flags(environment_signal_summary),
        "elements": elements,
        "omittedElementCount": max(0, total_elements - len(elements)),
        "recommendedNextAction": "Use the visible elementId/name for click/type tools, or request detail/rawRef if the target is missing.",
        "detailTool": "computer_use_observe_scene(..., depth_limit=..., element_limit=...) plus tool_observation_detail(rawRef)",
    }
    browser_automation = dict(metadata.get("browserAutomation") or {})
    browser_session_mode = str(browser_automation.get("profilePersistenceMode") or "").strip() or None
    if browser_session_mode:
        browser_automation["preservesLoginState"] = browser_session_mode in {
            "reused_existing_window",
            "attached_existing_debug_browser",
            "default_user_profile_launch",
        }
    if browser_automation:
        payload["browserSession"] = {
            "mode": browser_session_mode,
            "preservesLoginState": bool(browser_automation.get("preservesLoginState")),
            "attachedExistingBrowser": bool(browser_automation.get("attachedExistingBrowser")),
            "reusedExistingBrowserWindow": bool(browser_automation.get("reusedExistingBrowserWindow")),
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _computer_use_parse_variables_json(variables_json: str | None) -> Dict[str, Any]:
    if variables_json in (None, ""):
        return {}
    payload = json.loads(str(variables_json))
    if not isinstance(payload, dict):
        raise ValueError("variables_json 必须是 JSON 对象。")
    return {str(key).strip(): value for key, value in payload.items() if str(key).strip()}


def _computer_use_compact_memory_lookup(
    *,
    goal: str,
    app_hint: str | None,
    resolved_app: dict[str, Any] | None,
    route: dict[str, Any],
) -> str:
    recommended_match = dict(route.get("recommendedMatch") or {})
    response = build_compact_execution_route(
        action="lookup_muscle_memory",
        goal=goal,
        app_hint=app_hint,
        target_hint=None,
        resolved_app=resolved_app,
        route=route,
    )
    if recommended_match:
        response["recommendedToolSummary"] = (
            response.get("recommendedToolSummary")
            or "已找到可复用肌肉记忆，请先按推荐执行路径路由，而不是直接进入学习模式。"
        )
    return json.dumps(_agent_compact_desktop_route_payload(response), ensure_ascii=False, indent=2)


def _computer_use_plan_step_contract(step_payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(step_payload, dict):
        return None
    nested_result = dict(step_payload.get("result") or {})
    action_result = dict(nested_result.get("result") or {})
    if not action_result:
        return None
    metadata = dict(action_result.get("metadata") or {})
    observation = dict(action_result.get("observation") or {})
    verification = dict(action_result.get("verification") or {})
    scene = dict(metadata.get("scene") or {})
    update_request = dict(metadata.get("updateRequest") or {}) if isinstance(metadata.get("updateRequest"), dict) else None
    evidence_summary = dict(metadata.get("evidenceSummary") or {})
    if not evidence_summary:
        evidence_summary = build_evidence_summary_payload(
            message=action_result.get("message"),
            observation=observation,
            metadata=metadata,
            artifact=action_result.get("artifact"),
        )
    visual_decision = dict(metadata.get("visualDecision") or evidence_summary.get("visualDecision") or {})
    visual_signal_summary = dict(metadata.get("visualSignalSummary") or {})
    if not visual_signal_summary:
        visual_signal_summary = build_visual_signal_summary_payload(
            metadata=metadata,
            visual_decision=visual_decision,
            verification=verification,
            evidence_summary=evidence_summary,
        )
    timing_signal_summary = dict(metadata.get("timingSignalSummary") or {})
    if not timing_signal_summary:
        timing_signal_summary = build_timing_signal_summary_payload(
            metadata=metadata,
            scene=scene,
            evidence_summary=evidence_summary,
        )
    environment_signal_summary = dict(metadata.get("environmentSignalSummary") or {})
    if not environment_signal_summary:
        environment_signal_summary = build_environment_signal_summary_payload(
            metadata=metadata,
            observation=observation,
            evidence_summary=evidence_summary,
        )
    action_type = str(action_result.get("actionType") or action_result.get("action_type") or step_payload.get("action") or "").strip()
    return {
        "index": step_payload.get("index"),
        "action": str(step_payload.get("action") or action_type or "").strip() or None,
        "status": str(step_payload.get("status") or action_result.get("status") or "").strip() or None,
        "attemptCount": int(step_payload.get("attemptCount") or 0),
        "elapsedSeconds": step_payload.get("elapsedSeconds"),
        "summary": action_result.get("message") or verification.get("reason") or "",
        "blockedReason": metadata.get("blockedReason"),
        "recommendedNextAction": str(metadata.get("recommendedNextAction") or "").strip()
        or recommended_next_action_payload(
            action_type=action_type,
            status=str(action_result.get("status") or step_payload.get("status") or ""),
            verification=verification,
            scene=scene,
            update_request=update_request,
        ),
        "verification": verification,
        "scene": scene,
        "visualSignalSummary": _compact_visual_signal_summary(visual_signal_summary),
        "timingSignalSummary": _compact_timing_signal_summary(timing_signal_summary),
        "environmentSignalSummary": _compact_environment_signal_summary(
            draft_environment_signal_summary(metadata=environment_signal_summary)
        ),
    }


def _computer_use_aggregate_plan_step_contracts(step_contracts: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = {
        "completed": 0,
        "blocked": 0,
        "update_requested": 0,
        "failed": 0,
        "other": 0,
    }
    visual_roles: list[str] = []
    visual_providers: list[str] = []
    timing_transition_states: list[str] = []
    timing_stability_statuses: list[str] = []
    environment_page_identities: list[str] = []
    environment_affordances: list[str] = []
    environment_transition_states: list[str] = []
    environment_blocker_states: list[str] = []
    environment_dialog_confidence_levels: list[str] = []
    environment_notification_providers: list[str] = []
    environment_sound_providers: list[str] = []
    notification_modes: list[str] = []
    sound_modes: list[str] = []

    visual_locator_backed_steps = 0
    verified_visual_locator_steps = 0
    visual_judge_steps = 0
    visual_judge_selected_steps = 0
    wait_sensitive_steps = 0
    loading_sensitive_steps = 0
    stability_wait_observed_steps = 0
    stability_wait_timeout_steps = 0
    budget_exceeded_steps = 0
    time_budget_exceeded_steps = 0
    max_settle_budget_ms = 0
    max_elapsed_ms = 0
    max_post_action_settle_timeout_ms = 0
    max_post_action_settle_poll_ms = 0
    max_post_action_stable_rounds = 0
    ambient_observation_backed_steps = 0
    window_binding_verified_steps = 0
    dialog_aware_steps = 0
    focus_aware_steps = 0
    blocking_aware_steps = 0
    transition_aware_steps = 0
    notification_observed_steps = 0
    max_notification_candidate_count = 0
    sound_observed_steps = 0
    max_sound_active_session_count = 0
    notification_sensing_requested_count = 0
    notification_sensing_available_count = 0
    sound_sensing_requested_count = 0
    sound_sensing_available_count = 0
    notification_sensing_requested = False
    notification_sensing_available = False
    sound_sensing_requested = False
    sound_sensing_available = False

    def _extend_unique(target: list[str], values: list[Any]) -> None:
        for item in values:
            text = str(item or "").strip()
            if text and text not in target:
                target.append(text)

    for step_contract in step_contracts:
        status = str(step_contract.get("status") or "").strip().lower()
        if status in status_counts:
            status_counts[status] += 1
        elif status:
            status_counts["other"] += 1

        visual_signal_summary = dict(step_contract.get("visualSignalSummary") or {})
        if bool(visual_signal_summary.get("visualLocatorBacked")):
            visual_locator_backed_steps += int(visual_signal_summary.get("visualLocatorBackedSteps") or 0) or 1
        verified_visual_locator_steps += int(visual_signal_summary.get("verifiedVisualLocatorSteps") or 0)
        if bool(visual_signal_summary.get("visualJudgeBacked")):
            visual_judge_steps += int(visual_signal_summary.get("visualJudgeSteps") or 0) or 1
        visual_judge_selected_steps += int(visual_signal_summary.get("visualJudgeSelectedSteps") or 0)
        _extend_unique(visual_roles, list(visual_signal_summary.get("visualSemanticRoles") or []))
        _extend_unique(visual_providers, list(visual_signal_summary.get("visualLocatorProviders") or []))

        timing_signal_summary = dict(step_contract.get("timingSignalSummary") or {})
        if bool(timing_signal_summary.get("waitSensitive")):
            wait_sensitive_steps += int(timing_signal_summary.get("waitSensitiveSteps") or 0) or 1
        loading_sensitive_steps += int(timing_signal_summary.get("loadingSensitiveSteps") or 0)
        stability_wait_observed_steps += int(timing_signal_summary.get("stabilityWaitObservedSteps") or 0)
        stability_wait_timeout_steps += int(timing_signal_summary.get("stabilityWaitTimeoutSteps") or 0)
        budget_exceeded_steps += int(timing_signal_summary.get("budgetExceededSteps") or 0)
        time_budget_exceeded_steps += int(timing_signal_summary.get("timeBudgetExceededSteps") or 0)
        max_settle_budget_ms = max(max_settle_budget_ms, int(timing_signal_summary.get("maxSettleBudgetMs") or 0))
        max_elapsed_ms = max(max_elapsed_ms, int(timing_signal_summary.get("maxElapsedMs") or 0))
        max_post_action_settle_timeout_ms = max(
            max_post_action_settle_timeout_ms,
            int(timing_signal_summary.get("maxPostActionSettleTimeoutMs") or 0),
        )
        max_post_action_settle_poll_ms = max(
            max_post_action_settle_poll_ms,
            int(timing_signal_summary.get("maxPostActionSettlePollMs") or 0),
        )
        max_post_action_stable_rounds = max(
            max_post_action_stable_rounds,
            int(timing_signal_summary.get("maxPostActionStableRounds") or 0),
        )
        _extend_unique(timing_transition_states, list(timing_signal_summary.get("transitionStates") or []))
        _extend_unique(timing_stability_statuses, list(timing_signal_summary.get("stabilityWaitStatuses") or []))

        environment_signal_summary = dict(step_contract.get("environmentSignalSummary") or {})
        if bool(environment_signal_summary.get("desktopEnvironmentAware")):
            ambient_observation_backed_steps += int(environment_signal_summary.get("ambientObservationBackedSteps") or 0) or 1
        window_binding_verified_steps += int(environment_signal_summary.get("windowBindingVerifiedSteps") or 0)
        dialog_aware_steps += int(environment_signal_summary.get("dialogAwareSteps") or 0)
        focus_aware_steps += int(environment_signal_summary.get("focusAwareSteps") or 0)
        blocking_aware_steps += int(environment_signal_summary.get("blockingAwareSteps") or 0)
        transition_aware_steps += int(environment_signal_summary.get("transitionAwareSteps") or 0)
        _extend_unique(environment_page_identities, list(environment_signal_summary.get("pageIdentities") or []))
        _extend_unique(environment_affordances, list(environment_signal_summary.get("affordances") or []))
        _extend_unique(environment_transition_states, list(environment_signal_summary.get("transitionStates") or []))
        _extend_unique(environment_blocker_states, list(environment_signal_summary.get("blockerStates") or []))
        _extend_unique(
            environment_dialog_confidence_levels,
            list(environment_signal_summary.get("dialogConfidenceLevels") or []),
        )
        notification_sensing_requested = notification_sensing_requested or bool(
            environment_signal_summary.get("notificationSensingRequested")
        )
        notification_sensing_available = notification_sensing_available or bool(
            environment_signal_summary.get("notificationSensingAvailable")
        )
        sound_sensing_requested = sound_sensing_requested or bool(environment_signal_summary.get("soundSensingRequested"))
        sound_sensing_available = sound_sensing_available or bool(environment_signal_summary.get("soundSensingAvailable"))
        notification_sensing_requested_count += 1 if bool(
            environment_signal_summary.get("notificationSensingRequested")
        ) else 0
        notification_sensing_available_count += 1 if bool(
            environment_signal_summary.get("notificationSensingAvailable")
        ) else 0
        sound_sensing_requested_count += 1 if bool(environment_signal_summary.get("soundSensingRequested")) else 0
        sound_sensing_available_count += 1 if bool(environment_signal_summary.get("soundSensingAvailable")) else 0
        _extend_unique(
            notification_modes,
            [
                environment_signal_summary.get("notificationSensingMode"),
                *(list(environment_signal_summary.get("notificationSensingModes") or [])),
            ],
        )
        _extend_unique(
            sound_modes,
            [
                environment_signal_summary.get("soundSensingMode"),
                *(list(environment_signal_summary.get("soundSensingModes") or [])),
            ],
        )
        _extend_unique(
            environment_notification_providers,
            list(environment_signal_summary.get("notificationSignalProviders") or []),
        )
        _extend_unique(environment_sound_providers, list(environment_signal_summary.get("soundSignalProviders") or []))
        notification_observed_steps += int(environment_signal_summary.get("notificationObservedSteps") or 0)
        max_notification_candidate_count = max(
            max_notification_candidate_count,
            int(environment_signal_summary.get("maxNotificationCandidateCount") or 0),
        )
        sound_observed_steps += int(environment_signal_summary.get("soundObservedSteps") or 0)
        max_sound_active_session_count = max(
            max_sound_active_session_count,
            int(environment_signal_summary.get("maxSoundActiveSessionCount") or 0),
        )

    visual_signal_summary = _compact_visual_signal_summary(
        {
            "visualLocatorBacked": visual_locator_backed_steps > 0,
            "visualLocatorBackedSteps": visual_locator_backed_steps,
            "verifiedVisualLocatorSteps": verified_visual_locator_steps,
            "visualJudgeBacked": visual_judge_steps > 0,
            "visualJudgeSteps": visual_judge_steps,
            "visualJudgeSelectedSteps": visual_judge_selected_steps,
            "visualSemanticRoles": visual_roles,
            "visualLocatorProviders": visual_providers,
        }
    )
    timing_signal_summary = _compact_timing_signal_summary(
        {
            "waitSensitive": wait_sensitive_steps > 0,
            "waitSensitiveSteps": wait_sensitive_steps,
            "loadingSensitiveSteps": loading_sensitive_steps,
            "transitionStates": timing_transition_states,
            "stabilityWaitObservedSteps": stability_wait_observed_steps,
            "stabilityWaitTimeoutSteps": stability_wait_timeout_steps,
            "stabilityWaitStatuses": timing_stability_statuses,
            "budgetExceededSteps": budget_exceeded_steps,
            "timeBudgetExceededSteps": time_budget_exceeded_steps,
            "maxSettleBudgetMs": max_settle_budget_ms,
            "maxElapsedMs": max_elapsed_ms,
            "maxPostActionSettleTimeoutMs": max_post_action_settle_timeout_ms,
            "maxPostActionSettlePollMs": max_post_action_settle_poll_ms,
            "maxPostActionStableRounds": max_post_action_stable_rounds,
        }
    )
    environment_signal_summary = _compact_environment_signal_summary(
        {
            "desktopEnvironmentAware": ambient_observation_backed_steps > 0
            or window_binding_verified_steps > 0
            or dialog_aware_steps > 0
            or focus_aware_steps > 0
            or blocking_aware_steps > 0
            or transition_aware_steps > 0
            or notification_sensing_requested
            or sound_sensing_requested
            or notification_sensing_available
            or sound_sensing_available,
            "ambientObservationBackedSteps": ambient_observation_backed_steps,
            "windowBindingVerifiedSteps": window_binding_verified_steps,
            "dialogAwareSteps": dialog_aware_steps,
            "focusAwareSteps": focus_aware_steps,
            "blockingAwareSteps": blocking_aware_steps,
            "transitionAwareSteps": transition_aware_steps,
            "pageIdentities": environment_page_identities,
            "affordances": environment_affordances,
            "transitionStates": environment_transition_states,
            "blockerStates": environment_blocker_states,
            "dialogConfidenceLevels": environment_dialog_confidence_levels,
            "notificationSensingRequested": notification_sensing_requested,
            "notificationSensingAvailable": notification_sensing_available,
            "notificationSensingRequestedCount": notification_sensing_requested_count,
            "notificationSensingAvailableCount": notification_sensing_available_count,
            "notificationSensingMode": notification_modes[0] if len(notification_modes) == 1 else ("mixed" if notification_modes else None),
            "notificationSensingModes": notification_modes,
            "notificationSignalProviders": environment_notification_providers,
            "notificationObserved": notification_observed_steps > 0,
            "notificationObservedSteps": notification_observed_steps,
            "notificationCandidateCount": max_notification_candidate_count,
            "maxNotificationCandidateCount": max_notification_candidate_count,
            "soundSensingRequested": sound_sensing_requested,
            "soundSensingAvailable": sound_sensing_available,
            "soundSensingRequestedCount": sound_sensing_requested_count,
            "soundSensingAvailableCount": sound_sensing_available_count,
            "soundSensingMode": sound_modes[0] if len(sound_modes) == 1 else ("mixed" if sound_modes else None),
            "soundSensingModes": sound_modes,
            "soundSignalProviders": environment_sound_providers,
            "soundObserved": sound_observed_steps > 0,
            "soundObservedSteps": sound_observed_steps,
            "soundActiveSessionCount": max_sound_active_session_count,
            "maxSoundActiveSessionCount": max_sound_active_session_count,
        }
    )
    execution_summary = {
        "ok": status_counts["blocked"] == 0 and status_counts["update_requested"] == 0 and status_counts["failed"] == 0,
        "totalSteps": len(step_contracts),
        "completedSteps": status_counts["completed"],
        "blockedSteps": status_counts["blocked"],
        "updateRequestedSteps": status_counts["update_requested"],
        "failedSteps": status_counts["failed"],
        "otherSteps": status_counts["other"],
    }
    return {
        "executionSummary": execution_summary,
        "visualSignalSummary": visual_signal_summary,
        "timingSignalSummary": timing_signal_summary,
        "environmentSignalSummary": environment_signal_summary,
        "steps": step_contracts[:5],
    }


def _computer_use_attach_plan_contract_summary(
    *,
    payload: dict[str, Any],
    action: str,
    goal: str | None = None,
) -> dict[str, Any]:
    patched = _compat_native_attr("_computer_use_attach_plan_contract_summary", _computer_use_attach_plan_contract_summary)
    if patched is not _computer_use_attach_plan_contract_summary:
        return patched(payload=payload, action=action, goal=goal)
    execution_payload = dict(payload.get("execution") or payload)
    step_contracts = [
        item
        for item in (
            _computer_use_plan_step_contract(dict(step or {}))
            for step in list(execution_payload.get("steps") or [])
            if isinstance(step, dict)
        )
        if isinstance(item, dict)
    ]
    contract_summary = _computer_use_aggregate_plan_step_contracts(step_contracts)
    execution_summary = dict(contract_summary.get("executionSummary") or {})
    payload["ok"] = bool(execution_summary.get("ok"))
    payload["action"] = action
    if goal:
        payload["goal"] = goal
    payload["executionSummary"] = execution_summary
    payload["visualSignalSummary"] = dict(contract_summary.get("visualSignalSummary") or {})
    payload["timingSignalSummary"] = dict(contract_summary.get("timingSignalSummary") or {})
    payload["environmentSignalSummary"] = dict(contract_summary.get("environmentSignalSummary") or {})
    payload["contractSummary"] = contract_summary
    return payload


def _computer_use_execute_task_step_samples(step_contracts: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for step in list(step_contracts or [])[: max(1, limit)]:
        if not isinstance(step, dict):
            continue
        samples.append(
            {
                "index": step.get("index"),
                "action": step.get("action"),
                "status": step.get("status"),
                "summary": step.get("summary"),
                "recommendedNextAction": step.get("recommendedNextAction"),
            }
        )
    return samples


def _computer_use_execute_task_next_action(
    *,
    ok: bool,
    requires_human_attention: bool,
    step_contracts: list[dict[str, Any]] | None = None,
) -> str:
    for step in list(step_contracts or []):
        if not isinstance(step, dict):
            continue
        recommended = str(step.get("recommendedNextAction") or "").strip()
        if recommended and str(step.get("status") or "").strip().lower() != "completed":
            return recommended
    if ok:
        return "observe_scene_verify"
    if requires_human_attention:
        return "request_human_attention"
    return "resolve_route_then_retry"


def _computer_use_execute_task_compact_computer_use_result(
    *,
    payload: dict[str, Any],
    execution_ready_mode: str,
    goal: str,
    app_hint: str | None,
    target_hint: str | None,
    resolved_app: dict[str, Any] | None,
    success_criteria: str | None,
) -> dict[str, Any]:
    from runtimes.computer_use.tool_surface import compact_execute_task_result

    return compact_execute_task_result(
        payload=payload,
        execution_ready_mode=execution_ready_mode,
        goal=goal,
        app_hint=app_hint,
        target_hint=target_hint,
        resolved_app=resolved_app,
        success_criteria=success_criteria,
    )


def _computer_use_execute_task_compact_rpa_result(
    *,
    raw_result: dict[str, Any],
    execution_ready_mode: str,
    goal: str,
    app_hint: str | None,
    target_hint: str | None,
    resolved_app: dict[str, Any] | None,
    success_criteria: str | None,
) -> dict[str, Any]:
    status = str(raw_result.get("status") or "").strip() or "unknown"
    fallback = dict(raw_result.get("fallback") or {})
    template_policy = dict(raw_result.get("templateExecutionPolicy") or {})
    prepared = dict(raw_result.get("prepared") or {})
    script = dict(raw_result.get("script") or prepared.get("script") or {})
    review_required = status in {"review_required", "blocked"}
    ok = status in {"completed", "completed_with_fallback"}
    executed_by = "hybrid" if execution_ready_mode == "hybrid_mode" or status == "completed_with_fallback" else "rpa"
    summary = "已通过 RPA 复用链执行桌面任务。"
    if executed_by == "hybrid" and ok:
        summary = "已通过 RPA 骨架执行，并在需要时由 ComputerUseRuntime 做局部修补与恢复。"
    elif not ok and review_required:
        summary = "RPA 执行链返回了需要人工处理的结果。"
    elif not ok:
        summary = "RPA 执行链未满足当前任务目标，需要重新路由或重试。"
    return {
        "ok": ok,
        "executionReadyMode": execution_ready_mode,
        "executedBy": executed_by,
        "summary": summary,
        "verification": {
            "passed": ok,
            "status": status,
            "successCriteria": str(success_criteria or "").strip() or None,
            "reviewRequired": review_required,
            "templateExecutionPolicy": {
                "executionPath": template_policy.get("executionPath"),
                "requiresHumanReview": template_policy.get("requiresHumanReview"),
                "trustStatus": template_policy.get("trustStatus"),
            },
            "fallbackUsed": bool(fallback),
        },
        "evidence": {
            "goal": goal,
            "app": {
                "requested": app_hint,
                "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
                "appId": (resolved_app or {}).get("appId") or script.get("appId") or prepared.get("appId"),
            },
            "target": target_hint,
            "scriptId": raw_result.get("scriptId") or prepared.get("scriptId"),
            "scriptName": script.get("name") or prepared.get("scriptName"),
            "fallback": {
                "mode": fallback.get("mode"),
                "recoveredStepCount": fallback.get("recoveredStepCount"),
                "fallbackStepId": fallback.get("fallbackStepId"),
            } if fallback else None,
        },
        "recommendedNextAction": (
            "observe_scene_verify"
            if ok
            else ("request_human_attention" if review_required else "resolve_route_then_retry")
        ),
        "requiresRetry": not ok and not review_required,
        "requiresHumanAttention": review_required,
    }


def _computer_use_compact_memory_list(
    *,
    templates: list[dict[str, Any]],
    app_hint: str | None,
    resolved_app: dict[str, Any] | None,
    status: str | None,
) -> str:
    items = []
    visual_locator_backed = 0
    visual_judge_backed = 0
    wait_sensitive = 0
    loading_sensitive = 0
    environment_aware = 0
    dialog_aware = 0
    focus_aware = 0
    notification_aware = 0
    sound_aware = 0
    notification_sensing_requested_count = 0
    notification_sensing_available_count = 0
    sound_sensing_requested_count = 0
    sound_sensing_available_count = 0
    notification_observed = 0
    sound_observed = 0
    max_notification_candidate_count = 0
    max_sound_active_session_count = 0
    notification_signal_providers: list[str] = []
    sound_signal_providers: list[str] = []
    notification_sensing_modes: list[str] = []
    sound_sensing_modes: list[str] = []

    def _extend_unique(target: list[str], values: list[Any]) -> None:
        for item in values:
            normalized = str(item or "").strip()
            if normalized and normalized not in target:
                target.append(normalized)

    for item in templates:
        if not isinstance(item, dict):
            continue
        governance = dict(item.get("governance") or {})
        view = dict(item.get("view") or {})
        metadata = dict(item.get("metadata") or {})
        timing_signal_summary = dict(view.get("timingSignalSummary") or {})
        if not timing_signal_summary:
            timing_signal_summary = draft_timing_signal_summary(
                {"signals": dict(view.get("promotionGateSignals") or {})},
                metadata=metadata,
            )
        environment_signal_summary = dict(view.get("environmentSignalSummary") or {})
        if not environment_signal_summary:
            environment_signal_summary = draft_environment_signal_summary(
                {"signals": dict(view.get("promotionGateSignals") or {})},
                metadata=metadata,
            )
        if bool(dict(view.get("visualSignalSummary") or {}).get("visualLocatorBacked")):
            visual_locator_backed += 1
        if bool(dict(view.get("visualSignalSummary") or {}).get("visualJudgeBacked")):
            visual_judge_backed += 1
        if bool(timing_signal_summary.get("waitSensitive")):
            wait_sensitive += 1
        if int(timing_signal_summary.get("loadingSensitiveSteps") or 0) > 0:
            loading_sensitive += 1
        environment_aware = environment_aware + 1 if bool(environment_signal_summary.get("desktopEnvironmentAware")) else environment_aware
        dialog_aware = dialog_aware + 1 if int(environment_signal_summary.get("dialogAwareSteps") or 0) > 0 else dialog_aware
        focus_aware = focus_aware + 1 if int(environment_signal_summary.get("focusAwareSteps") or 0) > 0 else focus_aware
        notification_aware = notification_aware + 1 if (
            bool(environment_signal_summary.get("notificationSensingRequested"))
            or bool(environment_signal_summary.get("notificationSensingAvailable"))
        ) else notification_aware
        sound_aware = sound_aware + 1 if (
            bool(environment_signal_summary.get("soundSensingRequested"))
            or bool(environment_signal_summary.get("soundSensingAvailable"))
        ) else sound_aware
        notification_sensing_requested_count = notification_sensing_requested_count + 1 if bool(
            environment_signal_summary.get("notificationSensingRequested")
        ) else notification_sensing_requested_count
        notification_sensing_available_count = notification_sensing_available_count + 1 if bool(
            environment_signal_summary.get("notificationSensingAvailable")
        ) else notification_sensing_available_count
        sound_sensing_requested_count = sound_sensing_requested_count + 1 if bool(
            environment_signal_summary.get("soundSensingRequested")
        ) else sound_sensing_requested_count
        sound_sensing_available_count = sound_sensing_available_count + 1 if bool(
            environment_signal_summary.get("soundSensingAvailable")
        ) else sound_sensing_available_count
        notification_observed = notification_observed + 1 if int(
            environment_signal_summary.get("notificationObservedSteps") or 0
        ) > 0 else notification_observed
        sound_observed = sound_observed + 1 if int(
            environment_signal_summary.get("soundObservedSteps") or 0
        ) > 0 else sound_observed
        max_notification_candidate_count = max(
            max_notification_candidate_count,
            int(environment_signal_summary.get("maxNotificationCandidateCount") or 0),
        )
        max_sound_active_session_count = max(
            max_sound_active_session_count,
            int(environment_signal_summary.get("maxSoundActiveSessionCount") or 0),
        )
        _extend_unique(
            notification_signal_providers,
            list(environment_signal_summary.get("notificationSignalProviders") or []),
        )
        _extend_unique(
            sound_signal_providers,
            list(environment_signal_summary.get("soundSignalProviders") or []),
        )
        _extend_unique(
            notification_sensing_modes,
            [
                environment_signal_summary.get("notificationSensingMode"),
                *(list(environment_signal_summary.get("notificationSensingModes") or [])),
            ],
        )
        _extend_unique(
            sound_sensing_modes,
            [
                environment_signal_summary.get("soundSensingMode"),
                *(list(environment_signal_summary.get("soundSensingModes") or [])),
            ],
        )
        items.append(
            _agent_compact_dict(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "appId": item.get("appId"),
                    "goal": _agent_preview_text(item.get("goal"), limit=280),
                    "status": item.get("status"),
                    "stage": governance.get("stage"),
                    "rolloutMode": governance.get("rolloutMode"),
                    "confidence": governance.get("confidence"),
                    "reviewSummary": _agent_preview_text(view.get("reviewSummary"), limit=240),
                    "riskFlags": _agent_limited_list(view.get("riskFlags"), limit=8),
                    "signals": _agent_compact_signal_bundle(
                        view.get("visualSignalSummary"),
                        timing_signal_summary,
                        environment_signal_summary,
                    ),
                }
            )
        )
    return json.dumps(
        {
            "ok": True,
            "action": "list_muscle_memories",
            "app": {
                "requested": app_hint,
                "resolved": (resolved_app or {}).get("displayName") or (resolved_app or {}).get("appId"),
                "appId": (resolved_app or {}).get("appId"),
            },
            "status": status,
            "count": len(items),
            "templateSummary": {
                "total": len(items),
                "visualLocatorBackedCount": visual_locator_backed,
                "visualJudgeBackedCount": visual_judge_backed,
                "waitSensitiveCount": wait_sensitive,
                "loadingSensitiveCount": loading_sensitive,
                "environmentAwareCount": environment_aware,
                "dialogAwareCount": dialog_aware,
                "focusAwareCount": focus_aware,
                "notificationAwareCount": notification_aware,
                "soundAwareCount": sound_aware,
                "notificationSensingRequested": notification_sensing_requested_count > 0,
                "notificationSensingAvailable": notification_sensing_available_count > 0,
                "soundSensingRequested": sound_sensing_requested_count > 0,
                "soundSensingAvailable": sound_sensing_available_count > 0,
                "notificationSensingRequestedCount": notification_sensing_requested_count,
                "notificationSensingAvailableCount": notification_sensing_available_count,
                "soundSensingRequestedCount": sound_sensing_requested_count,
                "soundSensingAvailableCount": sound_sensing_available_count,
                "notificationObserved": notification_observed > 0,
                "soundObserved": sound_observed > 0,
                "notificationObservedCount": notification_observed,
                "soundObservedCount": sound_observed,
                "notificationSignalProviders": notification_signal_providers,
                "soundSignalProviders": sound_signal_providers,
                "notificationSensingModes": notification_sensing_modes,
                "soundSensingModes": sound_sensing_modes,
                "maxNotificationCandidateCount": max_notification_candidate_count,
                "maxSoundActiveSessionCount": max_sound_active_session_count,
            },
            "manualControls": {
                "humanCanApprove": True,
                "humanCanFreeze": True,
                "humanCanRollback": True,
                "humanCanReject": True,
            },
            "templates": items,
        },
        ensure_ascii=False,
        indent=2,
    )


def _computer_use_compact_primitive_catalog(*, category: str | None = None, detail_level: str = "summary") -> str:
    matrix = primitive_validation_matrix()
    normalized_detail = str(detail_level or "summary").strip().lower()
    primitives = list_computer_use_primitives(category=category)
    compact_primitives = [
        _agent_compact_dict(
            {
                "actionType": item.get("actionType"),
                "id": item.get("id"),
                "category": item.get("category"),
                "affordances": _agent_limited_list(item.get("affordances"), limit=8),
                "requiresPageIdentity": item.get("requiresPageIdentity"),
                "requiresVerificationContract": item.get("requiresVerificationContract"),
                "requiresRecoveryPolicy": item.get("requiresRecoveryPolicy"),
                "supportsRpaPromotion": item.get("supportsRpaPromotion"),
                "notes": _agent_limited_list(item.get("notes"), limit=4) if category or normalized_detail == "diagnostic" else [],
            }
        )
        for item in primitives
        if isinstance(item, dict)
    ]
    payload = {
        "ok": True,
        "action": "list_primitives",
        "category": category,
        "detailLevel": normalized_detail,
        "summary": dict(matrix.get("summary") or {}),
        "categories": dict(matrix.get("categories") or {}),
    }
    if category or normalized_detail in {"detail", "diagnostic", "full"}:
        payload["primitives"] = compact_primitives[:50]
    else:
        payload["primitiveSamples"] = compact_primitives[:8]
    return json.dumps(
        _agent_compact_dict(payload),
        ensure_ascii=False,
        indent=2,
    )


def _computer_use_compact_driver_capabilities(*, detail_level: str = "summary") -> str:
    normalized_detail = str(detail_level or "summary").strip().lower()
    runtime_descriptor = _get_computer_use_runtime().runtime_descriptor()
    computer_use_runtime = _get_computer_use_runtime()
    availability = (
        computer_use_runtime.availability()
        if hasattr(computer_use_runtime, "availability")
        else {}
    )
    availability_details = dict((availability or {}).get("details") or {})
    capabilities = dict(availability_details.get("capabilities") or {})
    raw_capability_truth = dict(availability_details.get("capabilityTruth") or {})
    compact_truth_platforms: dict[str, Any] = {}
    for platform_key, platform_payload in dict(raw_capability_truth.get("platforms") or {}).items():
        platform = dict(platform_payload or {})
        compact_truth_platforms[str(platform_key)] = {
            "displayPlatform": platform.get("displayPlatform") or platform_key,
            "currentHost": bool(platform.get("currentHost")),
            "statusCounts": dict(platform.get("statusCounts") or {}),
            "facets": [
                {
                    "key": facet.get("key"),
                    "status": facet.get("status"),
                    "available": bool(facet.get("available")),
                    "validationLevel": facet.get("validationLevel"),
                }
                for facet in list(platform.get("facets") or [])
                if isinstance(facet, dict)
            ],
        }
    capability_truth = {
        "version": raw_capability_truth.get("version"),
        "currentPlatform": raw_capability_truth.get("currentPlatform"),
        "platforms": compact_truth_platforms,
        "browserLaneTruth": dict(raw_capability_truth.get("browserLaneTruth") or {}),
        "platformParity": dict(raw_capability_truth.get("platformParity") or {}),
        "knownGaps": list(raw_capability_truth.get("knownGaps") or []),
        "portableChecklist": list(raw_capability_truth.get("portableChecklist") or []),
        "screenWakePolicy": dict(raw_capability_truth.get("screenWakePolicy") or {}),
        "evidenceRefs": list(raw_capability_truth.get("evidenceRefs") or []),
    }
    experience_assets = dict(availability_details.get("experienceAssets") or {})
    driver_summary = {
        key: _agent_compact_dict(
            {
                "implemented": value.get("implemented"),
                "available": value.get("available"),
                "validationLevel": value.get("validationLevel"),
                "status": value.get("status"),
                "notes": _agent_limited_list((value.get("details") or {}).get("notes"), limit=3)
                if isinstance(value, dict)
                else [],
            }
        )
        for key, value in capabilities.items()
        if isinstance(value, dict)
    }
    if normalized_detail not in {"diagnostic", "detail", "full"}:
        current_platform = (
            raw_capability_truth.get("currentPlatform")
            or availability_details.get("platform")
            or sys.platform
        )
        current_platform_status = None
        for key, value in compact_truth_platforms.items():
            if isinstance(value, dict) and value.get("currentHost"):
                current_platform = value.get("displayPlatform") or key
                current_platform_status = value.get("statusCounts")
                break
        current_display = dict(availability_details.get("currentDisplay") or {})
        browser_lane = dict(availability_details.get("browserLane") or {})
        browser_profile = dict(availability_details.get("browserProfilePersistence") or {})
        compact_driver = {
            key: _agent_compact_dict(
                {
                    "available": value.get("available"),
                    "status": value.get("status"),
                    "validationLevel": value.get("validationLevel"),
                }
            )
            for key, value in capabilities.items()
            if isinstance(value, dict)
        }
        summary_payload = {
            "ok": True,
            "action": "desktop_capabilities",
            "detailLevel": normalized_detail,
            "summary": "Current desktop host capability snapshot; use diagnostic detail for platform matrix and driver notes.",
            "currentHost": {
                "platform": current_platform,
                "statusCounts": current_platform_status,
            },
            "driverHealth": compact_driver,
            "browser": {
                "enabled": browser_lane.get("enabled"),
                "provider": browser_lane.get("provider"),
                "profilePersistent": browser_profile.get("persistent"),
            },
            "display": _agent_compact_dict(
                {
                    "width": current_display.get("width"),
                    "height": current_display.get("height"),
                    "scaleFactor": current_display.get("scaleFactor"),
                }
            ),
            "knownGaps": _agent_limited_list(
                availability_details.get("knownGaps") or capability_truth.get("knownGaps"),
                limit=5,
            ),
            "primitiveMatrix": dict((primitive_validation_matrix().get("summary")) or {}),
            "recommendedNextAction": "Use computer_use_observe_scene for current UI state or detail_level='diagnostic' for full matrix.",
            "detailTool": "computer_use_desktop_capabilities(detail_level='diagnostic')",
        }
        return json.dumps(
            _agent_compact_dict(summary_payload),
            ensure_ascii=False,
            indent=2,
        )
    payload = {
        "ok": True,
        "action": "desktop_capabilities",
        "detailLevel": normalized_detail,
        "driver": driver_summary,
        "capabilityTruth": {
            "version": capability_truth.get("version"),
            "currentPlatform": capability_truth.get("currentPlatform"),
            "platformStatus": {
                key: {
                    "currentHost": value.get("currentHost"),
                    "statusCounts": value.get("statusCounts"),
                }
                for key, value in dict(capability_truth.get("platforms") or {}).items()
                if isinstance(value, dict)
            },
            "knownGaps": _agent_limited_list(capability_truth.get("knownGaps"), limit=8),
        },
        "browserSession": {
            "browserLane": dict(availability_details.get("browserLane") or {}),
            "browserProfilePersistence": dict(availability_details.get("browserProfilePersistence") or {}),
        },
        "currentDisplay": dict(availability_details.get("currentDisplay") or {}),
        "knownGaps": _agent_limited_list(availability_details.get("knownGaps"), limit=10),
        "portableChecklist": _agent_limited_list(availability_details.get("portableChecklist"), limit=10),
        "routePolicy": dict(availability_details.get("routePolicy") or {}),
        "runtime": {
            "kind": runtime_descriptor.get("kind"),
            "displayName": runtime_descriptor.get("displayName"),
            "summary": runtime_descriptor.get("summary"),
            "responsibilities": _agent_limited_list(runtime_descriptor.get("responsibilities"), limit=8),
            "promptHints": _agent_limited_list(runtime_descriptor.get("promptHints"), limit=8),
        },
        "primitiveMatrix": dict((primitive_validation_matrix().get("summary")) or {}),
    }
    if normalized_detail in {"diagnostic", "detail", "full"}:
        payload.update(
            {
                "capabilityMatrix": dict(availability_details.get("capabilityMatrix") or {}),
                "capabilityTruthPlatforms": dict(capability_truth.get("platforms") or {}),
                "screenWakePolicy": dict(availability_details.get("screenWakePolicy") or {}),
                "resolutionPolicy": dict(availability_details.get("resolutionPolicy") or {}),
                "coordinateAnchorPolicy": dict(availability_details.get("coordinateAnchorPolicy") or {}),
                "resourceCleanupPolicy": dict(availability_details.get("resourceCleanupPolicy") or {}),
                "visualActor": dict(availability_details.get("visualActor") or {}),
                "candidateBoardSources": _agent_limited_list(availability_details.get("candidateBoardSources"), limit=20),
                "platformProbeMatrix": dict(availability_details.get("platformProbeMatrix") or {}),
                "builtInPlaybookSeeds": _agent_limited_list(availability_details.get("builtInPlaybookSeeds"), limit=20),
                "experienceAssets": {
                    "policy": experience_assets.get("policy"),
                    "sources": _agent_limited_list(experience_assets.get("sources"), limit=12),
                    "externalReferences": _agent_limited_list(experience_assets.get("externalReferences"), limit=12),
                },
            }
        )
    return json.dumps(
        _agent_compact_dict(payload),
        ensure_ascii=False,
        indent=2,
    )


def _computer_use_execute_single_step(
    *,
    action: str,
    step: dict[str, Any],
    goal: str,
) -> dict[str, Any]:
    return _get_computer_use_runtime().execute_plan(
        **_computer_use_runtime_kwargs(goal),
        steps=[step],
        continue_on_error=False,
        max_steps=1,
    )


@tool
def computer_use_list_windows(title_filter: Optional[str] = None, limit: int = 20) -> str:
    """List current top-level Windows desktop windows via UI Automation.

    Use this before selecting a target application/window for structured desktop control.
    """
    try:
        result = _get_computer_use_runtime().list_windows(title_filter=title_filter, limit=max(1, min(limit, 50)))
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error listing windows: {e}"


@tool
def computer_use_observe(
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    include_screenshot: bool = True,
    depth_limit: int = 4,
    element_limit: int = 80,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Observe the current desktop/window and return a structured UI tree plus optional screenshot artifact.

    Prefer this tool before click/type actions so the agent can inspect available controls.
    """
    try:
        result = _get_computer_use_runtime().observe(
            **_computer_use_runtime_kwargs("observe_desktop"),
            window_title=window_title,
            window_handle=window_handle,
            include_screenshot=include_screenshot,
            depth_limit=max(1, min(depth_limit, 8)),
            element_limit=max(1, min(element_limit, 150)),
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_observation(
            raw_result=result,
            app_hint=None,
            resolved_app=None,
        )
    except Exception as e:
        return f"Error observing desktop: {e}"


@tool
def computer_use_find_element(
    name: Optional[str] = None,
    name_contains: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    class_name: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    limit: int = 10,
) -> str:
    """Find UI elements within a target window by name, automation_id, control type, or class name."""
    try:
        result = _get_computer_use_runtime().find_elements(
            name=name,
            name_contains=name_contains,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            window_title=window_title,
            window_handle=window_handle,
            limit=max(1, min(limit, 30)),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error finding element: {e}"


@tool
def computer_use_click(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    element_id: Optional[str] = None,
    name: Optional[str] = None,
    name_contains: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    class_name: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    double: bool = False,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Perform a real desktop click on a structured UI element resolved via Windows UIA."""
    target = {
        "element_id": element_id,
        "name": name,
        "name_contains": name_contains,
        "automation_id": automation_id,
        "control_type": control_type,
        "class_name": class_name,
        "window_title": window_title,
        "window_handle": window_handle,
        "double": double,
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="double_click" if double else "click",
        target=target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="double_click" if double else "click",
            summary=error_message or "Safety Guardian 已阻止桌面点击动作。",
            target_hint=automation_id or name or name_contains or element_id,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().click(
            **_computer_use_runtime_kwargs("computer_use_click"),
            **target,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="double_click" if double else "click",
            raw_result=raw_result,
            target_hint=automation_id or name or name_contains or element_id,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error clicking element: {e}"


@tool
def computer_use_type_text(
    text: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    element_id: Optional[str] = None,
    name: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    class_name: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    clear_first: bool = False,
    press_enter: bool = False,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Type text into a structured UI element using Windows UIA, with optional clear/enter behavior."""
    target = {
        "element_id": element_id,
        "name": name,
        "automation_id": automation_id,
        "control_type": control_type,
        "class_name": class_name,
        "window_title": window_title,
        "window_handle": window_handle,
        "clear_first": clear_first,
        "press_enter": press_enter,
        "text_preview": text[:80],
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target=target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="type_text",
            summary=error_message or "Safety Guardian 已阻止文本输入动作。",
            target_hint=automation_id or name or element_id,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().type_text(
            **_computer_use_runtime_kwargs("computer_use_type_text"),
            element_id=element_id,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
            window_title=window_title,
            window_handle=window_handle,
            text=text,
            clear_first=clear_first,
            press_enter=press_enter,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="type_text",
            raw_result=raw_result,
            target_hint=automation_id or name or element_id,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error typing text: {e}"


@tool
def computer_use_hotkey(
    sequence: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Send a desktop hotkey sequence using Windows UIA/keyboard injection, e.g. '^a' or '%{F4}'."""
    target = {
        "sequence": sequence,
        "window_title": window_title,
        "window_handle": window_handle,
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="hotkey",
        target=target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="hotkey",
            summary=error_message or "Safety Guardian 已阻止热键动作。",
            target_hint=sequence,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().hotkey(
            **_computer_use_runtime_kwargs("computer_use_hotkey"),
            sequence=sequence,
            window_title=window_title,
            window_handle=window_handle,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="hotkey",
            raw_result=raw_result,
            target_hint=sequence,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error sending hotkey: {e}"


@tool
def computer_use_scroll(
    amount: int,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    element_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Scroll a window or structured UI element using the mouse wheel. Positive goes up, negative goes down."""
    target = {
        "amount": amount,
        "element_id": element_id,
        "window_title": window_title,
        "window_handle": window_handle,
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="scroll",
        target=target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="scroll",
            summary=error_message or "Safety Guardian 已阻止滚动动作。",
            target_hint=element_id,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().scroll(
            **_computer_use_runtime_kwargs("computer_use_scroll"),
            amount=amount,
            element_id=element_id,
            window_title=window_title,
            window_handle=window_handle,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="scroll",
            raw_result=raw_result,
            target_hint=element_id,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error scrolling: {e}"


@tool
def computer_use_wait_for_element(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    name: Optional[str] = None,
    name_contains: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    class_name: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    timeout_ms: int = 10000,
    poll_ms: int = 300,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Wait until a structured UI element appears, then return the resolved target descriptor."""
    target = {
        "name": name,
        "name_contains": name_contains,
        "automation_id": automation_id,
        "control_type": control_type,
        "class_name": class_name,
        "window_title": window_title,
        "window_handle": window_handle,
        "timeout_ms": timeout_ms,
        "poll_ms": poll_ms,
    }
    try:
        raw_result = _get_computer_use_runtime().wait_for_element(
            **_computer_use_runtime_kwargs("computer_use_wait_for_element"),
            **target,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="wait_for_element",
            raw_result=raw_result,
            target_hint=automation_id or name or name_contains,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error waiting for element: {e}"


@tool
def computer_use_capture_screenshot(
    *,
    element_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Capture a desktop, window, or element screenshot and record it as a runtime artifact."""
    try:
        raw_result = _get_computer_use_runtime().capture_screenshot(
            **_computer_use_runtime_kwargs("computer_use_capture_screenshot"),
            element_id=element_id,
            window_title=window_title,
            window_handle=window_handle,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="capture_screenshot",
            raw_result=raw_result,
            target_hint=element_id,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error capturing screenshot: {e}"


@tool
def computer_use_open_app(
    *,
    app_id: Optional[str] = None,
    command: Optional[str] = None,
    window_title: Optional[str] = None,
    class_name: Optional[str] = None,
    wait_timeout_ms: int = 12000,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Open an application window for desktop automation. Prefer app_id so runtime can use app-specific profile."""
    try:
        raw_result = _get_computer_use_runtime().open_app(
            **_computer_use_runtime_kwargs(f"computer_use_open_app:{app_id or command or 'unknown'}"),
            app_id=app_id,
            command=command,
            window_title=window_title,
            class_name=class_name,
            wait_timeout_ms=max(2000, min(wait_timeout_ms, 30000)),
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="open_app",
            raw_result=raw_result,
            app_hint=app_id or command,
            target_hint=class_name,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error opening app: {e}"


@tool
def computer_use_focus_window(
    *,
    app_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    class_name: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Focus an existing application window before multi-step desktop actions."""
    try:
        raw_result = _get_computer_use_runtime().focus_window(
            **_computer_use_runtime_kwargs(f"computer_use_focus_window:{app_id or window_title or window_handle or 'unknown'}"),
            app_id=app_id,
            window_title=window_title,
            window_handle=window_handle,
            class_name=class_name,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="focus_window",
            raw_result=raw_result,
            app_hint=app_id,
            target_hint=class_name,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error focusing window: {e}"


@tool
def computer_use_find_and_type(
    *,
    text: str,
    app_id: Optional[str] = None,
    selector_key: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    clear_first: bool = False,
    press_enter: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """High-level desktop input action. Runtime will resolve the best selector from app profile or current window context."""
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target={
            "app_id": app_id,
            "selector_key": selector_key,
            "window_title": window_title,
            "window_handle": window_handle,
            "text_preview": text[:80],
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="find_and_type",
            summary=error_message or "Safety Guardian 已阻止 computer use 输入动作。",
            app_hint=app_id,
            target_hint=selector_key,
            resolved_app={"appId": app_id} if app_id else None,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().find_and_type(
            **_computer_use_runtime_kwargs("computer_use_find_and_type"),
            app_id=app_id,
            selector_key=selector_key,
            window_title=window_title,
            window_handle=window_handle,
            text=text,
            clear_first=clear_first,
            press_enter=press_enter,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="find_and_type",
            raw_result=raw_result,
            app_hint=app_id,
            target_hint=selector_key,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error typing via high-level computer use action: {e}"


@tool
def computer_use_scroll_list(
    *,
    amount: int,
    app_id: Optional[str] = None,
    selector_key: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """High-level list scroll action. Runtime resolves the list container and verifies scroll result."""
    allowed, error_message = _computer_use_action_guard(
        action_type="scroll",
        target={
            "app_id": app_id,
            "selector_key": selector_key,
            "window_title": window_title,
            "window_handle": window_handle,
            "amount": amount,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="scroll_list",
            summary=error_message or "Safety Guardian 已阻止 computer use 滚动动作。",
            app_hint=app_id,
            target_hint=selector_key,
            resolved_app={"appId": app_id} if app_id else None,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().scroll_list(
            **_computer_use_runtime_kwargs("computer_use_scroll_list"),
            app_id=app_id,
            selector_key=selector_key,
            window_title=window_title,
            window_handle=window_handle,
            amount=int(amount),
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="scroll_list",
            raw_result=raw_result,
            app_hint=app_id,
            target_hint=selector_key,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error scrolling list via computer use: {e}"


@tool
def computer_use_click_toolbar_action(
    *,
    action_name: str,
    app_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    class_name: Optional[str] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """High-level toolbar click action. Runtime uses app profile toolbar selectors and recovery strategy."""
    allowed, error_message = _computer_use_action_guard(
        action_type="click",
        target={
            "app_id": app_id,
            "action_name": action_name,
            "window_title": window_title,
            "window_handle": window_handle,
            "class_name": class_name,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _computer_use_guard_failure_response(
            action="click_toolbar_action",
            summary=error_message or "Safety Guardian 已阻止 computer use 工具栏动作。",
            app_hint=app_id,
            target_hint=action_name,
            resolved_app={"appId": app_id} if app_id else None,
            window_title=window_title,
        )
    try:
        raw_result = _get_computer_use_runtime().click_toolbar_action(
            **_computer_use_runtime_kwargs(f"computer_use_click_toolbar_action:{action_name}"),
            app_id=app_id,
            action_name=action_name,
            window_title=window_title,
            window_handle=window_handle,
            class_name=class_name,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        return _computer_use_compact_response(
            action="click_toolbar_action",
            raw_result=raw_result,
            app_hint=app_id,
            target_hint=action_name,
            resolved_app={"appId": app_id} if app_id else None,
            expected_window_title=window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
    except Exception as e:
        return f"Error clicking toolbar action via computer use: {e}"


@tool
def computer_use_execute_plan(
    steps_json: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    goal: Optional[str] = None,
    app_id: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    continue_on_error: bool = False,
    max_steps: int = 5,
) -> str:
    """Execute a short horizon desktop loop.

    Preferred mode:
    - Provide `goal`, let the planner run: 观察 -> 规划 2~5 步 -> 执行 -> 验证。

    Advanced mode:
    - Provide `steps_json` with a JSON array when the caller already knows the exact steps.

    Supported actions:
    observe / find / click / double_click / type_text / hotkey / scroll / wait / screenshot /
    open_app / focus_window / find_and_type / scroll_list / click_toolbar_action
    """
    effective_max_steps = max(1, min(max_steps, 8))

    if goal and goal.strip():
        try:
            runtime_kwargs = _computer_use_runtime_kwargs(goal.strip())
            planning = _get_computer_use_runtime().plan(
                **runtime_kwargs,
                app_id=app_id,
                window_title=window_title,
                window_handle=window_handle,
                max_steps=effective_max_steps,
                include_screenshot=False,
            )
            planned_steps = list(((planning.get("planner") or {}).get("steps")) or [])
            if not planned_steps:
                return "Error: planner 没有生成任何可执行步骤。"
            allowed, error_message = _guard_computer_use_steps(
                steps=planned_steps,
                tool_call_id=tool_call_id,
            )
            if not allowed:
                return error_message or "Safety Guardian 已阻止 planner 生成的桌面动作。"
            execution = _get_computer_use_runtime().execute_plan(
                **runtime_kwargs,
                steps=planned_steps,
                continue_on_error=continue_on_error,
                max_steps=effective_max_steps,
            )
            payload = _computer_use_attach_plan_contract_summary(
                payload={
                    **planning,
                    "execution": execution,
                },
                action="execute_plan",
                goal=goal.strip(),
            )
            return json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error planning/executing computer use goal: {e}"

    if not steps_json.strip():
        return "Error: 必须提供 goal 或 steps_json。"

    try:
        raw_steps = json.loads(steps_json)
    except Exception as e:
        return f"Error parsing plan JSON: {e}"

    if not isinstance(raw_steps, list):
        return "Error: steps_json 必须是 JSON 数组。"
    if not raw_steps:
        return "Error: 执行计划不能为空。"

    steps: list[dict] = []
    for raw_step in raw_steps[:effective_max_steps]:
        if not isinstance(raw_step, dict):
            return "Error: 计划中的每一步都必须是 JSON 对象。"
        step = dict(raw_step)
        if window_title is not None and "window_title" not in step:
            step["window_title"] = window_title
        if window_handle is not None and "window_handle" not in step:
            step["window_handle"] = window_handle
        steps.append(step)

    allowed, error_message = _guard_computer_use_steps(
        steps=steps,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return error_message or "Safety Guardian 已阻止 computer use 计划步骤。"

    try:
        result = _get_computer_use_runtime().execute_plan(
            **_computer_use_runtime_kwargs("computer_use_execute_plan"),
            steps=steps,
            continue_on_error=continue_on_error,
            max_steps=effective_max_steps,
        )
        payload = _computer_use_attach_plan_contract_summary(
            payload=dict(result or {}),
            action="execute_plan",
            goal=goal.strip() if goal else None,
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error executing computer use plan: {e}"


@tool
def computer_use_list_apps(
    app_query: Optional[str] = None,
    limit: int = 8,
    include_running: bool = True,
    force_refresh: bool = False,
    detail_level: str = "summary",
) -> str:
    """List desktop applications in a Supervisor-friendly way.

    Prefer this before launch/focus when you only know an approximate app name.
    """
    try:
        payload = _get_computer_use_runtime().list_apps(
            query=app_query,
            limit=max(1, min(limit, 20)),
            include_running=include_running,
            force_refresh=force_refresh,
        )
        normalized_detail = str(detail_level or "summary").strip().lower()
        apps = []
        for item in list(payload.get("apps") or [])[: max(1, min(limit, 20))]:
            if not isinstance(item, dict):
                continue
            windows = []
            for window in list(item.get("runningWindows") or [])[:2]:
                if not isinstance(window, dict):
                    continue
                windows.append(
                    _agent_compact_dict(
                        {
                            "handle": window.get("handle"),
                            "title": _agent_preview_text(window.get("title"), limit=180),
                            "className": window.get("className"),
                            "isVisible": window.get("isVisible"),
                        }
                    )
                )
            top_window = windows[0] if windows else {}
            app_payload = {
                "appId": item.get("appId"),
                "displayName": item.get("displayName"),
                "isRunning": item.get("isRunning"),
                "launchable": item.get("launchable"),
                "topWindowTitle": top_window.get("title"),
                "topWindowVisible": top_window.get("isVisible"),
                "aliases": list(item.get("aliases") or [])[:3],
            }
            if normalized_detail in {"detail", "diagnostic", "full"}:
                app_payload.update(
                    {
                        "profileBound": item.get("profileBound"),
                        "runningWindows": windows,
                        "aliases": list(item.get("aliases") or [])[:8],
                    }
                )
            apps.append(_agent_compact_dict(app_payload))
        return json.dumps(
            {
                "ok": True,
                "query": str(app_query or "").strip() or None,
                "apps": apps,
                "summary": payload.get("summary"),
                "platform": payload.get("platform"),
                "backend": payload.get("backend"),
                "detailTool": "computer_use_list_apps(detail_level='detail')",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Error listing desktop apps: {e}"


@tool
def computer_use_list_primitives(
    category: Optional[str] = None,
    detail_level: str = "summary",
) -> str:
    """List the canonical desktop primitives exposed to Supervisor and ComputerUseRuntime.

    Use this to understand the stable tool vocabulary before attempting exploratory GUI work.
    """
    try:
        normalized = str(category or "").strip().lower() or None
        return _computer_use_compact_primitive_catalog(category=normalized, detail_level=detail_level)
    except Exception as e:
        return f"Error listing desktop primitives: {e}"


@tool
def computer_use_desktop_capabilities(detail_level: str = "summary") -> str:
    """Return the current desktop driver/runtime capability summary in a compact format."""
    try:
        return _computer_use_compact_driver_capabilities(detail_level=detail_level)
    except Exception as e:
        return f"Error reading desktop capabilities: {e}"


@tool
def computer_use_lookup_muscle_memory(
    goal: str,
    *,
    app: Optional[str] = None,
    variables_json: Optional[str] = None,
    limit: int = 5,
) -> str:
    """Look up reusable desktop muscle memory before entering Computer Use learning mode.

    This is a read-only route advisor. It checks approved templates, candidate templates, and draft traces to decide
    whether the task should go to reuse_mode, hybrid_mode, or learn_mode.
    """
    normalized_goal = str(goal or "").strip()
    if not normalized_goal:
        return "Error: goal 不能为空。"
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    try:
        variables = _computer_use_parse_variables_json(variables_json)
        runtime_context = get_runtime_context()
        route = _get_rpa_runtime().recommend_execution_route(
            goal=normalized_goal,
            app_id=(resolved_app or {}).get("appId") or app_query,
            variables=variables,
            session_id=runtime_context.get("session_id"),
            run_id=runtime_context.get("run_id"),
            limit=max(1, min(limit, 10)),
            allow_materialization=False,
        )
        return _computer_use_compact_memory_lookup(
            goal=normalized_goal,
            app_hint=app_query,
            resolved_app=resolved_app,
            route=route,
        )
    except Exception as e:
        return f"Error looking up desktop muscle memory: {e}"


@tool
def computer_use_list_muscle_memories(
    app: Optional[str] = None,
    *,
    status: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List existing desktop muscle memories/templates in a human-readable, Supervisor-safe format."""
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    try:
        templates = _get_rpa_runtime().list_templates(
            limit=max(1, min(limit, 50)),
            app_id=(resolved_app or {}).get("appId") or app_query,
            status=status,
        )
        return _computer_use_compact_memory_list(
            templates=list(templates or []),
            app_hint=app_query,
            resolved_app=resolved_app,
            status=status,
        )
    except Exception as e:
        return f"Error listing desktop muscle memories: {e}"


@tool
def computer_use_resolve_execution_route(
    goal: str,
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    variables_json: Optional[str] = None,
    limit: int = 5,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command | str:
    """Resolve whether the desktop task should reuse muscle memory, run hybrid, or enter learning mode.

    This is the preferred Wave 6 entrypoint for Supervisor before invoking any concrete desktop primitive.
    """
    normalized_goal = str(goal or "").strip()
    if not normalized_goal:
        return "Error: goal 不能为空。"
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    try:
        variables = _computer_use_parse_variables_json(variables_json)
        payload, desktop_route = _computer_use_build_desktop_route(
            goal=normalized_goal,
            app_query=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            variables=variables,
            state=state,
            limit=limit,
        )
        payload_str = json.dumps(payload, ensure_ascii=False, indent=2)
        if not isinstance(state, dict):
            return payload_str
        updated_route_context = dict(state.get("current_route_context") or {})
        updated_route_context["desktopRoute"] = desktop_route
        return Command(
            update={
                "messages": [ToolMessage(content=payload_str, tool_call_id=tool_call_id)],
                "current_route_context": updated_route_context,
            }
        )
    except Exception as e:
        return f"Error resolving desktop execution route: {e}"


@tool
def computer_use_execute_task(
    goal: str = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    successCriteria: Optional[str] = None,
    variablesJson: Optional[str] = None,
    maxSteps: int = 5,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Execute a route-approved desktop task through the unified task-level broker and return a compact verification summary."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    route_goal = str(goal or "").strip()
    resolved_app = _computer_use_resolve_app(app_query)
    try:
        variables = _computer_use_parse_variables_json(variablesJson)
    except Exception as e:
        return f"Error parsing variablesJson: {e}"

    desktop_route: dict[str, Any] = {}
    gate_allowed, gate_failure, gated_desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_execute_task",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if gate_allowed:
        desktop_route = dict(gated_desktop_route or {})
    elif route_goal:
        try:
            _, desktop_route = _computer_use_build_desktop_route(
                goal=route_goal,
                app_query=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                variables=variables,
                state=state,
            )
        except Exception as e:
            return f"Error resolving desktop execution route inside computer_use_execute_task: {e}"
    else:
        return gate_failure or "Error: 桌面执行路由校验失败。"

    if not desktop_route and route_goal:
        try:
            _, desktop_route = _computer_use_build_desktop_route(
                goal=route_goal,
                app_query=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                variables=variables,
                state=state,
            )
        except Exception as e:
            return f"Error resolving desktop execution route inside computer_use_execute_task: {e}"

    mismatch_reason = _desktop_route_task_mismatch_reason(
        desktop_route=desktop_route,
        goal=route_goal,
        target=target_hint,
    )
    if mismatch_reason:
        return _desktop_route_gate_failure_response(
            gate_error_code="ROUTE_MISMATCH",
            summary=mismatch_reason,
            desktop_route=desktop_route,
            recommended_next_tool="computer_use_resolve_execution_route",
        )

    effective_goal = route_goal or str(desktop_route.get("goal") or "").strip()
    if not effective_goal:
        return "Error: goal 不能为空，且当前桌面路由也未绑定 goal。"

    effective_target = target_hint or str(desktop_route.get("target") or "").strip() or None
    execution_ready_mode = str(desktop_route.get("executionReadyMode") or "").strip() or "learn_mode"
    effective_app_id = (
        str((resolved_app or {}).get("appId") or "").strip()
        or str(desktop_route.get("appId") or "").strip()
        or None
    )
    effective_max_steps = max(1, min(int(maxSteps or 5), 8))
    success_criteria = str(successCriteria or "").strip() or None

    try:
        runtime_context = get_runtime_context()
        if execution_ready_mode in {"reuse_mode", "hybrid_mode"}:
            draft_id = _desktop_route_executable_draft_id(desktop_route)
            if not draft_id:
                return _desktop_route_gate_failure_response(
                    gate_error_code="STALE_ROUTE_CONTEXT",
                    summary="当前桌面路由缺少可执行的 RPA 骨架，请重新调用 computer_use_resolve_execution_route。",
                    desktop_route=desktop_route,
                    recommended_next_tool="computer_use_resolve_execution_route",
                )
            raw_result = _get_rpa_runtime().run_draft(
                script_id=draft_id,
                variables=variables,
                session_id=runtime_context.get("session_id"),
                run_id=runtime_context.get("run_id"),
                user_id=runtime_context.get("user_id") or "anonymous",
                project_id=runtime_context.get("project_id"),
                workspace_id=runtime_context.get("workspace_id"),
                workspace_path=runtime_context.get("workspace_path"),
                trigger_source="computer_use_execute_task",
            )
            payload = _computer_use_execute_task_compact_rpa_result(
                raw_result=dict(raw_result or {}),
                execution_ready_mode=execution_ready_mode,
                goal=effective_goal,
                app_hint=app_query,
                target_hint=effective_target,
                resolved_app=resolved_app,
                success_criteria=success_criteria,
            )
        else:
            planner_goal = effective_goal
            if effective_target:
                planner_goal = f"{planner_goal}\nTarget: {effective_target}"
            if success_criteria:
                planner_goal = f"{planner_goal}\nSuccess criteria: {success_criteria}"
            task_loop = _get_computer_use_runtime().prepare_task_loop(
                goal=planner_goal,
                app_id=effective_app_id or "browser_checkout",
            )
            computer_runtime = _get_computer_use_runtime()
            playbook_registry = getattr(computer_runtime, "playbook_executor_registry", None)
            if playbook_registry is not None and playbook_registry.can_handle(task_loop) is True:
                runtime_kwargs = _computer_use_runtime_kwargs(planner_goal)
                runtime_kwargs.pop("goal", None)
                raw_result = computer_runtime.execute_selected_playbook(
                    goal=planner_goal,
                    task_loop=task_loop,
                    allow_real_click=True,
                    playbook_inputs=variables,
                    **runtime_kwargs,
                )
                payload = {
                    "mode": "runtime_native_playbook",
                    "executionReadyMode": execution_ready_mode,
                    "goal": effective_goal,
                    "app": {
                        "requested": app_query,
                        "resolved": resolved_app,
                    },
                    "target": effective_target,
                    "successCriteria": success_criteria,
                    "result": raw_result,
                    "selectedPlaybook": (task_loop.get("domain") or {}).get("selectedPlaybook"),
                    "selectedPlaybookExecutor": raw_result.get("selectedPlaybookExecutor"),
                    "factResolution": {
                        "status": "resolved" if list(task_loop.get("factEvidence") or []) else task_loop.get("status"),
                        "evidence": list(task_loop.get("factEvidence") or []),
                    },
                    "laneDecision": raw_result.get("laneDecision") or task_loop.get("laneDecision"),
                    "verification": raw_result.get("verification") or task_loop.get("verifier"),
                    "recommendedNextAction": (
                        "ask_user"
                        if str(raw_result.get("status") or "").startswith("needs_human")
                        else "none"
                    ),
                    "humanInputRequest": raw_result.get("humanInputRequest"),
                    "resourceLease": raw_result.get("resourceLease"),
                }
                return _desktop_route_merge_into_response(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    desktop_route=desktop_route,
                    route_gate_applied=isinstance(state, dict),
                )
            planning = computer_runtime.plan(
                **_computer_use_runtime_kwargs(planner_goal),
                app_id=effective_app_id,
                max_steps=effective_max_steps,
                include_screenshot=False,
            )
            planned_steps = list(((planning.get("planner") or {}).get("steps")) or [])
            if not planned_steps:
                return "Error: ComputerUseRuntime planner 没有生成任何可执行步骤。"
            allowed, error_message = _guard_computer_use_steps(
                steps=planned_steps,
                tool_call_id=tool_call_id,
            )
            if not allowed:
                return error_message or "Safety Guardian 已阻止 planner 生成的桌面动作。"
            execution = computer_runtime.execute_plan(
                **_computer_use_runtime_kwargs(planner_goal),
                steps=planned_steps,
                continue_on_error=False,
                max_steps=effective_max_steps,
            )
            execution_payload = _computer_use_attach_plan_contract_summary(
                payload={
                    **planning,
                    "execution": execution,
                },
                action="execute_task",
                goal=effective_goal,
            )
            payload = _computer_use_execute_task_compact_computer_use_result(
                payload=execution_payload,
                execution_ready_mode=execution_ready_mode,
                goal=effective_goal,
                app_hint=app_query,
                target_hint=effective_target,
                resolved_app=resolved_app,
                success_criteria=success_criteria,
            )

        return _desktop_route_merge_into_response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error executing desktop task via broker: {e}"


@tool
def computer_use_launch_app(
    app: str,
    *,
    target: Optional[str] = None,
    wait_timeout_ms: int = 12000,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Launch a desktop app with minimal parameters.

    Provide a human app name or alias. The tool resolves the best app match, opens it, and returns
    verification/blocking/evidence in a compact format.
    """
    app_query = str(app or "").strip()
    if not app_query:
        return "Error: app 不能为空。"
    resolved_app = _computer_use_resolve_app(app_query)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_launch_app",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    launch_override = _computer_use_launch_target_override(
        app_query=app_query,
        resolved_app=resolved_app,
        target=target,
    )
    expected_window_title = (
        str(launch_override.get("expected_window_title") or "").strip()
        or _computer_use_effective_window_title(None, resolved_app)
    )
    try:
        raw_result = _get_computer_use_runtime().open_app(
            **_computer_use_runtime_kwargs(f"launch_app:{app_query}"),
            app_id=(resolved_app or {}).get("appId"),
            app_name=app_query,
            command=str(launch_override.get("command") or "").strip() or None,
            launch_target_path=str(launch_override.get("resolved_target_path") or "").strip() or None,
            window_title=expected_window_title,
            strict_window_title_match=bool(launch_override.get("strict_expected_window_title")),
            wait_timeout_ms=max(2000, min(wait_timeout_ms, 30000)),
            require_visual_guard=False,
            prefer_fast_path=True,
            post_action_settle_timeout_ms=650,
            post_action_settle_poll_ms=160,
            post_action_stable_rounds=1,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        resolved_app = _computer_use_update_resolved_app_from_raw_result(
            app_query=app_query,
            resolved_app=resolved_app,
            raw_result=raw_result,
        )
        response = _computer_use_compact_response(
            action="launch_app",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target,
            resolved_app=resolved_app,
            expected_window_title=expected_window_title,
            strict_expected_window_title=bool(launch_override.get("strict_expected_window_title")),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        fallback_target_path = str(launch_override.get("resolved_target_path") or "").strip() or None
        resolved_app_id = str((resolved_app or {}).get("appId") or "").strip().lower()
        if resolved_app_id == "explorer" and fallback_target_path:
            try:
                recovered_result = _get_computer_use_runtime().focus_window(
                    **_computer_use_runtime_kwargs(f"launch_app_recover:{app_query}"),
                    app_id=(resolved_app or {}).get("appId"),
                    target_path=fallback_target_path,
                    window_title=expected_window_title,
                    require_visual_guard=False,
                    prefer_fast_path=True,
                    post_action_settle_timeout_ms=220,
                    post_action_settle_poll_ms=120,
                    post_action_stable_rounds=1,
                    observe_notifications=observe_notifications,
                    observe_sound=observe_sound,
                    environment_probe_mode=environment_probe_mode,
                )
                resolved_app = _computer_use_update_resolved_app_from_raw_result(
                    app_query=app_query,
                    resolved_app=resolved_app,
                    raw_result=recovered_result,
                )
                response = _computer_use_compact_response(
                    action="launch_app",
                    raw_result=recovered_result,
                    app_hint=app_query,
                    target_hint=target,
                    resolved_app=resolved_app,
                    expected_window_title=expected_window_title,
                    strict_expected_window_title=bool(launch_override.get("strict_expected_window_title")),
                )
                return _desktop_route_merge_into_response(
                    response,
                    desktop_route=desktop_route,
                    route_gate_applied=isinstance(state, dict),
                )
            except Exception:
                pass
        return f"Error launching desktop app: {e}"


@tool
def computer_use_ensure_window(
    app: Optional[str] = None,
    *,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    class_name: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Ensure a desktop window is bound and focused before the next action.

    Use this to rebind/focus the correct application window instead of continuing with a stale foreground context.
    """
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_ensure_window",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    launch_override = _computer_use_launch_target_override(
        app_query=app_query,
        resolved_app=resolved_app,
        target=target,
    )
    effective_window_title = (
        str(launch_override.get("expected_window_title") or "").strip()
        or _computer_use_effective_window_title(window_title, resolved_app)
    )
    try:
        raw_result = _get_computer_use_runtime().focus_window(
            **_computer_use_runtime_kwargs(f"ensure_window:{app_query or effective_window_title or class_name or 'desktop'}"),
            app_id=(resolved_app or {}).get("appId"),
            target_path=str(launch_override.get("resolved_target_path") or "").strip() or None,
            window_title=effective_window_title,
            class_name=class_name,
            require_visual_guard=False,
            prefer_fast_path=True,
            post_action_settle_timeout_ms=220,
            post_action_settle_poll_ms=120,
            post_action_stable_rounds=1,
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        resolved_app = _computer_use_update_resolved_app_from_raw_result(
            app_query=app_query,
            resolved_app=resolved_app,
            raw_result=raw_result,
        )
        response = _computer_use_compact_response(
            action="ensure_window",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=str(target or "").strip() or effective_window_title or class_name,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(
                str(launch_override.get("strict_expected_window_title") or "").strip()
                or str(window_title or "").strip()
            ),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error ensuring desktop window: {e}"


@tool
def computer_use_observe_scene(
    app: Optional[str] = None,
    *,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    include_screenshot: bool = True,
    depth_limit: int = 4,
    element_limit: int = 60,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
) -> str:
    """Observe the current desktop scene in a compact, Supervisor-friendly format."""
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    launch_override = _computer_use_launch_target_override(
        app_query=app_query,
        resolved_app=resolved_app,
        target=target,
    )
    inferred_title = (
        str(launch_override.get("expected_window_title") or "").strip()
        or _computer_use_effective_window_title(window_title, resolved_app)
    )
    try:
        raw_result = _get_computer_use_runtime().observe(
            **_computer_use_runtime_kwargs(f"observe_scene:{app_query or inferred_title or 'desktop'}"),
            app_id=(resolved_app or {}).get("appId"),
            window_title=inferred_title,
            include_screenshot=include_screenshot,
            depth_limit=max(1, min(depth_limit, 8)),
            element_limit=max(1, min(element_limit, 120)),
            observe_notifications=observe_notifications,
            observe_sound=observe_sound,
            environment_probe_mode=environment_probe_mode,
        )
        resolved_app = _computer_use_update_resolved_app_from_raw_result(
            app_query=app_query,
            resolved_app=resolved_app,
            raw_result=raw_result,
        )
        return _computer_use_compact_observation(
            raw_result=raw_result,
            app_hint=app_query,
            resolved_app=resolved_app,
        )
    except Exception as e:
        return f"Error observing desktop scene: {e}"


@tool
def computer_use_click_target(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    target_text: Optional[str] = None,
    double: bool = False,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Click a semantic desktop target with built-in verification and blocking.

    Prefer short semantic hints such as 'primary_input', 'address_bar', 'confirm_action', or a visible element name.
    The runtime will decide whether to use structured lookup, anchor targeting, or a guarded fallback path.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_click_target",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="click_target_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    guard_target = {
        "app": app_query,
        "resolved_app_id": (resolved_app or {}).get("appId"),
        "target": target_hint,
        "window_title": effective_window_title,
        "window_handle": window_handle,
        "target_text": target_text,
        "double": bool(double),
    }
    point_hint = _computer_use_parse_point_tag(target_hint) or _computer_use_parse_point_tag(target_text)
    if point_hint:
        guard_target["point"] = list(point_hint)
    allowed, error_message = _computer_use_action_guard(
        action_type="double_click" if double else "click",
        target=guard_target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="double_click" if double else "click",
                summary=error_message or "Safety Guardian 已阻止桌面点击动作。",
                app_hint=app_query,
                target_hint=target_hint or target_text,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "double_click" if double else "click",
    }
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if point_hint:
        step["point"] = list(point_hint)
        step["coordinate_source"] = "vision_point_tag"
    elif target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    if target_text:
        step["target_text"] = target_text
    step["require_visual_guard"] = False
    step["prefer_fast_path"] = True
    step["post_action_settle_timeout_ms"] = 220
    step["post_action_settle_poll_ms"] = 120
    step["post_action_stable_rounds"] = 1
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action=step["action"],
            step=step,
            goal=f"{step['action']}:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action=step["action"],
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error clicking desktop target: {e}"


@tool
def computer_use_input_text(
    text: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    clear_first: bool = True,
    submit: bool = False,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Input text into a desktop target using the simplest possible interface.

    If `target` is provided, it is treated as a semantic target hint. If `target` is omitted, the tool falls back to
    window-level typing and blocks when editable focus cannot be confirmed.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_input_text",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="input_text_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    guard_target = {
        "app": app_query,
        "resolved_app_id": (resolved_app or {}).get("appId"),
        "target": target_hint,
        "window_title": effective_window_title,
        "window_handle": window_handle,
        "text_preview": text[:80],
        "clear_first": bool(clear_first),
        "submit": bool(submit),
    }
    point_hint = _computer_use_parse_point_tag(target_hint)
    if point_hint:
        guard_target["point"] = list(point_hint)
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target=guard_target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="input_text",
                summary=error_message or "Safety Guardian 已阻止桌面输入动作。",
                app_hint=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "type_text",
        "text": text,
        "clear_first": bool(clear_first),
        "press_enter": bool(submit),
    }
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if point_hint:
        step["point"] = list(point_hint)
        step["coordinate_source"] = "vision_point_tag"
    elif target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    else:
        step["window_typing"] = True
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="type_text",
            step=step,
            goal=f"input_text:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="input_text",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error inputting text on desktop: {e}"


@tool
def computer_use_paste_text(
    text: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    clear_first: bool = False,
    submit: bool = False,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Paste text via clipboard-first desktop input, with built-in focus confirmation and verification."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_paste_text",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="paste_text_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    guard_target = {
        "app": app_query,
        "resolved_app_id": (resolved_app or {}).get("appId"),
        "target": target_hint,
        "window_title": effective_window_title,
        "window_handle": window_handle,
        "text_preview": text[:80],
        "mode": "paste_text",
    }
    point_hint = _computer_use_parse_point_tag(target_hint)
    if point_hint:
        guard_target["point"] = list(point_hint)
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target=guard_target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="paste_text",
                summary=error_message or "Safety Guardian 已阻止文本粘贴动作。",
                app_hint=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "type_text",
        "text": text,
        "clear_first": bool(clear_first),
        "press_enter": bool(submit),
        "window_typing": True,
        "prefer_sendinput_text": True,
    }
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if point_hint:
        step["point"] = list(point_hint)
        step["coordinate_source"] = "vision_point_tag"
    elif target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="type_text",
            step=step,
            goal=f"paste_text:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="paste_text",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error pasting text on desktop: {e}"


@tool
def computer_use_paste_files(
    paths_json: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    target_path: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    text: Optional[str] = None,
    submit: bool = False,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Paste files into a desktop target via clipboard file payload, optionally with accompanying text."""
    try:
        raw_paths = json.loads(paths_json)
    except Exception as e:
        return f"Error parsing paths_json: {e}"
    if isinstance(raw_paths, str):
        file_paths = [raw_paths]
    elif isinstance(raw_paths, list):
        file_paths = [str(item) for item in raw_paths if str(item).strip()]
    else:
        return "Error: paths_json 必须是 JSON 数组或字符串。"
    if not file_paths:
        return "Error: 至少需要一个文件路径。"
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_paste_files",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    target_override = _computer_use_launch_target_override(
        app_query=app_query,
        resolved_app=resolved_app,
        target=target_path,
    )
    effective_window_title = (
        str(window_title or "").strip()
        or str(target_override.get("expected_window_title") or "").strip()
        or _computer_use_effective_window_title(None, resolved_app)
    )
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="paste_files_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
        target_path=str(target_override.get("resolved_target_path") or "").strip() or None,
    )
    if prebind_error:
        return prebind_error
    guard_target = {
        "app": app_query,
        "resolved_app_id": (resolved_app or {}).get("appId"),
        "target": target_hint,
        "target_path": str(target_override.get("resolved_target_path") or target_path or "").strip() or None,
        "window_title": effective_window_title,
        "window_handle": window_handle,
        "file_count": len(file_paths),
        "text_preview": str(text or "")[:80] or None,
        "mode": "paste_files",
    }
    allowed, error_message = _computer_use_action_guard(
        action_type="type_text",
        target=guard_target,
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="paste_files",
                summary=error_message or "Safety Guardian 已阻止文件粘贴动作。",
                app_hint=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "type_text",
        "text": str(text or ""),
        "file_paths": file_paths,
        "press_enter": bool(submit),
        "window_typing": True,
        "prefer_sendinput_text": True,
    }
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if target_override.get("resolved_target_path"):
        step["target_path"] = str(target_override.get("resolved_target_path"))
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    if str((resolved_app or {}).get("appId") or "").strip().lower() == "explorer" and target_hint == "content_receiver":
        step["file_paste_strategy"] = "sendinput"
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="type_text",
            step=step,
            goal=f"paste_files:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="paste_files",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error pasting files on desktop: {e}"


@tool
def computer_use_right_click_target(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    target_text: Optional[str] = None,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Right-click a desktop target with the same guarded semantics as click_target."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_right_click_target",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="right_click_target_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    allowed, error_message = _computer_use_action_guard(
        action_type="click",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "target": target_hint,
            "window_title": effective_window_title,
            "window_handle": window_handle,
            "target_text": target_text,
            "button": "right",
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="right_click",
                summary=error_message or "Safety Guardian 已阻止桌面右键动作。",
                app_hint=app_query,
                target_hint=target_hint or target_text,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {"action": "right_click"}
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    if target_text:
        step["target_text"] = target_text
    step["require_visual_guard"] = False
    step["prefer_fast_path"] = True
    step["post_action_settle_timeout_ms"] = 220
    step["post_action_settle_poll_ms"] = 120
    step["post_action_stable_rounds"] = 1
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="right_click",
            step=step,
            goal=f"right_click:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="right_click",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error right-clicking desktop target: {e}"


@tool
def computer_use_hover_target(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    window_handle: Optional[int] = None,
    target_text: Optional[str] = None,
    visual_locator: Optional[str] = None,
    visual_locator_scope: Optional[str] = None,
    visual_locator_scope_padding: Optional[list[int]] = None,
    visual_locator_scope_seed_strategy: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator: Optional[str] = None,
    post_action_visual_locator_confidence: Optional[float] = None,
    post_action_visual_locator_timeout_ms: int = 2500,
    post_action_visual_locator_read_text: bool = False,
    post_action_expect_text: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Hover over a desktop target while keeping verification, evidence, and blocking semantics."""
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_hover_target",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    resolved_app, effective_window_title, window_handle, prebind_error = _computer_use_prebind_window(
        action_name="hover_target_prebind",
        app_query=app_query,
        resolved_app=resolved_app,
        window_title=effective_window_title,
        window_handle=window_handle,
    )
    if prebind_error:
        return prebind_error
    allowed, error_message = _computer_use_action_guard(
        action_type="click",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "target": target_hint,
            "window_title": effective_window_title,
            "window_handle": window_handle,
            "target_text": target_text,
            "gesture": "hover",
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="hover",
                summary=error_message or "Safety Guardian 已阻止桌面悬停动作。",
                app_hint=app_query,
                target_hint=target_hint or target_text,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {"action": "hover"}
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if window_handle not in (None, ""):
        step["window_handle"] = int(window_handle)
    if target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
            step["profile_action"] = target_hint
        else:
            step["name"] = target_hint
    if target_text:
        step["target_text"] = target_text
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=visual_locator,
        visual_locator_scope=visual_locator_scope,
        visual_locator_scope_padding=visual_locator_scope_padding,
        visual_locator_scope_seed_strategy=visual_locator_scope_seed_strategy,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
    )
    _computer_use_apply_post_action_visual_check_step(
        step,
        post_action_visual_locator=post_action_visual_locator,
        post_action_visual_locator_confidence=post_action_visual_locator_confidence,
        post_action_visual_locator_timeout_ms=post_action_visual_locator_timeout_ms,
        post_action_visual_locator_read_text=post_action_visual_locator_read_text,
        post_action_expect_text=post_action_expect_text,
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="hover",
            step=step,
            goal=f"hover:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="hover",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint or target_text,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error hovering desktop target: {e}"


@tool
def computer_use_send_hotkey(
    sequence: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    window_title: Optional[str] = None,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Send a desktop hotkey with compact verification and evidence output."""
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_send_hotkey",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    allowed, error_message = _computer_use_action_guard(
        action_type="hotkey",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "window_title": effective_window_title,
            "sequence": sequence,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="send_hotkey",
                summary=error_message or "Safety Guardian 已阻止桌面热键动作。",
                app_hint=app_query,
                target_hint=sequence,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "hotkey",
        "sequence": sequence,
    }
    if effective_window_title:
        step["window_title"] = effective_window_title
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="hotkey",
            step=step,
            goal=f"hotkey:{app_query or effective_window_title or sequence}",
        )
        response = _computer_use_compact_response(
            action="send_hotkey",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=sequence,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error sending desktop hotkey: {e}"


@tool
def computer_use_scroll_view(
    amount: int,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    target: Optional[str] = None,
    window_title: Optional[str] = None,
    by_page: bool = False,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Scroll a desktop view with built-in verification and evidence.

    Use wheel-style scroll by default. Set `by_page=true` to use page up/down semantics.
    """
    app_query = str(app or "").strip() or None
    target_hint = str(target or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_scroll_view",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    action_name = "page_scroll" if by_page else "scroll"
    allowed, error_message = _computer_use_action_guard(
        action_type="scroll",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "target": target_hint,
            "window_title": effective_window_title,
            "amount": amount,
            "mode": action_name,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action=action_name,
                summary=error_message or "Safety Guardian 已阻止桌面滚动动作。",
                app_hint=app_query,
                target_hint=target_hint,
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": action_name,
    }
    if by_page:
        step["direction"] = "down" if int(amount) < 0 else "up"
        step["count"] = max(1, abs(int(amount)))
    else:
        step["amount"] = int(amount)
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    if effective_window_title:
        step["window_title"] = effective_window_title
    if target_hint:
        if (resolved_app or {}).get("appId"):
            step["selector_key"] = target_hint
        else:
            step["name"] = target_hint
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action=action_name,
            step=step,
            goal=f"{action_name}:{app_query or effective_window_title or target_hint or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action=action_name,
            raw_result=raw_result,
            app_hint=app_query,
            target_hint=target_hint,
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error scrolling desktop view: {e}"


@tool
def computer_use_drag_pointer(
    start_point_json: str = "",
    end_point_json: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    app: Optional[str] = None,
    window_title: Optional[str] = None,
    steps: int = 12,
    start_visual_locator: Optional[str] = None,
    end_visual_locator: Optional[str] = None,
    visual_locator_confidence: Optional[float] = None,
    visual_locator_timeout_ms: int = 2500,
    observe_notifications: bool = False,
    observe_sound: bool = False,
    environment_probe_mode: Optional[str] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Drag from one point to another with built-in verification and blocking."""
    start_point = None
    end_point = None
    if str(start_point_json or "").strip():
        try:
            start_point = json.loads(start_point_json)
        except Exception as e:
            return f"Error parsing start_point_json: {e}"
    if str(end_point_json or "").strip():
        try:
            end_point = json.loads(end_point_json)
        except Exception as e:
            return f"Error parsing end_point_json: {e}"
    if not (
        (isinstance(start_point, list) and len(start_point) == 2 and isinstance(end_point, list) and len(end_point) == 2)
        or (str(start_visual_locator or "").strip() and str(end_visual_locator or "").strip())
    ):
        return "Error: drag_pointer 需要提供坐标起终点，或同时提供 start_visual_locator 和 end_visual_locator。"
    app_query = str(app or "").strip() or None
    resolved_app = _computer_use_resolve_app(app_query)
    effective_window_title = _computer_use_effective_window_title(window_title, resolved_app)
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="computer_use_drag_pointer",
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    allowed, error_message = _computer_use_action_guard(
        action_type="drag",
        target={
            "app": app_query,
            "resolved_app_id": (resolved_app or {}).get("appId"),
            "window_title": effective_window_title,
            "start_point": start_point,
            "end_point": end_point,
            "start_visual_locator": start_visual_locator,
            "end_visual_locator": end_visual_locator,
            "steps": steps,
        },
        tool_call_id=tool_call_id,
    )
    if not allowed:
        return _desktop_route_merge_into_response(
            _computer_use_guard_failure_response(
                action="drag_pointer",
                summary=error_message or "Safety Guardian 已阻止拖拽动作。",
                app_hint=app_query,
                target_hint="drag",
                resolved_app=resolved_app,
                window_title=effective_window_title,
            ),
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    step: dict[str, Any] = {
        "action": "drag",
        "drag_steps": max(2, int(steps)),
    }
    if isinstance(start_point, list) and len(start_point) == 2:
        step["start_point"] = [int(start_point[0]), int(start_point[1])]
    if isinstance(end_point, list) and len(end_point) == 2:
        step["end_point"] = [int(end_point[0]), int(end_point[1])]
    if effective_window_title:
        step["window_title"] = effective_window_title
    if (resolved_app or {}).get("appId"):
        step["app_id"] = (resolved_app or {}).get("appId")
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=start_visual_locator,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
        prefix="start_",
    )
    _computer_use_apply_visual_locator_step(
        step,
        visual_locator=end_visual_locator,
        visual_locator_confidence=visual_locator_confidence,
        visual_locator_timeout_ms=visual_locator_timeout_ms,
        prefix="end_",
    )
    _computer_use_apply_environment_probe_step(
        step,
        observe_notifications=observe_notifications,
        observe_sound=observe_sound,
        environment_probe_mode=environment_probe_mode,
    )
    try:
        raw_result = _computer_use_execute_single_step(
            action="drag",
            step=step,
            goal=f"drag:{app_query or effective_window_title or 'desktop'}",
        )
        response = _computer_use_compact_response(
            action="drag_pointer",
            raw_result=raw_result,
            app_hint=app_query,
            target_hint="drag",
            resolved_app=resolved_app,
            expected_window_title=effective_window_title,
            strict_expected_window_title=bool(str(window_title or "").strip()),
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error dragging pointer: {e}"

