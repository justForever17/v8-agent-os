from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool


@tool
async def plugin_cli(
    plugin_id: str,
    profile_id: str,
    action_id: str,
    parameters: dict[str, str | bool] | None = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Run a task-authorized plugin CLI through its signed manifest.

    ``action_id`` must be a built-in lifecycle action or a signed manifest
    action. ``parameters`` are validated against that action's typed schema;
    arbitrary argv and undeclared flags are never accepted. Mutating, paid,
    deployment and deletion operations remain subject to Safety approval.
    """
    from erc.runtime_context import get_runtime_context
    from runtimes.plugin_manager.service import PluginManagerError, plugin_manager_service

    runtime_context = get_runtime_context() or {}
    session_id = str(runtime_context.get("session_id") or runtime_context.get("sessionId") or "").strip()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip() or None
    agent_id = str(runtime_context.get("agent_id") or runtime_context.get("agentId") or "supervisor").strip()
    runtime_kind = str(runtime_context.get("runtime_kind") or runtime_context.get("runtimeKind") or "chat").strip()
    grantee_type = "supervisor" if runtime_kind in {"chat", "supervisor"} or agent_id == "supervisor" else "subagent"
    if not session_id:
        return json.dumps({"ok": False, "error": "plugin_cli requires an active session"}, ensure_ascii=False)
    try:
        result = await plugin_manager_service.execute_cli(
            plugin_id=plugin_id,
            profile_id=profile_id,
            action_id=action_id,
            parameters=dict(parameters or {}),
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=agent_id or "supervisor",
            tool_call_id=tool_call_id,
        )
        return json.dumps({"ok": result.get("returnCode") == 0, **result}, ensure_ascii=False)
    except PluginManagerError as exc:
        return json.dumps({"ok": False, "code": exc.code, "error": str(exc)}, ensure_ascii=False)
