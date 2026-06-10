from __future__ import annotations

import json
import sys
from typing import Annotated, Any, Dict, Optional

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from core.tools.native.desktop_governance import _desktop_route_gate, _desktop_route_merge_into_response
from erc.runtime_context import get_runtime_context

__all__ = [
    "_get_rpa_runtime",
    "_rpa_compact_script_list",
    "_rpa_compact_run_existing_flow_response",
    "_rpa_compact_run_draft_response",
    "rpa_list_robot_scripts",
    "rpa_run_existing_flow",
    "rpa_run_draft",
]


def _compat_native_attr(name: str, local: Any) -> Any:
    native_module = sys.modules.get("core.native_tools")
    if native_module is None:
        return local
    patched = getattr(native_module, name, local)
    if patched is not local:
        return patched
    return local


def _get_rpa_runtime():
    patched = _compat_native_attr("_get_rpa_runtime", _get_rpa_runtime)
    if patched is not _get_rpa_runtime:
        return patched()
    from runtimes.rpa.runtime import rpa_runtime

    return rpa_runtime


def _rpa_parse_variables_json(variables_json: str | None) -> Dict[str, Any]:
    if variables_json in (None, ""):
        return {}
    payload = json.loads(str(variables_json))
    if not isinstance(payload, dict):
        raise ValueError("variables_json 必须是 JSON 对象。")
    return {str(key).strip(): value for key, value in payload.items() if str(key).strip()}


def _rpa_compact_script_list(
    *,
    scripts: list[dict[str, Any]],
    limit: int,
) -> str:
    items: list[dict[str, Any]] = []
    for item in list(scripts or []):
        items.append(
            {
                "name": item.get("name"),
                "path": item.get("path"),
                "updatedAt": item.get("updatedAt"),
                "size": item.get("size"),
            }
        )
    return json.dumps(
        {
            "ok": True,
            "action": "list_robot_scripts",
            "count": len(items),
            "limit": limit,
            "scripts": items,
        },
        ensure_ascii=False,
        indent=2,
    )


def _rpa_compact_run_existing_flow_response(
    *,
    raw_result: dict[str, Any],
    robot_file: str,
    cwd: str | None,
    output_dir: str | None,
) -> str:
    execution = dict(raw_result.get("execution") or {})
    status = str(raw_result.get("status") or "").strip() or "unknown"
    returncode = execution.get("returncode")
    stdout = str(execution.get("stdout") or "")
    stderr = str(execution.get("stderr") or "")
    output_path = (
        str(raw_result.get("outputDir") or "").strip()
        or str(output_dir or "").strip()
        or str((dict(raw_result.get("prepared") or {}).get("outputDir")) or "").strip()
    )
    return json.dumps(
        {
            "ok": status not in {"failed", "review_required", "blocked"},
            "action": "run_existing_rpa_flow",
            "status": status,
            "robotFile": str(raw_result.get("robotFile") or robot_file),
            "cwd": cwd,
            "outputDir": output_path or None,
            "runId": raw_result.get("runId"),
            "sessionId": raw_result.get("sessionId"),
            "execution": {
                "returncode": returncode,
                "stdoutTail": stdout[-4000:] if stdout else "",
                "stderrTail": stderr[-4000:] if stderr else "",
                "command": list(execution.get("command") or []),
            },
            "templateExecutionPolicy": dict(raw_result.get("templateExecutionPolicy") or {}),
            "review": {
                "required": status == "review_required",
                "approvalId": raw_result.get("approvalId"),
                "requiredApprovals": raw_result.get("requiredApprovals"),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def _rpa_compact_run_draft_response(
    *,
    raw_result: dict[str, Any],
    script_id: str,
    cwd: str | None,
    output_dir: str | None,
) -> str:
    execution = dict(raw_result.get("execution") or {})
    status = str(raw_result.get("status") or "").strip() or "unknown"
    stdout = str(execution.get("stdout") or "")
    stderr = str(execution.get("stderr") or "")
    prepared = dict(raw_result.get("prepared") or {})
    script = dict(raw_result.get("script") or prepared.get("script") or {})
    output_path = (
        str(raw_result.get("outputDir") or "").strip()
        or str(output_dir or "").strip()
        or str(prepared.get("outputDir") or "").strip()
    )
    return json.dumps(
        {
            "ok": status not in {"failed", "review_required", "blocked"},
            "action": "run_rpa_draft",
            "status": status,
            "scriptId": str(raw_result.get("scriptId") or script_id),
            "scriptName": script.get("name") or prepared.get("scriptName"),
            "goal": script.get("goal") or prepared.get("goal"),
            "appId": script.get("appId") or prepared.get("appId"),
            "cwd": cwd,
            "outputDir": output_path or None,
            "runId": raw_result.get("runId"),
            "sessionId": raw_result.get("sessionId"),
            "execution": {
                "returncode": execution.get("returncode"),
                "stdoutTail": stdout[-4000:] if stdout else "",
                "stderrTail": stderr[-4000:] if stderr else "",
                "command": list(execution.get("command") or []),
            },
            "templateExecutionPolicy": dict(raw_result.get("templateExecutionPolicy") or {}),
            "review": {
                "required": status == "review_required",
                "approvalId": raw_result.get("approvalId"),
                "requiredApprovals": raw_result.get("requiredApprovals"),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
def rpa_list_robot_scripts(
    limit: int = 20,
) -> str:
    """List locally available .robot scripts managed by the active RPA script store."""
    try:
        scripts = _get_rpa_runtime().script_store.list_robot_scripts(limit=max(1, min(limit, 100)))
        return _rpa_compact_script_list(scripts=list(scripts or []), limit=max(1, min(limit, 100)))
    except Exception as e:
        return f"Error listing RPA robot scripts: {e}"


@tool
def rpa_run_existing_flow(
    robot_file: str,
    *,
    variables_json: Optional[str] = None,
    timeout_ms: int = 600000,
    cwd: Optional[str] = None,
    output_dir: Optional[str] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    user_id: Optional[str] = "anonymous",
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    trigger_source: Optional[str] = "manual",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Run an existing .robot flow through RPARuntime without requiring trace compilation."""
    normalized_robot_file = str(robot_file or "").strip()
    if not normalized_robot_file:
        return "Error: robot_file 不能为空。"
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="rpa_run_existing_flow",
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    try:
        variables = _rpa_parse_variables_json(variables_json)
        raw_result = _get_rpa_runtime().run_existing_flow(
            robot_file=normalized_robot_file,
            variables=variables,
            output_dir=output_dir,
            timeout_ms=max(1000, min(timeout_ms, 3_600_000)),
            cwd=cwd,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id or "anonymous",
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            trigger_source=trigger_source or "manual",
        )
        response = _rpa_compact_run_existing_flow_response(
            raw_result=dict(raw_result or {}),
            robot_file=normalized_robot_file,
            cwd=cwd,
            output_dir=output_dir,
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error running existing .robot flow: {e}"


@tool
def rpa_run_draft(
    script_id: str,
    *,
    variables_json: Optional[str] = None,
    timeout_ms: int = 600000,
    cwd: Optional[str] = None,
    output_dir: Optional[str] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    user_id: Optional[str] = "anonymous",
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    trigger_source: Optional[str] = "manual",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Run an existing RPA draft script through RPARuntime."""
    normalized_script_id = str(script_id or "").strip()
    if not normalized_script_id:
        return "Error: script_id 不能为空。"
    gate_allowed, gate_failure, desktop_route = _desktop_route_gate(
        state=state,
        tool_name="rpa_run_draft",
    )
    if not gate_allowed:
        return gate_failure or "Error: 桌面执行路由校验失败。"
    runtime_context = get_runtime_context()
    try:
        variables = _rpa_parse_variables_json(variables_json)
        raw_result = _get_rpa_runtime().run_draft(
            script_id=normalized_script_id,
            variables=variables,
            output_dir=output_dir,
            timeout_ms=max(1000, min(timeout_ms, 3_600_000)),
            cwd=cwd,
            session_id=session_id or runtime_context.get("session_id"),
            run_id=run_id or runtime_context.get("run_id"),
            user_id=user_id or runtime_context.get("user_id") or "anonymous",
            project_id=project_id or runtime_context.get("project_id"),
            workspace_id=workspace_id or runtime_context.get("workspace_id"),
            workspace_path=workspace_path or runtime_context.get("workspace_path"),
            trigger_source=trigger_source or "manual",
        )
        response = _rpa_compact_run_draft_response(
            raw_result=dict(raw_result or {}),
            script_id=normalized_script_id,
            cwd=cwd,
            output_dir=output_dir,
        )
        return _desktop_route_merge_into_response(
            response,
            desktop_route=desktop_route,
            route_gate_applied=isinstance(state, dict),
        )
    except Exception as e:
        return f"Error running RPA draft: {e}"

