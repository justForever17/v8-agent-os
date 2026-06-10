from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from langchain_core.messages import HumanMessage

from core.native_tool_governance import _enforce_safety_decision
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian


_DESKTOP_ROUTE_SOURCE = "computer_use_resolve_execution_route"
_DESKTOP_ROUTE_COMPUTER_USE_MUTATING_TOOLS = {
    "computer_use_launch_app",
    "computer_use_ensure_window",
    "computer_use_click_target",
    "computer_use_input_text",
    "computer_use_paste_text",
    "computer_use_paste_files",
    "computer_use_right_click_target",
    "computer_use_hover_target",
    "computer_use_send_hotkey",
    "computer_use_scroll_view",
    "computer_use_drag_pointer",
}
_DESKTOP_ROUTE_RPA_MUTATING_TOOLS = {
    "rpa_run_draft",
    "rpa_run_existing_flow",
}


def _message_text_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _is_supervisor_delegated_task_message(message: Any) -> bool:
    if not isinstance(message, HumanMessage):
        return False
    normalized = _message_text_content(message).strip()
    return normalized.startswith("[Supervisor Delegated Task")


def _desktop_route_message_fingerprint(message: Any, index: int) -> str:
    explicit_id = str(getattr(message, "id", "") or "").strip()
    if explicit_id:
        return explicit_id
    digest = hashlib.md5(f"{index}:{_message_text_content(message)}".encode("utf-8")).hexdigest()[:12]
    return f"human:{index}:{digest}"


def _desktop_route_latest_bound_human_message(state: dict[str, Any] | None) -> tuple[str | None, Any | None]:
    messages = list((state or {}).get("messages") or [])
    fallback: tuple[str | None, Any | None] = (None, None)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, HumanMessage):
            continue
        fingerprint = _desktop_route_message_fingerprint(message, index)
        if fallback == (None, None):
            fallback = (fingerprint, message)
        if _is_supervisor_delegated_task_message(message):
            continue
        return fingerprint, message
    return fallback


def _desktop_route_normalize_token(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _desktop_route_compact_metadata(
    desktop_route: dict[str, Any] | None,
    *,
    route_gate_applied: bool,
    runtime_governed: bool = True,
    gate_error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "routeGateApplied": bool(route_gate_applied),
        "desktopRouteMode": str((desktop_route or {}).get("recommendedMode") or "").strip() or None,
        "executionReadyMode": str((desktop_route or {}).get("executionReadyMode") or "").strip() or None,
        "runtimeGoverned": bool(runtime_governed),
        "gateErrorCode": str(gate_error_code or "").strip() or None,
    }


def _desktop_route_merge_into_response(
    response: str,
    *,
    desktop_route: dict[str, Any] | None,
    route_gate_applied: bool,
    runtime_governed: bool = True,
    gate_error_code: str | None = None,
) -> str:
    try:
        payload = json.loads(response)
    except Exception:
        return response
    if not isinstance(payload, dict):
        return response
    payload.update(
        _desktop_route_compact_metadata(
            desktop_route,
            route_gate_applied=route_gate_applied,
            runtime_governed=runtime_governed,
            gate_error_code=gate_error_code,
        )
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _desktop_route_gate_failure_response(
    *,
    gate_error_code: str,
    summary: str,
    desktop_route: dict[str, Any] | None,
    recommended_next_tool: str,
    runtime_governed: bool = True,
) -> str:
    payload = {
        "ok": False,
        "status": "blocked",
        "blocked": True,
        "summary": summary,
        "gateErrorCode": gate_error_code,
        "recommendedNextTool": recommended_next_tool,
        "recommendedMode": str((desktop_route or {}).get("recommendedMode") or "").strip() or None,
        "executionReadyMode": str((desktop_route or {}).get("executionReadyMode") or "").strip() or None,
        "runtimeGoverned": bool(runtime_governed),
    }
    payload.update(
        _desktop_route_compact_metadata(
            desktop_route,
            route_gate_applied=True,
            runtime_governed=runtime_governed,
            gate_error_code=gate_error_code,
        )
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _desktop_route_app_stale_reason(
    *,
    desktop_route: dict[str, Any],
    app_query: str | None,
    resolved_app: dict[str, Any] | None,
) -> str | None:
    normalized_requested_app = _desktop_route_normalize_token(app_query)
    if not normalized_requested_app:
        return None
    route_app_id = _desktop_route_normalize_token(desktop_route.get("appId"))
    route_requested_app = _desktop_route_normalize_token(desktop_route.get("requestedApp"))
    resolved_app_id = _desktop_route_normalize_token((resolved_app or {}).get("appId"))
    if route_app_id and resolved_app_id and route_app_id != resolved_app_id:
        return "当前工具目标应用与已解析的桌面路由不一致。"
    if route_requested_app and normalized_requested_app != route_requested_app and route_app_id != resolved_app_id:
        return "当前工具目标应用与已绑定的桌面路由不一致。"
    return None


def _desktop_route_task_mismatch_reason(
    *,
    desktop_route: dict[str, Any],
    goal: str | None,
    target: str | None,
) -> str | None:
    normalized_goal = _desktop_route_normalize_token(goal)
    route_goal = _desktop_route_normalize_token(desktop_route.get("goal"))
    if normalized_goal and route_goal and normalized_goal != route_goal:
        return "当前任务目标与已绑定的桌面路由不一致，请重新调用 computer_use_resolve_execution_route。"

    normalized_target = _desktop_route_normalize_token(target)
    route_target = _desktop_route_normalize_token(desktop_route.get("target"))
    if normalized_target and route_target and normalized_target != route_target:
        return "当前任务 target 与已绑定的桌面路由不一致，请重新调用 computer_use_resolve_execution_route。"
    return None


def _desktop_route_executable_draft_id(desktop_route: dict[str, Any] | None) -> str | None:
    route_payload = dict(desktop_route or {})
    direct_draft_id = str(route_payload.get("recommendedDraftId") or "").strip()
    if direct_draft_id:
        return direct_draft_id
    recommended_match = dict(route_payload.get("recommendedMatch") or {})
    source = dict(recommended_match.get("source") or {})
    source_draft_id = str(source.get("draftId") or "").strip()
    return source_draft_id or None


def _desktop_route_gate(
    *,
    state: dict[str, Any] | None,
    tool_name: str,
    app_query: str | None = None,
    resolved_app: dict[str, Any] | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    if not isinstance(state, dict):
        return True, None, None

    current_route_context = dict(state.get("current_route_context") or {})
    desktop_route = dict(current_route_context.get("desktopRoute") or {})
    if not desktop_route:
        return (
            False,
            _desktop_route_gate_failure_response(
                gate_error_code="ROUTE_GATE_REQUIRED",
                summary="桌面类变更工具必须先调用 computer_use_resolve_execution_route，再按返回的 executionReadyMode 选择执行面。",
                desktop_route=None,
                recommended_next_tool="computer_use_resolve_execution_route",
            ),
            None,
        )

    latest_human_id, _ = _desktop_route_latest_bound_human_message(state)
    bound_human_id = str(desktop_route.get("boundHumanMessageId") or "").strip()
    if bound_human_id and latest_human_id and bound_human_id != latest_human_id:
        return (
            False,
            _desktop_route_gate_failure_response(
                gate_error_code="STALE_ROUTE_CONTEXT",
                summary="桌面路由已过期：检测到新的用户输入或任务上下文，请重新调用 computer_use_resolve_execution_route。",
                desktop_route=desktop_route,
                recommended_next_tool="computer_use_resolve_execution_route",
            ),
            desktop_route,
        )

    stale_app_reason = _desktop_route_app_stale_reason(
        desktop_route=desktop_route,
        app_query=app_query,
        resolved_app=resolved_app,
    )
    if stale_app_reason:
        return (
            False,
            _desktop_route_gate_failure_response(
                gate_error_code="STALE_ROUTE_CONTEXT",
                summary=stale_app_reason,
                desktop_route=desktop_route,
                recommended_next_tool="computer_use_resolve_execution_route",
            ),
            desktop_route,
        )

    execution_ready_mode = str(desktop_route.get("executionReadyMode") or "").strip() or "learn_mode"
    if tool_name in _DESKTOP_ROUTE_RPA_MUTATING_TOOLS:
        if execution_ready_mode not in {"reuse_mode", "hybrid_mode"}:
            return (
                False,
                _desktop_route_gate_failure_response(
                    gate_error_code="RUNTIME_MISMATCH",
                    summary="当前桌面路由已进入 learn_mode，Supervisor 应直接使用高层 computer_use_* 变更工具，而不是继续走 RPA 执行面。",
                    desktop_route=desktop_route,
                    recommended_next_tool="computer_use_launch_app",
                ),
                desktop_route,
            )
    elif tool_name in _DESKTOP_ROUTE_COMPUTER_USE_MUTATING_TOOLS:
        if execution_ready_mode in {"reuse_mode", "hybrid_mode"}:
            return (
                False,
                _desktop_route_gate_failure_response(
                    gate_error_code="RUNTIME_MISMATCH",
                    summary="当前桌面路由要求先从 RPA 主执行链进入，Supervisor 不应直接调用 mutating computer_use_* 工具。",
                    desktop_route=desktop_route,
                    recommended_next_tool=str(desktop_route.get("recommendedTool") or "rpa_run_draft"),
                ),
                desktop_route,
            )

    return True, None, desktop_route


def _computer_use_action_guard(
    *,
    action_type: str,
    target: dict,
    tool_call_id: str,
) -> tuple[bool, str | None]:
    runtime_context = get_runtime_context()
    decision = safety_guardian.assess_computer_use_action(
        action_type=action_type,
        target=target,
        runtime_context=runtime_context,
    )
    question = (
        f"Safety Guardian 检测到桌面控制动作存在风险，是否继续？\n\n"
        f"动作：{action_type}\n"
        f"目标：{json.dumps(target, ensure_ascii=False, indent=2)}"
    )
    return _enforce_safety_decision(decision, tool_call_id=tool_call_id, question=question)


def _guard_computer_use_steps(
    *,
    steps: list[dict],
    tool_call_id: str,
) -> tuple[bool, str | None]:
    for step in steps:
        action = str(step.get("action") or "").strip().lower()
        if action not in {
            "click",
            "double_click",
            "type_text",
            "hotkey",
            "scroll",
            "find_and_type",
            "scroll_list",
            "click_toolbar_action",
        }:
            continue
        effective_action = action
        if action == "find_and_type":
            effective_action = "type_text"
        elif action == "scroll_list":
            effective_action = "scroll"
        elif action == "click_toolbar_action":
            effective_action = "click"
        target = {
            "app_id": step.get("app_id"),
            "selector_key": step.get("selector_key"),
            "action_name": step.get("action_name"),
            "element_id": step.get("element_id"),
            "name": step.get("name"),
            "name_contains": step.get("name_contains"),
            "automation_id": step.get("automation_id"),
            "control_type": step.get("control_type"),
            "class_name": step.get("class_name"),
            "window_title": step.get("window_title"),
            "window_handle": step.get("window_handle"),
            "sequence": step.get("sequence"),
            "amount": step.get("amount"),
            "text_preview": str(step.get("text") or "")[:80] if effective_action == "type_text" else None,
        }
        allowed, error_message = _computer_use_action_guard(
            action_type=effective_action,
            target=target,
            tool_call_id=tool_call_id,
        )
        if not allowed:
            return False, error_message or f"Safety Guardian 已阻止 computer use 步骤：{action}"
    return True, None
