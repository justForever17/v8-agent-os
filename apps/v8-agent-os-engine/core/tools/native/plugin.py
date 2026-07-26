from __future__ import annotations

import json
from typing import Annotated, Literal

from langchain_core.tools import InjectedToolCallId, tool


@tool
async def plugin_broker(
    mode: Literal["list", "status", "authorize", "request"] = "list",
    plugin_id: str = "",
    component_ids: list[str] | None = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Discover, inspect, request, or authorize governed plugins.

    ``@插件`` is a strong user hint, not the only authorization path. Use
    ``list`` to inspect installed/available plugin components and ``status`` for
    a named plugin. A named status returns bounded CLI help plus the real Skill
    and MCP names/descriptions from their registered metadata. Component IDs are
    grant identifiers, never CLI actions, Skill names, or MCP tool names. Use
    ``authorize`` for the smallest component set needed by the current run, then
    invoke authorized CLI actions through ``plugin_cli`` with ``actionId`` plus
    typed parameters. Never bypass a plugin grant through ``run_system_command``.
    This tool never installs plugins, changes
    configuration, reads secrets, or creates session-scoped grants. Supervisor
    may authorize. Direct children and grandchildren may inspect only grants
    bound to their exact delegation identity, or return a structured request to
    their parent. A direct child may pass a smaller component subset to one
    grandchild layer through delegation_broker; grandchildren cannot delegate.
    """

    from erc.runtime_context import get_runtime_context
    from core.actor_identity import resolve_collaboration_actor
    from runtimes.plugin_manager.service import PluginManagerError, plugin_manager_service

    context = dict(get_runtime_context() or {})
    session_id = str(context.get("session_id") or context.get("sessionId") or "").strip()
    run_id = str(context.get("run_id") or context.get("runId") or "").strip()
    agent_id = str(context.get("agent_id") or context.get("agentId") or "supervisor").strip() or "supervisor"
    delegation_id = str(context.get("delegation_id") or context.get("delegationId") or "").strip() or None
    delegation_depth = context.get("delegation_depth") or context.get("delegationDepth")
    actor = resolve_collaboration_actor(runtime_context=context)

    normalized_mode = str(mode or "list").strip().lower()
    normalized_plugin_id = str(plugin_id or "").strip().lower()
    try:
        if normalized_mode not in {"list", "status", "authorize", "request"}:
            raise PluginManagerError("不支持的 plugin_broker mode", code="plugin_broker_mode_invalid")
        if not actor.is_collaboration_actor:
            raise PluginManagerError("当前内部 actor 不在插件协作授权面。", code="plugin_actor_not_supported", status_code=403)

        if not actor.is_supervisor:
            grants = plugin_manager_service.active_grants(
                session_id=session_id,
                run_id=run_id or None,
                grantee_type="subagent",
                grantee_id=agent_id,
                delegation_id=delegation_id,
            ) if session_id and delegation_id else []
            if normalized_mode == "authorize":
                raise PluginManagerError("只有 Supervisor 可以创建插件授权。", code="plugin_authorize_supervisor_only", status_code=403)
            if normalized_mode == "request":
                if not normalized_plugin_id or not list(component_ids or []):
                    raise PluginManagerError("request 需要 plugin_id 和最小 component_ids。", code="plugin_request_incomplete")
                return json.dumps(
                    {
                        "ok": False,
                        "mode": "request",
                        "status": "needs_parent_authorization",
                        "pluginId": normalized_plugin_id,
                        "componentIds": sorted({str(item).strip() for item in list(component_ids or []) if str(item).strip()}),
                        "delegationId": delegation_id,
                        "delegationDepth": int(delegation_depth or actor.delegation_depth),
                        "nextAction": "把这项插件需求作为结构化 blocker 回传父 Agent；不得自行安装、配置、读取密钥或扩大组件范围。",
                        "error": {"code": "plugin_parent_authorization_required", "message": "需要父级已有授权后再通过委派合同投影。"},
                    },
                    ensure_ascii=False,
                )
            visible_grants = [
                grant
                for grant in grants
                if not normalized_plugin_id or str(grant.get("pluginId") or "").strip().lower() == normalized_plugin_id
            ]
            return json.dumps(
                {
                    "ok": True,
                    "mode": normalized_mode,
                    "actor": actor.role,
                    "delegationId": delegation_id,
                    "delegationDepth": actor.delegation_depth,
                    "items": [
                        {
                            "pluginId": grant.get("pluginId"),
                            "status": "authorized",
                            "componentIds": list(grant.get("componentIds") or []),
                            "grantId": grant.get("grantId"),
                        }
                        for grant in visible_grants
                    ],
                    "count": len(visible_grants),
                    "nextAction": "直接调用已投影的插件工具；未授权需求用 request 回传父级。",
                },
                ensure_ascii=False,
            )

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

        if normalized_mode == "request":
            raise PluginManagerError("Supervisor 无需 request；请先 status，再 authorize 最小组件集合。", code="plugin_request_not_needed")

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
