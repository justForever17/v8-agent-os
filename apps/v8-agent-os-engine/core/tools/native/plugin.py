from __future__ import annotations

import json
from typing import Annotated, Literal

from langchain_core.tools import InjectedToolCallId, tool


@tool
async def plugin_broker(
    mode: Literal["list", "status", "authorize"] = "list",
    plugin_id: str = "",
    component_ids: list[str] | None = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Discover or authorize governed plugins for the current Supervisor task.

    ``@插件`` is a strong user hint, not the only authorization path. Use
    ``list`` to inspect installed/available plugin components, ``status`` for a
    named plugin, and ``authorize`` to create the smallest task-scoped grant
    needed by the current run. This tool never installs plugins, changes
    configuration, reads secrets, or creates session-scoped grants. Direct
    subagents receive only an explicit component subset through the delegation
    broker; grandchildren never inherit plugin grants.
    """

    from erc.runtime_context import get_runtime_context
    from runtimes.plugin_manager.service import PluginManagerError, plugin_manager_service

    context = dict(get_runtime_context() or {})
    session_id = str(context.get("session_id") or context.get("sessionId") or "").strip()
    run_id = str(context.get("run_id") or context.get("runId") or "").strip()
    agent_id = str(context.get("agent_id") or context.get("agentId") or "supervisor").strip() or "supervisor"
    runtime_kind = str(context.get("runtime_kind") or context.get("runtimeKind") or "chat").strip().lower()
    if agent_id != "supervisor" or runtime_kind not in {"chat", "supervisor"}:
        return json.dumps(
            {
                "ok": False,
                "mode": str(mode or "list"),
                "error": {"code": "plugin_broker_supervisor_only", "message": "插件授权只能由 Supervisor 发起。"},
            },
            ensure_ascii=False,
        )

    normalized_mode = str(mode or "list").strip().lower()
    normalized_plugin_id = str(plugin_id or "").strip().lower()
    try:
        if normalized_mode == "list":
            payload = plugin_manager_service.supervisor_catalog(
                session_id=session_id or None,
                run_id=run_id or None,
            )
            items = [
                item
                for item in list(payload.get("items") or [])
                if str(item.get("status") or "") != "not_installed"
            ]
            payload.update(
                {
                    "ok": True,
                    "items": items,
                    "count": len(items),
                    "catalogCount": int(payload.get("count") or 0),
                    "nextAction": "需要某个插件时，用 status 查看组件，再用 authorize 创建最小 task grant。",
                }
            )
            return json.dumps(payload, ensure_ascii=False)

        if normalized_mode == "status":
            if not normalized_plugin_id:
                raise PluginManagerError("status 需要 plugin_id", code="plugin_id_required")
            payload = plugin_manager_service.supervisor_catalog(
                plugin_id=normalized_plugin_id,
                session_id=session_id or None,
                run_id=run_id or None,
            )
            payload["ok"] = True
            return json.dumps(payload, ensure_ascii=False)

        if normalized_mode == "authorize":
            if not normalized_plugin_id:
                raise PluginManagerError("authorize 需要 plugin_id", code="plugin_id_required")
            if not session_id or not run_id:
                raise PluginManagerError("当前任务缺少 sessionId/runId，无法创建 task grant", code="grant_context_missing")
            payload = plugin_manager_service.authorize_for_supervisor(
                plugin_id=normalized_plugin_id,
                component_ids=list(component_ids or []),
                session_id=session_id,
                run_id=run_id,
            )
            payload["ok"] = True
            payload["toolCallId"] = str(tool_call_id or "")
            return json.dumps(payload, ensure_ascii=False)

        raise PluginManagerError("不支持的 plugin_broker mode", code="plugin_broker_mode_invalid")
    except PluginManagerError as exc:
        readiness = (
            plugin_manager_service.readiness_status(normalized_plugin_id)
            if normalized_plugin_id
            else {}
        )
        return json.dumps(
            {
                "ok": False,
                "mode": normalized_mode,
                "pluginId": normalized_plugin_id or None,
                "status": readiness.get("status") or "invalid",
                "configurationUrl": readiness.get("configurationUrl"),
                "error": {"code": exc.code, "message": str(exc)},
            },
            ensure_ascii=False,
        )
